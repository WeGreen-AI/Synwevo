"""Bounded, unwired DeepSeek transport. See docs/deepseek-provider.md before integration.

One invocation is one network attempt. There is no credential lookup, route selection,
retry, tool execution or billing here. The envelope deliberately is NOT a StepResult:
the gateway must persist attempt/usage metadata before handing its step to the engine.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

import httpx

from synwevo_core.billing.upstream import OFFICIAL_ORIGIN, loopback_origin, upstream_base  # noqa: F401 - re-exported

from .provider import Cancelled, Credential, ProviderError, StepResult

BASE_URL = OFFICIAL_ORIGIN
ENDPOINT = BASE_URL + "/chat/completions"
_STATUS = {400: "provider_bad_request", 401: "provider_auth", 402: "provider_balance",
           403: "provider_forbidden", 404: "provider_model_missing", 422: "provider_bad_request",
           429: "provider_rate_limited", 500: "provider_error", 503: "provider_overloaded"}
CAPABILITY_VERSION = "deepseek-chat-2026-09-30"
_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
#: DeepSeek reasoning_effort values (thinking-mode guide, re-read 2026-10-05). "none" means
#: thinking off: the request then says thinking.type=disabled and sends no reasoning_effort.
EFFORTS = ("none", "low", "high", "max")
#: The Agent's effort setting -> DeepSeek value (the provider has no medium/xhigh level).
_EFFORT = {"none": "none", "low": "low", "medium": "high", "high": "high", "xhigh": "high", "max": "max"}


def deepseek_effort(effort: object) -> str | None:
    """The DeepSeek reasoning_effort for an Agent effort setting, or None when unknown."""
    return _EFFORT.get(effort) if isinstance(effort, str) else None


def route_effort(wanted: str, allowed) -> str:
    """The level a governed route serves for a wanted DeepSeek effort: the allowed level nearest
    to it in EFFORTS order, the lower (cheaper) one on a tie; "none" when that is all the route
    offers. The person's setting is a preference; the route decides which levels exist."""
    order = EFFORTS.index(wanted)
    return min(allowed_efforts(allowed), key=lambda value: (abs(EFFORTS.index(value) - order), EFFORTS.index(value)))


def allowed_efforts(values) -> tuple[str, ...]:
    """A route's allowed DeepSeek efforts in canonical order; refuses empty/unknown/duplicates."""
    if not isinstance(values, (list, tuple)) or not values or len(set(values)) != len(values) \
            or any(value not in EFFORTS for value in values):
        raise ValueError("Invalid DeepSeek reasoning efforts")
    return tuple(value for value in EFFORTS if value in values)
_FINISH = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens",
           "content_filter": "refusal", "aborted": "aborted",
           "insufficient_system_resource": "insufficient_system_resource"}


