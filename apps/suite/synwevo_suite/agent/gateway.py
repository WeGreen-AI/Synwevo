"""Durable metering boundary between the Agent transport and the shared money ledger.

No price/credential discovery or enablement lives here. A trusted configuration service must
supply a confirmed route and published prices; a current payment authorizer must be installed
on Ledger. No network attempt is retried. Recovery settles saved evidence only. After an
upstream 429/503, later attempts wait out a bounded, jittered cool-down while they are still
provably unsent (nothing reserved, no dispatch fence); the refused attempt itself is never resent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import hmac
import json
import logging
import math
import random
import threading
import time
from typing import Callable
from uuid import uuid4

from synwevo_core.auth import Auth
from synwevo_core.billing import BillingError, ChargeContext, Ledger, PriceSnapshot, Usage, UsageEvidence
from synwevo_core.billing.models import identifier, integer
from synwevo_core.billing.provider_settings import INCOMPLETE_KINDS
from synwevo_core.billing.traffic import TrafficCancelled

from .deepseek import (BASE_URL, EFFORTS, Attempt, DeepSeekCancelled, DeepSeekError, DeepSeekProvider,
                       DeepSeekResult, Limits, allowed_efforts, deepseek_effort, normalize_usage, upstream_base)
from .provider import Credential, ProviderError

log = logging.getLogger("synwevo.agent.gateway")
#: Domain label of the key that pseudonymizes payers for the provider's user_id field.
PSEUDONYM_LABEL = b"synwevo/model-user-pseudonym/v1"
#: What a person sees when the upstream refused for the operator's account (HTTP 402) or a
#: closed route stopped the call. Never "your balance is insufficient": it is not theirs.
UNAVAILABLE = "模型服务暂时不可用，本次未扣费。请稍后再试。"
#: Refusals of a closed route: the platform breaker (an upstream 402, billing/risk.py) or a model
#: id the provider no longer lists. Both stop before any reservation or send.
CLOSED_CODES = ("risk_breaker_open", "provider_model_missing")
#: Upstream throttling (HTTP 429 rate limit, 503 overload) and the cool-down it starts.
THROTTLED = {429: "provider_rate_limited", 503: "provider_overloaded"}
BACKOFF_BASE_S = 1.0    # first computed cool-down; doubles per consecutive throttle
BACKOFF_CAP_S = 30.0    # computed cool-down never exceeds this (a Retry-After may, up to a day)
MAX_WAIT_S = 30.0       # a new call waits at most this long, unsent, before it is refused unsent
THROTTLED_MESSAGE = "模型服务繁忙，本次没有发送，也没有扣费。请稍后再试。"
#: Prompt-token allowances on top of the request's UTF-8 size (input_bound): the chat
#: template's fixed special tokens, and its tool-use instructions when tools are offered.
TEMPLATE_TOKENS = 64
TOOLS_TEMPLATE_TOKENS = 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _open_platform_breaker(store, *, environment: str, reason_code: str,
                           fallback: Callable[[], object] | None = None) -> str:
    """THE seam an upstream HTTP 402 (the operator's provider balance is empty) closes paid
    dispatch through. Returns which mechanism closed it.

    When the platform-wide paid-dispatch breaker is installed
    (``synwevo_core.billing.risk.trip_platform_breaker``), it is opened with source
    ``upstream_402``: it stops every paid call inside the dispatch transaction, is audited and
    alerts administrators, and an administrator closes it after topping up ("platform_breaker").
    Without that module, ``fallback`` records this package's own minimal state: the current
    provider revision is closed (``model_provider_health`` state ``upstream_balance``, audited,
    administrators alerted) until an administrator publishes the configuration again
    ("provider_revision"). Only a missing module selects the fallback; a broken one raises.
    """
    try:
        from synwevo_core.billing.risk import trip_platform_breaker
    except ModuleNotFoundError as exc:
        if exc.name != "synwevo_core.billing.risk":
            raise
        trip_platform_breaker = None
    if trip_platform_breaker is not None:
        trip_platform_breaker(store, environment=environment, source="upstream_402", reason_code=reason_code)
        return "platform_breaker"
    if fallback is not None:
        fallback()
        return "provider_revision"
    return "none"


@dataclass(frozen=True)
class Route:
    """Approved metadata, never model/browser supplied. No production defaults.

    input_token_ceiling must cover the provider's entire admitted context, including system,
    schemas and private history. It is NOT a character-count token estimate. Until a tighter
    tokenizer bound is verified, use the confirmed context ceiling as the conservative hold.
    Cost selection is explicitly the published dispatch-time contract, with its evidence ref.
    """
    version: str
    requested_model: str
    confirmed_route: str
    confirmation_ref: str
    cost_basis_ref: str
    sale_price_id: str
    cost_price_id: str
    input_token_ceiling: int
    output_token_ceiling: int
    valid_until: int
    reasoning_efforts: tuple = EFFORTS  # Governed per route; older snapshots allowed every value.
    # Incomplete endings the payer is charged for (provider_settings.ChargingPolicy); pinned on
    # the run with the quote. Older snapshots charged none: the platform absorbed them.
    charge_incomplete: tuple = ()

    def __post_init__(self):
        for value in (self.version, self.requested_model, self.confirmed_route, self.confirmation_ref,
                      self.cost_basis_ref, self.sale_price_id, self.cost_price_id):
            identifier(value)
        integer(self.input_token_ceiling, positive=True, maximum=2_000_000)
        integer(self.output_token_ceiling, positive=True, maximum=32768)
        integer(self.valid_until, positive=True)
        try:
            # Canonical tuple, so a JSON-restored snapshot compares equal to the current route.
            object.__setattr__(self, "reasoning_efforts", allowed_efforts(self.reasoning_efforts))
        except ValueError:
            raise BillingError("invalid_route") from None
        kinds = self.charge_incomplete
        if (not isinstance(kinds, (list, tuple)) or len(set(kinds)) != len(kinds)
                or any(kind not in INCOMPLETE_KINDS for kind in kinds)):
            raise BillingError("invalid_route")
        object.__setattr__(self, "charge_incomplete", tuple(k for k in INCOMPLETE_KINDS if k in kinds))


def _raw_counters(raw):
    """Keep only metering fields. Invalid strings/objects cannot smuggle prompts into bills."""
    if not isinstance(raw, dict):
        return {"invalid_type": type(raw).__name__}
    out = {}
    keys = ("prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens", "total_tokens")
    def numeric(value):
        if type(value) is int and abs(value) <= 2**63-1:
            return value
        return {"invalid_type": type(value).__name__}
    for key in keys:
        if key in raw:
            out[key] = numeric(raw[key])
    for key, child in (("prompt_tokens_details", "cached_tokens"), ("completion_tokens_details", "reasoning_tokens")):
        if key in raw:
            value = raw[key]
            out[key] = {child: numeric(value[child])} if isinstance(value, dict) and child in value else {
                "invalid_type": type(value).__name__}
    return out


class MeteredDeepSeek:
    """Internal gateway. Both authorization callbacks are mandatory, never implied by a key.

    current_authority(context) additionally checks the current run, model-processing policy
    and cancellation; Ledger independently checks payment authority in every money transaction.
    Keys remain caller-owned secrets. Stored request fingerprints contain no prompt or key.
    """
    name = "deepseek"

    def __init__(self, ledger: Ledger, *, current_authority: Callable[[ChargeContext], bool],
                 transport=None, limits: Limits | None = None, cost_admission=None, traffic=None,
                 base_url: str = BASE_URL, route_guard: Callable[[object], None] | None = None,
                 on_upstream_balance: Callable[[object], None] | None = None):
        if not callable(current_authority):
            raise BillingError("model_authority_required")
        if any(hook is not None and not callable(hook) for hook in (route_guard, on_upstream_balance)):
            raise BillingError("model_configuration_required")
        if cost_admission is not None and (getattr(cost_admission, "ledger", None) is not ledger
                                          or not callable(getattr(cost_admission, "admit", None))):
            raise BillingError("model_cost_admission_required")
        if traffic is not None and getattr(traffic, "ledger", None) is not ledger:
            raise BillingError("model_traffic_required")
        self.ledger, self.store = ledger, ledger.store
        self.cost_admission, self.traffic = cost_admission, traffic
        self.current_authority = current_authority
        self.transport = transport  # Trusted Python fixture injection only; not a route option.
        self.limits = limits or Limits()
        # Official origin, or a loopback FIXTURE double only in the test billing environment.
        self.base_url = upstream_base(ledger.environment, None if base_url == BASE_URL else base_url)
        # route_guard(route) raises BillingError while the route is closed (missing model id,
        # upstream 402); it runs before reserving and again inside the dispatch transaction.
        self.route_guard = route_guard
        # on_upstream_balance(route) closes paid dispatch after an upstream HTTP 402.
        self.on_upstream_balance = on_upstream_balance
        # Process-local upstream cool-down after 429/503 (one gateway instance serves the
        # deployment, docs/deepseek-provider.md). Clock, sleep and jitter are injectable for tests.
        self._throttle = threading.Lock()
        self._cool_until, self._strikes, self._cool_code = 0.0, 0, None
        self._clock, self._sleep, self._random = time.monotonic, time.sleep, random.random

    def pseudonym(self, payer_user_id: str) -> str:
        """The provider's user_id for a payer: HMAC-SHA256 under a key derived from the server
        root secret, scoped to environment and namespace. Stable per payer, reveals no account
        id (unlike a plain hash, it cannot be recomputed from a guessed id without the key),
        and matches DeepSeek's [a-zA-Z0-9-_]{1,512}. A root rekey changes every pseudonym."""
        key = hmac.new(Auth(self.store).secret(), PSEUDONYM_LABEL, hashlib.sha256).digest()
        scope = f"{self.ledger.environment}:{self.ledger.namespace}:{identifier(payer_user_id)}"
        return hmac.new(key, scope.encode(), hashlib.sha256).hexdigest()

    def _throttled(self, status: int | None, retry_after: float | None) -> None:
        """Start (or extend) the cool-down after an upstream 429/503. Honors Retry-After (plus
        up to 20% jitter, so waiting calls do not return together); otherwise full-range
        equal jitter over an exponential step, capped at BACKOFF_CAP_S."""
        with self._throttle:
            self._strikes = min(self._strikes + 1, 16)
            if retry_after is not None:
                delay = min(float(retry_after), 86400.0) * (1 + 0.2 * self._random())
            else:
                step = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (self._strikes - 1))
                delay = step / 2 + step / 2 * self._random()
            self._cool_until = max(self._cool_until, self._clock() + delay)
            self._cool_code = THROTTLED[status]

    def _recovered(self) -> None:
        with self._throttle:
            self._strikes = 0

    def _await_upstream(self, should_stop, deadline: int) -> None:
        """Before anything is reserved or fenced: wait out an upstream cool-down, bounded by
        MAX_WAIT_S and the run deadline; beyond that refuse the call unsent and unreserved."""
        while True:
            with self._throttle:
                wait, code = self._cool_until - self._clock(), self._cool_code
            if wait <= 0:
                return
            if wait > MAX_WAIT_S or self.ledger._now() + math.ceil(wait) >= deadline:
                error = ProviderError(code, THROTTLED_MESSAGE, retryable=True, retry_after=math.ceil(wait))
                error.metered_usage, error.usage_reliability = {"input_tokens": 0, "output_tokens": 0}, "not_dispatched"
                raise error
            if should_stop():
                stopped = DeepSeekCancelled(Attempt())
                stopped.not_dispatched = True  # Stopped while waiting: nothing reserved or sent.
                raise stopped
            self._sleep(min(wait, 0.25))

    def _guard(self, route):
        if self.route_guard is not None:
            self.route_guard(route)

    def _closed(self, route, exc):
        """An upstream 402 closes paid dispatch (never fails the caller's error path) and the
        person is told the service is unavailable and they were not charged — the empty
        balance is the operator's, not theirs."""
        hook = self.on_upstream_balance
        try:
            _open_platform_breaker(self.store, environment=self.ledger.environment, reason_code="http_402",
                                   fallback=(lambda: hook(route)) if hook is not None else None)
        except Exception as error:  # noqa: BLE001 - the 402 itself must still reach the person
            log.warning("closing paid dispatch after an upstream 402 failed: %s", type(error).__name__)
        exc.message = UNAVAILABLE
        exc.args = (UNAVAILABLE,)

    def _check(self, context):
        with self.store.conn() as connection:
            if connection.in_transaction:
                raise BillingError("billing_requires_own_transaction")
        if self.current_authority(context) is not True:
            raise BillingError("model_authority_denied")

    def _price(self, price_id, purpose):
        row = self.store.one("SELECT snapshot FROM billing_prices WHERE environment=? AND price_id=?",
                             (self.ledger.environment, price_id))
        if not row:
            raise BillingError("price_not_published")
        price = PriceSnapshot.from_dict(row["snapshot"] if isinstance(row["snapshot"], dict) else json.loads(row["snapshot"]))
        if price.purpose != purpose or price.effective_from > self.ledger._now():
            raise BillingError("price_not_applicable")
        return price

    def _prices(self, route):
        sale, cost = self._price(route.sale_price_id, "sale"), self._price(route.cost_price_id, "cost")
        if any(p.provider != "deepseek" or p.model != route.confirmed_route for p in (sale, cost)):
            raise BillingError("price_route_mismatch")
        return sale, cost

    @staticmethod
    def upper_bound(route, sale, *, input_tokens: int | None = None):
        # No cache discount is assumed; include at most one earlier fractional-unit carry.
        # The greater input rate is conservative even for an unusual published cache tariff.
        # Without a request (quotes, run budgets) the route's whole context ceiling is held.
        tokens = route.input_token_ceiling if input_tokens is None else min(input_tokens, route.input_token_ceiling)
        rate = max(sale.input_uncached.fraction, sale.input_cached.fraction)
        exact = (tokens * rate + route.output_token_ceiling * sale.output.fraction) / sale.per_tokens
        return integer((exact.numerator + exact.denominator - 1) // exact.denominator + 1)

    @staticmethod
    def input_bound(route, *, system, tools, messages) -> int:
        """A conservative upper bound on this request's prompt tokens, for its hold.

        DeepSeek's tokenizer is byte-level BPE: every ordinary token covers at least one UTF-8
        byte of the text it encodes, so a text never has more tokens than bytes. The request
        JSON contains every text sent (system, history including replayed reasoning, tool
        schemas and results) plus syntax and escapes, which only over-count; each message's JSON
        syntax (20+ bytes) outweighs the few special tokens the chat template wraps it in, and
        TEMPLATE_TOKENS / TOOLS_TEMPLATE_TOKENS cover the fixed template and tool instructions.
        Capped at the route's confirmed context ceiling. No official tokenizer is in the lock;
        if a provider count ever exceeds this, the ledger caps the charge at the hold and the
        platform absorbs the rest (the observation records the bound for reconciliation).
        """
        size = len(_json([system, tools, messages]).encode("utf-8"))
        bound = size + TEMPLATE_TOKENS + (TOOLS_TEMPLATE_TOKENS if tools else 0)
        return integer(min(bound, route.input_token_ceiling), positive=True)

    def open_run(self, context: ChargeContext, *, route: Route, budget: int, max_calls: int,
                 deadline: int) -> dict:
        self._check(context)
        if deadline > route.valid_until or route.valid_until <= self.ledger._now():
            raise BillingError("route_confirmation_expired")
        sale, _ = self._prices(route)
        ceiling = self.upper_bound(route, sale)
        if budget < ceiling:
            raise BillingError("run_budget_exceeded")
        # Every logical step permits one physical send in this first gateway. An explicit
        # rerun needs a new authorized run; neither failure nor an expired lease retries it.
        run_key = self.ledger.open_run(context, unit=sale.unit, budget=budget, max_calls=max_calls,
                                       max_attempts=max_calls, deadline=deadline)
        snapshot = _json(asdict(route))
        with self.store.tx() as t:
            row = t.execute("SELECT * FROM agent_metered_runs WHERE run_key=?", (run_key,)).fetchone()
            if row:
                if row["route"] != snapshot:
                    raise BillingError("run_route_conflict")
            else:
                t.execute("INSERT INTO agent_metered_runs(run_key,route) VALUES (?,?)", (run_key, snapshot))
        return {"run_key": run_key, "per_call_hold": ceiling, "unit": asdict(sale.unit)}

    def _run(self, context):
        row = self.store.one("SELECT r.*,m.route FROM billing_runs r JOIN agent_metered_runs m ON m.run_key=r.id "
                              "WHERE r.environment=? AND r.namespace=? AND r.run_id=?",
                              (self.ledger.environment, self.ledger.namespace, context.run_id))
        if not row:
            raise BillingError("run_not_found")
        fixed = json.loads(row["context"])
        if any(fixed[k] != v for k,v in asdict(context).items() if k != "workspace_id"):
            raise BillingError("run_context_conflict")
        return row, Route(**json.loads(row["route"]))

    def step(self, *, context: ChargeContext, logical_call_id: str, credential: Credential,
             system: list, tools: list, messages: list, effort: str, on_text, should_stop,
             validate_result=None) -> DeepSeekResult:
        self._check(context)
        run, route = self._run(context)
        if self.store.one("SELECT 1 FROM billing_attempts a JOIN billing_calls c ON c.id=a.call_key "
                          "WHERE c.run_key=? AND a.status IN ('dispatch_unknown','usage_pending') AND c.status='reserved'",
                          (run["id"],)):
            raise BillingError("usage_pending")
        if (credential.kind != "platform" or credential.provider != self.name
                or credential.model != route.requested_model):
            raise BillingError("model_configuration_changed")
        if route.valid_until <= self.ledger._now():
            raise BillingError("route_confirmation_expired")
        try:
            self._guard(route)  # A closed route reserves nothing and sends nothing.
        except BillingError as exc:
            exc.not_dispatched = True
            raise
        if deepseek_effort(effort) not in route.reasoning_efforts:
            # Refused before any reservation or send: the route does not offer this level.
            error = ProviderError("model_effort_unavailable",
                                  "当前模型路线不支持所选的推理投入，请在 Agent 设置中更换。本次没有发送，也不扣费。")
            error.metered_usage, error.usage_reliability = {"input_tokens": 0, "output_tokens": 0}, "not_dispatched"
            raise error
        identifier(logical_call_id)
        self._await_upstream(should_stop, run["deadline"])  # Provably unsent while waiting.
        sale, cost = self._prices(route)
        request = _json({"route": asdict(route), "context": asdict(context), "system": system,
                         "tools": tools, "messages": messages, "effort": effort})
        if len(request.encode()) > self.limits.request_bytes:
            raise BillingError("model_request_too_large")
        digest = hashlib.sha256(request.encode()).hexdigest()
        request = None  # Request bodies are not persisted in the money evidence tables.
        bound = self.input_bound(route, system=system, tools=tools, messages=messages)
        claim = None
        if self.traffic is not None:
            # Wait for rate/in-flight capacity before reserving any money (docs/platform/model-traffic.md).
            try:
                claim = self.traffic.acquire(context.payer_user_id,
                    est_tokens=bound + route.output_token_ceiling,
                    deadline=run["deadline"], should_stop=should_stop)
            except TrafficCancelled:
                raise DeepSeekCancelled(Attempt()) from None
            except BillingError as exc:
                exc.not_dispatched = True  # Waiting never reserves money or sends anything.
                raise
        try:
            return self._step(context, logical_call_id, credential, system, tools, messages, effort, on_text,
                              should_stop, validate_result, run, route, sale, cost, digest, claim, bound)
        finally:
            if claim is not None:
                self.traffic.abandon(claim)  # Only a grant never admitted is freed here.

    def _step(self, context, logical_call_id, credential, system, tools, messages, effort, on_text,
              should_stop, validate_result, run, route, sale, cost, digest, claim, bound):
        key, owner = uuid4().hex, uuid4().hex
        with self.store.tx() as t:
            prior = t.execute("SELECT request_digest FROM agent_metered_calls WHERE run_key=? AND logical_id=?",
                              (run["id"], logical_call_id)).fetchone()
            if prior:
                raise BillingError("model_call_already_recorded" if prior[0] == digest else "idempotency_conflict")
            t.execute("INSERT INTO agent_metered_calls(id,run_key,logical_id,request_digest,created_at) VALUES (?,?,?,?,?)",
                      (key, run["id"], logical_call_id, digest, self.ledger._now()))
        call_key, ticket = None, None
        try:
            call_key = self.ledger.reserve_call(context, logical_call_id=logical_call_id,
                amount=self.upper_bound(route, sale, input_tokens=bound), price_id=route.sale_price_id,
                deadline=min(run["deadline"], self.ledger._now()+self.ledger.max_hold_seconds))
            ticket = self.ledger.prepare_attempt(call_key, attempt_id=key, owner=owner,
                cost_price_id=route.cost_price_id, requested_model=route.requested_model)
            self.store.execute("UPDATE agent_metered_calls SET call_key=?,attempt_key=? WHERE id=?",
                               (call_key, ticket.attempt_key, key))

            admission_refusal = None

            def before_dispatch():
                nonlocal admission_refusal
                self._check(context)
                if should_stop():
                    raise DeepSeekCancelled(Attempt())
                if route.valid_until <= self.ledger._now():
                    raise BillingError("route_confirmation_expired")
                def admit(transaction, dispatch_ticket):
                    nonlocal admission_refusal
                    try:
                        # Under the dispatch writer lock: a 402 or a failed model check that
                        # committed a closure before this point stops this send too.
                        self._guard(route)
                        if self.cost_admission is None:
                            return
                        if claim is not None:
                            self.traffic.admit(transaction, claim, attempt_key=dispatch_ticket.attempt_key,
                                inflight_until=self.ledger._now() + math.ceil(self.limits.timeout_s) + 1)
                        self.cost_admission.admit(transaction, dispatch_ticket,
                            amount=self.upper_bound(route, cost, input_tokens=bound),
                            expires_at=self.ledger._now() + math.ceil(self.limits.timeout_s) + 1)
                    except BillingError as exc:
                        admission_refusal = exc
                        raise
                try:
                    self.ledger.dispatch(ticket, block_unresolved_run=True,
                                         admission=admit if self.cost_admission is not None
                                         or self.route_guard is not None else None)
                except BillingError as exc:
                    # Preserve the existing transport contract for fencing/unknown usage;
                    # only our typed payer-policy refusal needs the new user-facing reason.
                    if exc.code.startswith("payer_"):
                        admission_refusal = exc
                    raise

            provider = DeepSeekProvider(limits=replace(self.limits, max_tokens=route.output_token_ceiling),
                                        transport=self.transport, efforts=route.reasoning_efforts,
                                        base_url=self.base_url)
            try:
                result = provider.step(credential=credential, system=system, tools=tools, messages=messages,
                    effort=effort, on_text=on_text, should_stop=should_stop,
                    user_id=self.pseudonym(context.payer_user_id), before_dispatch=before_dispatch)
            except (DeepSeekError, DeepSeekCancelled) as exc:
                ending = ("client_cancelled" if isinstance(exc, DeepSeekCancelled)
                          else "provider_interrupted" if exc.code == "provider_interrupted" else None)
                self._observe(key, ticket, route, exc.attempt, completed=False, claim=claim, ending=ending, input_bound=bound)
                if isinstance(exc, DeepSeekError) and exc.code == "provider_balance":
                    self._closed(route, exc)
                if isinstance(exc, DeepSeekError) and exc.attempt.http_status in THROTTLED:
                    # This attempt reached the provider: it is never resent (the ledger's
                    # dispatch fence holds it). Only later, still unsent attempts wait.
                    self._throttled(exc.attempt.http_status, exc.retry_after)
                if admission_refusal is not None:
                    # The transport deliberately sanitizes callback exceptions. Preserve
                    # only our local typed refusal, after recording the unsent attempt.
                    admission_refusal.not_dispatched = True
                    # The refusing transaction has rolled back: a velocity brake latches now.
                    after_refusal = getattr(self.cost_admission, "after_refusal", None)
                    if after_refusal is not None:
                        after_refusal(admission_refusal)
                    raise admission_refusal from None
                raise
            # A syntactically valid stream can still be a refusal, truncation or an
            # invalid continuation of this Agent turn. Accept it before selling usage.
            try:
                if result.step.stop_reason == "refusal":
                    # The provider's content filter has its own code (agent/safety.py).
                    from .safety import refused
                    raise refused(result.step)
                if result.step.stop_reason == "max_tokens":
                    raise ProviderError("provider_truncated", "回答过长被截断，请把任务拆小一些")
                if validate_result:
                    validate_result(result.step)
            except ProviderError as exc:
                # A refusal/truncation raised above is the provider's ending; a rejected step
                # (validate_result) has none and is never charged.
                ending = {"refusal": "content_filter", "max_tokens": "length"}.get(result.step.stop_reason)
                self._observe(key, ticket, route, result.attempt, completed=False, claim=claim, ending=ending, input_bound=bound)
                # Keep the step's private reasoning for later thinking-mode requests; its tool
                # calls were not accepted, so they never run and get no tool result.
                replay = {k: v for k, v in result.replay_message.items() if k != "tool_calls"}
                exc.replay_message = {**replay, "content": replay.get("content") or "",
                                      "reasoning_content": replay.get("reasoning_content", "")}
                usage = result.attempt.usage
                exc.metered_usage = ({"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}
                    if result.attempt.usage_reliability == "trusted_terminal" and usage else {})
                exc.usage_reliability = result.attempt.usage_reliability
                raise
            self._observe(key, ticket, route, result.attempt, completed=True, claim=claim, input_bound=bound)
            self._recovered()
            return result
        except BaseException:
            # cancel_call releases only proven unsent/known consumption. Unknown holds remain
            # until their immutable deadline, even when cancellation occurs just after dispatch.
            if call_key:
                self.ledger.cancel_call(call_key)
            raise

    def _observe(self, key, ticket, route, attempt, *, completed, claim=None, ending=None, input_bound=None):
        payload = {k: getattr(attempt,k) for k in ("requested_model", "dispatch", "provider_request_id", "returned_model",
                   "capability_version", "http_status", "finish_reason", "stream_complete", "usage_reliability", "usage_reason")}
        payload.update(raw_usage=_raw_counters(attempt.raw_usage), completed=completed, ending=ending,
                       input_bound=input_bound,
                       confirmed_route=route.confirmed_route, confirmation_ref=route.confirmation_ref,
                       metered_at=self.ledger._now())
        with self.store.tx() as t:
            t.execute("INSERT INTO agent_model_observations(call_id,attempt_key,evidence,created_at) VALUES (?,?,?,?)",
                      (key, ticket.attempt_key, _json(payload), payload["metered_at"]))
        if claim is not None:
            usage = attempt.usage if attempt.usage_reliability == "trusted_terminal" else None
            self.traffic.finish(claim, tokens=(usage.prompt_tokens + usage.completion_tokens) if usage else None)
        if attempt.usage_reliability == "trusted_terminal" and attempt.provider_request_id:
            self._settle(key)
        elif attempt.dispatch != "definitely_not_requested":
            self.ledger.mark_usage_pending(ticket, request_id=attempt.provider_request_id, reason=attempt.usage_reason)
        else:
            # A committed dispatch fence can still exist if cancellation landed between the
            # hook and network handoff. Ledger conservatively retains that unknown window.
            self.ledger.cancel_call(self.store.one("SELECT call_key FROM billing_attempts WHERE id=?", (ticket.attempt_key,))["call_key"])

    def _settle(self, call_id):
        row = self.store.one("SELECT o.*,m.route FROM agent_model_observations o JOIN agent_metered_calls c ON c.id=o.call_id "
                             "JOIN agent_metered_runs m ON m.run_key=c.run_key WHERE o.call_id=?", (call_id,))
        if not row:
            raise BillingError("usage_evidence_missing")
        evidence, route = json.loads(row["evidence"]), Route(**json.loads(row["route"]))
        usage = normalize_usage(evidence["raw_usage"])
        if evidence["usage_reliability"] != "trusted_terminal" or usage is None or not evidence["provider_request_id"]:
            raise BillingError("usage_evidence_untrusted")
        # A provider-returned model string is recorded separately and never establishes route.
        proof = UsageEvidence("deepseek", evidence["provider_request_id"], "model-observation:"+call_id,
            Usage(usage.prompt_cache_miss_tokens, usage.prompt_cache_hit_tokens, usage.completion_tokens,
                  usage.reasoning_tokens or 0), route.confirmed_route, evidence["capability_version"], evidence["metered_at"])
        # An incomplete call is sold only when the route's pinned policy charges its ending
        # (never beyond the hold); otherwise the platform absorbs it as recorded cost.
        charged = evidence["completed"] or evidence.get("ending") in route.charge_incomplete
        result = self.ledger.settle_trusted_usage(row["attempt_key"], proof,
            outcome="completed" if charged else "platform_fault")
        if not charged:
            self.ledger.cancel_call(result.call_key)
        self.store.execute("UPDATE agent_metered_calls SET metering_state='settled' WHERE id=?", (call_id,))
        return result

    def recover(self, *, limit=100) -> int:
        """Bounded local reconciliation; never calls a provider or replays a business tool."""
        integer(limit, positive=True, maximum=500)
        self.ledger.expire_due()
        self.store.execute("UPDATE agent_metered_calls SET metering_state='released' WHERE metering_state='pending' "
                           "AND call_key IN (SELECT c.id FROM billing_calls c JOIN billing_runs r ON r.id=c.run_key "
                           "WHERE r.environment=? AND r.namespace=? AND c.status='released')",
                           (self.ledger.environment, self.ledger.namespace))
        rows = self.store.all("SELECT o.call_id,o.evidence FROM agent_model_observations o "
            "JOIN agent_metered_calls c ON c.id=o.call_id JOIN billing_runs r ON r.id=c.run_key "
            "WHERE r.environment=? AND r.namespace=? AND c.metering_state<>'settled' AND json_extract(o.evidence,'$.usage_reliability')='trusted_terminal' ORDER BY o.created_at LIMIT ?",
            (self.ledger.environment,self.ledger.namespace,limit))
        settled=0
        for row in rows:
            if json.loads(row["evidence"])["usage_reliability"] == "trusted_terminal":
                self._settle(row["call_id"])
                settled+=1
        return settled


class MeteredTurnProvider:
    """Agent integration port. Requires a previously authorized run keyed by the real turn ID.

    Composition may install this only alongside the governed route/quote and wallet service.
    It creates no grant, sets no default budget, reads no host secret and enables no paid route.
    """
    name = "deepseek"

    def __init__(self, gateway: MeteredDeepSeek, *, quotes=None, credential_resolver=None):
        if quotes is not None and (quotes.gateway is not gateway or not callable(credential_resolver)
                                   or gateway.ledger.authorize != quotes.authorize):
            raise BillingError("model_configuration_required")
        if quotes is not None and gateway.cost_admission is None:
            raise BillingError("model_cost_admission_required")
        self.gateway, self.quotes = gateway, quotes
        self.credential_resolver = credential_resolver

    def resolve_credential(self, store, user_id):
        if self.credential_resolver is not None:
            return self.credential_resolver(store, user_id)
        # A missing DeepSeek configuration must never borrow a different provider's key.
        return None

    def step_for_turn(self, *, turn, logical_call_id, **arguments):
        ledger, store = self.gateway.ledger, self.gateway.store
        if self.quotes is not None:
            try:
                self.quotes.open_turn(turn)
            except BillingError as exc:
                raise ProviderError("billing_" + exc.code, "本次运行的价格或预算授权不可用，请重新确认") from None
        row = store.one("SELECT context FROM billing_runs WHERE environment=? AND namespace=? AND run_id=?",
                        (ledger.environment, ledger.namespace, turn["id"]))
        if not row:
            raise ProviderError("billing_authorization_required", "请先确认本次运行的价格与预算")
        context = ChargeContext(**json.loads(row["context"]))
        if context.actor_user_id != turn["user_id"] or context.payer_user_id != turn["user_id"]:
            raise ProviderError("billing_authorization_required", "付款授权与本次运行不匹配")
        try:
            result = self.gateway.step(context=context, logical_call_id=logical_call_id, **arguments)
        except (DeepSeekError, DeepSeekCancelled) as exc:
            if getattr(exc, "not_dispatched", False):
                exc.metered_usage, exc.usage_reliability = {"input_tokens": 0, "output_tokens": 0}, "not_dispatched"
                raise
            usage = exc.attempt.usage
            exc.metered_usage = ({"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}
                                 if exc.attempt.usage_reliability == "trusted_terminal" and usage else {})
            exc.usage_reliability = exc.attempt.usage_reliability
            raise
        except BillingError as exc:
            error = ProviderError("billing_" + exc.code, UNAVAILABLE if exc.code in CLOSED_CODES
                                  else "本次运行的预算或用量状态暂不允许继续，请查看费用记录")
            if getattr(exc, "not_dispatched", False):
                error.metered_usage = {"input_tokens": 0, "output_tokens": 0}
                error.usage_reliability = "not_dispatched"
            raise error from None
        step = result.step
        step.api_message = result.replay_message
        step.usage_reliability = result.attempt.usage_reliability
        if result.attempt.usage_reliability == "trusted_terminal":
            usage = result.attempt.usage
            step.usage = {"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}
        return step
