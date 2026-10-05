"""Additive money tables. Applied only by Store's serial migration owner.

No balances, currencies, price versions, payment facts or charging defaults are seeded.
Rollback must retain these immutable financial facts; old code must not dispatch paid work.

Org-payments (2026-10-05) widens two CHECKs, which SQLite can only do by rebuilding the table
(``rebuild`` below, through ``synwevo_core.schema_widen``; rows are copied unchanged):

* account kinds gain ``org_wallet`` (an organization's wallet, owner = organization id),
  ``grant`` (the platform source of audited grants) and ``chargeback`` (where a disputed
  payment's recovered units go);
* journal kinds gain ``grant`` and ``chargeback``.

Dispute bounds (review money-4, 2026-10-05) widen one more: ``billing_chargebacks.disputed_units``
may be 0 (an inquiry, or a payment whose earlier disputes already took its whole credit back),
and the added ``inquiry`` column says which rows were inquiries (freeze only).

The wallet-never-negative trigger covers organization wallets too. Rollback: older code reads
the rebuilt tables as before; it does not know the new kinds, so its wallet page refuses a
statement containing them instead of mislabelling it (its response model is a closed list).
"""

ACCOUNT_KINDS = ("wallet", "org_wallet", "paid", "test_payment", "revenue", "grant", "chargeback")
JOURNAL_KINDS = ("paid", "test_payment", "sale", "grant", "chargeback")
WALLET_KINDS = ("wallet", "org_wallet")


def _sql_list(values) -> str:
    return ",".join(f"'{value}'" for value in values)


#: The exact clauses older releases created, and what replaces them (schema_widen.widen_checks).
WIDENED = {
    "billing_accounts": (("CHECK(kind IN ('wallet','paid','test_payment','revenue'))",
                          f"CHECK(kind IN ({_sql_list(ACCOUNT_KINDS)}))"),),
    "billing_journal": (("CHECK(kind IN ('paid','test_payment','sale'))",
                         f"CHECK(kind IN ({_sql_list(JOURNAL_KINDS)}))"),),
    # A dispute may take nothing (an inquiry, or a payment its earlier disputes already took
    # back: review money-4), yet it is still recorded and freezes the wallet.
    "billing_chargebacks": (("CHECK(typeof(disputed_units)='integer' AND disputed_units>0)",
                             "CHECK(typeof(disputed_units)='integer' AND disputed_units>=0)"),),
}

