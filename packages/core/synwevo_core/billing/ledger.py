"""Internal ledger service. No routes, network, implicit migration, or charging enablement.

All monetary decisions use Store.tx (BEGIN IMMEDIATE); all methods finish their transaction
before returning. Never dispatch a provider request until dispatch() has returned successfully.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from fractions import Fraction
import json
import time
from uuid import uuid4

from ..store import Store
from .bands import pin_band
from .models import (AttemptTicket, Balance, BillingError, ChargeContext, DisputeNotice, PaymentReceipt,
                     PriceSnapshot, Settlement, Unit, UsageEvidence, identifier, integer)

#: Account kinds that hold a payer's money (never negative); the others are platform accounts.
WALLET_KINDS = ("wallet", "org_wallet")
#: Wallet journal kinds the statements show (suite_schema.billing.JOURNAL_KINDS).
STATEMENT_KINDS = ("paid", "test_payment", "sale", "grant", "chargeback")
GRANT_KINDS = ("welcome", "admin")


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _id() -> str:
    return uuid4().hex


class Ledger:
    """Trusted application service; an instance binds a deployment environment and namespace.

    authorize(connection, context, action) MUST return True after current payment delegation,
    tool/data policy and budget authorization checks; there is no permissive default.
    verify_payment(connection, receipt) MUST return True only for a durable, authenticated
    provider fact. Both callbacks are local database checks, never network calls. Neither a
    context descriptor nor a PaymentReceipt constitutes proof of authority on its own.
    """

    def __init__(self, store: Store, *, environment: str, namespace: str, authorize=None,
                 verify_payment=None, clock=None, max_hold_seconds: int = 86400, verify_dispute=None):
        if environment not in ("test", "live"):
            raise BillingError("invalid_environment")
        self.store = store
        self.environment = environment
        self.namespace = identifier(namespace)
        self.authorize = authorize
        self.payer_budgets = None  # Trusted composition installs a policy consumer, never an implicit grant.
        # Organization payer policy (billing.org_payers.OrgPayers): the organization's current
        # grant to the member and its monthly limits, checked under the reservation's writer
        # lock. None refuses every organization payer.
        self.org_payers = None
        self.verify_payment = verify_payment
        # verify_dispute(connection, DisputeNotice) -> True only for a durable, authenticated
        # provider dispute fact (the same contract as verify_payment). None refuses chargebacks.
        self.verify_dispute = verify_dispute
        self.clock = clock or (lambda: int(time.time()))
        self.max_hold_seconds = integer(max_hold_seconds, positive=True, maximum=86400)

    def _now(self):
        return integer(self.clock())

    @contextmanager
    def _tx(self):
        # Store's nested tx is not a savepoint. Reject nesting: callers must not catch a billing
        # refusal then commit partial money writes, or send before the outer tx has committed.
        with self.store.conn() as connection:
            if connection.in_transaction:
                raise BillingError("billing_requires_own_transaction")
            # Store defaults to WAL/NORMAL. Money commits need WAL fsync before permitting
            # network dispatch or acknowledging payment; change this connection, not schema.
            connection.execute("PRAGMA synchronous=FULL")
        with self.store.tx() as t:
            yield t

    def _active_user(self, t, user_id):
        identifier(user_id)
        if not t.execute("SELECT 1 FROM users WHERE id=? AND status='active' AND deleted_at IS NULL",
                         (user_id,)).fetchone():
            raise BillingError("account_unavailable")

    def _authorize(self, t, context: ChargeContext, action: str):
        self._active_user(t, context.payer_user_id)
        self._active_user(t, context.actor_user_id)
        if self.authorize is None or self.authorize(t, context, action) is not True:
            raise BillingError("payment_authority_denied")

    def _unit(self, t, unit: Unit):
        if not isinstance(unit, Unit):
            raise BillingError("invalid_unit")
        row = t.execute("SELECT scale FROM billing_units WHERE code=?", (unit.code,)).fetchone()
        if row and row[0] != unit.scale:
            raise BillingError("unit_scale_conflict")
        if not row:
            t.execute("INSERT INTO billing_units(code,scale) VALUES (?,?)", (unit.code, unit.scale))

    def _account(self, t, unit: str, owner: str, kind: str = "wallet"):
        row = t.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? AND kind=? AND owner=?",
                        (self.environment, unit, kind, owner)).fetchone()
        if row:
            return row[0]
        key = _id()
        t.execute("INSERT INTO billing_accounts(id,environment,unit,owner,kind) VALUES (?,?,?,?,?)",
                  (key, self.environment, unit, owner, kind))
        t.execute("INSERT INTO billing_balances(account_id,amount) VALUES (?,0)", (key,))
        return key

    def _active_org(self, t, organization_id):
        identifier(organization_id)
        if not t.execute("SELECT 1 FROM organizations WHERE id=? AND status='active' AND deleted_at IS NULL",
                         (organization_id,)).fetchone():
            raise BillingError("organization_unavailable")

    @staticmethod
    def _payer_kind(kind):
        if kind not in ("user", "organization"):
            raise BillingError("invalid_payer")
        return "wallet" if kind == "user" else "org_wallet"

    def _payer_account(self, t, unit: str, payer_kind: str, payer_id: str):
        """The payer's wallet: a person's (``user``) or an organization's (``organization``)."""
        return self._account(t, unit, identifier(payer_id), self._payer_kind(payer_kind))

    def _context_account(self, t, unit: str, context: ChargeContext):
        kind, owner = context.payer
        return self._payer_account(t, unit, kind, owner)

    @staticmethod
    def _frozen(t, account) -> bool:
        return t.execute("SELECT 1 FROM billing_account_freezes WHERE account_id=? AND lifted_at IS NULL LIMIT 1",
                         (account,)).fetchone() is not None

    def _balance(self, t, account):
        row = t.execute("SELECT amount FROM billing_balances WHERE account_id=?", (account,)).fetchone()
        if row is None:
            raise BillingError("balance_cache_missing")
        held = t.execute("SELECT COALESCE(SUM(hold_units),0) FROM billing_calls WHERE account_id=? AND status='reserved'",
                         (account,)).fetchone()[0]
        if row[0] < held:
            raise BillingError("balance_cache_inconsistent")
        return row[0], held

    def _post(self, t, *, voucher, unit, debit, credit, amount, kind, reference):
        integer(amount)
        identifier(voucher)
        journal = _id()
        t.execute("INSERT INTO billing_journal(id,environment,namespace,voucher,unit,debit_account,"
                  "credit_account,amount,kind,reference,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (journal, self.environment, self.namespace, voucher, unit, debit, credit,
                   amount, kind, reference, self._now()))
        return journal

    def publish_price(self, snapshot: PriceSnapshot) -> str:
        """Persist an approved immutable version; management authorization belongs to caller.

        A version ID is deployment-global, not tenant-supplied. Publishing does not enable
        charging, and the service contains no production price or exchange-rate defaults.
        """
        if not isinstance(snapshot, PriceSnapshot):
            raise BillingError("invalid_price")
        encoded = _json(snapshot.to_dict())
        with self._tx() as t:
            self._unit(t, snapshot.unit)
            row = t.execute("SELECT snapshot FROM billing_prices WHERE environment=? AND price_id=?",
                            (self.environment, snapshot.price_id)).fetchone()
            if row:
                if row[0] != encoded:
                    raise BillingError("price_version_conflict")
            else:
                t.execute("INSERT INTO billing_prices VALUES (?,?,?,?)",
                          (self.environment, snapshot.price_id, encoded, self._now()))
        return snapshot.price_id

    def _price(self, t, price_id, purpose):
        row = t.execute("SELECT snapshot FROM billing_prices WHERE environment=? AND price_id=?",
                        (self.environment, identifier(price_id))).fetchone()
        if row is None:
            raise BillingError("price_not_published")
        price = PriceSnapshot.from_dict(json.loads(row[0]))
        if price.purpose != purpose or price.effective_from > self._now():
            raise BillingError("price_not_applicable")
        return price

    def create_payment_order(self, *, order_id: str, payer_user_id: str, provider: str,
                             merchant_id: str, amount_minor: int, currency: str, unit: Unit,
                             credit_units: int, exchange_version: str, payer_kind: str = "user",
                             payer_org_id: str | None = None) -> str:
        """Internal server-priced order only. Creates no balance and claims no payment.

        ``payer_user_id`` is the person who pays. With ``payer_kind='organization'`` the verified
        payment funds ``payer_org_id``'s wallet; whether that person may top it up is the
        caller's (current organization permission) decision, taken before this call.
        """
        for value in (order_id, payer_user_id, provider, merchant_id, currency, exchange_version):
            identifier(value)
        integer(amount_minor, positive=True)
        integer(credit_units, positive=True)
        self._payer_kind(payer_kind)
        if (payer_kind == "organization") != (payer_org_id is not None):
            raise BillingError("invalid_payer")
        args = (payer_user_id, provider, merchant_id, amount_minor, currency, unit.code,
                credit_units, exchange_version, payer_kind, payer_org_id)
        columns = ("payer_user_id", "provider", "merchant_id", "amount_minor", "currency", "unit",
                   "credit_units", "exchange_version", "payer_kind", "payer_org_id")
        with self._tx() as t:
            self._unit(t, unit)
            self._active_user(t, payer_user_id)
            if payer_org_id is not None:
                self._active_org(t, payer_org_id)
            row = t.execute("SELECT * FROM billing_orders WHERE environment=? AND namespace=? AND order_id=?",
                            (self.environment, self.namespace, order_id)).fetchone()
            if row:
                if tuple(row[c] for c in columns) != args:
                    raise BillingError("idempotency_conflict")
                return row["id"]
            key = _id()
            t.execute("INSERT INTO billing_orders(id,environment,namespace,order_id," + ",".join(columns) +
                      ",created_at) VALUES (" + ",".join("?" * (len(columns) + 5)) + ")",
                      (key, self.environment, self.namespace, order_id, *args, self._now()))
            return key

    def credit_verified_payment(self, receipt: PaymentReceipt) -> str:
        """Atomically credit one authoritative payment, fenced by transaction AND event ID.

        The mandatory verifier must check signature/inbox provenance, merchant, environment,
        terminal success and reversal/dispute state. There is no 'paid=True' shortcut.
        """
        if not isinstance(receipt, PaymentReceipt) or receipt.environment != self.environment:
            raise BillingError("payment_environment_mismatch")
        with self._tx() as t:
            if self.verify_payment is None or self.verify_payment(t, receipt) is not True:
                raise BillingError("payment_evidence_untrusted")
            order = t.execute("SELECT * FROM billing_orders WHERE environment=? AND namespace=? AND order_id=?",
                              (self.environment, self.namespace, receipt.order_id)).fetchone()
            if not order or any(order[k] != getattr(receipt, k) for k in
                                ("provider", "merchant_id", "amount_minor", "currency")):
                raise BillingError("payment_order_mismatch")
            facts = _json({k: getattr(receipt, k) for k in ("environment", "provider", "merchant_id",
                                                          "payment_id", "order_id", "amount_minor", "currency")}
                         | {"order_key": order["id"]})
            unique = (self.environment, receipt.provider, receipt.merchant_id, receipt.payment_id)
            # Transaction identity cannot be widened by changing order, payer or merchant.
            payment = t.execute("SELECT * FROM billing_payments WHERE environment=? AND provider=? AND payment_id=?",
                                (self.environment, receipt.provider, receipt.payment_id)).fetchone()
            if payment:
                if payment["facts"] != facts:
                    raise BillingError("payment_transaction_conflict")
                payment_key, journal = payment["id"], payment["journal_id"]
            else:
                if t.execute("SELECT 1 FROM billing_payments WHERE order_key=?", (order["id"],)).fetchone():
                    raise BillingError("order_already_funded")
                if order["payer_kind"] == "organization":
                    wallet = self._payer_account(t, order["unit"], "organization", order["payer_org_id"])
                else:
                    wallet = self._account(t, order["unit"], order["payer_user_id"])
                kind = "paid" if self.environment == "live" else "test_payment"
                source = self._account(t, order["unit"], "platform", kind)
                journal = self._post(t, voucher="payment:" + order["id"], unit=order["unit"],
                                     debit=source, credit=wallet, amount=order["credit_units"],
                                     kind=kind, reference=order["id"])
                payment_key = _id()
                t.execute("INSERT INTO billing_payments VALUES (?,?,?,?,?,?,?,?)",
                          (payment_key, *unique, order["id"], journal, facts))
            event_unique = (*unique[:3], receipt.event_id)
            event = t.execute("SELECT * FROM billing_payment_events WHERE environment=? AND provider=? AND merchant_id=? AND event_id=?",
                              event_unique).fetchone()
            if event:
                if (event["payment_key"] != payment_key or event["payload_sha256"] != receipt.payload_sha256
                        or event["evidence_ref"] != receipt.evidence_ref):
                    raise BillingError("payment_event_conflict")
            else:
                t.execute("INSERT INTO billing_payment_events VALUES (?,?,?,?,?,?,?,?)",
                          (*event_unique, payment_key, receipt.evidence_ref, receipt.payload_sha256, self._now()))
            return journal

    def grant(self, *, grant_id: str, kind: str, payer_kind: str, payer_id: str, unit: Unit, amount: int,
              reason: str, actor_id: str, abuse_key: str | None = None) -> dict:
        """Credit an audited grant: never a payment, never anonymous, never without a reason.

        ``kind='welcome'`` is the one-time allowance (a person only; ``abuse_key`` identifies the
        verified address so a deleted and re-registered account cannot take it twice); the
        database refuses a second welcome per account or per address. ``kind='admin'`` is an
        administrator's grant: ``actor_id`` is that administrator, whose permission the caller
        checked in the same request. Idempotent by ``grant_id``: a replay with the same facts
        returns the first result, changed facts conflict. The row is the audit record (actor,
        reason, time); the journal entry is kind ``grant`` from the platform grant account.
        ``created`` in the result says whether this call made the grant or found it.
        """
        identifier(grant_id)
        identifier(actor_id)
        integer(amount, positive=True)
        if kind not in GRANT_KINDS:
            raise BillingError("invalid_grant")
        if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500 or reason != reason.strip():
            raise BillingError("grant_reason_required")
        self._payer_kind(payer_kind)
        identifier(payer_id)
        if kind == "welcome" and (payer_kind != "user" or abuse_key is None):
            raise BillingError("invalid_grant")
        if abuse_key is not None:
            identifier(abuse_key)
        facts = (kind, payer_kind, payer_id, unit.code, amount, reason, actor_id, abuse_key)
        with self._tx() as t:
            self._unit(t, unit)
            row = t.execute("SELECT * FROM billing_grants WHERE environment=? AND grant_id=?",
                            (self.environment, grant_id)).fetchone()
            if row:
                if tuple(row[k] for k in ("kind", "payer_kind", "payer_id", "unit", "amount", "reason",
                                          "actor_id", "abuse_key")) != facts:
                    raise BillingError("idempotency_conflict")
                return {**self._grant_row(row), "created": False}
            if payer_kind == "user":
                self._active_user(t, payer_id)
            else:
                self._active_org(t, payer_id)
            if kind == "welcome" and t.execute(
                    "SELECT 1 FROM billing_grants WHERE environment=? AND kind='welcome' AND (payer_id=? OR abuse_key=?)",
                    (self.environment, payer_id, abuse_key)).fetchone():
                raise BillingError("welcome_already_granted")
            wallet = self._payer_account(t, unit.code, payer_kind, payer_id)
            source = self._account(t, unit.code, "platform", "grant")
            key = _id()
            journal = self._post(t, voucher="grant:" + key, unit=unit.code, debit=source, credit=wallet,
                                 amount=amount, kind="grant", reference=key)
            t.execute("INSERT INTO billing_grants(id,environment,grant_id,kind,payer_kind,payer_id,account_id,unit,"
                      "amount,reason,actor_id,abuse_key,journal_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (key, self.environment, grant_id, kind, payer_kind, payer_id, wallet, unit.code, amount,
                       reason, actor_id, abuse_key, journal, self._now()))
            return {**self._grant_row(t.execute("SELECT * FROM billing_grants WHERE id=?", (key,)).fetchone()),
                    "created": True}

    @staticmethod
    def _grant_row(row) -> dict:
        return {k: row[k] for k in ("id", "grant_id", "kind", "payer_kind", "payer_id", "unit", "amount",
                                     "reason", "actor_id", "journal_id", "created_at")}

    def count_grants(self, *, kind: str, since: int) -> int:
        """How many grants of ``kind`` this environment made since ``since`` (abuse caps)."""
        if kind not in GRANT_KINDS:
            raise BillingError("invalid_grant")
        integer(since)
        row = self.store.one("SELECT COUNT(*) AS n FROM billing_grants WHERE environment=? AND kind=? AND created_at>=?",
                             (self.environment, kind, since))
        return row["n"]

    def has_welcome(self, *, payer_id: str, abuse_key: str | None = None) -> bool:
        row = self.store.one("SELECT 1 AS x FROM billing_grants WHERE environment=? AND kind='welcome' "
                             "AND (payer_id=? OR abuse_key=?)", (self.environment, payer_id, abuse_key))
        return row is not None

    def record_chargeback(self, notice: DisputeNotice) -> dict:
        """A dispute opened after the payment was credited: take back what is still there.

        The disputed share of the order's credit (``ceil(credit_units * disputed / paid)``) is
        moved from the funded wallet to the platform chargeback account, limited to what the
        wallet has available (booked minus active holds), so the wallet never goes negative; the
        rest is recorded as outstanding. The wallet is frozen in the same transaction: it cannot
        reserve new paid calls until an administrator lifts the freeze. Idempotent per dispute;
        the verifier must have checked the durable, signature-verified provider event.

        Bounds (review money-2, money-4): a disputed amount above the paid amount (the provider
        documents it can differ, e.g. by currency fluctuation) is capped at the paid amount, never
        refused; all disputes of one payment together take at most its credit, so a second dispute
        object for the same charge cannot reach credit another payment funded; an inquiry (no
        funds withdrawn) takes nothing and only freezes. A dispute that takes nothing is still
        recorded (``disputed_units`` 0) and still freezes. Refusals: ``payment_not_credited``
        (no credited payment with this id here) and ``dispute_payment_mismatch`` (merchant or
        currency differ from the credited payment: a different fact, never retried).
        """
        if not isinstance(notice, DisputeNotice) or notice.environment != self.environment:
            raise BillingError("payment_environment_mismatch")
        with self._tx() as t:
            if self.verify_dispute is None or self.verify_dispute(t, notice) is not True:
                raise BillingError("payment_evidence_untrusted")
            old = t.execute("SELECT * FROM billing_chargebacks WHERE environment=? AND provider=? AND dispute_id=?",
                            (self.environment, notice.provider, notice.dispute_id)).fetchone()
            if old:
                if (old["merchant_id"], old["amount_minor"], old["currency"], bool(old["inquiry"])) != (
                        notice.merchant_id, notice.amount_minor, notice.currency, notice.inquiry):
                    raise BillingError("payment_event_conflict")
                return self._chargeback_row(t, old)
            payment = t.execute("SELECT p.*,o.credit_units,o.amount_minor AS paid_minor,o.currency AS paid_currency "
                                "FROM billing_payments p JOIN billing_orders o ON o.id=p.order_key "
                                "WHERE p.environment=? AND p.provider=? AND p.payment_id=?",
                                (self.environment, notice.provider, notice.payment_id)).fetchone()
            if not payment:
                raise BillingError("payment_not_credited")
            if payment["merchant_id"] != notice.merchant_id or payment["paid_currency"] != notice.currency:
                raise BillingError("dispute_payment_mismatch")
            journal_row = t.execute("SELECT credit_account,unit FROM billing_journal WHERE id=?",
                                    (payment["journal_id"],)).fetchone()
            wallet, unit = journal_row["credit_account"], journal_row["unit"]
            share = -(-payment["credit_units"] * min(notice.amount_minor, payment["paid_minor"]) // payment["paid_minor"])
            earlier = t.execute("SELECT COALESCE(SUM(disputed_units),0) FROM billing_chargebacks WHERE payment_key=?",
                                (payment["id"],)).fetchone()[0]
            disputed = 0 if notice.inquiry else max(0, min(share, payment["credit_units"] - earlier))
            booked, held = self._balance(t, wallet)
            recovered = max(0, min(disputed, booked - held))
            key, freeze_id = _id(), _id()
            owner = t.execute("SELECT kind,owner FROM billing_accounts WHERE id=?", (wallet,)).fetchone()
            # Write-ahead, before any change below: a restored backup must not let this wallet pay
            # again (synwevo_core.journal "payer_frozen", replayed by the freeze's id). A failed
            # append aborts the chargeback; the provider's event stays in the inbox to retry.
            from .. import journal as security_journal
            security_journal.record(self.store, security_journal.PAYER_FREEZE_KIND, freeze_id=freeze_id,
                                    environment=self.environment, account_id=wallet,
                                    payer_kind="organization" if owner["kind"] == "org_wallet" else "user",
                                    payer_id=owner["owner"], chargeback_id=key)
            journal = None
            if recovered:
                sink = self._account(t, unit, "platform", "chargeback")
                journal = self._post(t, voucher="chargeback:" + key, unit=unit, debit=wallet, credit=sink,
                                     amount=recovered, kind="chargeback", reference=key)
            t.execute("INSERT INTO billing_chargebacks(id,environment,provider,merchant_id,dispute_id,event_id,payment_key,"
                      "account_id,amount_minor,currency,disputed_units,recovered_units,outstanding_units,journal_id,"
                      "evidence_ref,payload_sha256,created_at,inquiry) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (key, self.environment, notice.provider, notice.merchant_id, notice.dispute_id, notice.event_id,
                       payment["id"], wallet, notice.amount_minor, notice.currency, disputed, recovered,
                       disputed - recovered, journal, notice.evidence_ref, notice.payload_sha256, self._now(),
                       int(notice.inquiry)))
            t.execute("INSERT INTO billing_account_freezes(id,account_id,reason,chargeback_id,created_at) "
                      "VALUES (?,?,'chargeback',?,?)", (freeze_id, wallet, key, self._now()))
            return self._chargeback_row(t, t.execute("SELECT * FROM billing_chargebacks WHERE id=?", (key,)).fetchone())

    @staticmethod
    def _chargeback_row(t, row) -> dict:
        account = t.execute("SELECT kind,owner FROM billing_accounts WHERE id=?", (row["account_id"],)).fetchone()
        freeze = t.execute("SELECT id,lifted_at FROM billing_account_freezes WHERE chargeback_id=?", (row["id"],)).fetchone()
        return {"id": row["id"], "dispute_id": row["dispute_id"], "payer_kind": "organization" if account["kind"] == "org_wallet" else "user",
                "payer_id": account["owner"], "amount_minor": row["amount_minor"], "currency": row["currency"],
                "disputed_units": row["disputed_units"], "recovered_units": row["recovered_units"],
                "outstanding_units": row["outstanding_units"], "journal_id": row["journal_id"],
                "inquiry": bool(row["inquiry"]), "freeze_id": freeze["id"] if freeze else None,
                "frozen": bool(freeze) and freeze["lifted_at"] is None, "created_at": row["created_at"]}

    def lift_freeze(self, freeze_id: str, *, actor_id: str, reason: str) -> dict:
        """End a freeze (an administrator's audited decision, e.g. the dispute was won or the
        outstanding amount was settled outside the product). The chargeback stays recorded."""
        identifier(freeze_id)
        identifier(actor_id)
        if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
            raise BillingError("grant_reason_required")
        with self._tx() as t:
            row = t.execute("SELECT f.* FROM billing_account_freezes f JOIN billing_accounts a ON a.id=f.account_id "
                            "WHERE f.id=? AND a.environment=?", (freeze_id, self.environment)).fetchone()
            if not row:
                raise BillingError("freeze_not_found")
            if row["lifted_at"] is None:
                t.execute("UPDATE billing_account_freezes SET lifted_at=?,lifted_by=?,lift_reason=? WHERE id=?",
                          (self._now(), actor_id, reason.strip(), freeze_id))
            row = t.execute("SELECT * FROM billing_account_freezes WHERE id=?", (freeze_id,)).fetchone()
            return {k: row[k] for k in ("id", "account_id", "reason", "chargeback_id", "created_at",
                                         "lifted_at", "lifted_by", "lift_reason")}

    def wallet_frozen(self, *, payer_kind: str, payer_id: str, unit: Unit) -> bool:
        kind = self._payer_kind(payer_kind)
        with self.store.conn() as t:
            row = t.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? AND owner=? AND kind=?",
                            (self.environment, unit.code, payer_id, kind)).fetchone()
            return bool(row) and self._frozen(t, row[0])

    def open_run(self, context: ChargeContext, *, unit: Unit, budget: int, max_calls: int,
                 max_attempts: int, deadline: int) -> str:
        """Fix payer/authorization and total budget. Parent budget does not freeze funds."""
        if not isinstance(context, ChargeContext):
            raise BillingError("invalid_context")
        integer(budget)
        integer(max_calls, positive=True, maximum=10000)
        integer(max_attempts, positive=True, maximum=10000)
        integer(deadline)
        encoded = _json(asdict(context))
        with self._tx() as t:
            self._authorize(t, context, "open_run")
            self._unit(t, unit)
            row = t.execute("SELECT * FROM billing_runs WHERE environment=? AND namespace=? AND run_id=?",
                            (self.environment, self.namespace, context.run_id)).fetchone()
            expected = (encoded, unit.code, budget, max_calls, max_attempts, deadline)
            if row:
                if tuple(row[k] for k in ("context", "unit", "budget", "max_calls", "max_attempts", "deadline")) != expected:
                    raise BillingError("idempotency_conflict")
                return row["id"]
            if deadline <= self._now():
                raise BillingError("run_expired")
            key = _id()
            t.execute("INSERT INTO billing_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (key, self.environment, self.namespace, context.run_id, *expected, self._now()))
            return key

    def _run(self, t, run_id):
        row = t.execute("SELECT * FROM billing_runs WHERE environment=? AND namespace=? AND run_id=?",
                        (self.environment, self.namespace, identifier(run_id))).fetchone()
        if row is None:
            raise BillingError("run_not_found")
        return row

    def _call(self, t, call_key):
        row = t.execute("SELECT c.* FROM billing_calls c JOIN billing_runs r ON r.id=c.run_key "
                        "WHERE c.id=? AND r.environment=? AND r.namespace=?",
                        (identifier(call_key), self.environment, self.namespace)).fetchone()
        if row is None:
            raise BillingError("call_not_found")
        return row

    def _event(self, t, call_key, kind, reason, attempt_key=None):
        t.execute("INSERT INTO billing_events(call_key,attempt_key,kind,reason,created_at) VALUES (?,?,?,?,?)",
                  (call_key, attempt_key, kind, reason, self._now()))

    def _release(self, t, call, reason):
        if call["status"] != "reserved":
            return
        t.execute("UPDATE billing_calls SET status='released' WHERE id=?", (call["id"],))
        # Already dispatched attempts stay unknown/pending, available for late cost evidence.
        t.execute("UPDATE billing_attempts SET status='released' WHERE call_key=? AND status='prepared'", (call["id"],))
        self._event(t, call["id"], "released", reason)

    def _expire(self, t):
        rows = t.execute("SELECT c.* FROM billing_calls c JOIN billing_runs r ON r.id=c.run_key "
                         "WHERE r.environment=? AND r.namespace=? AND c.status='reserved' AND c.deadline<=?",
                         (self.environment, self.namespace, self._now())).fetchall()
        for row in rows:
            self._release(t, row, "deadline_final_no_later_charge")
        return len(rows)

    def expire_due(self) -> int:
        """Call from a durable recovery worker; also run before reads/reserves/settlement."""
        with self._tx() as t:
            return self._expire(t)

    def reserve_call(self, context: ChargeContext, *, logical_call_id: str, amount: int,
                     price_id: str, deadline: int) -> str:
        if not isinstance(context, ChargeContext):
            raise BillingError("invalid_context")
        identifier(logical_call_id)
        integer(amount)
        integer(deadline)
        with self._tx() as t:
            self._expire(t)
            self._authorize(t, context, "reserve")
            run = self._run(t, context.run_id)
            fixed = json.loads(run["context"])
            # Workspace is attribution per call, never a change of payer or run authority.
            if any(fixed.get(k) != v for k, v in asdict(context).items() if k != "workspace_id"):
                raise BillingError("run_context_conflict")
            price = self._price(t, price_id, "sale")
            if price.unit.code != run["unit"]:
                raise BillingError("unit_mismatch")
            encoded = _json(asdict(context))
            row = t.execute("SELECT * FROM billing_calls WHERE run_key=? AND logical_call_id=?",
                            (run["id"], logical_call_id)).fetchone()
            if row:
                if (row["context"], row["hold_units"], row["price_id"], row["deadline"]) != (encoded, amount, price_id, deadline):
                    raise BillingError("idempotency_conflict")
                return row["id"]
            now = self._now()
            if not now < deadline <= min(run["deadline"], now + self.max_hold_seconds):
                raise BillingError("invalid_hold_deadline")
            calls = t.execute("SELECT COUNT(*),COALESCE(SUM(CASE WHEN status='reserved' THEN hold_units ELSE 0 END),0) "
                              "FROM billing_calls WHERE run_key=?", (run["id"],)).fetchone()
            spent = t.execute("SELECT COALESCE(SUM(j.amount),0) FROM billing_settlements s "
                              "JOIN billing_journal j ON j.id=s.journal_id JOIN billing_calls c ON c.id=s.call_key "
                              "WHERE c.run_key=?", (run["id"],)).fetchone()[0]
            if calls[0] >= run["max_calls"] or spent + calls[1] + amount > run["budget"]:
                raise BillingError("run_budget_exceeded")
            account = self._context_account(t, run["unit"], context)
            if self._frozen(t, account):
                raise BillingError("wallet_frozen")
            booked, held = self._balance(t, account)
            if amount > booked - held:
                raise BillingError("insufficient_funds")
            organization = context.payer_kind == "organization"
            if organization:
                if self.org_payers is None:
                    raise BillingError("organization_billing_not_configured")
                org_policy = self.org_payers.check(t, account, context, amount=amount, at=now)
                payer_policy = None
            else:
                payer_policy = self.payer_budgets.check(t, account, amount=amount, at=now) if self.payer_budgets else None
            key = _id()
            t.execute("INSERT INTO billing_calls(id,run_key,logical_call_id,context,account_id,price_id,hold_units,deadline,status,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,'reserved',?)",
                      (key, run["id"], logical_call_id, encoded, account, price_id, amount, deadline, now))
            if organization:
                self.org_payers.record(t, key, org_policy)
            if payer_policy is not None:
                self.payer_budgets.record(t, key, payer_policy)
            self._event(t, key, "reserved", "authorized_upper_bound")
            return key

    def prepare_attempt(self, call_key: str, *, attempt_id: str, owner: str, cost_price_id: str,
                        requested_model: str, lease_seconds: int = 60, retry_reason: str | None = None) -> AttemptTicket:
        for value in (attempt_id, owner, cost_price_id, requested_model):
            identifier(value)
        integer(lease_seconds, positive=True, maximum=300)
        if retry_reason is not None:
            identifier(retry_reason)
        with self._tx() as t:
            self._expire(t)
            call = self._call(t, call_key)
            self._authorize(t, ChargeContext(**json.loads(call["context"])), "prepare")
            cost = self._price(t, cost_price_id, "cost")
            sale = self._price(t, call["price_id"], "sale")
            if cost.provider != sale.provider or cost.model != sale.model:
                raise BillingError("price_route_mismatch")
            old = t.execute("SELECT * FROM billing_attempts WHERE call_key=? AND attempt_id=?", (call_key, attempt_id)).fetchone()
            if old:
                if (old["owner"], old["cost_price_id"], old["requested_model"], old["retry_reason"]) != (owner, cost_price_id, requested_model, retry_reason):
                    raise BillingError("idempotency_conflict")
                return AttemptTicket(old["id"], old["owner"], old["fence"])
            if call["status"] != "reserved" or call["cancel_requested"]:
                raise BillingError("call_not_dispatchable")
            attempts = t.execute("SELECT * FROM billing_attempts WHERE call_key=? ORDER BY rowid", (call_key,)).fetchall()
            if attempts:
                # Only a known, metered infrastructure failure can be retried. Lease expiry or
                # a missing receipt never authorizes a second network send.
                prior = t.execute("SELECT outcome FROM billing_usage WHERE attempt_key=?", (attempts[-1]["id"],)).fetchone()
                if not retry_reason or any(a["status"] != "settled" for a in attempts) or not prior or prior[0] != "platform_fault":
                    raise BillingError("retry_requires_known_platform_fault")
            elif retry_reason is not None:
                raise BillingError("unexpected_retry_reason")
            run = t.execute("SELECT * FROM billing_runs WHERE id=?", (call["run_key"],)).fetchone()
            count = t.execute("SELECT COUNT(*) FROM billing_attempts a JOIN billing_calls c ON c.id=a.call_key "
                              "WHERE c.run_key=?", (run["id"],)).fetchone()[0]
            if count >= run["max_attempts"]:
                raise BillingError("attempt_budget_exceeded")
            key, fence = _id(), _id()
            t.execute("INSERT INTO billing_attempts(id,call_key,attempt_id,owner,fence,lease_until,environment,provider,"
                      "requested_model,cost_price_id,retry_reason,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'prepared',?)",
                      (key, call_key, attempt_id, owner, fence, min(self._now() + lease_seconds, call["deadline"]),
                       self.environment, cost.provider, requested_model, cost_price_id, retry_reason, self._now()))
            self._event(t, call_key, "prepared", "no_network_yet", key)
            return AttemptTicket(key, owner, fence)

    def _attempt(self, t, key):
        row = t.execute("SELECT * FROM billing_attempts WHERE id=?", (identifier(key),)).fetchone()
        if row is None:
            raise BillingError("attempt_not_found")
        self._call(t, row["call_key"])
        return row

    def claim_prepared(self, attempt_key: str, *, owner: str, lease_seconds: int = 60) -> AttemptTicket:
        """Recover only a provably unsent, expired preparation; rotate the dispatch fence."""
        identifier(owner)
        integer(lease_seconds, positive=True, maximum=300)
        with self._tx() as t:
            self._expire(t)
            row = self._attempt(t, attempt_key)
            call = self._call(t, row["call_key"])
            self._authorize(t, ChargeContext(**json.loads(call["context"])), "claim")
            if (row["status"] != "prepared" or row["lease_until"] > self._now()
                    or call["status"] != "reserved" or call["cancel_requested"]):
                raise BillingError("attempt_not_claimable")
            fence = _id()
            t.execute("UPDATE billing_attempts SET owner=?,fence=?,lease_until=? WHERE id=?",
                      (owner, fence, min(self._now() + lease_seconds, call["deadline"]), attempt_key))
            self._event(t, call["id"], "claimed", "expired_preparation_only", attempt_key)
            return AttemptTicket(attempt_key, owner, fence)

    def dispatch(self, ticket: AttemptTicket, *, block_unresolved_run: bool = False,
                 admission=None, send_window: int | None = None) -> None:
        """Commit the one-shot send boundary; a replay is REFUSED, never permission to resend.

        Success means 'may have been sent' even if the process crashes before network I/O.
        There is deliberately no automatic recovery send for this ambiguous window.
        A trusted local admission callback may reserve provider-account capacity/cost in
        this same writer transaction. It must not perform network I/O. Its writes and
        the dispatch fence either commit together with FULL durability or both roll back.

        ``send_window`` is the dispatcher's bound, in seconds, on how long the send may take
        until its usage is settled (transport timeout plus settlement margin). A network
        dispatcher MUST pass it: the hold deadline is final (usage after it is the platform's
        cost, never the payer's), so a send that could outlive its hold is refused here,
        under the writer lock, before anything is sent (``run_time_too_short``).
        """
        if admission is not None and not callable(admission):
            raise BillingError("invalid_dispatch_admission")
        if send_window is not None:
            integer(send_window, positive=True, maximum=86400)
        with self._tx() as t:
            self._expire(t)
            attempt = self._attempt(t, ticket.attempt_key)
            call = self._call(t, attempt["call_key"])
            self._authorize(t, ChargeContext(**json.loads(call["context"])), "dispatch")
            if (attempt["owner"] != ticket.owner or attempt["fence"] != ticket.fence
                    or attempt["status"] != "prepared" or attempt["lease_until"] <= self._now()
                    or call["status"] != "reserved" or call["cancel_requested"]):
                raise BillingError("dispatch_fenced")
            if send_window is not None and call["deadline"] - self._now() < send_window:
                raise BillingError("run_time_too_short")
            context = ChargeContext(**json.loads(call["context"]))
            if self._frozen(t, call["account_id"]):
                raise BillingError("wallet_frozen")
            if context.payer_kind == "organization":
                if self.org_payers is None:
                    raise BillingError("organization_billing_not_configured")
                policy = self.org_payers.check(t, call["account_id"], context, amount=0, at=self._now())
                self._event(t, call["id"], "org_payer_checked", "grant:" + str(policy["grant_revision"]), ticket.attempt_key)
            elif self.payer_budgets is not None:
                policy = self.payer_budgets.check(t, call["account_id"], amount=0, at=self._now())
                self._event(t, call["id"], "payer_budget_checked", "revision:" + str(policy["revision"]), ticket.attempt_key)
            # Sequential model gateways must check under this same writer lock. A read
            # before reservation cannot exclude a competing worker crossing the send fence.
            if block_unresolved_run and t.execute(
                    "SELECT 1 FROM billing_attempts a JOIN billing_calls c ON c.id=a.call_key "
                    "WHERE c.run_key=? AND a.id<>? AND c.status='reserved' "
                    "AND a.status IN ('dispatch_unknown','usage_pending') LIMIT 1",
                    (call["run_key"], attempt["id"])).fetchone():
                raise BillingError("usage_pending")
            if admission is not None:
                admission(t, ticket)
            # One instant decides both the stored dispatch time and the pinned tariff band.
            dispatched_at = self._now()
            band, schedule = pin_band(t, self.environment, attempt["cost_price_id"], call["price_id"], dispatched_at)
            t.execute("UPDATE billing_attempts SET status='dispatch_unknown',dispatched_at=?,band=?,"
                      "band_schedule_revision=? WHERE id=?", (dispatched_at, band, schedule, ticket.attempt_key))
            self._event(t, call["id"], "dispatch_unknown", "committed_before_network", ticket.attempt_key)

    def mark_usage_pending(self, ticket: AttemptTicket, *, request_id: str | None = None,
                           reason: str = "awaiting_final_usage") -> None:
        identifier(reason)
        if request_id is not None:
            identifier(request_id)
        with self._tx() as t:
            attempt = self._attempt(t, ticket.attempt_key)
            if attempt["owner"] != ticket.owner or attempt["fence"] != ticket.fence:
                raise BillingError("dispatch_fenced")
            if attempt["status"] not in ("dispatch_unknown", "usage_pending"):
                raise BillingError("attempt_not_pending")
            if attempt["request_id"] and request_id and attempt["request_id"] != request_id:
                raise BillingError("provider_request_conflict")
            if attempt["status"] == "usage_pending" and (request_id is None or request_id == attempt["request_id"]):
                return
            t.execute("UPDATE billing_attempts SET status='usage_pending',request_id=COALESCE(request_id,?),"
                      "completed_at=COALESCE(completed_at,?) WHERE id=?", (request_id, self._now(), ticket.attempt_key))
            self._event(t, attempt["call_key"], "usage_pending", reason, ticket.attempt_key)

    def cancel_call(self, call_key: str) -> str:
        """Record cancellation; unknown dispatched consumption remains held until deadline."""
        with self._tx() as t:
            self._expire(t)
            call = self._call(t, call_key)
            if call["status"] != "reserved":
                return call["status"]
            if not call["cancel_requested"]:
                t.execute("UPDATE billing_calls SET cancel_requested=1 WHERE id=?", (call_key,))
                self._event(t, call_key, "cancel_requested", "stop_future_dispatch")
            unknown = t.execute("SELECT 1 FROM billing_attempts WHERE call_key=? AND status IN ('dispatch_unknown','usage_pending')",
                                (call_key,)).fetchone()
            if not unknown:
                self._release(t, call, "cancelled_no_unknown_consumption")
                return "released"
            return "reserved"

    def settle_trusted_usage(self, attempt_key: str, evidence: UsageEvidence, *, outcome: str = "completed") -> Settlement:
        """Adapter-only final usage boundary. Persist usage, cost, sale and release atomically.

        `platform_fault` records cost and keeps the original hold for a bounded retry; it
        never sells that attempt to the user. No authorization recheck blocks recording facts
        after revocation. Missing/estimated usage must not enter this method. An adapter may
        record zero usage only for a documented no-consumption answer (the DeepSeek gateway's
        HTTP error answers, docs/deepseek-provider.md "Error answers"), never as an estimate.
        """
        if not isinstance(evidence, UsageEvidence) or outcome not in ("completed", "platform_fault"):
            raise BillingError("invalid_usage_evidence")
        facts = _json(asdict(evidence))
        with self._tx() as t:
            self._expire(t)
            attempt = self._attempt(t, attempt_key)
            call = self._call(t, attempt["call_key"])
            old = t.execute("SELECT * FROM billing_usage WHERE attempt_key=?", (attempt_key,)).fetchone()
            if old:
                if old["facts"] != facts or old["outcome"] != outcome:
                    raise BillingError("usage_conflict")
                return self._settlement_result(t, call["id"], old)
            if attempt["status"] not in ("dispatch_unknown", "usage_pending"):
                raise BillingError("usage_without_dispatch")
            if evidence.provider != attempt["provider"] or (attempt["request_id"] and attempt["request_id"] != evidence.request_id):
                raise BillingError("provider_request_conflict")
            cost_price = self._price(t, attempt["cost_price_id"], "cost")
            sale = self._price(t, call["price_id"], "sale")
            if evidence.effective_model != cost_price.model:
                raise BillingError("unconfirmed_price_route")
            # The band pinned at dispatch prices both sides; never the settlement instant.
            cost = cost_price.exact(evidence.usage, attempt["band"])
            exact = sale.exact(evidence.usage, attempt["band"])
            late = call["status"] != "reserved"
            before, after, charged, absorbed = Fraction(0), Fraction(0), 0, exact
            if not late and outcome == "completed":
                remainder = t.execute("SELECT numerator,denominator FROM billing_remainders WHERE account_id=? AND price_id=?",
                                      (call["account_id"], call["price_id"])).fetchone()
                if remainder:
                    before = Fraction(int(remainder[0]), int(remainder[1]))
                total = before + exact
                whole = total.numerator // total.denominator
                charged = min(whole, call["hold_units"])
                after = total - whole
                absorbed = Fraction(0)
                if total > call["hold_units"]:
                    # Discard fractional overage too: carrying it would charge a future call
                    # for consumption outside this call's authorized bound.
                    after = Fraction(0)
                    absorbed = total - charged
                t.execute("INSERT INTO billing_remainders VALUES (?,?,?,?) ON CONFLICT(account_id,price_id) "
                          "DO UPDATE SET numerator=excluded.numerator,denominator=excluded.denominator",
                          (call["account_id"], call["price_id"], str(after.numerator), str(after.denominator)))
            t.execute("UPDATE billing_attempts SET request_id=?,status='settled',completed_at=COALESCE(completed_at,?) "
                      "WHERE id=?", (evidence.request_id, evidence.metered_at, attempt_key))
            t.execute("INSERT INTO billing_usage VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (attempt_key, facts, outcome, str(cost.numerator), str(cost.denominator), cost_price.unit.code,
                       charged, str(absorbed.numerator), str(absorbed.denominator), int(late), self._now()))
            if not late and outcome == "completed":
                destination = self._account(t, sale.unit.code, "platform", "revenue")
                journal = self._post(t, voucher="sale:" + call["id"], unit=sale.unit.code,
                                     debit=call["account_id"], credit=destination, amount=charged,
                                     kind="sale", reference=call["id"])
                t.execute("INSERT INTO billing_settlements VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (call["id"], attempt_key, journal, str(exact.numerator), str(exact.denominator),
                           str(before.numerator), str(before.denominator), str(after.numerator), str(after.denominator), self._now()))
                t.execute("UPDATE billing_calls SET status='settled' WHERE id=?", (call["id"],))
                self._event(t, call["id"], "settled", "trusted_usage", attempt_key)
            else:
                self._event(t, call["id"], "cost_only", "late_usage" if late else "platform_fault", attempt_key)
                if not late and call["cancel_requested"]:
                    self._release(t, call, "cancelled_known_platform_fault")
            usage = t.execute("SELECT * FROM billing_usage WHERE attempt_key=?", (attempt_key,)).fetchone()
            return self._settlement_result(t, call["id"], usage)

    @staticmethod
    def _settlement_result(t, call_key, usage):
        scale = t.execute("SELECT scale FROM billing_units WHERE code=?", (usage["cost_unit"],)).fetchone()[0]
        return Settlement(call_key, usage["attempt_key"], usage["charge"],
                          Fraction(int(usage["absorbed_n"]), int(usage["absorbed_d"])),
                          Fraction(int(usage["cost_n"]), int(usage["cost_d"])),
                          Unit(usage["cost_unit"], scale), bool(usage["late"]), usage["outcome"])

    def balance_for_user(self, *, requester_user_id: str, payer_user_id: str, unit: Unit) -> Balance:
        """Personal projection. A platform/workspace admin ID does not grant wallet access."""
        if requester_user_id != payer_user_id:
            raise BillingError("wallet_forbidden")
        with self._tx() as t:
            self._active_user(t, requester_user_id)
            self._expire(t)
            row = t.execute("SELECT a.id,u.scale FROM billing_accounts a JOIN billing_units u ON u.code=a.unit "
                            "WHERE a.environment=? AND a.unit=? AND a.owner=? AND a.kind='wallet'",
                            (self.environment, unit.code, payer_user_id)).fetchone()
            if not row:
                return Balance(unit, 0, 0)
            if row["scale"] != unit.scale:
                raise BillingError("unit_scale_conflict")
            booked, held = self._balance(t, row["id"])
            return Balance(unit, booked, held)

    def overview_for_user(self, *, requester_user_id: str, payer_user_id: str, units) -> dict:
        """One personal money snapshot, including settled sales across namespaces.

        Units are trusted host configuration, not caller-selected currency/precision. The
        indexed aggregate stays in SQLite; the HTTP request never pages through an unbounded
        history. Lifetime sales cannot exceed the ledger's bounded revenue account balance.
        """
        if requester_user_id != payer_user_id:
            raise BillingError("wallet_forbidden")
        if (not isinstance(units, (tuple, list)) or not 1 <= len(units) <= 16
                or any(not isinstance(unit, Unit) for unit in units)
                or len({unit.code for unit in units}) != len(units)):
            raise BillingError("invalid_unit")
        with self._tx() as t:
            self._active_user(t, requester_user_id)
            self._expire(t)
            through = 0
            balances = []
            for unit in units:
                known = t.execute("SELECT scale FROM billing_units WHERE code=?", (unit.code,)).fetchone()
                if not known or known[0] != unit.scale:
                    raise BillingError("unit_scale_conflict")
                account = t.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? "
                                    "AND owner=? AND kind='wallet'",
                                    (self.environment, unit.code, payer_user_id)).fetchone()
                booked = held = used = 0
                frozen = False
                if account:
                    booked, held = self._balance(t, account[0])
                    frozen = self._frozen(t, account[0])
                    latest = t.execute("SELECT COALESCE(MAX(rowid),0) FROM billing_journal WHERE environment=? "
                                       "AND (debit_account=? OR credit_account=?)",
                                       (self.environment, account[0], account[0])).fetchone()[0]
                    through = max(through, latest)
                    used = t.execute("SELECT COALESCE(SUM(amount),0) FROM billing_journal "
                                     "WHERE environment=? AND debit_account=? AND kind='sale'",
                                     (self.environment, account[0])).fetchone()[0]
                balances.append({"unit": unit.code, "scale": unit.scale, "booked": booked,
                                 "used": used, "held": held, "available": booked-held, "frozen": frozen})
            return {"balances": balances, "used_through_seq": through}

    def overview_for_payer(self, *, payer_kind: str, payer_id: str, units) -> dict:
        """The same snapshot as overview_for_user for any payer. No requester check: the caller
        has decided (current organization permission) that this reader may see this wallet."""
        kind = self._payer_kind(payer_kind)
        identifier(payer_id)
        if (not isinstance(units, (tuple, list)) or not 1 <= len(units) <= 16
                or any(not isinstance(unit, Unit) for unit in units)):
            raise BillingError("invalid_unit")
        with self._tx() as t:
            self._expire(t)
            balances, through = [], 0
            for unit in units:
                known = t.execute("SELECT scale FROM billing_units WHERE code=?", (unit.code,)).fetchone()
                if not known or known[0] != unit.scale:
                    raise BillingError("unit_scale_conflict")
                account = t.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? AND owner=? AND kind=?",
                                    (self.environment, unit.code, payer_id, kind)).fetchone()
                booked = held = used = 0
                frozen = False
                if account:
                    booked, held = self._balance(t, account[0])
                    frozen = self._frozen(t, account[0])
                    through = max(through, t.execute("SELECT COALESCE(MAX(rowid),0) FROM billing_journal WHERE environment=? "
                                                     "AND (debit_account=? OR credit_account=?)",
                                                     (self.environment, account[0], account[0])).fetchone()[0])
                    used = t.execute("SELECT COALESCE(SUM(amount),0) FROM billing_journal WHERE environment=? "
                                     "AND debit_account=? AND kind='sale'", (self.environment, account[0])).fetchone()[0]
                balances.append({"unit": unit.code, "scale": unit.scale, "booked": booked, "used": used,
                                 "held": held, "available": booked - held, "frozen": frozen})
            return {"balances": balances, "used_through_seq": through}

    def statement_for_payer(self, *, payer_kind: str, payer_id: str, after_seq: int = 0, limit: int = 100) -> list[dict]:
        """statement_for_user for any payer; the caller decided this reader may see it."""
        kind = self._payer_kind(payer_kind)
        identifier(payer_id)
        integer(after_seq)
        integer(limit, positive=True, maximum=500)
        with self._tx() as t:
            rows = t.execute("SELECT j.rowid AS seq,j.id,j.kind,j.unit,u.scale,j.created_at,"
                             "CASE WHEN a.id=j.credit_account THEN j.amount ELSE -j.amount END AS delta "
                             "FROM billing_journal j JOIN billing_accounts a ON a.id IN (j.debit_account,j.credit_account) "
                             "JOIN billing_units u ON u.code=j.unit WHERE a.kind=? AND a.owner=? "
                             "AND j.environment=? AND j.rowid>? ORDER BY j.rowid LIMIT ?",
                             (kind, payer_id, self.environment, after_seq, limit)).fetchall()
            return [dict(r) for r in rows]

    def statement_for_user(self, *, requester_user_id: str, payer_user_id: str,
                           after_seq: int = 0, limit: int = 100) -> list[dict]:
        """Allowlisted personal journal projection; no cost/evidence/delegation/workspace leak.

        Stable cursor is journal rowid (facts cannot be deleted); page across all namespaces of
        this payer, in this environment. Unit and scale accompany every amount.
        """
        if requester_user_id != payer_user_id:
            raise BillingError("wallet_forbidden")
        integer(after_seq)
        integer(limit, positive=True, maximum=500)
        with self._tx() as t:
            self._active_user(t, requester_user_id)
            rows = t.execute("SELECT j.rowid AS seq,j.id,j.kind,j.unit,u.scale,j.created_at,"
                             "CASE WHEN a.id=j.credit_account THEN j.amount ELSE -j.amount END AS delta "
                             "FROM billing_journal j JOIN billing_accounts a ON a.id IN (j.debit_account,j.credit_account) "
                             "JOIN billing_units u ON u.code=j.unit WHERE a.kind='wallet' AND a.owner=? "
                             "AND j.environment=? AND j.rowid>? ORDER BY j.rowid LIMIT ?",
                             (payer_user_id, self.environment, after_seq, limit)).fetchall()
            return [dict(r) for r in rows]

    def call_status(self, call_key: str) -> dict:
        """Internal recovery projection, not an authorization-bearing public route."""
        with self._tx() as t:
            self._expire(t)
            call = self._call(t, call_key)
            return {k: call[k] for k in ("id", "status", "cancel_requested", "hold_units", "deadline")}

    def audit_balances(self, *, rebuild: bool = False) -> list[dict]:
        """Internal maintenance: compare/rebuild caches from immutable balanced facts.

        Rebuild only derived caches under the same write lock, never a supplied target balance.
        Returns the pre-repair differences. Journal facts and active holds stay unchanged.
        """
        if type(rebuild) is not bool:
            raise BillingError("invalid_rebuild")
        with self._tx() as t:
            accounts = t.execute("SELECT id,kind FROM billing_accounts WHERE environment=?", (self.environment,)).fetchall()
            differences = []
            for account in accounts:
                expected = t.execute("SELECT COALESCE(SUM(delta),0) FROM billing_entries WHERE account_id=?", (account["id"],)).fetchone()[0]
                cached = t.execute("SELECT amount FROM billing_balances WHERE account_id=?", (account["id"],)).fetchone()
                actual = cached[0] if cached else None
                if actual != expected:
                    differences.append({"account_id": account["id"], "cached": actual, "journal": expected})
                    if rebuild:
                        t.execute("INSERT INTO billing_balances VALUES (?,?) ON CONFLICT(account_id) DO UPDATE SET amount=excluded.amount",
                                  (account["id"], expected))
                if account["kind"] in WALLET_KINDS:
                    held = t.execute("SELECT COALESCE(SUM(hold_units),0) FROM billing_calls WHERE account_id=? AND status='reserved'",
                                     (account["id"],)).fetchone()[0]
                    if expected < held:
                        raise BillingError("journal_hold_invariant_broken")
            # Fractional carries are also projections; recover them from immutable settlements
            # in journal order, never lose accrued micro-usage on a cache rebuild.
            rows = t.execute("SELECT c.account_id,c.price_id,s.remainder_after_n,s.remainder_after_d "
                             "FROM billing_settlements s JOIN billing_calls c ON c.id=s.call_key "
                             "JOIN billing_journal j ON j.id=s.journal_id WHERE j.environment=? ORDER BY j.rowid",
                             (self.environment,)).fetchall()
            expected_remainders = {(r["account_id"], r["price_id"]): (r["remainder_after_n"], r["remainder_after_d"]) for r in rows}
            cached_remainders = {(r["account_id"], r["price_id"]): (r["numerator"], r["denominator"]) for r in t.execute(
                "SELECT r.* FROM billing_remainders r JOIN billing_accounts a ON a.id=r.account_id WHERE a.environment=?", (self.environment,))}
            for key in expected_remainders.keys() | cached_remainders.keys():
                expected = expected_remainders.get(key, ("0", "1"))
                actual = cached_remainders.get(key)
                if actual != expected:
                    differences.append({"account_id": key[0], "price_id": key[1], "cached": actual, "journal": expected})
                    if rebuild:
                        t.execute("INSERT INTO billing_remainders VALUES (?,?,?,?) ON CONFLICT(account_id,price_id) "
                                  "DO UPDATE SET numerator=excluded.numerator,denominator=excluded.denominator", (*key, *expected))
            return differences
