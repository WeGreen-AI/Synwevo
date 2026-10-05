"""Synwevo Agent's operations: the person's conversations, turns, confirmations, settings and
memories (all personal), and each workspace's AI-processing policy (set by its owners/admins)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field as F, SecretStr, field_serializer, field_validator

from synwevo_core import data_flow, sealed
from synwevo_core.agent_safety import AgentReports, ProviderConsents
from synwevo_core.billing import BillingError
from synwevo_core.capabilities import Operation, canonical, fingerprint
from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id, now

from .engine import Agent, _j
from .provider import EFFORTS, KEY_PURPOSE, configured_model, resolve_credential
from .tools import workspace_policy
from .turn_cost import turn_costs

CONV = r"^conv_[a-f0-9]{12}$"
MSG = r"^msg_[a-f0-9]{12}$"
TURN = r"^turn_[a-f0-9]{12}$"
CALL = r"^tc_[a-f0-9]{12}$"
MEM = r"^mem_[a-f0-9]{12}$"
WS = r"^[A-Za-z0-9_-]{1,100}$"


def T(zh, hant, en):
    return {"zh-Hans": zh, "zh-Hant": hant, "en": en}


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ProviderConsentRef(_In):
    provider: Literal["deepseek"] = "deepseek"


class ProviderConsentAccept(_In):
    provider: Literal["deepseek"] = "deepseek"
    version: str = F(min_length=1, max_length=40)


class MessageReport(_In):
    conversation_id: str = F(pattern=CONV)
    message_id: str = F(pattern=MSG)
    category: Literal["harmful", "illegal", "privacy", "wrong", "other"]
    note: str | None = F(default=None, max_length=1000)
    include_excerpt: bool = False


class ListConversations(_In):
    status: Literal["active", "archived"] = "active"
    q: str | None = F(default=None, max_length=100)
    limit: int = F(default=50, ge=1, le=200)
    before: float | None = None


class CreateConversation(_In):
    title: str | None = F(default=None, max_length=200)
    scope: list[str] | None = F(default=None, max_length=50)


class ConversationRef(_In):
    conversation_id: str = F(pattern=CONV)


class UpdateConversation(_In):
    conversation_id: str = F(pattern=CONV)
    title: str | None = F(default=None, max_length=200)
    scope: list[str] | None = F(default=None, max_length=50)
    clear_scope: bool = False
    status: Literal["active", "archived"] | None = None
    head_id: str | None = F(default=None, pattern=MSG)


class StartTurn(_In):
    conversation_id: str = F(pattern=CONV)
    text: str | None = F(default=None, max_length=20_000)
    parent_message_id: str | None = F(default=None, pattern=MSG)
    regenerate_of: str | None = F(default=None, pattern=MSG)
    quote_id: str | None = F(default=None, pattern=r"^quote_[a-f0-9]{12}$")
    consent_id: str | None = F(default=None, pattern=r"^consent_[a-f0-9]{12}$")


class QuoteTurn(_In):
    conversation_id: str = F(pattern=CONV)
    text: str | None = F(default=None, max_length=20_000)
    parent_message_id: str | None = F(default=None, pattern=MSG)
    regenerate_of: str | None = F(default=None, pattern=MSG)
    budget: str = F(pattern=r"^[1-9][0-9]{0,18}$")
    max_calls: int = F(strict=True, ge=1, le=12)
    run_seconds: int = F(strict=True, ge=1, le=3600)
    #: Who pays (shared contract): the person (default) or an organization that granted them.
    payer_kind: Literal["user", "organization"] = "user"
    payer_id: str | None = F(default=None, max_length=200)


class Payer(_In):
    kind: Literal["user", "organization"]
    id: str


class QuoteRef(_In):
    conversation_id: str = F(pattern=CONV)
    quote_id: str = F(pattern=r"^quote_[a-f0-9]{12}$")


class MoneyUnit(_In):
    code: str
    scale: int = F(strict=True, ge=0, le=18)


class RationalRate(_In):
    numerator: str = F(pattern=r"^[0-9]+$")
    denominator: str = F(pattern=r"^[1-9][0-9]*$")


class TokenRates(_In):
    input_uncached: RationalRate
    input_cached: RationalRate
    output: RationalRate


class ModelTerms(_In):
    environment: Literal["test", "live"]
    model: str
    confirmed_route: str
    route_version: str
    price_id: str
    unit: MoneyUnit
    rates: TokenRates
    # A time-banded tariff: ``rates`` are its peak (highest, held) rates; these apply off-peak.
    off_peak_rates: TokenRates | None = None
    per_tokens: int = F(strict=True, ge=1)
    terms_ref: str
    per_call_hold: str = F(pattern=r"^[0-9]+$")


class ModelCapabilities(_In):
    vision: bool
    tools: bool
    thinking: bool


#: What each governed DeepSeek model accepts, as DeepSeek documents it (docs/deepseek-provider.md,
#: read 2026-10-05). Display metadata only: the model ID itself is governed configuration, and a
#: model missing here is shown without capability claims rather than with guessed ones.
MODEL_CAPABILITIES = {
    "deepseek-flash": {"vision": True, "tools": True, "thinking": True},
    "deepseek-v4-pro": {"vision": False, "tools": True, "thinking": True},
}


class ModelOffer(ModelTerms):
    max_calls_limit: int
    run_seconds_limit: int
    capabilities: ModelCapabilities | None = None


class WalletAmounts(_In):
    booked: str
    held: str
    available: str


class SpendingLimit(_In):
    configured: bool
    enabled: bool
    usage_state: Literal["available", "capacity", "inconsistent"] | None
    #: The tightest remaining UTC period (decimal smallest units) and which period it is.
    remaining: str | None
    period: Literal["daily", "weekly", "monthly"] | None


class BillingStatus(_In):
    available: bool
    reason: Literal["not_configured", "unavailable"] | None = None
    environment: Literal["test", "live"] | None = None
    payer: dict | None = None
    model: str | None = None
    unit: MoneyUnit | None = None
    per_call_hold: str | None = None
    balance: WalletAmounts | None = None
    limit: SpendingLimit | None = None


class ConsentCreate(QuoteRef):
    max_runs: int = F(strict=True, ge=1, le=1000)
    expires_at: int | None = F(strict=True, ge=1)
    until_revoked: bool = F(strict=True)


class ConsentRef(_In):
    consent_id: str = F(pattern=r"^consent_[a-f0-9]{12}$")


class ConsentResult(ModelTerms):
    id: str
    payer: Payer
    conversation_id: str
    scope: list[str]
    max_runs: int
    used_runs: int
    remaining_runs: int
    status: Literal["active", "revoked", "expired", "exhausted", "scope_changed", "model_changed", "conversation_unavailable"]
    created_at: int
    expires_at: int | None
    until_revoked: bool
    budget: str
    total_maximum: str
    max_calls: int
    run_seconds: int


class ConsentList(_In):
    consents: list[ConsentResult]


class PayerBudgetSet(_In):
    unit_code: str = F(min_length=1, max_length=200)
    expected_revision: int = F(strict=True, ge=0, le=2**63-2)
    enabled: bool = F(strict=True)
    daily_budget: str = F(pattern=r"^(0|[1-9][0-9]{0,18})$")
    weekly_budget: str = F(pattern=r"^(0|[1-9][0-9]{0,18})$")
    monthly_budget: str = F(pattern=r"^(0|[1-9][0-9]{0,18})$")


class PayerBudgetHistory(_In):
    revision: int
    enabled: bool
    daily_budget: str
    weekly_budget: str
    monthly_budget: str
    created_at: int


class PayerBudgetPeriod(_In):
    start: int
    end: int
    spent: str
    held: str
    remaining: str


class PayerBudgetPeriods(_In):
    daily: PayerBudgetPeriod
    weekly: PayerBudgetPeriod
    monthly: PayerBudgetPeriod


class PayerBudgetResult(_In):
    unit: MoneyUnit
    revision: int
    configured: bool
    enabled: bool
    daily_budget: str
    weekly_budget: str
    monthly_budget: str
    usage_state: Literal["available", "capacity", "inconsistent"]
    periods: PayerBudgetPeriods | None
    history: list[PayerBudgetHistory]


class PayerBudgetList(_In):
    environment: Literal["test", "live"]
    timezone: Literal["UTC"]
    budgets: list[PayerBudgetResult]


class QuoteResult(ModelTerms):
    id: str
    payer_user_id: str
    payer: Payer
    conversation_id: str
    budget: str = F(pattern=r"^[0-9]+$")
    max_calls: int
    deadline: int
    expires_at: int
    turn_id: str | None
    status: Literal["quoted", "accepted", "expired", "revoked"]


class TurnRef(_In):
    turn_id: str = F(pattern=TURN)


class Decide(_In):
    tool_call_id: str = F(pattern=CALL)
    confirmation: str | None = F(default=None, max_length=100)


class Settings(_In):
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    prefer_personal_key: bool | None = None
    memory_enabled: bool | None = None
    personal_key: SecretStr | None = F(default=None, min_length=20, max_length=300)
    clear_personal_key: bool = False

    @field_validator("personal_key")
    @classmethod
    def valid_key(cls, value):
        if value is not None and any(c.isspace() for c in value.get_secret_value()):
            raise ValueError("密钥格式不正确")
        return value

    @field_serializer("personal_key")
    def key_snapshot(self, value):
        # invoke() persists model_dump(), including failed runs. Keep the raw secret only on
        # the validated instance; a digest distinguishes two keys under the same idempotency key.
        return {"sha256": fingerprint(value.get_secret_value())} if value is not None else None


class Empty(_In):
    pass


class MemoryAdd(_In):
    body: str = F(min_length=1, max_length=500)


class MemoryUpdate(_In):
    memory_id: str = F(pattern=MEM)
    body: str = F(min_length=1, max_length=500)


class MemoryRef(_In):
    memory_id: str = F(pattern=MEM)


class TransferPolicySet(_In):
    outbound: Literal["none", "audience", "organization", "any"]
    #: The revision the person was looking at (0 while nobody has chosen a policy).
    revision: int = F(ge=0)


class PolicySet(_In):
    processing: Literal["none", "platform", "any"]
    providers: list[Literal["anthropic", "deepseek"]] | None = F(default=None, max_length=2)

    @field_validator("providers")
    @classmethod
    def valid_providers(cls, value):
        # The omitted field is the legacy client contract. Explicit null is not a scope.
        if value is None or len(set(value)) != len(value):
            raise ValueError("提供方范围必须是无重复的列表")
        return sorted(value)


def _ledger(agent):
    gateway = getattr(getattr(agent, "provider", None), "gateway", None)
    return getattr(gateway, "ledger", None)


def with_costs(agent, user_id: str, conversation: dict) -> dict:
    """Attach each answer's ledger cost (turn_cost.turn_costs) to its turn; None without a ledger run."""
    ledger = _ledger(agent)
    turns = [m["turn"] for m in conversation.get("messages", []) if m.get("turn")]
    costs = {}
    if ledger is not None and turns:
        active = {t["id"] for t in turns if t["status"] in ("queued", "running", "waiting_confirmation")}
        costs = turn_costs(agent.store, environment=ledger.environment, namespace=ledger.namespace,
                           user_id=user_id, turn_ids=[t["id"] for t in turns], active=active)
    for turn in turns:
        turn["cost"] = costs.get(turn["id"])
    return conversation