JOURNAL_APPLY = """CREATE TRIGGER IF NOT EXISTS billing_journal_apply AFTER INSERT ON billing_journal BEGIN
    SELECT CASE WHEN (SELECT COUNT(*) FROM billing_balances WHERE account_id IN
        (NEW.debit_account,NEW.credit_account))<>2 THEN RAISE(ABORT,'balance_cache_missing') END;
    UPDATE billing_balances SET amount=amount-NEW.amount WHERE account_id=NEW.debit_account;
    UPDATE billing_balances SET amount=amount+NEW.amount WHERE account_id=NEW.credit_account;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM billing_balances b JOIN billing_accounts a ON a.id=b.account_id
        WHERE a.kind IN ('wallet','org_wallet') AND a.id=NEW.debit_account AND b.amount<0)
        THEN RAISE(ABORT,'wallet_negative') END;
END;"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_units (
    code TEXT PRIMARY KEY, scale INTEGER NOT NULL CHECK(typeof(scale)='integer' AND scale BETWEEN 0 AND 18)
);
CREATE TABLE IF NOT EXISTS billing_prices (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    price_id TEXT NOT NULL, snapshot TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,price_id)
);
CREATE TABLE IF NOT EXISTS billing_accounts (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    unit TEXT NOT NULL REFERENCES billing_units(code), owner TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN (""" + _sql_list(ACCOUNT_KINDS) + """)),
    UNIQUE(environment,unit,kind,owner), UNIQUE(id,environment,unit)
);
CREATE TABLE IF NOT EXISTS billing_balances (
    account_id TEXT PRIMARY KEY REFERENCES billing_accounts(id),
    amount INTEGER NOT NULL CHECK(typeof(amount)='integer')
);
CREATE TABLE IF NOT EXISTS billing_journal (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL, namespace TEXT NOT NULL,
    voucher TEXT NOT NULL, unit TEXT NOT NULL,
    debit_account TEXT NOT NULL, credit_account TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>=0),
    kind TEXT NOT NULL CHECK(kind IN (""" + _sql_list(JOURNAL_KINDS) + """)),
    reference TEXT NOT NULL, created_at INTEGER NOT NULL,
    CHECK(debit_account<>credit_account), UNIQUE(environment,namespace,voucher),
    FOREIGN KEY(debit_account,environment,unit) REFERENCES billing_accounts(id,environment,unit),
    FOREIGN KEY(credit_account,environment,unit) REFERENCES billing_accounts(id,environment,unit)
);
CREATE VIEW IF NOT EXISTS billing_entries AS
    SELECT id AS journal_id, environment,unit,debit_account AS account_id,-amount AS delta FROM billing_journal
    UNION ALL
    SELECT id,environment,unit,credit_account,amount FROM billing_journal;
CREATE INDEX IF NOT EXISTS billing_journal_personal_debits ON billing_journal(environment,debit_account,kind);
CREATE INDEX IF NOT EXISTS billing_journal_personal_credits ON billing_journal(environment,credit_account);
""" + JOURNAL_APPLY + """
CREATE TABLE IF NOT EXISTS billing_orders (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL, namespace TEXT NOT NULL, order_id TEXT NOT NULL,
    payer_user_id TEXT NOT NULL REFERENCES users(id), provider TEXT NOT NULL, merchant_id TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor>0),
    currency TEXT NOT NULL, unit TEXT NOT NULL REFERENCES billing_units(code),
    credit_units INTEGER NOT NULL CHECK(typeof(credit_units)='integer' AND credit_units>0),
    exchange_version TEXT NOT NULL, created_at INTEGER NOT NULL,
    UNIQUE(environment,namespace,order_id)
);
CREATE TABLE IF NOT EXISTS billing_payments (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL, provider TEXT NOT NULL, merchant_id TEXT NOT NULL,
    payment_id TEXT NOT NULL, order_key TEXT NOT NULL UNIQUE REFERENCES billing_orders(id),
    journal_id TEXT NOT NULL UNIQUE REFERENCES billing_journal(id), facts TEXT NOT NULL,
    UNIQUE(environment,provider,payment_id)
);
CREATE TABLE IF NOT EXISTS billing_payment_events (
    environment TEXT NOT NULL, provider TEXT NOT NULL, merchant_id TEXT NOT NULL, event_id TEXT NOT NULL,
    payment_key TEXT NOT NULL REFERENCES billing_payments(id), evidence_ref TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,provider,merchant_id,event_id)
);
CREATE TABLE IF NOT EXISTS billing_runs (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL, namespace TEXT NOT NULL, run_id TEXT NOT NULL,
    context TEXT NOT NULL, unit TEXT NOT NULL REFERENCES billing_units(code),
    budget INTEGER NOT NULL CHECK(typeof(budget)='integer' AND budget>=0),
    max_calls INTEGER NOT NULL, max_attempts INTEGER NOT NULL, deadline INTEGER NOT NULL,
    created_at INTEGER NOT NULL, UNIQUE(environment,namespace,run_id)
);
CREATE TABLE IF NOT EXISTS billing_calls (
    id TEXT PRIMARY KEY, run_key TEXT NOT NULL REFERENCES billing_runs(id), logical_call_id TEXT NOT NULL,
    context TEXT NOT NULL, account_id TEXT NOT NULL REFERENCES billing_accounts(id),
    price_id TEXT NOT NULL, hold_units INTEGER NOT NULL CHECK(typeof(hold_units)='integer' AND hold_units>=0),
    deadline INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('reserved','settled','released')),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
    created_at INTEGER NOT NULL, UNIQUE(run_key,logical_call_id)
);
CREATE INDEX IF NOT EXISTS billing_calls_holds ON billing_calls(account_id,status);
CREATE INDEX IF NOT EXISTS billing_calls_expiry ON billing_calls(status,deadline);
CREATE TABLE IF NOT EXISTS billing_attempts (
    id TEXT PRIMARY KEY, call_key TEXT NOT NULL REFERENCES billing_calls(id), attempt_id TEXT NOT NULL,
    owner TEXT NOT NULL, fence TEXT NOT NULL UNIQUE, lease_until INTEGER NOT NULL,
    environment TEXT NOT NULL, provider TEXT NOT NULL, requested_model TEXT NOT NULL,
    cost_price_id TEXT NOT NULL, retry_reason TEXT,
    status TEXT NOT NULL CHECK(status IN ('prepared','dispatch_unknown','usage_pending','settled','released')),
    request_id TEXT, created_at INTEGER NOT NULL, dispatched_at INTEGER,
    UNIQUE(call_key,attempt_id), UNIQUE(environment,provider,request_id)
);
CREATE TABLE IF NOT EXISTS billing_usage (
    attempt_key TEXT PRIMARY KEY REFERENCES billing_attempts(id), facts TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('completed','platform_fault')),
    cost_n TEXT NOT NULL, cost_d TEXT NOT NULL, cost_unit TEXT NOT NULL REFERENCES billing_units(code),
    charge INTEGER NOT NULL CHECK(typeof(charge)='integer' AND charge>=0),
    absorbed_n TEXT NOT NULL, absorbed_d TEXT NOT NULL, late INTEGER NOT NULL CHECK(late IN (0,1)),
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_settlements (
    call_key TEXT PRIMARY KEY REFERENCES billing_calls(id),
    attempt_key TEXT NOT NULL UNIQUE REFERENCES billing_usage(attempt_key),
    journal_id TEXT NOT NULL UNIQUE REFERENCES billing_journal(id),
    exact_n TEXT NOT NULL, exact_d TEXT NOT NULL,
    remainder_before_n TEXT NOT NULL, remainder_before_d TEXT NOT NULL,
    remainder_after_n TEXT NOT NULL, remainder_after_d TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_remainders (
    account_id TEXT NOT NULL REFERENCES billing_accounts(id), price_id TEXT NOT NULL,
    numerator TEXT NOT NULL, denominator TEXT NOT NULL, PRIMARY KEY(account_id,price_id)
);
CREATE TABLE IF NOT EXISTS billing_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, call_key TEXT NOT NULL REFERENCES billing_calls(id),
    attempt_key TEXT REFERENCES billing_attempts(id), kind TEXT NOT NULL, reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
-- Audited grants: a welcome allowance (system actor, once per account and per address) or an
-- administrator's grant (named actor, mandatory reason). Never a payment; never reversible by edit.
CREATE TABLE IF NOT EXISTS billing_grants (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    grant_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('welcome','admin')),
    payer_kind TEXT NOT NULL CHECK(payer_kind IN ('user','organization')), payer_id TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES billing_accounts(id), unit TEXT NOT NULL REFERENCES billing_units(code),
    amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>0),
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 8 AND 500),
    actor_id TEXT NOT NULL, abuse_key TEXT,
    journal_id TEXT NOT NULL UNIQUE REFERENCES billing_journal(id), created_at INTEGER NOT NULL,
    UNIQUE(environment,grant_id), CHECK(kind<>'welcome' OR (payer_kind='user' AND abuse_key IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS billing_grants_one_welcome ON billing_grants(environment,payer_id) WHERE kind='welcome';
CREATE UNIQUE INDEX IF NOT EXISTS billing_grants_one_welcome_address ON billing_grants(environment,abuse_key) WHERE kind='welcome';
CREATE INDEX IF NOT EXISTS billing_grants_time ON billing_grants(environment,kind,created_at);
-- A dispute (chargeback) after the funds were credited: recover what the wallet still holds,
-- record the rest as outstanding, freeze the wallet. The wallet never goes negative.
CREATE TABLE IF NOT EXISTS billing_chargebacks (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    provider TEXT NOT NULL, merchant_id TEXT NOT NULL, dispute_id TEXT NOT NULL, event_id TEXT NOT NULL,
    payment_key TEXT NOT NULL REFERENCES billing_payments(id), account_id TEXT NOT NULL REFERENCES billing_accounts(id),
    amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor>0), currency TEXT NOT NULL,
    disputed_units INTEGER NOT NULL CHECK(typeof(disputed_units)='integer' AND disputed_units>=0),
    recovered_units INTEGER NOT NULL CHECK(typeof(recovered_units)='integer' AND recovered_units>=0),
    outstanding_units INTEGER NOT NULL CHECK(typeof(outstanding_units)='integer' AND outstanding_units>=0),
    journal_id TEXT UNIQUE REFERENCES billing_journal(id), evidence_ref TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
    created_at INTEGER NOT NULL, UNIQUE(environment,provider,dispute_id),
    CHECK(recovered_units+outstanding_units=disputed_units)
);
CREATE INDEX IF NOT EXISTS billing_chargebacks_account ON billing_chargebacks(account_id,created_at);
-- A frozen wallet cannot reserve new paid calls. Lifting is an audited administrator decision.
CREATE TABLE IF NOT EXISTS billing_account_freezes (
    id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES billing_accounts(id),
    reason TEXT NOT NULL CHECK(reason IN ('chargeback')), chargeback_id TEXT UNIQUE REFERENCES billing_chargebacks(id),
    created_at INTEGER NOT NULL, lifted_at INTEGER, lifted_by TEXT, lift_reason TEXT,
    CHECK((lifted_at IS NULL)=(lifted_by IS NULL) AND (lifted_at IS NULL)=(lift_reason IS NULL))
);
CREATE INDEX IF NOT EXISTS billing_freezes_active ON billing_account_freezes(account_id) WHERE lifted_at IS NULL;
CREATE TRIGGER IF NOT EXISTS billing_freezes_preserve BEFORE UPDATE ON billing_account_freezes
WHEN NEW.id<>OLD.id OR NEW.account_id<>OLD.account_id OR NEW.reason<>OLD.reason
 OR NEW.chargeback_id IS NOT OLD.chargeback_id OR NEW.created_at<>OLD.created_at OR OLD.lifted_at IS NOT NULL
BEGIN SELECT RAISE(ABORT,'immutable_billing_contract'); END;
CREATE TRIGGER IF NOT EXISTS billing_freezes_no_delete BEFORE DELETE ON billing_account_freezes
BEGIN SELECT RAISE(ABORT,'immutable_billing_contract'); END;
"""