@dataclass(frozen=True)
class Limits:
    request_bytes: int = 1_048_576
    stream_bytes: int = 2_097_152
    event_bytes: int = 262_144
    events: int = 20_000
    text_bytes: int = 64_000
    reasoning_bytes: int = 262_144
    tool_argument_bytes: int = 65_536
    all_tool_argument_bytes: int = 262_144
    tool_calls: int = 20
    max_tokens: int = 8192
    timeout_s: float = 120.0
    read_timeout_s: float = 15.0
    connect_timeout_s: float = 10.0
    cancel_poll_s: float = 0.05

    def __post_init__(self):
        ceilings = {"request_bytes": 2_097_152, "stream_bytes": 8_388_608,
                    "event_bytes": 1_048_576, "events": 100_000, "text_bytes": 256_000,
                    "reasoning_bytes": 1_048_576, "tool_argument_bytes": 262_144,
                    "all_tool_argument_bytes": 1_048_576, "tool_calls": 20, "max_tokens": 32768}
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError("Invalid DeepSeek size limit")
        for name, ceiling in (("timeout_s", 300), ("read_timeout_s", 60),
                              ("connect_timeout_s", 30), ("cancel_poll_s", 1)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= ceiling:
                raise ValueError("Invalid DeepSeek time limit")


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: int | None = None  # Already included in completion_tokens.


@dataclass
class Attempt:
    requested_model: str | None = None
    dispatch: Literal["definitely_not_requested", "unknown", "observed"] = "definitely_not_requested"
    provider_request_id: str | None = None  # Completion body id, not an invented HTTP header.
    returned_model: str | None = None
    effective_route: str | None = None  # Needs independent coordinator verification.
    capability_version: str = CAPABILITY_VERSION
    http_status: int | None = None
    finish_reason: str | None = None
    stream_complete: bool = False
    usage_reliability: Literal["pending", "unreliable", "trusted_terminal"] = "pending"
    usage_reason: str = "terminal_usage_missing"
    usage: TokenUsage | None = None
    raw_usage: object = field(default=None, repr=False)


@dataclass
class DeepSeekResult:
    step: StepResult = field(repr=False)  # Visible text / validated tool JSON only.
    replay_message: dict = field(repr=False)  # Private provider history, including reasoning.
    attempt: Attempt


class DeepSeekError(ProviderError):
    def __init__(self, code: str, attempt: Attempt, *, retry_after: float | None = None,
                 message: str = "模型请求未完成，请核实调用状态"):
        super().__init__(code, message, retryable=False, retry_after=retry_after)
        self.attempt = attempt
        # Private reasoning/visible text received before the failure (never unrun tool calls).
        # Thinking mode with tools refuses later requests that omit it (docs/deepseek-provider.md).
        self.replay_message: dict | None = None


class DeepSeekCancelled(Cancelled):
    def __init__(self, attempt: Attempt):
        super().__init__("模型请求已停止")
        self.attempt = attempt
        self.replay_message: dict | None = None


def _with_partial(exc, state: dict):
    assembly = state.get("assembly")
    if assembly is not None and getattr(exc, "replay_message", None) is None:
        exc.replay_message = assembly.partial()
    if assembly is not None and isinstance(exc, DeepSeekCancelled):
        assembly.usage_before_cancel()
    return exc


class _Invalid(Exception):
    pass


def _require(condition: bool) -> None:
    if not condition:
        raise _Invalid()


def _integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def normalize_usage(raw: object) -> TokenUsage | None:
    """No coercions, inferred cache misses, zero defaults or duplicate categories."""
    if not isinstance(raw, dict):
        return None
    keys = ("prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
            "completion_tokens", "total_tokens")
    if not all(_integer(raw.get(k)) for k in keys):
        return None
    prompt, hit, miss, output, total = (raw[k] for k in keys)
    if prompt != hit + miss or total != prompt + output:
        return None
    if "prompt_tokens_details" in raw:
        detail = raw["prompt_tokens_details"]
        if not isinstance(detail, dict) or not _integer(detail.get("cached_tokens")) or detail["cached_tokens"] != hit:
            return None
    reasoning = None
    if "completion_tokens_details" in raw:
        detail = raw["completion_tokens_details"]
        if not isinstance(detail, dict):
            return None
        if "reasoning_tokens" in detail:
            reasoning = detail["reasoning_tokens"]
            if not _integer(reasoning) or reasoning > output:
                return None
    return TokenUsage(prompt, hit, miss, output, total, reasoning)


def _object_pairs(pairs):
    out = {}
    for key, value in pairs:
        _require(key not in out)
        out[key] = value
    return out


def _constant(_):
    raise _Invalid()


def _loads(data: str):
    return json.loads(data, object_pairs_hook=_object_pairs, parse_constant=_constant)


def _dump(value: object, limit: int) -> bytes:
    # Validate depth/node count before recursive JSON encoding; include every field in the
    # request budget (system, tool schemas, tool results and private reasoning history).
    stack, nodes, chars = [(value, 0)], 0, 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        _require(nodes <= 100_000 and depth <= 64)
        if isinstance(item, dict):
            _require(len(item) <= 10_000 and all(isinstance(k, str) for k in item))
            stack.extend((v, depth + 1) for v in item.values())
            stack.extend((k, depth + 1) for k in item)
        elif isinstance(item, list):
            _require(len(item) <= 10_000)
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, str):
            chars += len(item)
            _require(chars <= limit)
        else:
            _require(item is None or type(item) in (bool, int, float))
    pieces, size = [], 0
    for piece in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
        encoded = piece.encode("utf-8")
        size += len(encoded)
        _require(size <= limit)
        pieces.append(encoded)
    return b"".join(pieces)