def billing_status(agent, user_id: str) -> dict:
    """The composer's money facts for the personal payer: balance and the tightest UTC limit.

    The unit is the governed offer's sale unit; without a usable offer nothing is invented.
    """
    from synwevo_core.billing import Unit
    quotes = getattr(agent.provider, "quotes", None)
    if quotes is None:
        return {"available": False, "reason": "not_configured"}
    try:
        offer = quotes.offer()
        unit = Unit(**offer["unit"])
        balance = quotes.ledger.balance_for_user(requester_user_id=user_id, payer_user_id=user_id, unit=unit)
    except BillingError:
        return {"available": False, "reason": "unavailable"}
    limit = None
    service = getattr(quotes.ledger, "payer_budgets", None)
    if service is not None:
        try:
            view = next((b for b in service.read(user_id)["budgets"] if b["unit"]["code"] == unit.code), None)
        except BillingError:
            view = None
        if view is None:
            limit = {"configured": False, "enabled": False, "usage_state": None, "remaining": None, "period": None}
        else:
            # Without an enabled limit no paid call is admitted; a remaining figure would mislead.
            periods = (view.get("periods") or {}) if view["configured"] and view["enabled"] else {}
            tightest = min(periods.items(), key=lambda kv: int(kv[1]["remaining"]), default=None)
            limit = {"configured": bool(view["configured"]), "enabled": bool(view["enabled"]),
                     "usage_state": view["usage_state"],
                     "remaining": tightest[1]["remaining"] if tightest else None,
                     "period": tightest[0] if tightest else None}
    return {"available": True, "environment": quotes.ledger.environment,
            "payer": {"kind": "user", "id": user_id}, "model": offer["model"], "unit": offer["unit"],
            "per_call_hold": offer["per_call_hold"],
            "balance": {"booked": str(balance.booked), "held": str(balance.held), "available": str(balance.available)},
            "limit": limit}


