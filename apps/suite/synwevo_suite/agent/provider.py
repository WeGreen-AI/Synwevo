"""The model behind the personal Agent: which provider, with whose credential, and one streamed step.

Configuration is the deployment's (`SYNWEVO_AGENT_ANTHROPIC_API_KEY`, `SYNWEVO_AGENT_MODEL`) or the
person's own key (sealed in `agent_settings`). With neither there is no model: the Agent says it is
not configured — it never answers with canned text.

A step is one Messages API call, streamed. The runner hands text as it arrives to a callback and
gets back the final message (content blocks as plain dicts, stop reason, usage) or a
:class:`ProviderError` with a stable code the page can word.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from synwevo_core import env, sealed
from synwevo_core.store import Store

log = logging.getLogger("synwevo.agent")

EFFORTS = ("low", "medium", "high", "xhigh", "max")
KEY_PURPOSE = "agent-provider-key"


class ProviderError(Exception):
    """A failure the person should see in words: ``code`` is stable, ``retryable`` says whether
    trying again later can help, ``retry_after`` (seconds) when the provider said so."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


class Cancelled(Exception):
    pass


@dataclass
class StepResult:
    content: list[dict]
    stop_reason: str | None
    usage: dict = field(default_factory=dict)
    model: str | None = None
    stop_details: dict | None = None


@dataclass
class Credential:
    kind: str                       # "platform" or "personal"
    api_key: str = field(repr=False)
    model: str

    @property
    def label(self) -> str:
        return f"anthropic:{self.kind}"


class Provider(Protocol):
    name: str

    def step(self, *, credential: Credential, system: list[dict], tools: list[dict], messages: list[dict],
             effort: str, on_text: Callable[[str], None], should_stop: Callable[[], bool]) -> StepResult: ...


def configured_model() -> str | None:
    model = (env.get("AGENT_MODEL") or "").strip()
    return model if model and len(model) <= 200 and not any(c.isspace() for c in model) else None


def resolve_credential(store: Store, user_id: str) -> Credential | None:
    """The credential this person's next turn uses: their own key when they prefer it and have
    one, else the platform's, else nothing."""
    model = configured_model()
    if not model:
        return None
    row = store.one("SELECT prefer_personal_key, personal_key_sealed FROM agent_settings WHERE user_id=?", (user_id,))
    platform_key = (env.get("AGENT_ANTHROPIC_API_KEY") or "").strip()
    personal = None
    if row and row["personal_key_sealed"]:
        try:
            personal = sealed.unseal(store, KEY_PURPOSE, row["personal_key_sealed"])
        except Exception:  # noqa: BLE001 — a key that no longer opens is as good as none
            log.warning("agent personal key for a user could not be unsealed")
    if personal and (row["prefer_personal_key"] or not platform_key):
        return Credential("personal", personal, model)
    if platform_key:
        return Credential("platform", platform_key, model)
    return None


def replayable(content: list[dict]) -> list[dict]:
    """The assistant content as it must be sent back on the next request.

    Blocks are echoed unchanged (thinking blocks included — they carry the model's context and
    stripping them breaks the request), except after a mid-output fallback: blocks before the last
    ``fallback`` marker that belong to the declined model's attempt (thinking, redacted thinking,
    tool use) are omitted, as the API requires."""
    last = max((i for i, b in enumerate(content) if b.get("type") == "fallback"), default=-1)
    if last < 0:
        return content
    keep = []
    for i, b in enumerate(content):
        if i < last and b.get("type") in ("thinking", "redacted_thinking", "tool_use", "server_tool_use"):
            continue
        keep.append(b)
    return keep


class AnthropicProvider:
    """The configured Claude model through the official SDK; no model fallback."""
    name = "anthropic"

    def __init__(self, *, timeout_s: float = 120.0, max_tokens: int = 16000):
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens

    def step(self, *, credential: Credential, system: list[dict], tools: list[dict], messages: list[dict],
             effort: str, on_text: Callable[[str], None], should_stop: Callable[[], bool]) -> StepResult:
        import anthropic

        # D5: the host's Claude tooling configuration is not a product provider configuration.
        # The SDK otherwise inherits ANTHROPIC_BASE_URL and ANTHROPIC_CUSTOM_HEADERS.
        ignored_headers = {line.partition(":")[0].strip(): anthropic.omit
                           for line in os.environ.get("ANTHROPIC_CUSTOM_HEADERS", "").splitlines() if ":" in line}
        client = anthropic.Anthropic(api_key=credential.api_key, base_url="https://api.anthropic.com",
                                     default_headers=ignored_headers, timeout=self.timeout_s, max_retries=2)
        kwargs: dict[str, Any] = dict(
            model=credential.model, max_tokens=self.max_tokens, system=system, tools=tools, messages=messages,
            output_config={"effort": effort if effort in EFFORTS else "medium"},
            cache_control={"type": "ephemeral"},
        )
        try:
            if should_stop():
                raise Cancelled()
            with client.messages.stream(**kwargs) as stream:
                for event in stream:
                    if should_stop():
                        raise Cancelled()
                    if event.type == "text":
                        on_text(event.text)
                final = stream.get_final_message()
        except Cancelled:
            raise
        except ValueError as exc:
            # The SDK could not parse a streamed tool input at all; the step is re-issued by the runner.
            raise ProviderError("provider_bad_output", "模型返回的内容无法解析，请重试", retryable=True) from exc
        except anthropic.AuthenticationError as exc:
            raise ProviderError("provider_auth", "模型服务拒绝了访问凭据，请检查密钥是否有效") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError("provider_forbidden", "模型服务不允许这个凭据使用该模型") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError("provider_model_missing", "配置的模型不存在或不可用") from exc
        except anthropic.RateLimitError as exc:
            after = _retry_after(exc)
            raise ProviderError("provider_rate_limited", "模型服务的调用额度暂时用尽，请稍后再试",
                                retryable=True, retry_after=after) from exc
        except anthropic.BadRequestError as exc:
            # Provider error bodies can echo prompts and credentials. Never log them.
            log.warning("agent provider rejected a request (bad_request)")
            raise ProviderError("provider_bad_request", "模型服务拒绝了这次请求") from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderError("provider_timeout", "模型服务响应超时，请重试", retryable=True) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("provider_unreachable", "无法连接模型服务，请稍后重试", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            code = "provider_overloaded" if exc.status_code == 529 else "provider_error"
            raise ProviderError(code, "模型服务暂时不可用，请稍后重试", retryable=exc.status_code >= 500) from exc
        finally:
            client.close()
        content = [b.model_dump(mode="json", exclude_none=True) for b in final.content]
        usage = final.usage.model_dump(mode="json", exclude_none=True) if final.usage else {}
        details = final.stop_details.model_dump(mode="json") if getattr(final, "stop_details", None) else None
        return StepResult(content=content, stop_reason=final.stop_reason, usage=usage, model=final.model,
                          stop_details=details)


def _retry_after(exc) -> float | None:
    try:
        return float(exc.response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        return None