# Immutable facts: even accidental SQL UPDATE/DELETE/INSERT OR REPLACE must not rewrite them.
for _table in ("units", "prices", "accounts", "journal", "orders", "payments", "payment_events",
               "runs", "usage", "settlements", "events", "grants", "chargebacks"):
    for _operation in ("UPDATE", "DELETE"):
        SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS billing_{_table}_no_{_operation.lower()}
BEFORE {_operation} ON billing_{_table} BEGIN SELECT RAISE(ABORT,'immutable_billing_fact'); END;
"""
    # REPLACE can bypass DELETE triggers when recursive_triggers is OFF (Store's default).
    _unique = {
        "units": (("code",),), "prices": (("environment", "price_id"),),
        "accounts": (("id",), ("environment", "unit", "kind", "owner")),
        "journal": (("id",), ("environment", "namespace", "voucher")),
        "orders": (("id",), ("environment", "namespace", "order_id")),
        "payments": (("id",), ("order_key",), ("journal_id",), ("environment", "provider", "payment_id")),
        "payment_events": (("environment", "provider", "merchant_id", "event_id"),),
        "runs": (("id",), ("environment", "namespace", "run_id")),
        "usage": (("attempt_key",),),
        "settlements": (("call_key",), ("attempt_key",), ("journal_id",)), "events": (("seq",),),
        "grants": (("id",), ("journal_id",), ("environment", "grant_id")),
        "chargebacks": (("id",), ("environment", "provider", "dispute_id")),
    }[_table]
    _match = " OR ".join("(" + " AND ".join(f"{key}=NEW.{key}" for key in keys) + ")" for keys in _unique)
    SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS billing_{_table}_no_replace BEFORE INSERT ON billing_{_table}
WHEN EXISTS(SELECT 1 FROM billing_{_table} WHERE {_match})
BEGIN SELECT RAISE(ABORT,'immutable_billing_fact'); END;
"""