def settings_json(store, user_id: str, agent=None) -> dict:
    row = store.one("SELECT * FROM agent_settings WHERE user_id=?", (user_id,))
    cred = agent._credential(user_id) if agent is not None else resolve_credential(store, user_id)
    from synwevo_core import env
    paid = bool(agent and getattr(agent.provider, "quotes", None))
    return {"effort": row["effort"] if row else "medium",
            "prefer_personal_key": bool(row and row["prefer_personal_key"]),
            "memory_enabled": bool(row["memory_enabled"]) if row else True,
            "personal_key": {"set": bool(row and row["personal_key_sealed"]),
                             "hint": row["personal_key_hint"] if row else None},
            "model_configured": bool(cred) if paid else bool(configured_model()),
            "platform_configured": bool(cred) if paid else bool((env.get("AGENT_ANTHROPIC_API_KEY") or "").strip()
                                        and configured_model()),
            "active": {"credential": cred.kind, "model": cred.model, "provider": cred.provider} if cred else None,
            "personal_key_supported": not paid,
            "billing": {"required": paid},
            "efforts": list(EFFORTS)}


def update_settings(store, user_id: str, i: Settings, agent=None) -> dict:
    paid = bool(agent and getattr(agent.provider, "quotes", None))
    if paid and (i.personal_key is not None or i.prefer_personal_key is True):
        raise ServiceError(409, "model_personal_key_unsupported", "当前付费模型服务不支持个人密钥，可清除已保存的旧密钥。")
    row = store.one("SELECT * FROM agent_settings WHERE user_id=?", (user_id,))
    if not row:
        store.execute("INSERT INTO agent_settings (user_id, updated_at) VALUES (?,?)", (user_id, now()))
    sets, args = ["updated_at=?"], [now()]
    if i.effort is not None:
        sets.append("effort=?")
        args.append(i.effort)
    if i.prefer_personal_key is not None:
        sets.append("prefer_personal_key=?")
        args.append(int(i.prefer_personal_key))
    if i.memory_enabled is not None:
        sets.append("memory_enabled=?")
        args.append(int(i.memory_enabled))
    if i.clear_personal_key:
        sets += ["personal_key_sealed=NULL", "personal_key_hint=NULL"]
    elif i.personal_key is not None:
        key = i.personal_key.get_secret_value()
        if any(c.isspace() for c in key):
            raise ServiceError(400, "bad_key", "密钥格式不正确")
        # Kept sealed; only a short hint (the last four characters) is ever shown again.
        sets += ["personal_key_sealed=?", "personal_key_hint=?"]
        args += [sealed.seal(store, KEY_PURPOSE, key), "…" + key[-4:]]
    store.execute(f"UPDATE agent_settings SET {', '.join(sets)} WHERE user_id=?", (*args, user_id))
    return settings_json(store, user_id, agent)


