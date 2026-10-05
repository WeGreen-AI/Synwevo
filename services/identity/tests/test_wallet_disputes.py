"""Provider disputes on the real accounts application (review findings money-2, money-4, payments-2).

Business rules, independent of the implementation:

* A disputed amount above the paid amount still freezes the wallet and recovers the payment's
  credit (capped), and the provider is told it was handled; it is not a failure retried forever.
* A dispute in another currency than the credited payment is rejected with its own code and an
  administrator alert; never retried forever, never silently applied.
* A dispute for a payment this platform never credited (another product on the same provider
  account) is rejected with an alert instead of failing forever; if the payment is credited here
  later (events out of order), the dispute is applied then.
* An inquiry (status ``warning_*``: no funds withdrawn) freezes the wallet and takes nothing.
* The crash-recovery sweep cannot be starved by rows that keep failing: fewest attempts first,
  backoff between tries, and a bounded number of tries.

Fixtures only: the Stripe API is an httpx.MockTransport, deliveries are signed with a fixture
secret, alerts are captured by a test double; isolated temporary data directory.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from threading import Event, current_thread
import time

import pytest

from synwevo_identity.payments import _facts

from .test_wallet_topups import cny_booked, created, deliver, session_event, world  # noqa: F401 - fixture


def dispute_event(i, *, pi="pi_fixture1", amount=1000, currency="cny", status="needs_response"):
    return {"id": f"evt_dispute_{i}", "object": "event", "type": "charge.dispute.created", "livemode": False,
            "data": {"object": {"id": f"dp_fixture_{i}", "object": "dispute", "payment_intent": pi,
                                "charge": "ch_fixture1", "amount": amount, "currency": currency, "status": status}}}


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    from synwevo_core.billing import risk
    monkeypatch.setattr(risk, "alert_payment_webhook_failure",
                        lambda store, *, environment, provider, code: sent.append(code) or 1)
    return sent


def paid(world):
    world["configure"]()
    c, order, session = created(world)
    assert deliver(world, session_event(order, session_id=session)).json()["status"] == "processed"
    return c, order


def inbox(world, event_id):
    return world["store"].one("SELECT status,result_code,attempts FROM payment_webhook_inbox WHERE event_id=?", (event_id,))


def test_a_dispute_above_the_paid_amount_freezes_and_recovers_the_credit(world, alerts):
    c, _ = paid(world)
    r = deliver(world, dispute_event("fx", amount=1005))         # CNY 10.05 disputed of CNY 10.00 paid
    assert r.status_code == 200 and r.json()["status"] == "processed", r.text
    assert cny_booked(world) == 0
    assert c.get("/api/auth/wallet/topup-options").json()["reason"] == "wallet_frozen"
    assert alerts == []


def test_a_dispute_in_another_currency_is_rejected_with_its_own_code_and_alerted(world, alerts):
    paid(world)
    r = deliver(world, dispute_event("usd", currency="usd"))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert inbox(world, "evt_dispute_usd")["result_code"] == "dispute_payment_mismatch"
    assert alerts == ["dispute_payment_mismatch"] and cny_booked(world) == 10**7
    assert world["store"].all("SELECT * FROM billing_account_freezes") == []


def test_an_inquiry_freezes_the_wallet_and_takes_nothing(world, alerts):
    c, _ = paid(world)
    r = deliver(world, dispute_event("inquiry", status="warning_needs_response"))
    assert r.json()["status"] == "processed"
    assert cny_booked(world) == 10**7, "询问不扣回"
    assert c.get("/api/auth/wallet/topup-options").json()["reason"] == "wallet_frozen"
    row = world["store"].one("SELECT inquiry,disputed_units,recovered_units FROM billing_chargebacks")
    assert row == {"inquiry": 1, "disputed_units": 0, "recovered_units": 0}


def test_a_dispute_for_a_payment_never_credited_here_is_rejected_not_retried_forever(world, alerts):
    world["configure"]()
    r = deliver(world, dispute_event("foreign", pi="pi_other_product"))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert inbox(world, "evt_dispute_foreign")["result_code"] == "dispute_payment_unknown"
    assert alerts == ["dispute_payment_unknown"]
    world["api"].state.topups.process_pending()
    assert inbox(world, "evt_dispute_foreign")["status"] == "rejected", "不再被清扫任务反复重试"


def test_a_dispute_before_the_credit_is_applied_when_the_payment_is_credited(world, alerts):
    world["configure"]()
    c, order, session = created(world)
    assert deliver(world, dispute_event("early")).status_code == 200
    assert cny_booked(world) == 0 and world["store"].all("SELECT * FROM billing_chargebacks") == []
    assert deliver(world, session_event(order, session_id=session)).json()["status"] == "processed"
    assert inbox(world, "evt_dispute_early")["status"] == "processed"
    assert cny_booked(world) == 0, "入账后立即应用先到的争议"
    assert c.get("/api/auth/wallet/topup-options").json()["reason"] == "wallet_frozen"


def test_a_dispute_for_a_reported_but_not_yet_credited_payment_waits_for_the_credit(world, alerts):
    world["configure"]()
    c, order, session = created(world)
    # Our checkout reported the payment (not paid yet: an asynchronous method), then a dispute.
    assert deliver(world, session_event(order, paid=False, session_id=session)).json()["status"] == "ignored"
    assert deliver(world, dispute_event("waiting")).status_code == 500, "付款已报告、尚未入账：让渠道重试"
    assert alerts == []
    paid_event = session_event(order, kind="checkout.session.async_payment_succeeded", session_id=session)
    assert deliver(world, paid_event).json()["status"] == "processed"
    assert inbox(world, "evt_dispute_waiting")["status"] == "processed" and cny_booked(world) == 0


@pytest.mark.parametrize("worker", ["webhook", "sweep"])
@pytest.mark.parametrize("pause_at", ["unknown_payment", "result"])
def test_concurrent_credit_and_dispute_are_reconciled_once(world, alerts, monkeypatch, worker, pause_at):
    """FIXTURE barriers cover credit both before and after the dispute's waiting decision.
    The sweep uses a second service/Store, as another account process would; no process-local
    mutex can stand in for the durable ordering of these two independent ledger transactions.
    """
    from synwevo_core.store import Store
    from synwevo_identity.payments import TopupService
    from .test_wallet_topups import CNY

    world["configure"]()
    client, order, session = created(world)
    credit_service = world["api"].state.topups
    event = dispute_event(f"race_{worker}_{pause_at}")
    dispute_service = (credit_service if worker == "webhook" else TopupService(
        Store(world["store"].root), environment="test", units=(CNY,)))
    ready, resume = Event(), Event()
    blocked_thread = []

    def pause():
        if current_thread() is blocked_thread[0]:
            ready.set()
            assert resume.wait(10), "fixture dispute was not resumed"

    if pause_at == "unknown_payment":
        original = dispute_service._credit_expected

        def unknown(payment_id):
            result = original(payment_id)
            assert result is False
            pause()
            return result

        monkeypatch.setattr(dispute_service, "_credit_expected", unknown)
    else:
        original = dispute_service._process

        def processed(event_id, kind, facts, digest):
            result = original(event_id, kind, facts, digest)
            if event_id == event["id"]:
                pause()
            return result

        monkeypatch.setattr(dispute_service, "_process", processed)

    if worker == "sweep":
        _crash_left(world, event, int(time.time()))

    def run_dispute():
        if worker == "sweep":
            blocked_thread.append(current_thread())
            return dispute_service.process_pending()
        # TestClient runs the webhook in a worker thread, so capture that thread at processing.
        process = dispute_service._process

        def capture(*args):
            if not blocked_thread:
                blocked_thread.append(current_thread())
            return process(*args)

        monkeypatch.setattr(dispute_service, "_process", capture)
        return deliver(world, event)

    with ThreadPoolExecutor(1) as pool:
        handling = pool.submit(run_dispute)
        try:
            assert ready.wait(10)
            assert deliver(world, session_event(order, session_id=session)).json()["status"] == "processed"
        finally:
            resume.set()
        result = handling.result(timeout=10)
    if worker == "sweep":
        assert result == 1
    else:
        assert result.json()["status"] == "processed"
    assert inbox(world, event["id"])["status"] == "processed"
    assert cny_booked(world) == 0
    assert client.get("/api/auth/wallet/topup-options").json()["reason"] == "wallet_frozen"
    assert deliver(world, event).json()["status"] == "processed"
    credit_service.process_pending()
    assert world["store"].one("SELECT COUNT(*) AS n FROM billing_chargebacks")["n"] == 1
    assert world["store"].one("SELECT COUNT(*) AS n FROM billing_journal WHERE kind='chargeback'")["n"] == 1
    assert world["store"].one("SELECT COUNT(*) AS n FROM billing_account_freezes")["n"] == 1
    assert alerts == []


def _crash_left(world, event, received_at):
    body = json.dumps(event).encode()
    world["store"].execute(
        "INSERT INTO payment_webhook_inbox(provider,event_id,environment,type,payload_sha256,facts_json,"
        "signature_verified,received_at,status,attempts) VALUES ('stripe',?,'test',?,?,?,1,?,'received',1)",
        (event["id"], event["type"], hashlib.sha256(body).hexdigest(),
         json.dumps(_facts(event), sort_keys=True, separators=(",", ":")), received_at))


def test_rows_that_keep_failing_cannot_starve_the_recovery_of_a_crash_left_credit(world, alerts, monkeypatch):
    world["configure"]()
    topups = world["api"].state.topups
    c, order, session = created(world)
    # FIXTURE: 60 older inbox rows whose processing keeps failing (a broken dependency).
    store = world["store"]
    for i in range(60):
        _crash_left(world, dispute_event(f"broken{i}", pi=f"pi_broken{i}"), int(time.time()) - 1000 + i)
    real = topups._process
    monkeypatch.setattr(topups, "_process", lambda event_id, kind, facts, digest:
                        ("failed", "processing_error") if event_id.startswith("evt_dispute_broken") else real(event_id, kind, facts, digest))
    topups.process_pending()
    # A credit event left 'received' by a crash, newer than every failing row.
    _crash_left(world, session_event(order, session_id=session), int(time.time()))
    for _ in range(3):
        topups.clock = lambda: int(time.time()) + 3600 * (_ + 1)      # hourly sweeps
        topups.process_pending()
    assert inbox(world, session_event(order, session_id=session)["id"])["status"] == "processed"
    assert cny_booked(world) == 10**7


def test_a_failing_row_is_retried_with_backoff_and_a_bounded_number_of_times(world, alerts, monkeypatch):
    from synwevo_identity.payments import PENDING_MAX_ATTEMPTS
    world["configure"]()
    topups = world["api"].state.topups
    _crash_left(world, dispute_event("stuck"), int(time.time()))
    monkeypatch.setattr(topups, "_process", lambda *a: ("failed", "processing_error"))
    start = int(time.time())
    topups.clock = lambda: start
    topups.process_pending()
    tried = inbox(world, "evt_dispute_stuck")["attempts"]
    topups.process_pending()                                           # the same instant: backing off
    assert inbox(world, "evt_dispute_stuck")["attempts"] == tried
    for hour in range(1, 24 * 30):                                      # a month of hourly sweeps
        topups.clock = lambda: start + 3600 * hour
        topups.process_pending()
    row = inbox(world, "evt_dispute_stuck")
    assert row["status"] == "failed" and row["attempts"] == PENDING_MAX_ATTEMPTS, "有界重试，不无限进行"
    assert alerts.count("webhook_retry_exhausted") == 1
