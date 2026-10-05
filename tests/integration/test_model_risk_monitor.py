"""Upstream balance monitor, breaker, reconciliation and alerts on isolated real Stores.

No real provider traffic: the balance endpoint is an httpx MockTransport fixture and the key is
a fixture string that is not a real credential. Ledger facts are created through the real
Ledger/CostAdmission path with synthetic prices. Expected amounts are independent arithmetic:
the fixture cost price is 1000 units per uncached input token and 2000 per output token, in a
fixture unit coded CNY with 6 decimals (1 yuan = 1,000,000 units).
"""
from dataclasses import replace
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from synwevo_core import journal
from synwevo_core.billing import Ledger, PaymentReceipt, PriceSnapshot, Rate, Unit, Usage, UsageEvidence, ChargeContext
from synwevo_core.billing.admission import CostAdmission
from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.billing.provider_settings import ProviderSettings
from synwevo_core.billing.risk import RiskControls, alert_payment_webhook_failure
from synwevo_core.billing.risk_monitor import RiskMonitor
from synwevo_core.billing.upstream import BALANCE_URL, DAY, Reconciliation, UpstreamBalance
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.platform_access import PlatformAccess
from synwevo_core.store import Store

KEY = "fixture-platform-credential-not-a-real-key"
DAY0 = 2_000_073_600  # A UTC midnight (2033-05-19), so windows are whole fixture days.
COST = Unit("CNY", 6)
SALE = Unit("FIXTURECREDITS", 6)
RISK = {"allowlist_only": False, "payer_window_s": 3600, "payer_window_cost": None, "route_window_s": 3600,
        "route_window_cost": None, "balance_warn": "200000000", "balance_close": "50000000", "balance_poll_s": 300,
        "reconcile_tolerance": "1000", "pending_alert": "4000000"}


def balance_body(total, *, available=True, currency="CNY"):
    return {"is_available": available, "balance_infos": [
        {"currency": currency, "total_balance": total, "granted_balance": "0.00", "topped_up_balance": total}]}


@pytest.fixture
def platform(tmp_path):
    assert DAY0 % DAY == 0
    store = Store(tmp_path / "isolated-risk-monitor")
    identity = Identity(store)
    owner = identity.create_user("risk-owner@example.test", None, status="active", with_personal_workspace=False)["id"]
    payer = identity.create_user("risk-payer@example.test", None, status="active", with_personal_workspace=False)["id"]
    store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (owner,))
    PlatformAccess(store).host_grant(owner, "super_admin", source="fixture:risk-monitor", reason="isolated alert recipient")
    ticks = [DAY0 + 3600]
    clock = lambda: ticks[0]  # noqa: E731
    ledger = Ledger(store, environment="test", namespace="agent", clock=clock, authorize=lambda t, c, a: True,
                    verify_payment=lambda t, r: r.evidence_ref == "fixture-verified-inbox")
    sale = PriceSnapshot("fixture-sale", "sale", SALE, "deepseek", "fixture-route", Rate(1000), Rate(100), Rate(2000),
                         "fixture-terms", DAY0 - DAY, per_tokens=1)
    ledger.publish_price(sale)
    ledger.publish_price(replace(sale, price_id="fixture-cost", purpose="cost", unit=COST))
    ledger.create_payment_order(order_id="fixture-order", payer_user_id=payer, provider="fixture-payments",
                                merchant_id="fixture-merchant", amount_minor=100, currency="XTS", unit=SALE,
                                credit_units=10**12, exchange_version="fixture-exchange")
    ledger.credit_verified_payment(PaymentReceipt("test", "fixture-payments", "fixture-merchant", "fixture-payment",
                                                  "fixture-event", "fixture-order", 100, "XTS", "fixture-verified-inbox", "a" * 64))
    costs = CostSettings(store, environment="test", clock=clock)
    spec = {"enabled": True, "unit": {"code": "CNY", "scale": 6}, "daily_budget": str(10**12), "weekly_budget": str(10**12),
            "monthly_budget": str(10**12), "unknown_budget": str(10**12), "max_inflight": 100, "max_unknown": 100, "risk": RISK}
    with store.tx():
        costs.append(spec, expected_revision=0, actor_id=owner, reason="isolated risk fixture policy", operation_id="fixture-cost-1")
    settings = ProviderSettings(store, environment="test", clock=clock)
    route = {"requested_model": "fixture-request", "confirmed_route": "fixture-route", "confirmation_ref": "fixture-confirmation",
             "cost_basis_ref": "fixture-cost-evidence", "account_confirmation_ref": "fixture-account-binding",
             "sale_price_id": "fixture-sale", "cost_price_id": "fixture-cost", "input_token_ceiling": 1000,
             "output_token_ceiling": 32, "valid_until": DAY0 + 30 * DAY}
    with store.tx():
        settings.append({"enabled": True, "route": route}, {"action": "replace", "api_key": KEY}, expected_revision=0,
                        actor_id=owner, reason="isolated provider fixture", operation_id="fixture-provider-1")
    seen, replies = [], []

    def respond(request):
        seen.append(request)
        status, body = replies.pop(0) if replies else (200, balance_body("1000.00"))
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body)

    return SimpleNamespace(store=store, owner=owner, payer=payer, ticks=ticks, clock=clock, ledger=ledger, costs=costs,
                           seen=seen, replies=replies, transport=httpx.MockTransport(respond))