def _text(value) -> str:
    if isinstance(value, str):
        return value
    _require(isinstance(value, list))
    _require(all(isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str) for b in value))
    return "\n".join(b["text"] for b in value)


def _history(messages: list[dict], limits: Limits, *, thinking: bool = False) -> list[dict]:
    """Accept native DeepSeek replay messages or the engine's text/tool block history.

    Foreign thinking/signatures/images are refused, never silently stripped. Native
    replay_message is the lossless route for reasoning and exact tool argument strings.
    With thinking on, every assistant message carries a reasoning string: an earlier turn that
    produced none (thinking off, legacy visible-text replay) is sent with an empty one, because
    a tool-carrying request that omits any earlier reasoning_content is refused with HTTP 400.
    """
    out, pending, used = [], set(), set()
    for message in messages:
        role, content = message.get("role"), message.get("content")
        _require(role in ("user", "assistant", "tool"))
        calls = []
        if role == "assistant":
            _require(not pending)
            if isinstance(content, list):
                texts = []
                for block in content:
                    if block.get("type") == "text":
                        texts.append(_text(block.get("text")))
                    else:
                        _require(block.get("type") == "tool_use" and isinstance(block.get("input"), dict))
                        calls.append({"id": block.get("id"), "type": "function", "function": {
                            "name": block.get("name"), "arguments": _dump(block["input"], limits.tool_argument_bytes).decode()}})
                msg = {"role": role, "content": "\n".join(texts) or None}
            else:
                _require(content is None or isinstance(content, str))
                msg = {"role": role, "content": content}
                calls = message.get("tool_calls") or []
            _require(isinstance(calls, list) and len(calls) <= limits.tool_calls)
            clean_calls = []
            for call in calls:
                cid, fn = call.get("id"), call.get("function", {})
                _require(isinstance(cid, str) and bool(_ID.fullmatch(cid)) and cid not in used)
                _require(call.get("type") == "function" and isinstance(fn.get("name"), str) and bool(_NAME.fullmatch(fn["name"])))
                args = fn.get("arguments")
                _require(isinstance(args, str) and len(args.encode()) <= limits.tool_argument_bytes and isinstance(_loads(args), dict))
                pending.add(cid)
                used.add(cid)
                clean_calls.append({"id": cid, "type": "function", "function": {"name": fn["name"], "arguments": args}})
            if clean_calls:
                msg["tool_calls"] = clean_calls
            if "reasoning_content" in message:
                _require(isinstance(message["reasoning_content"], str))
                msg["reasoning_content"] = message["reasoning_content"]
            elif thinking:
                msg["reasoning_content"] = ""
            out.append(msg)
        elif role == "tool":
            cid = message.get("tool_call_id")
            _require(isinstance(cid, str) and cid in pending)
            pending.remove(cid)
            out.append({"role": "tool", "tool_call_id": cid, "content": _text(content)})
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    cid = block.get("tool_use_id")
                    _require(isinstance(cid, str) and cid in pending)
                    pending.remove(cid)
                    out.append({"role": "tool", "tool_call_id": cid, "content": _text(block.get("content"))})
                else:
                    _require(not pending and block.get("type") == "text")
                    out.append({"role": "user", "content": _text(block.get("text"))})
        else:
            _require(not pending)
            out.append({"role": "user", "content": _text(content)})
    _require(bool(out) and not pending)
    return out


class _SSE:
    """Incremental byte framing, including split UTF-8/CRLF and bounded comments."""
    def __init__(self, limits: Limits):
        self.limits = limits
        self.line = bytearray()
        self.data: list[bytes] = []
        self.event_size = self.total = self.count = 0
        self.after_cr = False

    def feed(self, chunk: bytes):
        self.total += len(chunk)
        _require(self.total <= self.limits.stream_bytes)
        for byte in chunk:
            if self.after_cr and byte == 10:
                self.after_cr = False
                continue
            self.after_cr = byte == 13
            self.event_size += 1
            _require(self.event_size <= self.limits.event_bytes)
            if byte not in (10, 13):
                self.line.append(byte)
                continue
            line = bytes(self.line)
            self.line.clear()
            if not line:
                self.count += 1
                _require(self.count <= self.limits.events)
                self.event_size = 0
                if self.data:
                    data = b"\n".join(self.data).decode("utf-8")
                    self.data.clear()
                    yield data
            elif line.startswith(b"data:"):
                self.data.append(line[6:] if line.startswith(b"data: ") else line[5:])
            elif line == b"data":
                self.data.append(b"")
            elif line.startswith(b"event:"):
                _require(line[6:].strip() in (b"", b"message"))
            else:
                # Keep-alive comments and SSE id/retry fields never affect usage/retries.
                _require(line.startswith((b":", b"id:", b"retry:")))


