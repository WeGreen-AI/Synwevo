"""A receipt's payment time is when the provider says the payment happened (review payments-3).

Business rule: the receipt carries what really happened. ``paid_at`` ("付款时间") is the time the
provider recorded the completed payment (the signed event's ``created``); ``credited_at``
("入账时间") is when this platform credited the wallet. A delivery processed days later (provider
retries, the crash-recovery sweep) must not move the payment time; when the provider's time is
not known, the receipt shows no payment time rather than the processing time.

Fixtures only: the Stripe API is an httpx.MockTransport, deliveries are signed with a fixture
secret; isolated temporary data directory.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from .test_wallet_topups import created, deliver, session_event, world  # noqa: F401 - fixture

DAY = 86400


def test_a_late_delivery_keeps_the_providers_payment_time_and_shows_processing_as_credit_time(world):
    world["configure"]()
    c, order, session = created(world)
    paid_at = int(time.time())
    later = paid_at + 2 * DAY                     # delivered (or swept) two days after the payment
    topups = world["api"].state.topups
    topups.clock = lambda: later
    event = session_event(order, session_id=session, created=paid_at)
    assert deliver(world, event, at=later).json()["status"] == "processed"
    receipt = c.get(f"/api/auth/wallet/receipts/{order}").json()
    assert receipt["paid_at"] == paid_at, "付款时间来自支付服务商"
    assert receipt["credited_at"] == later, "入账时间是本平台处理的时间"


@pytest.mark.parametrize("value", [None, "1700000000", True, -5, "future"])
def test_without_a_trustworthy_provider_time_the_receipt_shows_no_payment_time(world, value):
    world["configure"]()
    c, order, session = created(world)
    event = session_event(order, session_id=session)
    if value is None:
        del event["created"]
    else:
        event["created"] = int(time.time()) + 3 * DAY if value == "future" else value
    assert deliver(world, event).json()["status"] == "processed"
    receipt = c.get(f"/api/auth/wallet/receipts/{order}").json()
    assert receipt["paid_at"] is None and receipt["credited_at"], "不把处理时间写成付款时间"


def test_the_recorded_provider_time_cannot_be_rewritten(world):
    world["configure"]()
    c, order, session = created(world)
    event = session_event(order, session_id=session, created=int(time.time()) - 60)
    deliver(world, event)
    with pytest.raises(sqlite3.IntegrityError, match="immutable_payment_evidence"):
        world["store"].execute("UPDATE payment_webhook_inbox SET provider_created_at=1 WHERE event_id=?", (event["id"],))


def test_an_existing_inbox_gains_the_provider_time_with_every_row_kept(tmp_path, monkeypatch):
    from synwevo_core.store import Store
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    store = Store(tmp_path / "isolated-old-inbox")
    # FIXTURE: the previous release's inbox (no provider time column, no trigger) with one row.
    with store.conn() as c:
        c.executescript("""BEGIN;
            DROP TRIGGER payment_inbox_created_fixed;
            ALTER TABLE payment_webhook_inbox DROP COLUMN provider_created_at;
            INSERT INTO payment_webhook_inbox(provider,event_id,environment,type,payload_sha256,facts_json,
                signature_verified,received_at,status) VALUES ('stripe','evt_fixture_old','test','charge.succeeded',
                '""" + "a" * 64 + """','{}',1,1,'processed');
            COMMIT;""")
    upgraded = Store(store.root)
    row = upgraded.one("SELECT status,provider_created_at FROM payment_webhook_inbox WHERE event_id='evt_fixture_old'")
    assert row == {"status": "processed", "provider_created_at": None}, "旧行保留，付款时间未知"
    # an unknown time may be learnt once (a redelivery carries it), then it is fixed
    upgraded.execute("UPDATE payment_webhook_inbox SET provider_created_at=5 WHERE event_id='evt_fixture_old'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable_payment_evidence"):
        upgraded.execute("UPDATE payment_webhook_inbox SET provider_created_at=6 WHERE event_id='evt_fixture_old'")
    before = upgraded.all("SELECT name,sql FROM sqlite_master ORDER BY name")
    assert Store(store.root).all("SELECT name,sql FROM sqlite_master ORDER BY name") == before