def poll(p):
    return UpstreamBalance(p.store, environment="test", transport=p.transport, clock=p.clock).poll()


def alerts(p, kind):
    return p.store.all("SELECT * FROM notifications WHERE user_id=? AND kind=?", (p.owner, kind))


def spend(p, name, *, input_tokens=0, output_tokens=0, settle=True, bound=5_000_000):
    """One real admitted call; settled at exact fixture cost unless `settle` is False."""
    ctx = ChargeContext(p.payer, p.payer, "fixture-run-" + name, "fixture-approval")
    p.ledger.open_run(ctx, unit=SALE, budget=10**12, max_calls=10, max_attempts=10, deadline=p.ticks[0] + 900)
    call = p.ledger.reserve_call(ctx, logical_call_id=name, amount=10**9, price_id="fixture-sale", deadline=p.ticks[0] + 600)
    ticket = p.ledger.prepare_attempt(call, attempt_id=name, owner="fixture-worker", cost_price_id="fixture-cost",
                                      requested_model="fixture-request")
    admission = CostAdmission(p.ledger, current_policy=p.costs.policy)
    p.ledger.dispatch(ticket, admission=lambda t, a: admission.admit(t, a, amount=bound, expires_at=p.ticks[0] + 120))
    if settle:
        p.ledger.settle_trusted_usage(ticket.attempt_key, UsageEvidence(
            "deepseek", "fixture-request-" + name, "fixture-final-" + name, Usage(input_tokens, 0, output_tokens, 0),
            "fixture-route", "fixture-capability", p.ticks[0]))
    return ticket


# ---------------------------------------------------------------- 3. upstream balance monitor
def test_balance_poll_sends_the_sealed_key_only_in_the_header_and_records_amounts(platform, caplog):
    p = platform
    caplog.set_level(logging.DEBUG)
    p.replies.append((200, balance_body("250.01")))  # Above the 200-yuan warning threshold.
    reading = poll(p)
    assert (reading["status"], reading["code"], reading["total_units"], reading["unit"], reading["is_available"]) == (
        "ok", "ok", 250_010_000, "CNY", 1)
    assert [str(r.url) for r in p.seen] == [BALANCE_URL] and p.seen[0].method == "GET"
    assert p.seen[0].headers["authorization"] == "Bearer " + KEY
    stored = json.dumps(p.store.all("SELECT * FROM model_risk_balance_readings"))
    assert KEY not in stored and KEY not in caplog.text
    assert RiskControls(p.store, environment="test").breaker()["state"] == "closed"
    assert alerts(p, "billing.risk.low_balance") == []


