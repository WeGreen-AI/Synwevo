"""Grants, chargebacks, freezes and organization wallets in the ledger (org-payments).

Isolated fixture stores only: fixture units/prices/receipts, no payment provider, no model, no
network, never the real data directory. Each test states a business rule the previous ledger
did not have: grants as their own audited kind, one welcome per account and address, a dispute
after spend that cannot drive a wallet negative, a frozen wallet that cannot reserve, and an
organization's money kept in the organization's wallet.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from synwevo_core.billing import (BillingError, ChargeContext, DisputeNotice, Ledger, PaymentReceipt,
                                 PriceSnapshot, Rate, Unit)
from synwevo_core.identity import Identity
from synwevo_core.store import Store

UNIT = Unit("FIXTURE-CNY", 2)
START = 2_000_000_000
REASON = "fixture: support compensation for a failed run"


def fixture_authorize(t, context, action):
    """Test-only current authorization table; not a production authorization implementation."""
    return t.execute("SELECT 1 FROM fixture_billing_authority WHERE actor=?", (context.actor_user_id,)).fetchone() is not None


def fixture_verify(t, receipt):
    """Test-only durable inbox facts (payments and disputes), never a production 'accept' hook."""
    row = t.execute("SELECT receipt FROM fixture_payment_inbox WHERE event_id=?", (receipt.event_id,)).fetchone()
    return row is not None and json.loads(row[0]) == asdict(receipt)


@pytest.fixture
def money(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    store = Store(tmp_path / "isolated-grant-fixture")
    with store.conn() as c:
        c.executescript("""
            CREATE TABLE fixture_payment_inbox(event_id TEXT PRIMARY KEY,receipt TEXT NOT NULL);
            CREATE TABLE fixture_billing_authority(actor TEXT);
        """)
    identity = Identity(store)
    users = [identity.create_user(f"grant-fixture-{i}@example.test", None, status="active",
                                  with_personal_workspace=False)["id"] for i in range(2)]
    with store.tx() as t:
        t.executemany("INSERT INTO fixture_billing_authority VALUES (?)", [(u,) for u in users])
    ticks = [START]
    ledger = Ledger(store, environment="test", namespace="fixture-agent", authorize=fixture_authorize,
                    verify_payment=fixture_verify, verify_dispute=fixture_verify, clock=lambda: ticks[0],
                    max_hold_seconds=1000)
    ledger.publish_price(PriceSnapshot("fixture-sale", "sale", UNIT, "fixture-provider", "fixture-model",
                                       Rate(1), Rate(1), Rate(1), "fixture-terms", START, per_tokens=1))
    return store, ledger, users, ticks


def trust(store, fact):
    with store.tx() as t:
        t.execute("INSERT OR IGNORE INTO fixture_payment_inbox VALUES (?,?)", (fact.event_id, json.dumps(asdict(fact))))


def pay(money, order, *, user=0, amount_minor=1000, credit=1000, org=None):
    store, ledger, users, _ = money
    ledger.create_payment_order(order_id=order, payer_user_id=users[user], provider="fixture-pay",
                                merchant_id="fixture-merchant", amount_minor=amount_minor, currency="CNY",
                                unit=UNIT, credit_units=credit, exchange_version="fixture-identity",
                                payer_kind="organization" if org else "user", payer_org_id=org)
    receipt = PaymentReceipt("test", "fixture-pay", "fixture-merchant", "pi-" + order, "evt-" + order, order,
                             amount_minor, "CNY", "inbox-" + order, "b" * 64)
    trust(store, receipt)
    return ledger.credit_verified_payment(receipt)


def dispute(money, order, amount_minor, *, dispute_id=None):
    notice = DisputeNotice("test", "fixture-pay", "fixture-merchant", dispute_id or "dp-" + order,
                           "evt-dp-" + (dispute_id or order), "pi-" + order, amount_minor, "CNY",
                           "inbox-dp-" + order, "c" * 64)
    trust(money[0], notice)
    return money[1].record_chargeback(notice)


def balance(money, user=0):
    uid = money[2][user]
    return money[1].balance_for_user(requester_user_id=uid, payer_user_id=uid, unit=UNIT)


def spend(money, amount, *, user=0, run="run-1"):
    """Hold ``amount`` for a call and settle it at exactly that sale (fixture price 1 per token)."""
    from synwevo_core.billing import Usage, UsageEvidence
    store, ledger, users, _ = money
    uid = users[user]
    ctx = ChargeContext(uid, uid, run, "fixture-approval")
    ledger.open_run(ctx, unit=UNIT, budget=10**6, max_calls=10, max_attempts=10, deadline=START + 900)
    call = ledger.reserve_call(ctx, logical_call_id="call-" + run, amount=amount, price_id="fixture-sale",
                               deadline=START + 500)
    ledger.publish_price(PriceSnapshot("fixture-cost", "cost", UNIT, "fixture-provider", "fixture-model",
                                       Rate(0), Rate(0), Rate(0), "fixture-terms", START, per_tokens=1))
    ticket = ledger.prepare_attempt(call, attempt_id="a-" + run, owner="w", cost_price_id="fixture-cost",
                                    requested_model="fixture-model")
    ledger.dispatch(ticket)
    ledger.settle_trusted_usage(ticket.attempt_key, UsageEvidence(
        "fixture-provider", "req-" + run, "ev-" + run, Usage(amount, 0, 0), "fixture-model", "cap", START))
    return call


# ---------------------------------------------------------------- grants
def test_an_admin_grant_is_its_own_audited_kind_with_actor_and_reason(money):
    store, ledger, users, _ = money
    result = ledger.grant(grant_id="g-1", kind="admin", payer_kind="user", payer_id=users[0], unit=UNIT,
                          amount=250, reason=REASON, actor_id=users[1])
    assert balance(money).booked == 250
    statement = ledger.statement_for_user(requester_user_id=users[0], payer_user_id=users[0])
    assert [(row["kind"], row["delta"]) for row in statement] == [("grant", 250)], "赠送与支付入账严格区分"
    row = store.one("SELECT * FROM billing_grants WHERE id=?", (result["id"],))
    assert (row["actor_id"], row["reason"], row["kind"]) == (users[1], REASON, "admin")
    # the same grant replayed is the same grant; changed facts under the same id conflict
    assert ledger.grant(grant_id="g-1", kind="admin", payer_kind="user", payer_id=users[0], unit=UNIT,
                        amount=250, reason=REASON, actor_id=users[1]) == result
    with pytest.raises(BillingError, match="idempotency_conflict"):
        ledger.grant(grant_id="g-1", kind="admin", payer_kind="user", payer_id=users[0], unit=UNIT,
                     amount=999, reason=REASON, actor_id=users[1])
    assert balance(money).booked == 250
    for reason in ("", "short", " " * 20, "x" * 501):
        with pytest.raises(BillingError, match="grant_reason_required"):
            ledger.grant(grant_id="g-2", kind="admin", payer_kind="user", payer_id=users[0], unit=UNIT,
                         amount=1, reason=reason, actor_id=users[1])
    # grant facts are immutable like every other money fact
    with pytest.raises(sqlite3.IntegrityError, match="immutable_billing_fact"):
        store.execute("UPDATE billing_grants SET amount=1 WHERE id=?", (result["id"],))
    assert ledger.audit_balances() == []


def test_the_welcome_allowance_is_once_per_account_and_once_per_address(money):
    store, ledger, users, _ = money
    ledger.grant(grant_id="welcome:" + users[0], kind="welcome", payer_kind="user", payer_id=users[0], unit=UNIT,
                 amount=100, reason="welcome allowance", actor_id="system:welcome", abuse_key="addr-hash-0")
    with pytest.raises(BillingError, match="welcome_already_granted"):
        ledger.grant(grant_id="welcome-again", kind="welcome", payer_kind="user", payer_id=users[0], unit=UNIT,
                     amount=100, reason="welcome allowance", actor_id="system:welcome", abuse_key="addr-hash-x")
    # a second account on the same verified address (deleted and re-registered) gets none
    with pytest.raises(BillingError, match="welcome_already_granted"):
        ledger.grant(grant_id="welcome:" + users[1], kind="welcome", payer_kind="user", payer_id=users[1], unit=UNIT,
                     amount=100, reason="welcome allowance", actor_id="system:welcome", abuse_key="addr-hash-0")
    assert balance(money, 1).booked == 0 and balance(money).booked == 100
    # the database itself refuses a second welcome even if a caller skipped the check
    with pytest.raises(BillingError, match="invalid_grant"):
        ledger.grant(grant_id="w-org", kind="welcome", payer_kind="organization", payer_id="org-x", unit=UNIT,
                     amount=1, reason="welcome allowance", actor_id="system:welcome", abuse_key="k")
    assert ledger.has_welcome(payer_id=users[0]) and ledger.has_welcome(payer_id="nobody", abuse_key="addr-hash-0")
    assert ledger.count_grants(kind="welcome", since=START) == 1


# ---------------------------------------------------------------- chargebacks and freezes
def test_a_dispute_after_spend_recovers_what_is_left_freezes_and_never_goes_negative(money):
    store, ledger, users, ticks = money
    pay(money, "order-1", amount_minor=1000, credit=1000)
    spend(money, 700)
    assert balance(money).booked == 300
    result = dispute(money, "order-1", 1000)
    assert (result["disputed_units"], result["recovered_units"], result["outstanding_units"]) == (1000, 300, 700)
    assert result["frozen"] is True and balance(money).booked == 0, "钱包不为负，差额记为欠款"
    kinds = [r["kind"] for r in ledger.statement_for_user(requester_user_id=users[0], payer_user_id=users[0])]
    assert kinds == ["test_payment", "sale", "chargeback"]
    # replayed dispute: same answer, nothing posted twice
    assert dispute(money, "order-1", 1000) == result
    assert store.one("SELECT COUNT(*) AS n FROM billing_journal WHERE kind='chargeback'")["n"] == 1
    # a frozen wallet cannot reserve, even after a new payment funds it again
    pay(money, "order-2", amount_minor=500, credit=500)
    uid = users[0]
    ctx = ChargeContext(uid, uid, "run-frozen", "fixture-approval")
    ledger.open_run(ctx, unit=UNIT, budget=1000, max_calls=5, max_attempts=5, deadline=START + 900)
    with pytest.raises(BillingError, match="wallet_frozen"):
        ledger.reserve_call(ctx, logical_call_id="c", amount=10, price_id="fixture-sale", deadline=START + 500)
    assert ledger.wallet_frozen(payer_kind="user", payer_id=uid, unit=UNIT)
    lifted = ledger.lift_freeze(result["freeze_id"], actor_id=users[1], reason="dispute won at the provider")
    assert lifted["lifted_by"] == users[1]
    ledger.reserve_call(ctx, logical_call_id="c", amount=10, price_id="fixture-sale", deadline=START + 500)
    with pytest.raises(sqlite3.IntegrityError, match="immutable_billing_contract"):
        store.execute("UPDATE billing_account_freezes SET lifted_at=NULL,lifted_by=NULL,lift_reason=NULL")
    assert ledger.audit_balances() == []


def test_a_dispute_needs_verified_evidence_and_the_funded_payment(money):
    store, ledger, users, _ = money
    pay(money, "order-1", amount_minor=1000, credit=100000)
    untrusted = DisputeNotice("test", "fixture-pay", "fixture-merchant", "dp-x", "evt-untrusted", "pi-order-1",
                              10, "CNY", "inbox", "d" * 64)
    with pytest.raises(BillingError, match="payment_evidence_untrusted"):
        ledger.record_chargeback(untrusted)
    with pytest.raises(BillingError, match="payment_order_mismatch"):
        dispute(money, "order-unknown", 10)
    with pytest.raises(BillingError, match="payment_order_mismatch"):
        dispute(money, "order-1", 1001, dispute_id="too-much")
    # a partial dispute takes the proportional share, rounded towards the platform
    result = dispute(money, "order-1", 333, dispute_id="partial")
    assert result["disputed_units"] == 33300 and result["recovered_units"] == 33300
    assert balance(money).booked == 100000 - 33300


# ---------------------------------------------------------------- organization wallets
def test_an_organization_top_up_funds_the_organization_not_the_person(money):
    from synwevo_core.organizations import Organizations
    store, ledger, users, _ = money
    org = Organizations(store, Identity(store)).create(users[0], "Fixture org", "Fixture ws", "fixture-org-pay")
    oid = org["organization"]["id"] if "organization" in org else org["id"]
    pay(money, "org-order", org=oid, amount_minor=800, credit=800)
    assert balance(money).booked == 0, "组织充值进入组织钱包，不进入个人钱包"
    overview = ledger.overview_for_payer(payer_kind="organization", payer_id=oid, units=(UNIT,))
    assert overview["balances"][0]["booked"] == 800 and overview["balances"][0]["frozen"] is False
    assert [r["kind"] for r in ledger.statement_for_payer(payer_kind="organization", payer_id=oid)] == ["test_payment"]
    # an organization wallet can never go negative either
    account = store.one("SELECT id FROM billing_accounts WHERE kind='org_wallet' AND owner=?", (oid,))["id"]
    sink = store.one("SELECT id FROM billing_accounts WHERE kind='test_payment'")["id"]
    with pytest.raises(sqlite3.IntegrityError, match="wallet_negative"):
        store.execute("INSERT INTO billing_journal(id,environment,namespace,voucher,unit,debit_account,credit_account,"
                      "amount,kind,reference,created_at) VALUES ('j-neg','test','x','v-neg',?,?,?,801,'chargeback','r',0)",
                      (UNIT.code, account, sink))
    # a grant to an organization is an admin grant only
    ledger.grant(grant_id="g-org", kind="admin", payer_kind="organization", payer_id=oid, unit=UNIT, amount=5,
                 reason=REASON, actor_id=users[1])
    assert ledger.overview_for_payer(payer_kind="organization", payer_id=oid, units=(UNIT,))["balances"][0]["booked"] == 805
    with pytest.raises(BillingError, match="organization_unavailable"):
        ledger.create_payment_order(order_id="o-missing", payer_user_id=users[0], provider="fixture-pay",
                                    merchant_id="m", amount_minor=1, currency="CNY", unit=UNIT, credit_units=1,
                                    exchange_version="x", payer_kind="organization", payer_org_id="org-missing")
    assert ledger.audit_balances() == []


# ---------------------------------------------------------------- migration
OLD_TABLES = """
CREATE TABLE billing_units (
    code TEXT PRIMARY KEY, scale INTEGER NOT NULL CHECK(typeof(scale)='integer' AND scale BETWEEN 0 AND 18)
);
CREATE TABLE billing_accounts (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    unit TEXT NOT NULL REFERENCES billing_units(code), owner TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('wallet','paid','test_payment','revenue')),
    UNIQUE(environment,unit,kind,owner), UNIQUE(id,environment,unit)
);
CREATE TABLE billing_balances (
    account_id TEXT PRIMARY KEY REFERENCES billing_accounts(id),
    amount INTEGER NOT NULL CHECK(typeof(amount)='integer')
);
CREATE TABLE billing_journal (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL, namespace TEXT NOT NULL,
    voucher TEXT NOT NULL, unit TEXT NOT NULL,
    debit_account TEXT NOT NULL, credit_account TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>=0),
    kind TEXT NOT NULL CHECK(kind IN ('paid','test_payment','sale')),
    reference TEXT NOT NULL, created_at INTEGER NOT NULL,
    CHECK(debit_account<>credit_account), UNIQUE(environment,namespace,voucher),
    FOREIGN KEY(debit_account,environment,unit) REFERENCES billing_accounts(id,environment,unit),
    FOREIGN KEY(credit_account,environment,unit) REFERENCES billing_accounts(id,environment,unit)
);
CREATE VIEW billing_entries AS
    SELECT id AS journal_id, environment,unit,debit_account AS account_id,-amount AS delta FROM billing_journal
    UNION ALL
    SELECT id,environment,unit,credit_account,amount FROM billing_journal;