class _Assembly:
    def __init__(self, attempt: Attempt, limits: Limits, tool_names: set[str]):
        self.attempt, self.limits, self.tool_names = attempt, limits, tool_names
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.calls: dict[int, dict] = {}
        self.sizes = {"content": 0, "reasoning_content": 0, "arguments": 0}
        self.terminal_usage = None
        self.early_usage = False

    def partial(self) -> dict | None:
        """The private replay of an interrupted step: reasoning and visible text, no tool calls.

        Unrun (or unparsed) tool calls are dropped: they were never executed, so no tool result
        can follow them. The reasoning stays because later thinking-mode requests need it.
        """
        text, reasoning = "".join(self.text), "".join(self.reasoning)
        if not text and not reasoning:
            return None
        return {"role": "assistant", "content": text, "reasoning_content": reasoning}

    def usage_before_cancel(self) -> None:
        """The person stopped after the final chunk (finish_reason + usage) arrived and before
        [DONE]: that chunk is the provider's last word on usage, so it is kept as trusted
        evidence. Stopping any earlier leaves usage unknown, exactly as before."""
        a = self.attempt
        if a.finish_reason is None or self.terminal_usage is None or self.early_usage or a.usage_reliability != "pending":
            return
        usage = normalize_usage(self.terminal_usage)
        if usage is not None:
            a.usage, a.raw_usage = usage, self.terminal_usage
            a.usage_reliability, a.usage_reason = "trusted_terminal", "terminal_usage_before_cancel"

    def chunk(self, chunk: dict, on_text: Callable[[str], None]):
        a = self.attempt
        _require(isinstance(chunk, dict) and "error" not in chunk and not a.finish_reason)
        _require(chunk.get("object") == "chat.completion.chunk")
        cid, model = chunk.get("id"), chunk.get("model")
        _require(isinstance(cid, str) and bool(_ID.fullmatch(cid)))
        _require(isinstance(model, str) and bool(_ID.fullmatch(model)))
        _require(a.provider_request_id in (None, cid) and a.returned_model in (None, model))
        a.provider_request_id, a.returned_model = cid, model
        choices = chunk.get("choices")
        _require(isinstance(choices, list) and len(choices) == 1)
        choice = choices[0]
        _require(isinstance(choice, dict) and type(choice.get("index")) is int and choice["index"] == 0)
        delta, finish = choice.get("delta"), choice.get("finish_reason")
        _require(isinstance(delta, dict) and (finish is None or finish in _FINISH))
        _require(delta.get("role") in (None, "assistant"))
        for key, parts, bound in (("content", self.text, self.limits.text_bytes),
                                   ("reasoning_content", self.reasoning, self.limits.reasoning_bytes)):
            value = delta.get(key)
            if value is not None:
                _require(isinstance(value, str))
                self.sizes[key] += len(value.encode())
                _require(self.sizes[key] <= bound)
                parts.append(value)
                if key == "content" and value:
                    on_text(value)
        tool_deltas = delta.get("tool_calls") or []
        _require(isinstance(tool_deltas, list) and len(tool_deltas) <= self.limits.tool_calls)
        for tool in tool_deltas:
            index, fn = tool.get("index"), tool.get("function")
            _require(type(index) is int and 0 <= index < self.limits.tool_calls and isinstance(fn, dict))
            if index not in self.calls:
                tid, name = tool.get("id"), fn.get("name")
                _require(tool.get("type") == "function" and isinstance(tid, str) and bool(_ID.fullmatch(tid)))
                _require(isinstance(name, str) and name in self.tool_names)
                _require(tid not in {c["id"] for c in self.calls.values()})
                self.calls[index] = {"id": tid, "name": name, "parts": [], "size": 0}
            call = self.calls[index]
            _require(tool.get("id") in (None, call["id"]) and tool.get("type") in (None, "function"))
            _require(fn.get("name") in (None, call["name"]))
            fragment = fn.get("arguments", "")
            _require(isinstance(fragment, str))
            size = len(fragment.encode())
            call["size"] += size
            self.sizes["arguments"] += size
            _require(call["size"] <= self.limits.tool_argument_bytes and self.sizes["arguments"] <= self.limits.all_tool_argument_bytes)
            call["parts"].append(fragment)
        if chunk.get("usage") is not None:
            if finish is None:
                self.early_usage = True
            else:
                self.terminal_usage = chunk["usage"]
                a.raw_usage = chunk["usage"]  # Retain evidence even if [DONE] never arrives.
                a.usage_reason = "stream_incomplete"
        a.finish_reason = finish

    def done(self) -> DeepSeekResult:
        a = self.attempt
        _require(a.finish_reason is not None)
        a.stream_complete = True
        a.raw_usage = self.terminal_usage
        if self.early_usage:
            a.usage_reliability, a.usage_reason = "unreliable", "nonterminal_usage"
        elif self.terminal_usage is not None:
            a.usage = normalize_usage(self.terminal_usage)
            a.usage_reliability = "trusted_terminal" if a.usage else "unreliable"
            a.usage_reason = "terminal_usage_valid" if a.usage else "terminal_usage_invalid"
        text = "".join(self.text)
        content = [{"type": "text", "text": text}] if text else []
        replay = {"role": "assistant", "content": text or None}
        if self.reasoning:
            replay["reasoning_content"] = "".join(self.reasoning)
        _require(a.finish_reason != "tool_calls" or bool(self.calls))
        _require(not self.calls or a.finish_reason == "tool_calls")
        native = []
        _require(sorted(self.calls) == list(range(len(self.calls))))
        for call in (self.calls[i] for i in sorted(self.calls)):
            arguments = "".join(call["parts"])
            parsed = _loads(arguments)
            _require(isinstance(parsed, dict))
            _dump(parsed, self.limits.tool_argument_bytes)  # Reject excessive nesting / nonfinite numbers.
            content.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": parsed})
            native.append({"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": arguments}})
        if native:
            replay["tool_calls"] = native
        if a.finish_reason in ("aborted", "insufficient_system_resource"):
            raise DeepSeekError("provider_interrupted", a)
        # This intentionally does not translate absent usage into the engine's zero defaults.
        # A content-filter stop stays a refusal, named so the Agent can tell it apart (safety.py).
        details = {"category": "content_filter"} if a.finish_reason == "content_filter" else None
        return DeepSeekResult(StepResult(content, _FINISH[a.finish_reason], model=a.returned_model,
                                         stop_details=details), replay, a)


