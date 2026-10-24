"""Single-run authorization of a paid Agent turn. Payment authority is separate from tool permission.

A send from the page (``via=ui``, send-and-go) is authorized automatically: ``issue_automatic``
mints the quote inside the turn's admission transaction and the engine binds it at once, so
the deadline is fixed at admission and there is no issue-to-bind gap. The bound is the governed
default — one call's hold times ``MAX_CALLS`` — or the person's own lower cap from their Agent
settings. The legacy explicit path (``agent.turn.quote`` → ``issue`` → ``bind``, and continuing
consents) stays for compatibility and audit; the page no longer uses it.

The person pays by default; an organization pays when the person chose it for this run and
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
    #: The most model calls one answer may make; the automatic bound holds this many calls.
    MAX_CALLS = 12
    #: The longest run (seconds) an authorization may name; the automatic run uses the lesser of
    #: this and the route's remaining validity.
    MAX_RUN_SECONDS = 3600
    #: How long an explicit (legacy) quote stays acceptable, at most.
    QUOTE_TTL = 300

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
                "max_calls_limit": self.MAX_CALLS,
                "run_seconds_limit": min(self.MAX_RUN_SECONDS, route.valid_until - self.ledger._now()),
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
        integer(max_calls, positive=True, maximum=self.MAX_CALLS)
        integer(run_seconds, positive=True, maximum=self.MAX_RUN_SECONDS)
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
            # rr-money-1: the quote is acceptable only while the run it fixes still has time
            # for one whole send after binding (bind refuses later anyway); never already past.
            expires_at = max(now + 1, min(now + self.QUOTE_TTL, deadline - self.gateway.send_window))
            self.store.execute("INSERT INTO agent_model_quotes(id,environment,namespace,user_id,conversation_id,"
                "binding_digest,scope_json,terms_json,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (quote_id, self.ledger.environment, self.ledger.namespace, user_id, current["id"], digest,
                 current["scope_json"], _json(terms), now, expires_at))
            return self.get(user_id, quote_id)

    # ------------------------------------------------------------------ send-and-go
    def _turn_transaction(self) -> None:
        with self.store.conn() as connection:
            if not connection.in_transaction or not getattr(self.store._local, "depth", 0):
                raise BillingError("quote_requires_turn_transaction")

    def personal_cap(self, user_id) -> dict | None:
        """The person's own maximum for one answer from their Agent settings, or None: ``{"amount":
        int, "unit": <sale unit code>}``. Stored by agent.settings.update; checked here at send."""
        row = self.store.one("SELECT run_budget_cap, run_budget_cap_unit FROM agent_settings WHERE user_id=?", (user_id,))
        if not row or row["run_budget_cap"] is None:
            return None
        try:
            amount = integer(int(row["run_budget_cap"]), positive=True)
        except (ValueError, TypeError, BillingError):
            raise BillingError("run_cap_invalid") from None
        return {"amount": amount, "unit": row["run_budget_cap_unit"]}

    def _bound(self, user_id, route, sale) -> dict:
        """The automatic run's bound for this person on this route: the governed default (one
        call's hold x MAX_CALLS) or the person's lower cap. Fails closed on a cap that cannot be
        honoured (another unit, or below one call's hold); a cap is never silently ignored."""
        hold = self.gateway.upper_bound(route, sale)
        default = hold * self.MAX_CALLS
        cap = self.personal_cap(user_id)
        if cap is None:
            return {"hold": hold, "default": default, "budget": default, "cap": None, "source": "default"}
        if cap["unit"] != sale.unit.code:
            raise BillingError("run_cap_unit_changed")
        budget = min(cap["amount"], default)
        if budget < hold:
            error = BillingError("run_cap_below_call_hold")
            error.detail = {"per_call_hold": str(hold), "cap": str(cap["amount"]), "unit": asdict(sale.unit)}
            raise error
        return {"hold": hold, "default": default, "budget": budget, "cap": cap["amount"], "source": "personal_cap"}

    def _run_seconds(self, route) -> int:
        """The automatic run's length: the lesser of MAX_RUN_SECONDS and what is left of the route's
        validity; a route with less than one send's window left refuses the run (fail closed)."""
        seconds = min(self.MAX_RUN_SECONDS, route.valid_until - self.ledger._now())
        if seconds < self.gateway.send_window:
            raise BillingError("route_confirmation_expired")
        return seconds

    def _personal_limits_configured(self, user_id, unit_code) -> None:
        """Admission-time read of the personal spending-limit policy (D-4): a person without an
        enabled policy would only be refused at the first call; refuse the send instead, so nothing
        is stored. Enforcement at reserve and dispatch is unchanged (billing.payer_budgets)."""
        service = getattr(self.ledger, "payer_budgets", None)
        if service is None:
            return
        with self.store.conn() as t:
            policy = service._policy(t, user_id, unit_code)
        if policy is None:
            raise BillingError("payer_budget_not_configured")
        if not policy["enabled"]:
            raise BillingError("payer_budget_disabled")

    def issue_automatic(self, user_id, conversation, *, text, parent_id, regenerate_of, payer=None,
                        context_refs=None, file_refs=None, issued: dict, seen: dict | None = None) -> str:
        """Mint the run authorization of a person's own send from the page, inside the turn's
        admission transaction; the engine binds it at once with :meth:`bind`.

        ``issued`` is the audit record of who asked (``actor_user_id``, ``via``, ``source``,
        ``op_run_id``); it is kept in the immutable quote terms with ``mode`` and ``bound``.
        ``seen`` is the price the page displayed (:meth:`shown_price`). No outstanding-quote
        count (the quote never stays unbound) and no welcome grant (the ledger owns its
        transaction; the payers read grants it before the first send)."""
        self._turn_transaction()
        if not isinstance(issued, dict) or issued.get("via") != "ui" or not issued.get("actor_user_id"):
            raise BillingError("automatic_issue_requires_person")
        route = self.current_route()
        if not isinstance(route, Route):
            raise BillingError("model_route_unavailable")
        now = self.ledger._now()
        run_seconds = self._run_seconds(route)
        sale, _ = self.gateway._prices(route)
        bound = self._bound(user_id, route, sale)
        self.shown_price(seen, route, sale, bound)
        payer = self._payer(user_id, payer)
        if payer["kind"] == "user":
            self._personal_limits_configured(user_id, sale.unit.code)
        current = self._conversation(user_id, conversation["id"])
        digest = self.binding(conversation, text=text, parent_id=parent_id, regenerate_of=regenerate_of,
                              context_refs=context_refs, file_refs=file_refs)
        if digest != self.binding(current, text=text, parent_id=parent_id, regenerate_of=regenerate_of,
                                  context_refs=context_refs, file_refs=file_refs):
            raise BillingError("quote_context_changed")
        quote_id = new_id("quote")
        deadline = now + run_seconds
        terms = {"route": asdict(route), "sale": sale.to_dict(), "budget": bound["budget"],
                 "max_calls": self.MAX_CALLS, "deadline": deadline, "per_call_hold": bound["hold"], "payer": payer,
                 "issued": {"mode": "automatic", "actor_user_id": issued["actor_user_id"], "via": "ui",
                            "source": issued.get("source") or "agent.turn.start",
                            "op_run_id": issued.get("op_run_id"), "bound": bound["source"]}}
        self.store.execute("INSERT INTO agent_model_quotes(id,environment,namespace,user_id,conversation_id,"
            "binding_digest,scope_json,terms_json,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (quote_id, self.ledger.environment, self.ledger.namespace, user_id, current["id"], digest,
             current["scope_json"], _json(terms), now, now + 1))
        return quote_id

    @staticmethod
    def shown_price(seen: dict | None, route, sale, bound: dict) -> None:
        """The price the page showed is the price the send runs at (AD-1). ``seen`` holds the sale
        price id and route version the person's page displayed (agent.billing.status ``run``).
        When either is given and differs from what this run would bind, refuse with the current
        figures (``price_changed``; the admission rolls back, nothing is stored) so the page shows
        them before the person sends again. A page that sends neither (an older cached bundle)
        is not checked."""
        if not seen:
            return
        price_id, version = seen.get("price_id"), seen.get("route_version")
        if (price_id is None or price_id == sale.price_id) and (version is None or version == route.version):
            return
        error = BillingError("price_changed")
        error.detail = {"current": {"price_id": sale.price_id, "route_version": route.version,
                                    "maximum": str(bound["budget"]), "per_call_hold": str(bound["hold"]),
                                    "unit": asdict(sale.unit)}}
        raise error

    def run_bound(self, user_id) -> dict | None:
        """What an automatic send would be bound to now (agent.billing.status ``run``); None when
        the route is unusable. ``usable`` is False when the person's cap could not be honoured
        or the route has less than one send's window left — the send would be refused.
        ``price_id`` and ``route_version`` are what the send would bind; the page sends them back
        as ``seen_price_id``/``seen_route_version`` (:meth:`shown_price`)."""
        route = self.current_route()
        if not isinstance(route, Route):
            return None
        sale, _ = self.gateway._prices(route)
        hold = self.gateway.upper_bound(route, sale)
        default = hold * self.MAX_CALLS
        run_seconds = max(0, min(self.MAX_RUN_SECONDS, route.valid_until - self.ledger._now()))
        usable = run_seconds >= self.gateway.send_window
        shown = {"price_id": sale.price_id, "route_version": route.version}
        try:
            bound = self._bound(user_id, route, sale)
        except BillingError:
            cap = self.personal_cap(user_id)
            return {"maximum": str(default), "default": str(default), "cap": str(cap["amount"]) if cap else None,
                    "source": "personal_cap" if cap else "default", "per_call_hold": str(hold),
                    "max_calls": self.MAX_CALLS, "run_seconds": run_seconds, "usable": False, **shown}
        return {"maximum": str(bound["budget"]), "default": str(default),
                "cap": None if bound["cap"] is None else str(bound["cap"]), "source": bound["source"],
                "per_call_hold": str(hold), "max_calls": self.MAX_CALLS, "run_seconds": run_seconds, "usable": usable,
                **shown}

    def price_notice(self, user_id, offer: dict) -> dict | None:
        """The person's last bound run compared with the current offer (D-7): the previous pricing
        when the route version, sale price or per-call hold differs, else None. Information only;
        it never blocks a send."""
        row = self.store.one("SELECT terms_json, created_at FROM agent_model_quotes WHERE user_id=? AND environment=? "
                             "AND namespace=? AND accepted_turn_id IS NOT NULL ORDER BY created_at DESC, rowid DESC LIMIT 1",
                             (user_id, self.ledger.environment, self.ledger.namespace))
        if not row:
            return None
        terms = json.loads(row["terms_json"])
        sale, route = terms["sale"], terms["route"]
        previous = {"model": route["requested_model"], "confirmed_route": route["confirmed_route"],
                    "route_version": route["version"], "price_id": sale["price_id"], "unit": sale["unit"],
                    "rates": self._rates(sale), "off_peak_rates": off_peak_rates(sale), "per_tokens": sale["per_tokens"],
                    "per_call_hold": str(terms["per_call_hold"]), "terms_ref": sale["terms_ref"],
                    "environment": self.ledger.environment}
        same = (previous["route_version"] == offer["route_version"] and previous["price_id"] == offer["price_id"]
                and previous["per_call_hold"] == offer["per_call_hold"])
        return None if same else {"previous": previous, "previous_run_at": int(row["created_at"])}

    @staticmethod
    def _rates(sale: dict) -> dict:
        # Amounts/rationals are decimal strings for browsers (no IEEE-754 precision loss).
        return {key: {"numerator": str(sale[key]["numerator"]), "denominator": str(sale[key]["denominator"])}
                for key in ("input_uncached", "input_cached", "output")}

    def authorization_of(self, row) -> str:
        """How a quote was authorized: ``automatic`` (send-and-go), ``consent`` (minted under a
        continuing consent) or ``confirmed`` (an explicit legacy quote)."""
        terms = json.loads(row["terms_json"])
        if (terms.get("issued") or {}).get("mode") == "automatic":
            return "automatic"
        if self.store.one("SELECT 1 FROM agent_quote_consents WHERE quote_id=?", (row["id"],)):
            return "consent"
        return "confirmed"

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
        rates = self._rates(sale)
        return {"id": row["id"], "environment": row["environment"], "payer_user_id": user_id,
                "authorization": self.authorization_of(row),
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
        self._turn_transaction()
        row = self._row(user_id, quote_id)
        if row["accepted_turn_id"]:
            raise BillingError("quote_already_used")
        terms = json.loads(row["terms_json"])
        now = self.ledger._now()
        if terms["deadline"] - now < self.gateway.send_window:
            # rr-money-1: a run bound with less than one send's window left could never make a
            # call; refuse here, unstored, rather than admit a turn that fails at its first step.
            raise BillingError("run_time_too_short")
        if row["revoked_at"] is not None or row["expires_at"] <= now:
            raise BillingError("quote_expired_or_revoked")
        if row["binding_digest"] != self.binding(conversation, text=text, parent_id=parent_id, regenerate_of=regenerate_of, context_refs=context_refs, file_refs=file_refs):
            raise BillingError("quote_context_changed")
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