CREATE INDEX billing_journal_personal_debits ON billing_journal(environment,debit_account,kind);
CREATE TRIGGER billing_journal_apply AFTER INSERT ON billing_journal BEGIN
    UPDATE billing_balances SET amount=amount-NEW.amount WHERE account_id=NEW.debit_account;
    UPDATE billing_balances SET amount=amount+NEW.amount WHERE account_id=NEW.credit_account;
    SELECT CASE WHEN EXISTS(SELECT 1 FROM billing_balances b JOIN billing_accounts a ON a.id=b.account_id
        WHERE a.kind='wallet' AND a.id=NEW.debit_account AND b.amount<0)
        THEN RAISE(ABORT,'wallet_negative') END;
END;
CREATE TRIGGER billing_journal_no_update BEFORE UPDATE ON billing_journal BEGIN SELECT RAISE(ABORT,'immutable_billing_fact'); END;
INSERT INTO billing_units VALUES ('FIXTURE-CNY',2);
INSERT INTO billing_accounts VALUES ('acc-wallet','test','FIXTURE-CNY','fixture-owner','wallet');
INSERT INTO billing_accounts VALUES ('acc-src','test','FIXTURE-CNY','platform','test_payment');
INSERT INTO billing_balances VALUES ('acc-wallet',0),('acc-src',0);
INSERT INTO billing_journal VALUES ('j-old','test','agent','payment:old','FIXTURE-CNY','acc-src','acc-wallet',42,'test_payment','old',1);
"""


def test_an_existing_ledger_is_widened_in_place_with_every_row_and_balance_kept(tmp_path, monkeypatch):
    """The previous release's exact table text is upgraded by Store, not recreated empty."""
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    root = tmp_path / "isolated-old-ledger"
    root.mkdir()
    old = sqlite3.connect(root / "docweave.sqlite3")
    old.executescript(OLD_TABLES)
    old.commit()
    old.close()
    store = Store(root)
    sql = {r["name"]: r["sql"] for r in store.all("SELECT name,sql FROM sqlite_master WHERE name LIKE 'billing_%'")}
    assert "'org_wallet'" in sql["billing_accounts"] and "'chargeback'" in sql["billing_journal"]
    assert "org_wallet" in sql["billing_journal_apply"], "新的钱包不为负触发器覆盖组织钱包"
    assert "billing_journal_no_replace" in sql and "billing_journal_no_update" in sql, "不可变触发器仍在"
    assert store.one("SELECT amount FROM billing_balances WHERE account_id='acc-wallet'")["amount"] == 42
    assert store.one("SELECT kind,amount FROM billing_journal WHERE id='j-old'") == {"kind": "test_payment", "amount": 42}
    assert store.all("SELECT * FROM billing_entries WHERE journal_id='j-old' ORDER BY delta") == [
        {"journal_id": "j-old", "environment": "test", "unit": "FIXTURE-CNY", "account_id": "acc-src", "delta": -42},
        {"journal_id": "j-old", "environment": "test", "unit": "FIXTURE-CNY", "account_id": "acc-wallet", "delta": 42}]
    with store.conn() as c:
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "外键检查在迁移后恢复"
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError, match="immutable_billing_fact"):
        store.execute("UPDATE billing_journal SET amount=1 WHERE id='j-old'")
    # opening again changes nothing (idempotent), and the new kinds are accepted
    before = store.all("SELECT name,sql FROM sqlite_master ORDER BY name")
    store = Store(root)
    assert store.all("SELECT name,sql FROM sqlite_master ORDER BY name") == before
    store.execute("INSERT INTO billing_accounts VALUES ('acc-grant','test','FIXTURE-CNY','platform','grant')")
    store.execute("INSERT INTO billing_balances VALUES ('acc-grant',0)")
    store.execute("INSERT INTO billing_journal VALUES ('j-new','test','agent','grant:new','FIXTURE-CNY','acc-grant',"
                  "'acc-wallet',8,'grant','new',2)")
    assert store.one("SELECT amount FROM billing_balances WHERE account_id='acc-wallet'")["amount"] == 50
