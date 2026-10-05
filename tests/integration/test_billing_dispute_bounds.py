"""How much a dispute may take back, and when it takes nothing (review findings money-2, money-4).

Business rules, independent of the implementation:

* A disputed amount above the paid amount (the provider documents that it can differ, e.g.
  through currency fluctuation) is still a dispute of that payment: it is recorded, capped at the
  payment's credit, and the wallet is frozen. It is never refused and retried forever.
* A dispute whose merchant or currency does not match the credited payment is a different fact:
  refused with its own code, nothing recovered, nothing frozen.
* A dispute for a payment never credited here is refused with its own code (the service decides
  whether to wait for the credit or reject it).
* All disputes of one payment together never take back more than that payment's credit, so a
  second dispute object for the same charge cannot take credit funded by another payment.
* An inquiry (the issuer only asked; no funds were withdrawn) freezes the wallet but takes
  nothing back. An escalation reported as a new dispute then recovers the payment's credit.

Isolated fixture stores only: fixture units, prices and receipts; no provider, no network.
"""
from __future__ import annotations

import sqlite3

import pytest

from synwevo_core.billing import BillingError, DisputeNotice
from synwevo_core.store import Store

from tests.integration.test_billing_grants_chargebacks import balance, money, pay, trust  # noqa: F401


def notice(money, order, amount_minor, *, dispute_id, currency="CNY", merchant="fixture-merchant", inquiry=False,
           event=None):
    fact = DisputeNotice("test", "fixture-pay", merchant, dispute_id, event or "evt-" + dispute_id, "pi-" + order,
                         amount_minor, currency, "inbox-" + dispute_id, "c" * 64, **({"inquiry": True} if inquiry else {}))
    trust(money[0], fact)
    return fact


def record(money, order, amount_minor, **kwargs):
    return money[1].record_chargeback(notice(money, order, amount_minor, **kwargs))


# ---------------------------------------------------------------- money-2: amount above the payment
def test_a_dispute_above_the_paid_amount_is_capped_at_the_credit_and_still_freezes(money):
    pay(money, "order-1", amount_minor=10000, credit=10000)
    result = record(money, "order-1", 10050, dispute_id="dp-fx")     # CNY 100.50 disputed of CNY 100 paid
    assert (result["disputed_units"], result["recovered_units"], result["outstanding_units"]) == (10000, 10000, 0)
    assert result["frozen"] is True and result["amount_minor"] == 10050, "按提供方原样记录争议金额"
    assert balance(money).booked == 0
    # replayed: the same answer, nothing taken twice
    assert record(money, "order-1", 10050, dispute_id="dp-fx") == result


def test_a_dispute_whose_currency_or_merchant_differs_is_refused_with_its_own_code(money):
    store, _, _, _ = money
    pay(money, "order-1", amount_minor=1000, credit=1000)
    with pytest.raises(BillingError, match="dispute_payment_mismatch"):
        record(money, "order-1", 1000, dispute_id="dp-usd", currency="USD")
    with pytest.raises(BillingError, match="dispute_payment_mismatch"):
        record(money, "order-1", 1000, dispute_id="dp-merchant", merchant="fixture-other-merchant")
    assert store.all("SELECT * FROM billing_chargebacks") == [] and store.all("SELECT * FROM billing_account_freezes") == []
    assert balance(money).booked == 1000


def test_a_dispute_for_a_payment_never_credited_here_says_so(money):
    with pytest.raises(BillingError, match="payment_not_credited"):
        record(money, "order-unknown", 10, dispute_id="dp-unknown")
    assert money[0].all("SELECT * FROM billing_account_freezes") == []


# ---------------------------------------------------------------- money-4: bounded per payment
def test_two_disputes_on_one_payment_never_take_more_than_its_credit(money):
    pay(money, "order-1", amount_minor=1000, credit=1000)
    pay(money, "order-2", amount_minor=1000, credit=1000)
    first = record(money, "order-1", 1000, dispute_id="dp-first")
    second = record(money, "order-1", 1000, dispute_id="dp-second")
    assert first["recovered_units"] == 1000
    assert (second["disputed_units"], second["recovered_units"], second["outstanding_units"]) == (0, 0, 0)
    assert balance(money).booked == 1000, "未争议的第二笔充值不被扣回"
    assert second["frozen"] is True, "每一项争议都冻结钱包"


def test_partial_disputes_of_one_payment_add_up_to_at_most_its_credit(money):
    pay(money, "order-1", amount_minor=1000, credit=1000)
    pay(money, "order-2", amount_minor=500, credit=500)
    assert record(money, "order-1", 600, dispute_id="dp-a")["disputed_units"] == 600
    assert record(money, "order-1", 600, dispute_id="dp-b")["disputed_units"] == 400
    assert balance(money).booked == 500
    assert money[0].one("SELECT SUM(disputed_units) AS n FROM billing_chargebacks")["n"] == 1000