def test_balance_poll_follows_the_governed_test_upstream_and_refuses_any_other(platform):
    # The same origin rule as the chat gateway: the official origin, or (test environment only)
    # a loopback FIXTURE double. The monitor started with the gateway's origin polls that double.
    p = platform
    double = "http://127.0.0.1:31791"
    monitor = RiskMonitor(p.store, environment="test", transport=p.transport, clock=p.clock, base_url=double + "/")
    assert monitor.base_url == double
    assert UpstreamBalance(p.store, environment="test", base_url=BALANCE_URL[:-len("/user/balance")]).base_url == \
        BALANCE_URL[:-len("/user/balance")]
    assert monitor.run_once().ran[0] == "balance"
    assert [str(r.url) for r in p.seen] == [double + "/user/balance"]
    for environment, origin in (("live", double), ("test", "http://example.test:31791"),
                                ("test", "http://127.0.0.1:31791/proxy"), ("test", "http://127.0.0.1")):
        with pytest.raises(ValueError):
            UpstreamBalance(p.store, environment=environment, base_url=origin)
        with pytest.raises(ValueError):
            RiskMonitor(p.store, environment=environment, base_url=origin)
    assert len(p.seen) == 1


def test_upstream_errors_are_recorded_as_codes_without_the_key(platform, caplog):
    p = platform
    caplog.set_level(logging.DEBUG)
    p.replies.extend([(401, {"error": {"message": "Authentication Fails"}}),
                      (200, httpx.ConnectError("refused while sending Bearer " + KEY)),
                      (200, {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "9.00"}]}),
                      (200, {"is_available": "yes"})])
    codes = [poll(p)["code"] for _ in range(4)]
    assert codes == ["upstream_http_401", "upstream_unreachable", "currency_mismatch", "upstream_bad_response"]
    assert KEY not in json.dumps(p.store.all("SELECT * FROM model_risk_balance_readings")) and KEY not in caplog.text
    assert RiskControls(p.store, environment="test").breaker()["state"] == "closed"


def test_low_balance_alerts_once_per_day_without_closing_dispatch(platform):
    p = platform
    p.replies.extend([(200, balance_body("150.00")), (200, balance_body("149.00"))])
    poll(p)
    p.ticks[0] += 300
    poll(p)
    assert len(alerts(p, "billing.risk.low_balance")) == 1
    assert RiskControls(p.store, environment="test").breaker()["state"] == "closed"


@pytest.mark.parametrize("body,code", [(balance_body("49.99"), "upstream_balance_below_close"),
                                       (balance_body("900.00", available=False), "upstream_unavailable")])
def test_hard_threshold_or_unavailable_upstream_closes_paid_dispatch(platform, body, code):
    p = platform
    p.replies.append((200, body))
    poll(p)
    breaker = RiskControls(p.store, environment="test").breaker()
    assert (breaker["state"], breaker["source"], breaker["reason_code"]) == ("open", "balance", code)
    assert len(alerts(p, "billing.risk.breaker_opened")) == 1
    assert [e["event_id"] for e in journal.read(p.store.root) if e["kind"] == "model_breaker_opened"] == [breaker["id"]]
    with pytest.raises(Exception) as refused:
        spend(p, "after-breaker", input_tokens=1)
    assert getattr(refused.value, "code", None) == "risk_breaker_open"


def test_unconfigured_provider_is_reported_honestly_and_nothing_is_requested(tmp_path):
    store = Store(tmp_path / "isolated-unconfigured")
    seen = []
    reading = UpstreamBalance(store, environment="test", transport=httpx.MockTransport(
        lambda r: seen.append(r) or httpx.Response(200))).poll()
    assert (reading["status"], reading["code"]) == ("not_configured", "cost_policy_missing") and seen == []


