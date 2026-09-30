"""Durable metering boundary between the Agent transport and the shared money ledger.

No price/credential discovery or enablement lives here. A trusted configuration service must
supply a confirmed route and published prices; a current payment authorizer must be installed
on Ledger. No network attempt is retried. Recovery settles saved evidence only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Callable
from uuid import uuid4

from synwevo_core.billing import BillingError, ChargeContext, Ledger, PriceSnapshot, Usage, UsageEvidence
from synwevo_core.billing.models import identifier, integer

from .deepseek import (Attempt, DeepSeekCancelled, DeepSeekError,
                       DeepSeekProvider, DeepSeekResult, Limits, normalize_usage)
from .provider import Credential, ProviderError


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


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

    def __post_init__(self):
        for value in (self.version, self.requested_model, self.confirmed_route, self.confirmation_ref,
                      self.cost_basis_ref, self.sale_price_id, self.cost_price_id):
            identifier(value)
        integer(self.input_token_ceiling, positive=True, maximum=2_000_000)
        integer(self.output_token_ceiling, positive=True, maximum=32768)
        integer(self.valid_until, positive=True)


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
                 transport=None, limits: Limits | None = None):
        if not callable(current_authority):
            raise BillingError("model_authority_required")
        self.ledger, self.store = ledger, ledger.store
        self.current_authority = current_authority
        self.transport = transport  # Trusted Python fixture injection only; not a route option.
        self.limits = limits or Limits()

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
    def upper_bound(route, sale):
        # No cache discount is assumed; include at most one earlier fractional-unit carry.
        # The greater input rate is conservative even for an unusual published cache tariff.
        rate = max(sale.input_uncached.fraction, sale.input_cached.fraction)
        exact = (route.input_token_ceiling * rate + route.output_token_ceiling * sale.output.fraction) / sale.per_tokens
        return integer((exact.numerator + exact.denominator - 1) // exact.denominator + 1)

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
        if credential.kind != "platform" or credential.model != route.requested_model:
            raise BillingError("model_configuration_changed")
        if route.valid_until <= self.ledger._now():
            raise BillingError("route_confirmation_expired")
        identifier(logical_call_id)
        sale, _ = self._prices(route)
        request = _json({"route": asdict(route), "context": asdict(context), "system": system,
                         "tools": tools, "messages": messages, "effort": effort})
        if len(request.encode()) > self.limits.request_bytes:
            raise BillingError("model_request_too_large")
        digest = hashlib.sha256(request.encode()).hexdigest()
        request = None  # Request bodies are not persisted in the money evidence tables.
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
                amount=self.upper_bound(route, sale), price_id=route.sale_price_id,
                deadline=min(run["deadline"], self.ledger._now()+self.ledger.max_hold_seconds))
            ticket = self.ledger.prepare_attempt(call_key, attempt_id=key, owner=owner,
                cost_price_id=route.cost_price_id, requested_model=route.requested_model)
            self.store.execute("UPDATE agent_metered_calls SET call_key=?,attempt_key=? WHERE id=?",
                               (call_key, ticket.attempt_key, key))

            def before_dispatch():
                self._check(context)
                if should_stop():
                    raise DeepSeekCancelled(Attempt())
                if route.valid_until <= self.ledger._now():
                    raise BillingError("route_confirmation_expired")
                self.ledger.dispatch(ticket, block_unresolved_run=True)

            provider = DeepSeekProvider(limits=replace(self.limits, max_tokens=route.output_token_ceiling),
                                        transport=self.transport)
            # A scoped pseudonym, stable per platform/environment/payer, reveals no account ID.
            pseudonym = hashlib.sha256((self.ledger.environment + ":" + self.ledger.namespace + ":" +
                                       context.payer_user_id).encode()).hexdigest()
            try:
                result = provider.step(credential=credential, system=system, tools=tools, messages=messages,
                    effort=effort, on_text=on_text, should_stop=should_stop,
                    user_id=pseudonym, before_dispatch=before_dispatch)
            except (DeepSeekError, DeepSeekCancelled) as exc:
                self._observe(key, ticket, route, exc.attempt, completed=False)
                raise
            # A syntactically valid stream can still be a refusal, truncation or an
            # invalid continuation of this Agent turn. Accept it before selling usage.
            try:
                if result.step.stop_reason == "refusal":
                    raise ProviderError("provider_refused", "模型拒绝回答这个请求")
                if result.step.stop_reason == "max_tokens":
                    raise ProviderError("provider_truncated", "回答过长被截断，请把任务拆小一些")
                if validate_result:
                    validate_result(result.step)
            except ProviderError as exc:
                self._observe(key, ticket, route, result.attempt, completed=False)
                usage = result.attempt.usage
                exc.metered_usage = ({"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}
                    if result.attempt.usage_reliability == "trusted_terminal" and usage else {})
                exc.usage_reliability = result.attempt.usage_reliability
                raise
            self._observe(key, ticket, route, result.attempt, completed=True)
            return result
        except BaseException:
            # cancel_call releases only proven unsent/known consumption. Unknown holds remain
            # until their immutable deadline, even when cancellation occurs just after dispatch.
            if call_key:
                self.ledger.cancel_call(call_key)
            raise

    def _observe(self, key, ticket, route, attempt, *, completed):
        payload = {k: getattr(attempt,k) for k in ("requested_model", "dispatch", "provider_request_id", "returned_model",
                   "capability_version", "http_status", "finish_reason", "stream_complete", "usage_reliability", "usage_reason")}
        payload.update(raw_usage=_raw_counters(attempt.raw_usage), completed=completed,
                       confirmed_route=route.confirmed_route, confirmation_ref=route.confirmation_ref,
                       metered_at=self.ledger._now())
        with self.store.tx() as t:
            t.execute("INSERT INTO agent_model_observations(call_id,attempt_key,evidence,created_at) VALUES (?,?,?,?)",
                      (key, ticket.attempt_key, _json(payload), payload["metered_at"]))
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
        result = self.ledger.settle_trusted_usage(row["attempt_key"], proof,
            outcome="completed" if evidence["completed"] else "platform_fault")
        if not evidence["completed"]:
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

    def __init__(self, gateway: MeteredDeepSeek):
        self.gateway = gateway

    def step_for_turn(self, *, turn, logical_call_id, **arguments):
        ledger, store = self.gateway.ledger, self.gateway.store
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
            usage = exc.attempt.usage
            exc.metered_usage = ({"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}
                                 if exc.attempt.usage_reliability == "trusted_terminal" and usage else {})
            exc.usage_reliability = exc.attempt.usage_reliability
            raise
        except BillingError as exc:
            raise ProviderError("billing_" + exc.code, "本次运行的预算或用量状态暂不允许继续，请查看费用记录") from None
        step = result.step
        step.api_message = result.replay_message
        step.usage_reliability = result.attempt.usage_reliability
        if result.attempt.usage_reliability == "trusted_terminal":
            usage = result.attempt.usage
            step.usage = {"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}
        return step