SCHEMA += """
CREATE TRIGGER IF NOT EXISTS billing_calls_preserve_contract BEFORE UPDATE ON billing_calls
WHEN NEW.id<>OLD.id OR NEW.run_key<>OLD.run_key OR NEW.logical_call_id<>OLD.logical_call_id
 OR NEW.context<>OLD.context OR NEW.account_id<>OLD.account_id OR NEW.price_id<>OLD.price_id
 OR NEW.hold_units<>OLD.hold_units OR NEW.deadline<>OLD.deadline OR NEW.created_at<>OLD.created_at
 OR NEW.cancel_requested<OLD.cancel_requested OR (OLD.status<>'reserved' AND NEW.status<>OLD.status)
BEGIN SELECT RAISE(ABORT,'immutable_billing_contract'); END;
CREATE TRIGGER IF NOT EXISTS billing_attempts_preserve_contract BEFORE UPDATE ON billing_attempts
WHEN NEW.id<>OLD.id OR NEW.call_key<>OLD.call_key OR NEW.attempt_id<>OLD.attempt_id
 OR NEW.environment<>OLD.environment OR NEW.provider<>OLD.provider OR NEW.requested_model<>OLD.requested_model
 OR NEW.cost_price_id<>OLD.cost_price_id OR NEW.retry_reason IS NOT OLD.retry_reason
 OR NEW.created_at<>OLD.created_at OR (OLD.request_id IS NOT NULL AND NEW.request_id IS NOT OLD.request_id)
 OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS NOT OLD.dispatched_at)
 OR (OLD.status<>'prepared' AND (NEW.owner<>OLD.owner OR NEW.fence<>OLD.fence OR NEW.lease_until<>OLD.lease_until))
 OR (OLD.status IN ('settled','released') AND NEW.status<>OLD.status)
 OR (OLD.status IN ('dispatch_unknown','usage_pending') AND NEW.status IN ('prepared','released'))
BEGIN SELECT RAISE(ABORT,'immutable_billing_contract'); END;
CREATE TRIGGER IF NOT EXISTS billing_calls_no_delete BEFORE DELETE ON billing_calls
BEGIN SELECT RAISE(ABORT,'immutable_billing_contract'); END;
CREATE TRIGGER IF NOT EXISTS billing_attempts_no_delete BEFORE DELETE ON billing_attempts
BEGIN SELECT RAISE(ABORT,'immutable_billing_contract'); END;
CREATE TRIGGER IF NOT EXISTS billing_calls_no_replace BEFORE INSERT ON billing_calls
WHEN EXISTS(SELECT 1 FROM billing_calls WHERE id=NEW.id OR (run_key=NEW.run_key AND logical_call_id=NEW.logical_call_id))
BEGIN SELECT RAISE(ABORT,'immutable_billing_contract'); END;
CREATE TRIGGER IF NOT EXISTS billing_attempts_no_replace BEFORE INSERT ON billing_attempts
WHEN EXISTS(SELECT 1 FROM billing_attempts WHERE id=NEW.id OR fence=NEW.fence
 OR (call_key=NEW.call_key AND attempt_id=NEW.attempt_id)
 OR (environment=NEW.environment AND provider=NEW.provider AND request_id=NEW.request_id))
BEGIN SELECT RAISE(ABORT,'immutable_billing_contract'); END;
"""