# ---------------------------------------------------------------- 5. reconciliation
def test_daily_reconciliation_compares_balance_change_with_ledger_cost(platform):
    p = platform
    p.ticks[0] = DAY0 - 60
    p.replies.append((200, balance_body("110.00")))
    poll(p)
    p.ticks[0] = DAY0 + 3600
    spend(p, "settled", input_tokens=1000, output_tokens=1000)  # 1,000,000 + 2,000,000 = 3.000000 yuan
    spend(p, "unknown", settle=False, bound=7)                   # pending exposure: 0..7 more
    p.ticks[0] = DAY0 + DAY + 60
    p.replies.append((200, balance_body("106.9990")))            # 3.0010 yuan spent upstream
    poll(p)
    p.ticks[0] = DAY0 + DAY + 700
    row = Reconciliation(p.store, environment="test", clock=p.clock).daily(DAY0)
    # Drift is measured from the recorded range [3.000000, 3.000007]: 1000 - 7 above its top.
    assert (row["status"], row["code"], row["upstream_units"], row["ledger_cost_n"], row["ledger_cost_d"],
            row["pending_units"], row["drift_n"], row["tolerance_units"]) == (
        "ok", "within_tolerance", 3_001_000, "3000000", "1", 7, "993", 1000)
    assert alerts(p, "billing.risk.reconciliation") == []


@pytest.mark.parametrize("spent,drift,status", [
    ("4.20", "0", "ok"),               # within [cost 3, cost 3 + unknown bound 2]: the stopped call cost 1.2
    ("5.00", "0", "ok"),               # the stopped call cost its whole bound
    ("5.50", "500000", "drift"),        # above any bound: billed more than recorded (e.g. an error answer)
    ("2.50", "-500000", "drift"),       # below the known cost
])
def test_reconciliation_treats_unknown_usage_as_a_range_not_as_drift(platform, spent, drift, status):
    # A call stopped mid-stream has no usage on our side but may cost up to its bound upstream.
    p = platform
    p.ticks[0] = DAY0 - 60
    p.replies.append((200, balance_body("110.00")))
    poll(p)
    p.ticks[0] = DAY0 + 3600
    spend(p, "settled", input_tokens=1000, output_tokens=1000)  # 3.000000 yuan
    spend(p, "stopped", settle=False, bound=2_000_000)          # unknown, at most 2.000000 yuan
    p.ticks[0] = DAY0 + DAY + 60
    from decimal import Decimal
    p.replies.append((200, balance_body(str(Decimal("110.00") - Decimal(spent)))))
    poll(p)
    p.ticks[0] = DAY0 + DAY + 700
    row = Reconciliation(p.store, environment="test", clock=p.clock).daily(DAY0)
    assert (row["status"], row["pending_units"], row["drift_n"]) == (status, 2_000_000, drift)


def test_unrecorded_topup_shows_drift_and_alerts_and_a_recorded_one_reconciles(platform):
    p = platform
    p.ticks[0] = DAY0 - 60
    p.replies.append((200, balance_body("110.00")))
    poll(p)
    p.ticks[0] = DAY0 + 3600
    spend(p, "settled", input_tokens=1000, output_tokens=1000)
    p.ticks[0] = DAY0 + DAY + 60
    p.replies.append((200, balance_body("207.00")))  # 100 yuan topped up, 3 spent.
    poll(p)
    p.ticks[0] = DAY0 + DAY + 700
    reconciliation = Reconciliation(p.store, environment="test", clock=p.clock)
    drift = reconciliation.daily(DAY0)
    assert (drift["status"], drift["upstream_units"], drift["drift_n"]) == ("drift", -97_000_000, "-100000000")
    assert len(alerts(p, "billing.risk.reconciliation")) == 1
    with pytest.raises(ServiceError):
        reconciliation.record_topup(amount_units=100_000_000, topped_up_at=DAY0 + 7200, actor_id=p.owner, reason="short")
    reconciliation.record_topup(amount_units=100_000_000, topped_up_at=DAY0 + 7200, actor_id=p.owner,
                                reason="upstream top-up receipt fixture")
    again = reconciliation.daily(DAY0, trigger="admin", actor_id=p.owner)
    assert (again["status"], again["topup_units"], again["drift_n"]) == ("ok", 100_000_000, "0")