#: One origin rule for every request carrying the platform key (chat, GET /models, and the risk
#: monitor's GET /user/balance in synwevo_core): synwevo_core.billing.upstream.upstream_base.
_loopback = loopback_origin


def _base(value: str) -> str:
    if value != BASE_URL and not _loopback(value):
        raise ValueError("DeepSeek upstream must be the official origin or a loopback test double")
    return value.rstrip("/")


async def alist_models(credential: Credential, *, transport: httpx.AsyncBaseTransport | None = None,
                       base_url: str = BASE_URL, timeout_s: float = 15.0) -> frozenset[str]:
    """GET /models: the model ids the platform account can currently request.

    One bounded request, no redirects/retries/proxies; the key goes only to the official (or
    loopback test) origin. Errors are DeepSeekError with the same codes as chat requests.
    """
    a = Attempt()
    base = _base(base_url)
    if (credential.provider != "deepseek" or not isinstance(credential.api_key, str)
            or not 1 <= len(credential.api_key) <= 4096 or not all(33 <= ord(c) <= 126 for c in credential.api_key)):
        raise DeepSeekError("provider_invalid_request", a)
    timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, 10.0))
    client_transport = transport if transport is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    try:
        async with asyncio.timeout(timeout_s):
            async with httpx.AsyncClient(transport=client_transport, timeout=timeout, trust_env=False,
                                         follow_redirects=False) as client:
                a.dispatch = "unknown"
                async with client.stream("GET", base + "/models", headers={
                        "Authorization": "Bearer " + credential.api_key, "Accept": "application/json",
                        "Accept-Encoding": "identity"}) as response:
                    a.dispatch, a.http_status = "observed", response.status_code
                    if response.status_code != 200:
                        raise DeepSeekError("provider_redirect" if 300 <= response.status_code < 400
                                            else _STATUS.get(response.status_code, "provider_error"), a)
                    body = bytearray()
                    async for piece in response.aiter_raw():
                        body.extend(piece)
                        _require(len(body) <= 262_144)
                    _require(response.headers.get("content-type", "").split(";")[0].strip().lower() == "application/json")
                    listing = _loads(body.decode("utf-8"))
                    _require(isinstance(listing, dict) and isinstance(listing.get("data"), list) and len(listing["data"]) <= 1000)
                    ids = set()
                    for item in listing["data"]:
                        _require(isinstance(item, dict) and isinstance(item.get("id"), str) and bool(_ID.fullmatch(item["id"])))
                        ids.add(item["id"])
                    return frozenset(ids)
    except DeepSeekError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise DeepSeekError("provider_timeout", a) from None
    except httpx.HTTPError:
        raise DeepSeekError("provider_transport", a) from None
    except Exception:
        raise DeepSeekError("provider_bad_output", a) from None