#: An order says whose wallet a verified payment funds: the person who paid (``user``, the only
#: kind before org-payments) or an organization the person topped up (``organization``, with
#: ``payer_org_id``). Added columns, so existing orders read as personal.
COLUMNS = (
    ("billing_orders", "payer_kind", "TEXT NOT NULL DEFAULT 'user' CHECK(payer_kind IN ('user','organization'))"),
    ("billing_orders", "payer_org_id", "TEXT"),
    # An inquiry (no funds withdrawn) freezes and takes nothing; earlier rows were chargebacks.
    ("billing_chargebacks", "inquiry", "INTEGER NOT NULL DEFAULT 0 CHECK(inquiry IN (0,1))"),
)


def _journal_trigger_current(c) -> bool:
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='billing_journal_apply'").fetchone()
    return row is not None and "org_wallet" in row[0]


def rebuild(c) -> list[str]:
    """Widen the account/journal kind CHECKs and the wallet trigger of an existing database.

    Runs outside the module transaction (suite_schema.apply): foreign keys must be off while a
    referenced table is rebuilt. Idempotent; a fresh database is already wide and does nothing.
    """
    from ..schema_widen import widen_checks

    def replace_trigger(t):
        if not _journal_trigger_current(t):
            t.execute("DROP TRIGGER IF EXISTS billing_journal_apply")
            t.execute(JOURNAL_APPLY)

    return widen_checks(c, WIDENED, extra=replace_trigger, force=not _journal_trigger_current(c))