def test_missing_boundary_reading_is_incomplete_not_ok(platform):
    p = platform
    p.ticks[0] = DAY0 + DAY + 700
    row = Reconciliation(p.store, environment="test", clock=p.clock).daily(DAY0)
    assert (row["status"], row["code"]) == ("incomplete", "balance_reading_missing")
    assert len(alerts(p, "billing.risk.reconciliation")) == 1
    with pytest.raises(ServiceError):
        Reconciliation(p.store, environment="test", clock=p.clock).daily(DAY0 + DAY)  # Not over yet.


def test_ledger_audit_reports_cache_differences_without_repairing(platform):
    p = platform
    reconciliation = Reconciliation(p.store, environment="test", clock=p.clock)
    assert reconciliation.ledger_audit()["status"] == "ok"
    p.store.execute("UPDATE billing_balances SET amount=amount+1 WHERE account_id=(SELECT id FROM billing_accounts "
                    "WHERE kind='wallet' AND owner=?)", (p.payer,))  # Isolated fixture corruption of a cache.
    row = reconciliation.ledger_audit()
    assert (row["status"], row["differences"]) == ("drift", 1)
    assert len(alerts(p, "billing.risk.ledger_audit")) == 1
    assert reconciliation.ledger_audit()["differences"] == 1  # Compare only: nothing was rebuilt.


# ---------------------------------------------------------------- scheduling and the other alerts
def test_monitor_round_runs_each_due_job_once_under_one_lease(platform):
    p = platform
    p.ticks[0] = DAY0 + 700
    monitor = RiskMonitor(p.store, environment="test", transport=p.transport, clock=p.clock)
    first = monitor.run_once()
    assert not first.failures and first.ran == ["balance", "pending_exposure", "daily", "ledger_audit"]
    second = monitor.run_once()
    assert second.ran == ["pending_exposure"] and len(p.seen) == 1
    assert RiskMonitor(p.store, environment="test", transport=p.transport, clock=p.clock).run_once().busy
    p.ticks[0] += 300
    assert monitor.run_once().ran == ["balance", "pending_exposure"] and len(p.seen) == 2


def test_unconfirmed_usage_exposure_above_threshold_alerts_once_a_day(platform):
    p = platform
    monitor = RiskMonitor(p.store, environment="test", transport=p.transport, clock=p.clock)
    spend(p, "pending-1", settle=False, bound=4_000_000)
    monitor.run_once()
    assert alerts(p, "billing.risk.pending_exposure") == []  # Equal to the threshold is not above it.
    spend(p, "pending-2", settle=False, bound=1)
    monitor.run_once()
    monitor.run_once()
    assert len(alerts(p, "billing.risk.pending_exposure")) == 1


def test_payment_webhook_failure_hook_alerts_with_codes_only(platform):
    p = platform
    assert alert_payment_webhook_failure(p.store, environment="test", provider="stripe", code="signature_invalid") == 1
    assert alert_payment_webhook_failure(p.store, environment="test", provider="stripe", code="signature_invalid") == 1
    rows = alerts(p, "billing.risk.payment_webhook_failed")
    assert len(rows) == 1 and json.loads(rows[0]["params"]) == {"provider": "stripe", "code": "signature_invalid"}


def test_alert_wording_exists_in_every_language():
    from synwevo_core.notifications import CATALOGUE, LOCALES
    kinds = [k for k in CATALOGUE if k.startswith("billing.risk.")]
    assert len(kinds) == 8
    for kind in kinds:
        assert all(CATALOGUE[kind][part][loc] for part in ("title", "body") for loc in LOCALES)
