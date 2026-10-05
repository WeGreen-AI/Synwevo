"""Content safety on the Agent's two edges: what the person sends and what the model returns.

* **Input** — a message matching a governed rule (administrators' keyword/pattern lists,
  `synwevo_core.agent_safety.SafetyRules`) is refused before anything is stored or sent:
  ``content_blocked_input`` (422). The text stays with the person to edit.
* **Output** — the streamed answer is checked as it arrives. On a match no further text is
  shown, the model call is stopped, what was already shown of that answer is withheld and the
  turn ends ``content_blocked_output``. The provider's own filter (DeepSeek ``finish_reason:
  content_filter``) ends it ``content_filtered_output``. Whether a stopped call is charged is the
  administrators' incomplete-call policy, decided in the gateway, not here.

Every stop is recorded as rule, direction and length, never the text (`SafetyEvents`).
"""
from __future__ import annotations

from synwevo_core.agent_safety import SafetyEvents, SafetyRules
from synwevo_core.errors import ServiceError

from .provider import ProviderError, StepResult

#: How much already-seen text is re-checked with each new piece, so a keyword split across two
#: streamed pieces is still found without re-reading the whole answer every time.
WINDOW = 512


def refused(step: StepResult) -> ProviderError:
    """The error for a model step that ended in a refusal: the provider's content filter has its
    own code, any other refusal stays ``provider_refused``."""
    category = (step.stop_details or {}).get("category")
    if category == "content_filter":
        return ProviderError("content_filtered_output", "回答触发了模型提供方的内容安全过滤，已停止生成")
    return ProviderError("provider_refused", "模型拒绝回答这个请求" + (f"（{category}）" if category else ""))


def check_input(store, user_id: str, text: str, *, conversation_id: str | None) -> None:
    """Refuse a message that matches an input rule, recording only which rule and how long."""
    rules = SafetyRules(store).compiled()
    rule = rules.match(text or "", "input")
    if rule is None:
        return
    SafetyEvents(store).record(user_id, "input", rule=rule, version=rules.version,
                               conversation_id=conversation_id, characters=len(text or ""))
    raise ServiceError(422, "content_blocked_input", "这条消息触发了内容安全规则，没有发送给模型，请修改后再发送",
                       {"category": rule["category"]})


class OutputGuard:
    """Watches one turn's streamed answer against the output rules in force when it started."""

    def __init__(self, store):
        self.store = store
        self.rules = SafetyRules(store).compiled()
        self.tail = ""
        self.rule: dict | None = None

    @property
    def blocked(self) -> bool:
        return self.rule is not None

    def admit(self, chunk: str) -> bool:
        """True when ``chunk`` may be shown; False (and blocked from now on) when it completes a
        match. Only the last ``WINDOW`` characters are carried between pieces."""
        if self.rule is not None:
            return False
        seen = self.tail + chunk
        rule = self.rules.match(seen, "output")
        if rule is not None:
            self.rule = rule
            return False
        self.tail = seen[-WINDOW:]
        return True

    def check_result(self, step: StepResult) -> None:
        """A last look at the whole step (text that reached no callback is checked too)."""
        if self.rule is not None:
            return
        text = "".join(b.get("text") or "" for b in step.content or [] if b.get("type") == "text")
        rule = self.rules.match(text, "output")
        if rule is not None:
            self.rule = rule

    def record(self, t: dict, characters: int) -> ProviderError:
        SafetyEvents(self.store).record(t["user_id"], "output", rule=self.rule, version=self.rules.version,
                                        conversation_id=t["conversation_id"], turn_id=t["id"], characters=characters)
        return ProviderError("content_blocked_output", "回答触发了内容安全规则，已停止生成，被拦下的内容不会显示")