def policies_json(store, identity, user_id: str) -> dict:
    """Every workspace the person belongs to, with its AI-processing policy and whether they may change it."""
    out = []
    for w in identity.workspaces_for(user_id):
        out.append({"workspace_id": w["id"], "name": w["name"], "kind": w["kind"], "role": w["role"],
                    **workspace_policy(store, w["id"]), "can_change": w["role"] in ("owner", "admin"),
                    "transfer": transfer_json(store, w)})
    return {"workspaces": out}


def transfer_json(store, w: dict) -> dict:
    """Where the workspace's content may go (synwevo_core.data_flow), with the organization's cap."""
    policy = data_flow.workspace_outbound(store, w["id"])
    cap = data_flow.organization_outbound(store, w.get("organization_id"))
    return {"outbound": policy["outbound"], "explicit": policy["explicit"], "revision": policy["revision"],
            "in_organization": bool(w.get("organization_id")), "organization_outbound": cap["outbound"]}


def register(registry, agent: Agent, store, identity) -> None:
    def result_guard(c, row):
        inp, out = _j(row.get("input"), {}), _j(row.get("output"), {})
        cid = inp.get("conversation_id")
        if c.op.id == "agent.conversation.create":
            cid = out.get("id")
        if c.op.id in ("agent.conversation.delete", "agent.memory.delete", "agent.settings.update",
                       "agent.workspace_policy.set", "agent.transfer_policy.set", "agent.billing.budget.set",
                       "agent.consent.create", "agent.consent.revoke", "agent.provider_consent.accept",
                       "agent.provider_consent.withdraw", "agent.message.report"):
            # Only a deletion receipt, masked key state or policy enum; no business body.
            return
        if c.op.id.startswith("agent.memory."):
            mem = store.one("SELECT body FROM agent_memories WHERE id=? AND user_id=?", (out.get("id"), c.user_id))
            if not mem or mem["body"] != out.get("body"):
                raise ServiceError(409, "result_access_changed", "记忆已更改或删除")
            return
        if c.op.id.startswith("agent.tool_call."):
            item = store.one("SELECT conversation_id FROM agent_tool_calls WHERE id=? AND user_id=?",
                             (inp.get("tool_call_id"), c.user_id))
            cid = item["conversation_id"] if item else None
        elif c.op.id.startswith("agent.turn.") and c.op.id != "agent.turn.quote":
            item = store.one("SELECT conversation_id FROM agent_turns WHERE id=? AND user_id=?",
                             (inp.get("turn_id") or out.get("turn_id"), c.user_id))
            cid = item["conversation_id"] if item else None
        if not cid:
            raise ServiceError(409, "result_access_unverifiable", "无法核实原结果的访问权限")
        conv = agent.conversation(c.user_id, cid)
        if agent._sealed(c.user_id, conv):
            raise ServiceError(409, "result_access_changed", "来源访问权限已变化")

    def op(**kw):
        kw.setdefault("need", "personal")
        if kw.get("side_effect", "none") != "none":
            kw.setdefault("result_guard", result_guard)
        registry.register(Operation(app="agent", **kw))

    op(id="agent.conversation.list", title=T("列出会话", "列出會話", "List conversations"),
       summary="The person's own Agent conversations.", input=ListConversations, agent="never",
       handler=lambda c, i: agent.list_conversations(c.user_id, status=i.status, q=i.q, limit=i.limit, before=i.before))
    op(id="agent.conversation.create", title=T("新建会话", "新建會話", "New conversation"),
       summary="Start a conversation.", input=CreateConversation, side_effect="write", agent="never",
       handler=lambda c, i: agent.create_conversation(c.user_id, i.title, i.scope))
    def conversation_get(c, i):
        out = agent.conversation_json(c.user_id, agent.conversation(c.user_id, i.conversation_id), full=True)
        return with_costs(agent, c.user_id, out)

    op(id="agent.conversation.get", title=T("打开会话", "開啟會話", "Open a conversation"),
       summary="A conversation with its messages, branches, tool calls and each answer's ledger cost.",
       input=ConversationRef, agent="never", handler=conversation_get)
    op(id="agent.conversation.update", title=T("修改会话", "修改會話", "Update a conversation"),
       summary="Rename, archive or restore a conversation, set its workspace scope, or switch branch.",
       input=UpdateConversation, side_effect="write", agent="never",
       handler=lambda c, i: agent.update_conversation(
           c.user_id, i.conversation_id, title=i.title,
           scope=(None if i.clear_scope else i.scope) if (i.clear_scope or i.scope is not None) else ...,
           status=i.status, head_id=i.head_id))
    op(id="agent.conversation.delete", title=T("删除会话", "刪除會話", "Delete a conversation"),
       summary="Delete a conversation permanently.", input=ConversationRef, side_effect="write", agent="never",
       handler=lambda c, i: agent.delete_conversation(c.user_id, i.conversation_id))
    def billing_offer(c, i):
        quotes = getattr(agent.provider, "quotes", None)
        if quotes is None:
            raise ServiceError(503, "billing_not_configured", "付费模型服务尚未配置")
        from synwevo_core.billing import BillingError
        try:
            offer = quotes.offer()
        except BillingError as exc:
            raise ServiceError(503, "billing_" + exc.code, "模型价格暂不可用") from None
        return {**offer, "capabilities": MODEL_CAPABILITIES.get(offer["model"])}

    op(id="agent.billing.offer", title=T("查看模型价格", "查看模型價格", "Model pricing"), input=Empty,
       summary="Current governed model offer, without starting a call or granting payment authority.",
       agent="never", output=ModelOffer, handler=billing_offer)

    op(id="agent.billing.status", title=T("查看余额与额度", "查看餘額與額度", "Balance and limits"), input=Empty,
       summary="The person's own wallet balance and tightest spending limit for the governed model's unit; "
               "reads only, grants nothing.",
       # The ledger reads in its own transaction (it refuses to nest in the operation's).
       agent="never", unattended=False, transactional=False, output=BillingStatus,
       handler=lambda c, i: billing_status(agent, c.user_id))

    def payer_budget(c, i, *, change=False):
        quotes = getattr(agent.provider, "quotes", None)
        service = getattr(quotes.ledger, "payer_budgets", None) if quotes else None
        if service is None:
            raise ServiceError(503, "billing_not_configured", "付费模型服务尚未配置")
        from synwevo_core.billing import BillingError
        try:
            if not change:
                return service.read(c.user_id)
            result = service.set(c.user_id, unit_code=i.unit_code, expected_revision=i.expected_revision,
                enabled=i.enabled, daily_budget=int(i.daily_budget), weekly_budget=int(i.weekly_budget),
                monthly_budget=int(i.monthly_budget))
            c.touches("payer_budget", c.user_id)
            c.emit("agent.payer_budget.changed", object_kind="payer_budget", object_id=c.user_id,
                   data={"unit_code": i.unit_code, "revision": result["revision"]})
            return result
        except BillingError as exc:
            raise ServiceError(409, "billing_" + exc.code, "个人支出上限暂不可用，请重新核对设置") from None

    op(id="agent.billing.budget.get", title=T("查看个人支出上限", "查看個人支出上限", "Personal spending limits"),
       summary="UTC payer-wide limits, current reserved and settled amounts, and latest 20 revisions per unit.",
       input=Empty, output=PayerBudgetList, agent="never", unattended=False, handler=payer_budget)
    op(id="agent.billing.budget.set", title=T("设置个人支出上限", "設定個人支出上限", "Set personal spending limits"),
       summary="Change only this person's calendar limits; grants no run, tool or payment authority.",
       input=PayerBudgetSet, output=PayerBudgetResult, side_effect="write", agent="never", unattended=False,
       events=("agent.payer_budget.changed",), handler=lambda c, i: payer_budget(c, i, change=True))
    def quote_read(c, i, *, revoke=False):
        quotes = getattr(agent.provider, "quotes", None)
        if quotes is None:
            raise ServiceError(503, "billing_not_configured", "付费模型服务尚未配置")
        agent.conversation(c.user_id, i.conversation_id)
        try:
            result = quotes.get(c.user_id, i.quote_id)
            if result["conversation_id"] != i.conversation_id:
                raise ServiceError(404, "quote_not_found", "报价不存在")
            return quotes.revoke(c.user_id, i.quote_id) if revoke else result
        except BillingError as exc:
            raise ServiceError(404, "billing_" + exc.code, "报价不可用") from None

    op(id="agent.turn.quote", title=T("查看运行报价", "查看執行報價", "Quote a run"),
       summary="Bind this message and conversation to a personal price, deadline and maximum budget; no model call or funds held.",
       input=QuoteTurn, output=QuoteResult, side_effect="write", agent="never", unattended=False,
       handler=lambda c, i: agent.quote_turn(c.user, i.conversation_id, i.text, parent_id=i.parent_message_id,
           regenerate_of=i.regenerate_of, budget=int(i.budget), max_calls=i.max_calls, run_seconds=i.run_seconds,
           payer={"kind": i.payer_kind, "id": i.payer_id}))
    op(id="agent.quote.get", title=T("查看报价", "查看報價", "Read a quote"), input=QuoteRef,
       summary="Read the person's own fixed price and budget.", agent="never", output=QuoteResult, handler=quote_read)
    op(id="agent.quote.revoke", title=T("撤销运行预算", "撤銷執行預算", "Revoke a run budget"), input=QuoteRef,
       summary="Stop this grant from authorizing further paid calls; incurred facts still settle.",
       side_effect="write", agent="never", output=QuoteResult, handler=lambda c, i: quote_read(c, i, revoke=True))
    def person_only(c):
        # Spending consent is granted and used only by the person in the page. Capabilities refuses
        # agent/flow/automation; "system" and machine/delegated API callers are refused here too
        # until an API surface is reviewed for it on its own.
        if c.via != "ui":
            raise ServiceError(403, "person_required", "此操作需要本人在页面操作")

    def model_consent(c, i, *, action):
        person_only(c)
        quotes = getattr(agent.provider, "quotes", None)
        if quotes is None:
            raise ServiceError(503, "billing_not_configured", "付费模型服务尚未配置")
        try:
            if action == "list":
                agent.conversation(c.user_id, i.conversation_id)
                return quotes.consents.list(c.user_id, i.conversation_id)
            if action == "create":
                conv = agent.conversation(c.user_id, i.conversation_id)
                if agent._sealed(c.user_id, conv):
                    raise ServiceError(409, "conversation_sealed", "来源权限已变化，请新建会话")
                basis = quotes.get(c.user_id, i.quote_id)
                if basis["conversation_id"] != i.conversation_id:
                    raise ServiceError(404, "quote_not_found", "报价不存在")
                result = quotes.consents.create(c.user_id, i.quote_id, max_runs=i.max_runs,
                    expires_at=i.expires_at, until_revoked=i.until_revoked)
            elif action == "revoke":
                already = quotes.consents.get(c.user_id, i.consent_id)["status"] == "revoked"
                result = quotes.consents.revoke(c.user_id, i.consent_id)
                if already:
                    return result
            else:
                return quotes.consents.get(c.user_id, i.consent_id)
            c.touches("model_consent", result["id"])
            c.emit("agent.consent." + ("created" if action == "create" else "revoked"),
                object_kind="model_consent", object_id=result["id"],
                data={"consent_id": result["id"], "conversation_id": result["conversation_id"]})
            return result
        except BillingError as exc:
            if exc.code.endswith("_not_found"):
                raise ServiceError(404, "billing_" + exc.code, "付款授权不存在") from None
            raise ServiceError(409, "billing_" + exc.code, "持续付款授权暂不可用，请重新核对范围与预算") from None

    for action, title, input_model, output_model in (
        ("create", T("允许本会话后续运行", "允許本會話後續執行", "Allow further runs in this conversation"), ConsentCreate, ConsentResult),
        ("get", T("查看持续付款授权", "查看持續付款授權", "Read continuing payment consent"), ConsentRef, ConsentResult),
        ("list", T("列出本会话付款授权", "列出本會話付款授權", "Conversation payment consents"), ConversationRef, ConsentList),
        ("revoke", T("撤销持续付款授权", "撤銷持續付款授權", "Revoke continuing payment consent"), ConsentRef, ConsentResult),
    ):
        writes = action in ("create", "revoke")
        event = "agent.consent." + ("created" if action == "create" else "revoked")
        op(id="agent.consent." + action, title=title, input=input_model, output=output_model,
           summary="Personal bounded model-spending consent; does not authorize business tools or unattended runs.",
           side_effect="write" if writes else "none", agent="never", unattended=False,
           events=(event,) if writes else (), handler=lambda c, i, action=action: model_consent(c, i, action=action))

    def start_turn(c, i):
        if i.consent_id:
            person_only(c)
        return agent.start_turn(c.user, i.conversation_id, i.text, parent_id=i.parent_message_id,
            regenerate_of=i.regenerate_of, quote_id=i.quote_id, consent_id=i.consent_id)

    consents = ProviderConsents(store)

    def provider_consent(c, i, *, action):
        if action != "get":
            person_only(c)
        if action == "accept":
            result = consents.accept(c.user_id, i.provider, i.version)
        elif action == "withdraw":
            result = consents.withdraw(c.user_id, i.provider)
        else:
            result = consents.status(c.user_id, i.provider)
        credential = agent._credential(c.user_id)
        # Whether the model the person would use right now is this provider (so the page knows
        # to ask before the first message), and their history of decisions.
        return {**result, "required": bool(credential and credential.provider == i.provider),
                "history": [h for h in consents.history(c.user_id) if h["provider"] == i.provider][-20:]}

    op(id="agent.provider_consent.get", title=T("查看模型提供方数据同意", "查看模型提供方資料同意", "Model provider data consent"),
       summary="Whether the person agreed to the current version of sending their messages to the named model provider.",
       input=ProviderConsentRef, agent="never", handler=lambda c, i: provider_consent(c, i, action="get"))
    op(id="agent.provider_consent.accept", title=T("同意向模型提供方发送内容", "同意向模型提供方傳送內容", "Agree to send content to the model provider"),
       summary="Record the person's separate consent to this version; nothing is sent to the provider until then.",
       input=ProviderConsentAccept, side_effect="write", agent="never", unattended=False,
       handler=lambda c, i: provider_consent(c, i, action="accept"))
    op(id="agent.provider_consent.withdraw", title=T("撤回模型提供方数据同意", "撤回模型提供方資料同意", "Withdraw model provider data consent"),
       summary="Stop every further model call to this provider for the person.",
       input=ProviderConsentRef, side_effect="write", agent="never", unattended=False,
       handler=lambda c, i: provider_consent(c, i, action="withdraw"))

    reports = AgentReports(store)

    def report_message(c, i):
        person_only(c)
        agent.conversation(c.user_id, i.conversation_id)            # 404 for anyone but the owner
        msg = store.one("SELECT body FROM agent_messages WHERE id=? AND conversation_id=? AND role='assistant'",
                        (i.message_id, i.conversation_id))
        if not msg:
            raise ServiceError(404, "message_not_found", "消息不存在")
        # The answer's text reaches the platform's reviewers only when the person chose to send it.
        out = reports.create(c.user_id, conversation_id=i.conversation_id, message_id=i.message_id,
                             category=i.category, note=i.note, excerpt=msg["body"] if i.include_excerpt else None)
        return {"id": out["id"], "status": out["status"], "category": out["category"],
                "included_excerpt": out["excerpt"] is not None, "created_at": out["created_at"]}

    op(id="agent.message.report", title=T("举报回答", "檢舉回答", "Report an answer"),
       summary="Report an assistant answer to the platform's reviewers; the answer's text is included only if the person chooses.",
       input=MessageReport, side_effect="write", agent="never", unattended=False, handler=report_message)

    op(id="agent.turn.start", title=T("发送消息", "傳送訊息", "Send a message"),
       summary="Send a message (or regenerate an answer) and start the answer.", input=StartTurn,
       side_effect="write", agent="never", transactional=False,
       handler=start_turn)
    op(id="agent.turn.cancel", title=T("停止回答", "停止回答", "Stop the answer"),
       summary="Stop an answer: nothing further runs.", input=TurnRef, side_effect="write", agent="never",
       transactional=False, handler=lambda c, i: agent.cancel(c.user_id, i.turn_id))
    op(id="agent.tool_call.confirm", title=T("确认执行", "確認執行", "Confirm an action"),
       summary="Run the exact action the person was shown.", input=Decide, side_effect="write", agent="never",
       transactional=False, unattended=False,
       handler=lambda c, i: agent.decide(c.user, i.tool_call_id, confirm=True, confirmation=i.confirmation))
    op(id="agent.tool_call.decline", title=T("拒绝执行", "拒絕執行", "Decline an action"),
       summary="Decline a proposed action.", input=Decide, side_effect="write", agent="never", transactional=False,
       unattended=False, handler=lambda c, i: agent.decide(c.user, i.tool_call_id, confirm=False, confirmation=None))
    op(id="agent.settings.get", title=T("查看 Agent 设置", "查看 Agent 設定", "Agent settings"),
       summary="Model access and preferences.", input=Empty, agent="never",
       handler=lambda c, i: settings_json(store, c.user_id, agent))
    op(id="agent.settings.update", title=T("修改 Agent 设置", "修改 Agent 設定", "Change Agent settings"),
       summary="Change effort, memory, or the person's own provider key.", input=Settings, side_effect="write",
       agent="never", handler=lambda c, i: update_settings(store, c.user_id, i, agent))
    op(id="agent.memory.list", title=T("查看记忆", "查看記憶", "Memories"), summary="What the Agent remembers about the person.",
       input=Empty, agent="never",
       handler=lambda c, i: {"memories": store.all("SELECT id, body, created_at, updated_at FROM agent_memories "
                                                   "WHERE user_id=? ORDER BY created_at", (c.user_id,))})

    def add_memory(c, i):
        n = store.one("SELECT COUNT(*) AS n FROM agent_memories WHERE user_id=?", (c.user_id,))["n"]
        if n >= 50:
            raise ServiceError(409, "too_many_memories", "记忆已满 50 条，请先删除一些")
        mid = new_id("mem")
        store.execute("INSERT INTO agent_memories (id,user_id,body,created_at,updated_at) VALUES (?,?,?,?,?)",
                      (mid, c.user_id, i.body.strip(), now(), now()))
        return {"id": mid, "body": i.body.strip()}

    op(id="agent.memory.add", title=T("记住一条偏好", "記住一條偏好", "Remember a preference"),
       summary="Remember a personal preference the person stated (never business facts from a workspace).",
       input=MemoryAdd, side_effect="write", agent="confirm", handler=add_memory)

    def update_memory(c, i):
        n = store.execute("UPDATE agent_memories SET body=?, updated_at=? WHERE id=? AND user_id=?",
                          (i.body.strip(), now(), i.memory_id, c.user_id))
        if not n:
            raise ServiceError(404, "memory_not_found", "记忆不存在")
        return {"id": i.memory_id, "body": i.body.strip()}

    op(id="agent.memory.update", title=T("修改记忆", "修改記憶", "Edit a memory"), summary="Edit a memory.",
       input=MemoryUpdate, side_effect="write", agent="never", handler=update_memory)

    def delete_memory(c, i):
        if not store.execute("DELETE FROM agent_memories WHERE id=? AND user_id=?", (i.memory_id, c.user_id)):
            raise ServiceError(404, "memory_not_found", "记忆不存在")
        return {"deleted": i.memory_id}

    op(id="agent.memory.delete", title=T("删除记忆", "刪除記憶", "Delete a memory"), summary="Delete a memory.",
       input=MemoryRef, side_effect="write", agent="never", handler=delete_memory)
    op(id="agent.workspace_policy.list", title=T("查看 AI 处理政策", "查看 AI 處理政策", "AI processing policies"),
       summary="Each of the person's workspaces and whether its content may be processed by the Agent's model.",
       input=Empty, agent="never", handler=lambda c, i: policies_json(store, identity, c.user_id))

    def set_policy(c, i):
        current = workspace_policy(store, c.workspace_id)
        providers = i.providers if i.providers is not None else (
            current["providers"] if current["providers_explicit"] else ["anthropic"])
        ts = now()
        order = ("none", "platform", "any")
        if (order.index(i.processing) < order.index(current["processing"])
                or set(current["providers"]) - set(providers)):
            # A narrowing is a withdrawal: journalled ahead of the change so a restored backup
            # cannot reopen the workspace's content to a model (synwevo_core.journal).
            from synwevo_core import journal
            journal.record(store, "ai_policy_narrowed", workspace_id=c.workspace_id,
                           processing=i.processing, providers=sorted(providers))
        store.execute("INSERT INTO agent_workspace_policies (workspace_id, processing, updated_by, updated_at) VALUES (?,?,?,?) "
                      "ON CONFLICT(workspace_id) DO UPDATE SET processing=excluded.processing, updated_by=excluded.updated_by, "
                      "updated_at=excluded.updated_at", (c.workspace_id, i.processing, c.user_id, ts))
        store.execute("INSERT INTO agent_workspace_model_providers (workspace_id, providers_json, updated_by, updated_at) "
                      "VALUES (?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET providers_json=excluded.providers_json, "
                      "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                      (c.workspace_id, canonical(providers), c.user_id, ts))
        c.touches("workspace", c.workspace_id)
        result = {"workspace_id": c.workspace_id, "processing": i.processing, "providers": providers,
                  "providers_explicit": True}
        c.emit("agent.workspace_policy.changed", object_kind="workspace", object_id=c.workspace_id, data=result)
        return result

    def set_transfer(c, i):
        current = data_flow.workspace_outbound(store, c.workspace_id)
        if current["revision"] != i.revision:
            raise ServiceError(409, "transfer_policy_changed", data_flow.REFUSALS["transfer_policy_changed"])
        if current["explicit"] and current["outbound"] == i.outbound:
            return {"workspace_id": c.workspace_id, **current}  # nothing changed, no new revision
        result = {"workspace_id": c.workspace_id,
                  **data_flow.set_workspace_outbound(store, c.workspace_id, i.outbound, actor_id=c.user_id,
                                                     revision=i.revision)}
        c.touches("workspace", c.workspace_id)
        c.emit("agent.transfer_policy.changed", object_kind="workspace", object_id=c.workspace_id,
               data={"workspace_id": c.workspace_id, "outbound": i.outbound, "revision": result["revision"],
                     "before": current["outbound"]})
        return result

    op(id="agent.transfer_policy.set", title=T("设置数据流政策", "設定資料流政策", "Set the data-flow policy"),
       summary="Owners and admins decide where this workspace's content may be carried: nowhere, to workspaces "
               "whose members already belong here, within the organization, or anywhere the person may write.",
       input=TransferPolicySet, need="manage", side_effect="write", agent="never", unattended=False,
       events=("agent.transfer_policy.changed",), handler=set_transfer)
    op(id="agent.workspace_policy.set", title=T("设置 AI 处理政策", "設定 AI 處理政策", "Set the AI processing policy"),
       summary="Owners and admins decide whether the workspace's content may be sent to the Agent's model provider.",
       input=PolicySet, need="manage", side_effect="write", agent="never", unattended=False,
       events=("agent.workspace_policy.changed",), handler=set_policy)