def list_models(credential: Credential, **kwargs) -> frozenset[str]:
    """Synchronous entry point for worker threads (never from a running event loop)."""
    return asyncio.run(alist_models(credential, **kwargs))


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, *, limits: Limits | None = None, thinking: bool = True,
                 transport: httpx.AsyncBaseTransport | None = None, efforts=EFFORTS,
                 base_url: str = BASE_URL):
        _require(type(thinking) is bool)
        self.limits = limits or Limits()
        self.thinking = thinking
        self.efforts = allowed_efforts(efforts)  # The governed route's allowed values.
        self.base_url = _base(base_url)  # Official origin, or a loopback FIXTURE double in tests.
        self._transport = transport  # Trusted dependency injection for isolated tests only.

    def step(self, **kwargs) -> DeepSeekResult:
        """Synchronous worker entry point. Async callers must use astep, not block their loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise DeepSeekError("provider_async_context", Attempt())
        return asyncio.run(self.astep(**kwargs))

    async def astep(self, *, credential: Credential, system: list[dict], tools: list[dict],
                    messages: list[dict], effort: str, on_text: Callable[[str], None],
                    should_stop: Callable[[], bool], user_id: str | None = None,
                    before_dispatch: Callable[[], None] | None = None) -> DeepSeekResult:
        """before_dispatch must durably fence/reserve this attempt or raise before any send.

        Caller callbacks must return promptly. Cancellation closes the async stream even
        during a blocked read; it cannot guarantee the upstream stopped spending tokens.
        """
        a = Attempt()
        limits = self.limits
        deadline = time.monotonic() + limits.timeout_s
        value = deepseek_effort(effort)
        if value is not None and value not in self.efforts:
            raise DeepSeekError("model_effort_unavailable", a)  # Refused before any send.
        thinking = self.thinking and value != "none"
        try:
            if should_stop():
                raise DeepSeekCancelled(a)
            _require(credential.provider == self.name)
            _require(isinstance(credential.model, str) and bool(_ID.fullmatch(credential.model)))
            a.requested_model = credential.model
            _require(isinstance(credential.api_key, str) and 1 <= len(credential.api_key) <= 4096
                     and all(33 <= ord(c) <= 126 for c in credential.api_key))
            _require(effort in _EFFORT and isinstance(messages, list) and isinstance(tools, list))
            # Bound originals before translation (including unsupported input fields).
            _dump([system, messages, tools], limits.request_bytes)
            _require(len(tools) <= limits.tool_calls)
            definitions, names = [], set()
            for tool in tools:
                name = tool.get("name")
                _require(isinstance(name, str) and bool(_NAME.fullmatch(name)) and name not in names)
                _require(isinstance(tool.get("input_schema"), dict))
                _require(isinstance(tool.get("description", ""), str))
                names.add(name)
                definitions.append({"type": "function", "function": {"name": name,
                    "description": tool.get("description", ""), "parameters": tool["input_schema"]}})
            history = _history(messages, limits, thinking=thinking)
            prompt = _text(system)
            if prompt:
                history.insert(0, {"role": "system", "content": prompt})
            # thinking.type and reasoning_effort always agree: effort "none" (or thinking
            # switched off) sends disabled and no effort, never "enabled" + "none".
            payload = {"model": credential.model, "messages": history, "stream": True,
                       "stream_options": {"include_usage": True}, "max_tokens": limits.max_tokens,
                       "thinking": {"type": "enabled" if thinking else "disabled"}}
            if thinking:
                payload["reasoning_effort"] = value
            if definitions:
                payload["tools"] = definitions
            if user_id is not None:
                _require(isinstance(user_id, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,512}", user_id)))
                payload["user_id"] = user_id
            body = _dump(payload, limits.request_bytes)
        except DeepSeekCancelled:
            raise
        except Exception:
            raise DeepSeekError("provider_invalid_request", a) from None

        async def trace(event, info):
            # httpcore's transport observation, not proof of inference/billability.
            if event in ("http11.send_request_body.complete", "http2.send_request_body.complete"):
                a.dispatch = "observed"

        async def exchange():
            timeout = httpx.Timeout(limits.read_timeout_s, connect=limits.connect_timeout_s,
                                    write=limits.connect_timeout_s, pool=limits.connect_timeout_s)
            transport = self._transport if self._transport is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False)
            async with httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=False,
                                         follow_redirects=False) as client:
                if should_stop():
                    raise DeepSeekCancelled(a)
                if before_dispatch:
                    before_dispatch()
                if should_stop():
                    raise DeepSeekCancelled(a)
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                a.dispatch = "unknown"
                async with client.stream("POST", self.base_url + "/chat/completions", content=body, extensions={"trace": trace},
                                         headers={"Authorization": "Bearer " + credential.api_key,
                                                  "Content-Type": "application/json", "Accept": "text/event-stream",
                                                  "Accept-Encoding": "identity"}) as response:
                    a.dispatch, a.http_status = "observed", response.status_code
                    if response.status_code != 200:
                        code = "provider_redirect" if 300 <= response.status_code < 400 else _STATUS.get(response.status_code, "provider_error")
                        after = response.headers.get("retry-after", "")
                        retry_after = float(after) if re.fullmatch(r"[0-9]{1,6}", after) and int(after) <= 86400 else None
                        raise DeepSeekError(code, a, retry_after=retry_after)
                    _require(response.headers.get("content-type", "").split(";")[0].strip().lower() == "text/event-stream")
                    _require(response.headers.get("content-encoding", "identity").lower() == "identity")
                    parser, assembly = _SSE(limits), _Assembly(a, limits, names)
                    state["assembly"] = assembly
                    async for chunk in response.aiter_raw():
                        if should_stop():
                            raise DeepSeekCancelled(a)
                        if time.monotonic() >= deadline:
                            raise TimeoutError()
                        for event in parser.feed(chunk):
                            if should_stop():
                                raise DeepSeekCancelled(a)
                            if time.monotonic() >= deadline:
                                raise TimeoutError()
                            if event == "[DONE]":
                                return assembly.done()
                            assembly.chunk(_loads(event), on_text)
                    raise DeepSeekError("provider_stream_incomplete", a)

        task, state = None, {}
        try:
            async with asyncio.timeout(limits.timeout_s):
                task = asyncio.create_task(exchange())
                while not task.done():
                    await asyncio.wait({task}, timeout=limits.cancel_poll_s)
                    if not task.done() and should_stop():
                        raise DeepSeekCancelled(a)
                return task.result()
        except (DeepSeekError, DeepSeekCancelled) as exc:
            raise _with_partial(exc, state)
        except asyncio.CancelledError:
            raise _with_partial(DeepSeekCancelled(a), state) from None
        except (TimeoutError, httpx.TimeoutException):
            raise _with_partial(DeepSeekError("provider_timeout", a), state) from None
        except httpx.HTTPError:
            raise _with_partial(DeepSeekError("provider_transport", a), state) from None
        except Exception:
            raise _with_partial(DeepSeekError("provider_bad_output" if a.dispatch != "definitely_not_requested"
                                              else "provider_dispatch_blocked", a), state) from None
        finally:
            if task is not None:
                if not task.done():
                    task.cancel()
                # Observe cancellation/exception and finish closing the transport; no detached work.
                await asyncio.gather(task, return_exceptions=True)