def test_an_inquiry_freezes_without_taking_money_and_a_later_escalation_recovers(money):
    store, ledger, users, _ = money
    pay(money, "order-1", amount_minor=1000, credit=1000)
    inquiry = record(money, "order-1", 1000, dispute_id="dp-inquiry", inquiry=True)
    assert (inquiry["disputed_units"], inquiry["recovered_units"], inquiry["journal_id"]) == (0, 0, None)
    assert inquiry["frozen"] is True and inquiry["inquiry"] is True and balance(money).booked == 1000
    assert [r["kind"] for r in ledger.statement_for_user(requester_user_id=users[0], payer_user_id=users[0])] == ["test_payment"]
    # the same dispute cannot be replayed as something else
    with pytest.raises(BillingError, match="payment_event_conflict"):
        record(money, "order-1", 1000, dispute_id="dp-inquiry", inquiry=False, event="evt-dp-inquiry-replayed")
    escalated = record(money, "order-1", 1000, dispute_id="dp-escalated")
    assert escalated["recovered_units"] == 1000 and escalated["inquiry"] is False and balance(money).booked == 0


def test_a_dispute_notice_inquiry_flag_is_a_strict_boolean():
    with pytest.raises(BillingError):
        DisputeNotice("test", "p", "m", "d", "e", "pi", 1, "CNY", "ref", "a" * 64, inquiry=1)


# ---------------------------------------------------------------- migration
OLD_CHARGEBACKS = """CREATE TABLE billing_chargebacks (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    provider TEXT NOT NULL, merchant_id TEXT NOT NULL, dispute_id TEXT NOT NULL, event_id TEXT NOT NULL,
    payment_key TEXT NOT NULL REFERENCES billing_payments(id), account_id TEXT NOT NULL REFERENCES billing_accounts(id),
    amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor>0), currency TEXT NOT NULL,
    disputed_units INTEGER NOT NULL CHECK(typeof(disputed_units)='integer' AND disputed_units>0),
    recovered_units INTEGER NOT NULL CHECK(typeof(recovered_units)='integer' AND recovered_units>=0),
    outstanding_units INTEGER NOT NULL CHECK(typeof(outstanding_units)='integer' AND outstanding_units>=0),
    journal_id TEXT UNIQUE REFERENCES billing_journal(id), evidence_ref TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
    created_at INTEGER NOT NULL, UNIQUE(environment,provider,dispute_id),
    CHECK(recovered_units+outstanding_units=disputed_units)
)"""
COLUMNS = ("id,environment,provider,merchant_id,dispute_id,event_id,payment_key,account_id,amount_minor,currency,"
           "disputed_units,recovered_units,outstanding_units,journal_id,evidence_ref,payload_sha256,created_at")


def test_an_existing_chargeback_table_is_widened_in_place_with_its_rows_and_freezes(money):
    store, ledger, _, _ = money
    pay(money, "order-1", amount_minor=1000, credit=1000)
    old = record(money, "order-1", 1000, dispute_id="dp-before-upgrade")
    # FIXTURE: put the previous release's exact table text back, rows unchanged.
    with store.conn() as c:
        c.execute("PRAGMA foreign_keys=OFF")
        c.executescript(f"""BEGIN;
            {OLD_CHARGEBACKS.replace("TABLE billing_chargebacks", "TABLE fixture_chargebacks_old")};
            INSERT INTO fixture_chargebacks_old({COLUMNS}) SELECT {COLUMNS} FROM billing_chargebacks;
            DROP TABLE billing_chargebacks;
            ALTER TABLE fixture_chargebacks_old RENAME TO billing_chargebacks;
            CREATE INDEX IF NOT EXISTS billing_chargebacks_account ON billing_chargebacks(account_id,created_at);
            COMMIT;""")
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        c.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(f"INSERT INTO billing_chargebacks({COLUMNS}) SELECT 'x',environment,provider,merchant_id,'dp-zero',"
                      "event_id,payment_key,account_id,amount_minor,currency,0,0,0,NULL,evidence_ref,payload_sha256,"
                      "created_at FROM billing_chargebacks")
    upgraded = Store(store.root)
    sql = upgraded.one("SELECT sql FROM sqlite_master WHERE name='billing_chargebacks'")["sql"]
    assert "disputed_units>=0" in sql and "inquiry" in sql
    row = upgraded.one("SELECT * FROM billing_chargebacks WHERE dispute_id='dp-before-upgrade'")
    assert (row["recovered_units"], row["inquiry"]) == (1000, 0), "旧争议原样保留，视为正式拒付"
    assert upgraded.one("SELECT chargeback_id FROM billing_account_freezes")["chargeback_id"] == old["id"]
    with upgraded.conn() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    before = upgraded.all("SELECT name,sql FROM sqlite_master ORDER BY name")
    assert Store(store.root).all("SELECT name,sql FROM sqlite_master ORDER BY name") == before, "再次打开不再变化"
