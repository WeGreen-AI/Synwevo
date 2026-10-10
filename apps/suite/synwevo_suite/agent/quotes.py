"""Single-run price consent. Payment authority is separate from tool permission.

The person pays by default; an organization pays when the person chose it for this quote and
the organization's current grant lets them charge it (billing.org_payers; org-payments).

Composition supplies an approved route, a current policy checker and the ledger; no route,
price, secret, budget or funds are inferred from the host or from a model response. The quote
contains no prompt, and binds a digest of the exact intended turn and conversation state.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from synwevo_core.billing import BillingError, ChargeContext
from synwevo_core.billing.models import integer
from synwevo_core.store import new_id

from .consents import ModelConsents, off_peak_rates
from .gateway import Route, _json


class RunQuotes:
    def __init__(self, gateway, *, current_route, current_authority):
        if not callable(current_route) or not callable(current_authority):
            raise BillingError("model_authority_required")
        self.gateway, self.ledger, self.store = gateway, gateway.ledger, gateway.store
        self.current_route, self.current_authority = current_route, current_authority
        self.consents = ModelConsents(self)
        self.welcome = None  # billing.welcome.WelcomeAllowance, installed by composition when configured

    @staticmethod
    def binding(conversation, *, text, parent_id, regenerate_of, context_refs=None, file_refs=None):
        value = {"conversation": conversation["id"], "head": conversation["head_id"],
                 "scope": conversation["scope_json"], "updated_at": conversation["updated_at"],
                 "text": text, "parent_id": parent_id, "regenerate_of": regenerate_of}
        if context_refs:
            value["context_refs"] = context_refs
        if file_refs:
            value["file_refs"] = file_refs
        return hashlib.sha256(_json(value).encode()).hexdigest()

    def _conversation(self, user_id, cid):
        row = self.store.one("SELECT c.* FROM agent_conversations c JOIN users u ON u.id=c.user_id "
                             "WHERE c.id=? AND c.user_id=? AND u.status='active'", (cid, user_id))
        if not row or row["status"] != "active":
            raise BillingError("quote_conversation_unavailable")
        return row

    def offer(self):
        route = self.current_route()
        if not isinstance(route, Route) or route.valid_until <= self.ledger._now():
            raise BillingError("model_route_unavailable")
        sale, _ = self.gateway._prices(route)
        raw = asdict(sale)
        return {"environment": self.ledger.environment, "model": route.requested_model,
                "confirmed_route": route.confirmed_route, "route_version": route.version,
                "price_id": sale.price_id, "unit": raw["unit"], "terms_ref": sale.terms_ref,
                "rates": {key: {"numerator": str(raw[key]["numerator"]), "denominator": str(raw[key]["denominator"])}
                          for key in ("input_uncached", "input_cached", "output")},
                "off_peak_rates": off_peak_rates(raw), "per_tokens": sale.per_tokens, "per_call_hold": str(self.gateway.upper_bound(route, sale)),
                "max_calls_limit": 12, "run_seconds_limit": min(3600, route.valid_until - self.ledger._now()),
                # A shorter run could not finish even one call inside its hold (gateway.send_window).
                "run_seconds_min": self.gateway.send_window}

    @staticmethod
    def payer_of(terms) -> dict:
        """The payer a quote/consent was issued for. Older terms (before org-payments) are personal."""
        payer = terms.get("payer") or {"kind": "user"}
        return {"kind": payer["kind"], "id": payer.get("id")}

    def _payer(self, user_id, payer) -> dict:
        """Normalize the requested payer. A person is the default; an organization only when its
        current grant lets this member charge it (rechecked at every reservation and send)."""
        if payer is None or payer.get("kind") in (None, "user"):
            if payer is not None and payer.get("id") not in (None, user_id):
                raise BillingError("payer_forbidden")
            return {"kind": "user", "id": user_id}
        oid = payer.get("id")
        if payer.get("kind") != "organization" or not isinstance(oid, str) or not oid:
            raise BillingError("invalid_payer")
        org_payers = self.ledger.org_payers
        if org_payers is None:
            raise BillingError("organization_billing_not_configured")
        with self.store.tx() as t:
            grant = org_payers._grant(t, oid, user_id)
            if (not grant or not grant["enabled"] or not org_payers._org_active(t, oid)
                    or not org_payers._member(t, oid, user_id)):
                raise BillingError("org_payer_not_granted")
        return {"kind": "organization", "id": oid}

    def issue(self, user_id, conversation, *, text, parent_id, regenerate_of,
              budget: int, max_calls: int, run_seconds: int, payer=None, context_refs=None, file_refs=None) -> dict:
        integer(budget, positive=True)
        integer(max_calls, positive=True, maximum=12)
        integer(run_seconds, positive=True, maximum=3600)
        if run_seconds < self.gateway.send_window:
            # Every call's hold ends at the run deadline and usage after it is never charged:
            # a run too short for one whole send would only be refused at its first call.
            raise BillingError("run_time_too_short")
        route = self.current_route()
        if not isinstance(route, Route):
            raise BillingError("model_route_unavailable")
        now = self.ledger._now()
        deadline = now + run_seconds
        if deadline > route.valid_until:
            raise BillingError("route_confirmation_expired")
        sale, _ = self.gateway._prices(route)
        hold = self.gateway.upper_bound(route, sale)
        if budget < hold:
            raise BillingError("run_budget_exceeded")
        if self.welcome is not None:
            self.welcome.ensure(user_id)   # first Agent use; a no-op once granted or when ineligible
        payer = self._payer(user_id, payer)
        with self.store.tx():
            current = self._conversation(user_id, conversation["id"])
            digest = self.binding(conversation, text=text, parent_id=parent_id, regenerate_of=regenerate_of, context_refs=context_refs, file_refs=file_refs)
            if digest != self.binding(current, text=text, parent_id=parent_id, regenerate_of=regenerate_of, context_refs=context_refs, file_refs=file_refs):
                raise BillingError("quote_context_changed")
            outstanding = self.store.one("SELECT COUNT(*) AS n FROM agent_model_quotes WHERE user_id=? "
                "AND accepted_turn_id IS NULL AND revoked_at IS NULL AND expires_at>?", (user_id, now))["n"]
            if outstanding >= 100:
                raise BillingError("quote_limit_exceeded")
            quote_id = new_id("quote")
            # Flat prices keep the pre-band term shape, so existing consents stay comparable.
            terms = {"route": asdict(route), "sale": sale.to_dict(), "budget": budget,
                     "max_calls": max_calls, "deadline": deadline, "per_call_hold": hold, "payer": payer}
            self.store.execute("INSERT INTO agent_model_quotes(id,environment,namespace,user_id,conversation_id,"
                "binding_digest,scope_json,terms_json,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (quote_id, self.ledger.environment, self.ledger.namespace, user_id, current["id"], digest,
                 current["scope_json"], _json(terms), now, min(now + 300, deadline)))
            return self.get(user_id, quote_id)

    def _row(self, user_id, quote_id):
        row = self.store.one("SELECT * FROM agent_model_quotes WHERE id=? AND user_id=? AND environment=? AND namespace=?",
            (quote_id, user_id, self.ledger.environment, self.ledger.namespace))
        if not row:
            raise BillingError("quote_not_found")
        return row

    def get(self, user_id, quote_id):
        row = self._row(user_id, quote_id)
        self._conversation(user_id, row["conversation_id"])
        terms = json.loads(row["terms_json"])
        sale, route = terms["sale"], terms["route"]
        # Amounts/rationals are decimal strings for browsers (no IEEE-754 precision loss).
        rates = {key: {"numerator": str(sale[key]["numerator"]), "denominator": str(sale[key]["denominator"])}
                 for key in ("input_uncached", "input_cached", "output")}
        return {"id": row["id"], "environment": row["environment"], "payer_user_id": user_id,
                "conversation_id": row["conversation_id"], "model": route["requested_model"],
                "confirmed_route": route["confirmed_route"], "route_version": route["version"],
                "price_id": sale["price_id"], "unit": sale["unit"], "rates": rates, "off_peak_rates": off_peak_rates(sale),
                "per_tokens": sale["per_tokens"], "terms_ref": sale["terms_ref"],
                "budget": str(terms["budget"]), "per_call_hold": str(terms["per_call_hold"]),
                "max_calls": terms["max_calls"], "deadline": terms["deadline"],
                "payer": self.payer_of(terms) | ({"id": user_id} if self.payer_of(terms)["kind"] == "user" else {}),
                "expires_at": row["expires_at"], "turn_id": row["accepted_turn_id"],
                "status": "revoked" if row["revoked_at"] is not None else "accepted" if row["accepted_turn_id"]
                    else "expired" if row["expires_at"] <= self.ledger._now() else "quoted"}

    def bind(self, user_id, quote_id, conversation, *, turn_id, text, parent_id, regenerate_of, context_refs=None, file_refs=None):
        """Atomically bind with the Agent's turn insert. No network or ledger call here."""
        with self.store.conn() as connection:
            if not connection.in_transaction or not getattr(self.store._local, "depth", 0):
                raise BillingError("quote_requires_turn_transaction")
        row = self._row(user_id, quote_id)
        if row["accepted_turn_id"]:
            raise BillingError("quote_already_used")
        if row["revoked_at"] is not None or row["expires_at"] <= self.ledger._now():
            raise BillingError("quote_expired_or_revoked")
        if row["binding_digest"] != self.binding(conversation, text=text, parent_id=parent_id, regenerate_of=regenerate_of, context_refs=context_refs, file_refs=file_refs):
            raise BillingError("quote_context_changed")
        terms = json.loads(row["terms_json"])
        if self.current_route() != Route(**terms["route"]):
            raise BillingError("model_configuration_changed")
        self._conversation(user_id, conversation["id"])
        self.store.execute("UPDATE agent_model_quotes SET accepted_turn_id=? WHERE id=?", (turn_id, quote_id))

    def authorize(self, transaction, context: ChargeContext, action: str) -> bool:
        """Ledger callback, checked under its writer lock at open/reserve/dispatch.

        Business/policy authority is explicitly delegated to composition. The same payer and
        turn must remain authorized; a balance or admin role never supplies that authority.
        """
        if context.actor_user_id != context.payer_user_id or context.delegation_id is not None:
            return False
        row = transaction.execute("SELECT q.* FROM agent_model_quotes q JOIN agent_turns t ON t.id=q.accepted_turn_id "
            "JOIN agent_conversations c ON c.id=t.conversation_id WHERE q.id=? AND q.user_id=? "
            "AND q.environment=? AND q.namespace=? AND t.id=? AND t.user_id=q.user_id "
            "AND c.user_id=q.user_id AND c.id=q.conversation_id AND c.status='active' "
            "AND c.scope_json IS q.scope_json AND t.cancel_requested=0 AND t.status IN ('queued','running','waiting_confirmation')",
            (context.authorization_ref, context.actor_user_id, self.ledger.environment, self.ledger.namespace, context.run_id)).fetchone()
        if not row or row["revoked_at"] is not None:
            return False
        if not self.consents.authorize(transaction, context.actor_user_id, row["id"]):
            return False
        terms = json.loads(row["terms_json"])
        payer = self.payer_of(terms)
        # The run's payer is the one the person chose for this quote, never another.
        if context.payer != ((payer["kind"], payer["id"]) if payer["kind"] == "organization" else ("user", context.actor_user_id)):
            return False
        return (terms["deadline"] > self.ledger._now() and self.current_route() == Route(**terms["route"])
                and self.current_authority(context) is True)

    def open_turn(self, turn):
        row = self.store.one("SELECT * FROM agent_model_quotes WHERE accepted_turn_id=? AND user_id=? "
                             "AND environment=? AND namespace=?", (turn["id"], turn["user_id"],
                             self.ledger.environment, self.ledger.namespace))
        if not row:
            raise BillingError("authorization_required")
        terms = json.loads(row["terms_json"])
        payer = self.payer_of(terms)
        if payer["kind"] == "organization":
            context = ChargeContext(turn["user_id"], turn["user_id"], turn["id"], row["id"],
                                    payer_kind="organization", organization_id=payer["id"])
        else:
            context = ChargeContext(turn["user_id"], turn["user_id"], turn["id"], row["id"])
        return self.gateway.open_run(context, route=Route(**terms["route"]), budget=terms["budget"],
                                     max_calls=terms["max_calls"], deadline=terms["deadline"])

    def revoke(self, user_id, quote_id):
        with self.store.tx():
            row = self._row(user_id, quote_id)
            self._conversation(user_id, row["conversation_id"])
            if row["revoked_at"] is None:
                self.store.execute("UPDATE agent_model_quotes SET revoked_at=? WHERE id=?", (self.ledger._now(), quote_id))
            return self.get(user_id, quote_id)
