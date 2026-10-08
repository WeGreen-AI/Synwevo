"""Platform money administration on the real accounts application (fixtures only).

Payment settings are written through the real route with fixture keys shaped like Stripe
test-mode keys (not real credentials); grants and freezes go through the real ledger. No
provider, model or network is contacted; isolated temporary data directory.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from synwevo_core.billing import DisputeNotice, Ledger, PaymentReceipt, Unit
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import now

from .platform_fixture import make_platform_admin

PW = "admin-payments-fixture-passphrase-1"
FIXTURE_SECRET_KEY = "sk_test_" + "fixture0not0a0real0key0000"
FIXTURE_LIVE_KEY = "sk_live_" + "fixture0not0a0real0key0000"
FIXTURE_WEBHOOK_SECRET = "whsec_" + "fixture0not0a0real0endpoint0secret"
CNY = Unit("CNY", 6)
SPEC = {"enabled": True, "operator_name": "Synwevo Fixture Operator",
        "packages": [{"id": "cny-10", "amount_minor": 1000, "label": None}],
        "custom_amount": False, "min_amount_minor": 500, "max_amount_minor": 20000,
        "velocity": {"max_orders_per_hour": 5, "max_orders_per_day": 10, "max_pending": 3,
                     "max_amount_per_day_minor": 100000}}
REASON = "fixture: configure the payment provider"


@pytest.fixture
def world(tmp_path, monkeypatch):
    for key, value in {"DATA_DIR": str(tmp_path / "d"), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "disabled", "PUBLIC_URL": "http://testserver", "ISSUER": "http://accounts.testserver",
                       "BILLING_ENVIRONMENT": "test", "TEST_INSTANCE": "1",
                       "BILLING_WALLET_UNITS": json.dumps([{"code": "CNY", "scale": 6}]),
                       "SECRET": "synthetic-admin-payments-fixture-key-123456789"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    store, ident = api.state.identity.store, api.state.identity
    people = {}
    for key in ("root", "fin", "aud", "plain"):
        u = ident.create_user(f"{key}@example.test", PW, status="active")
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
        people[key] = ident.get_user(u["id"])
    make_platform_admin(store, people["root"])
    make_platform_admin(store, people["fin"], role="finance")
    make_platform_admin(store, people["aud"], role="auditor")
    clients = []

    def login(key, *, reauth=True):
        c = TestClient(api)
        assert c.post("/api/auth/login", json={"email": people[key]["email"], "password": PW}).status_code == 200
        if reauth:
            r = post(c, "/api/auth/reauth", {"password": PW, "scope": "admin"})
            assert r.status_code == 200, r.text
        clients.append(c)
        return c

    yield {"api": api, "store": store, "ident": ident, "people": people, "login": login}
    for c in clients:
        c.close()


def post(client, path, body):
    return client.post(path, json=body, headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


def publish(c, *, revision=0, spec=SPEC, credential=None, key="fixture_publish_0001"):
    return post(c, "/api/admin/billing/payments/publish", {
        "expected_revision": revision, "spec": spec, "reason": REASON, "idempotency_key": key,
        "credential": credential if credential is not None else
        {"action": "replace", "secret_key": FIXTURE_SECRET_KEY, "webhook_secret": FIXTURE_WEBHOOK_SECRET}})


def test_finance_publishes_payment_settings_and_the_secrets_never_come_back(world):
    c = world["login"]("fin")
    before = c.get("/api/admin/billing/payments").json()
    assert before["status"] == "unconfigured" and before["credential_configured"] is False
    assert before["webhook_url"] == "http://accounts.testserver/api/auth/payments/stripe/webhook"
    assert "charge.dispute.created" in before["webhook_events"]
    r = publish(c)
    assert r.status_code == 200, r.text
    state = r.json()["state"]
    assert state["revision"] == 1 and state["credential_configured"] is True and state["spec"]["enabled"] is True
    # the same request replayed is the same operation; a changed one under the same key conflicts
    assert publish(c).json()["operation_id"] == r.json()["operation_id"]
    assert publish(c, spec={**SPEC, "operator_name": "Other"}).status_code == 409
    store = world["store"]
    everything = json.dumps([store.all(f"SELECT * FROM {t}") for t in
                             ("audit_events", "admin_operations", "payment_settings_versions")])
    for secret in (FIXTURE_SECRET_KEY, FIXTURE_WEBHOOK_SECRET):
        assert secret not in r.text and secret not in c.get("/api/admin/billing/payments").text
        assert secret not in everything, "密钥只以密封形式存储，不进入审计、操作记录或版本"
    assert store.one("SELECT secret_enc FROM payment_credentials")["secret_enc"] not in (None, FIXTURE_SECRET_KEY)
    # the top-up page now offers the package
    assert world["api"].state.topups.configuration()[0].spec["packages"][0]["id"] == "cny-10"


def test_a_live_key_is_refused_on_a_test_deployment_and_enabling_needs_credentials(world):
    c = world["login"]("fin")
    r = publish(c, credential={"action": "replace", "secret_key": FIXTURE_LIVE_KEY, "webhook_secret": FIXTURE_WEBHOOK_SECRET})
    assert r.status_code == 400 and r.json()["error"] == "payment_key_mode"
    assert FIXTURE_LIVE_KEY not in r.text
    r = publish(c, credential={"action": "keep"})
    assert r.status_code == 409 and r.json()["error"] == "payment_credential_required"
    r = publish(c, spec={**SPEC, "packages": [{"id": "x", "amount_minor": 100, "label": None}]})
    assert r.status_code == 400 and r.json()["error"] == "payment_config_invalid", "套餐金额必须在上下限之内"
    assert world["store"].all("SELECT * FROM payment_settings_versions") == []


def test_channels_are_validated_governed_versioned_and_idempotent(world):
    c = world["login"]("fin")
    for invalid in ([], ["card", "card"], ["unknown"], ["card", 1], None, "card", ["Card"]):
        r = publish(c, spec={**SPEC, "payment_methods": invalid})
        assert r.status_code == 400 and r.json()["error"] == "payment_config_invalid", r.text
    assert world["store"].all("SELECT * FROM payment_settings_versions") == []
    spec = {**SPEC, "payment_methods": ["alipay", "card"]}
    r = publish(c, spec=spec)
    assert r.status_code == 200, r.text
    assert r.json()["state"]["spec"]["payment_methods"] == ["card", "alipay"]
    assert publish(c, spec=spec).json()["operation_id"] == r.json()["operation_id"]
    assert publish(c, spec={**SPEC, "payment_methods": ["card"]}).status_code == 409
    assert c.get("/api/admin/billing/payments").json()["spec"]["payment_methods"] == ["card", "alipay"]
    assert len(world["store"].all("SELECT * FROM payment_settings_versions")) == 1
    unchanged = publish(c, revision=1, spec={**SPEC, "payment_methods": ["card", "alipay"]},
                        credential={"action": "keep"}, key="fixture_same_methods_01")
    assert unchanged.status_code == 409 and unchanged.json()["error"] == "payment_no_change"


def test_legacy_payment_spec_retains_its_shape_and_no_change_semantics(world):
    c = world["login"]("fin")
    assert publish(c).status_code == 200
    assert c.get("/api/admin/billing/payments").json()["spec"] == SPEC
    r = publish(c, revision=1, credential={"action": "keep"}, key="fixture_legacy_no_change")
    assert r.status_code == 409 and r.json()["error"] == "payment_no_change"
    raw = world["store"].one("SELECT spec_json FROM payment_settings_versions")
    assert "payment_methods" not in json.loads(raw["spec_json"])


def test_legacy_editor_cannot_reset_explicit_channels_by_omitting_the_field(world):
    c = world["login"]("fin")
    assert publish(c, spec={**SPEC, "payment_methods": ["card"]}).status_code == 200
    same = publish(c, revision=1, credential={"action": "keep"}, key="fixture_legacy_keep_channels")
    assert same.status_code == 409 and same.json()["error"] == "payment_no_change"
    updated = publish(c, revision=1, spec={**SPEC, "operator_name": "Updated fixture merchant"},
                      credential={"action": "keep"}, key="fixture_legacy_edit_operator")
    assert updated.status_code == 200, updated.text
    assert updated.json()["state"]["spec"]["payment_methods"] == ["card"]
    assert updated.json()["state"]["revision"] == 2


def test_only_the_billing_permissions_reach_money_and_writes_need_a_fresh_confirmation(world):
    aud = world["login"]("aud")
    assert aud.get("/api/admin/billing/finance").status_code == 403
    assert publish(aud).json()["error"] == "platform_permission_denied"
    stale = world["login"]("fin", reauth=False)
    assert stale.get("/api/admin/billing/finance").status_code == 200
    r = publish(stale)
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"
    plain = world["login"]("plain", reauth=False)
    assert plain.get("/api/admin/billing/finance").status_code == 404


def test_an_admin_grant_needs_a_reason_and_lands_as_a_grant(world):
    c = world["login"]("fin")
    plain = world["people"]["plain"]
    body = {"payer_kind": "user", "email": plain["email"], "amount_minor": 250,
            "reason": "compensation for an interrupted run", "idempotency_key": "fixture_grant_000001"}
    assert post(c, "/api/admin/billing/grants", {**body, "reason": "short"}).json()["error"] == "missing_reason"
    r = post(c, "/api/admin/billing/grants", body)
    assert r.status_code == 200, r.text
    assert post(c, "/api/admin/billing/grants", body).json() == r.json(), "幂等：同一请求只赠送一次"
    ledger = Ledger(world["store"], environment="test", namespace="read")
    uid = plain["id"]
    assert ledger.balance_for_user(requester_user_id=uid, payer_user_id=uid, unit=CNY).booked == 250 * 10**4
    row = world["store"].one("SELECT kind,actor_id,reason FROM billing_grants")
    assert row == {"kind": "admin", "actor_id": world["people"]["fin"]["id"], "reason": body["reason"]}
    audit = world["store"].one("SELECT * FROM audit_events WHERE action='admin.billing.grant' AND result='ok'")
    assert json.loads(audit["meta"])["reason"] == body["reason"]
    missing = post(c, "/api/admin/billing/grants", {**body, "email": "nobody@example.test", "idempotency_key": "fixture_grant_000002"})
    assert missing.status_code == 404


def test_the_finance_view_shows_money_metadata_and_lifts_a_freeze(world):
    store, people = world["store"], world["people"]
    uid = people["plain"]["id"]
    # Fixture facts through the real ledger: a verified payment, a chat spend, and a dispute.
    receipt = PaymentReceipt("test", "stripe", "stripe-platform", "pi_fixture", "evt_fixture", "top_fixture",
                             1000, "CNY", "stripe-inbox:evt_fixture", "a" * 64)
    notice = DisputeNotice("test", "stripe", "stripe-platform", "dp_fixture", "evt_dp", "pi_fixture", 1000, "CNY",
                           "stripe-inbox:evt_dp", "b" * 64)
    ledger = Ledger(store, environment="test", namespace="wallet-topup", verify_payment=lambda t, r: r == receipt,
                    verify_dispute=lambda t, n: n == notice)
    ledger.create_payment_order(order_id="top_fixture", payer_user_id=uid, provider="stripe", merchant_id="stripe-platform",
                                amount_minor=1000, currency="CNY", unit=CNY, credit_units=10**7,
                                exchange_version="cny-fen-identity-v1")
    ledger.credit_verified_payment(receipt)
    store.execute("INSERT INTO payment_topups(order_id,environment,provider,payer_kind,payer_id,initiator_id,amount_minor,"
                  "currency,unit,credit_units,settings_revision,status,created_at,credited_at,method) VALUES "
                  "('top_fixture','test','stripe','user',?,?,1000,'CNY','CNY',10000000,1,'paid',?,?,'alipay')",
                  (uid, uid, int(time.time()), int(time.time())))
    chargeback = ledger.record_chargeback(notice)
    c = world["login"]("fin")
    view = c.get("/api/admin/billing/finance").json()
    assert view["configured"] is True and view["unit"] == {"code": "CNY", "scale": 6}
    assert view["payments"][0]["order_id"] == "top_fixture" and view["payments"][0]["method"] == "alipay"
    assert view["totals"]["paid_minor"] == 1000 and view["totals"]["frozen_wallets"] == 1
    assert view["chargebacks"][0]["frozen"] is True and view["chargebacks"][0]["payer_email"] == people["plain"]["email"]
    assert view["current"]["unused_units"] == 0 and view["base_date_timezone"] == "Asia/Tokyo"
    assert [d["date"][5:] for d in view["base_dates"]][:2] in (["03-31", "09-30"], ["09-30", "03-31"])
    r = post(c, f"/api/admin/billing/freezes/{chargeback['freeze_id']}/lift", {"reason": "dispute won at the provider"})
    assert r.status_code == 200 and r.json()["freeze"]["lifted_by"] == people["fin"]["id"]
    assert c.get("/api/admin/billing/finance").json()["totals"]["frozen_wallets"] == 0
    assert post(c, "/api/admin/billing/freezes/nope/lift", {"reason": "dispute won at the provider"}).status_code == 404


def test_base_dates_are_march_31_and_september_30_in_japan():
    from synwevo_identity.admin_payments import base_dates
    # 2026-10-05 12:00 UTC: the last ended base dates are 2026-09-30, 2026-03-31, 2025-09-30, 2025-03-31
    at = 1_791_201_600
    dates = base_dates(at)
    assert [d for d, _ in dates] == ["2026-09-30", "2026-03-31", "2025-09-30", "2025-03-31"]
    # 2026-09-30 24:00 JST is 2026-09-30 15:00 UTC
    assert dates[0][1] == 1_790_780_400 - 1


def test_an_existing_grants_table_learns_the_finance_role(tmp_path, monkeypatch):
    """The previous release's exact table text is widened in place (schema_widen), offers kept."""
    import sqlite3
    from synwevo_core.identity import Identity
    from synwevo_core.store import Store
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    root = tmp_path / "isolated-old-grants"
    root.mkdir()
    old = sqlite3.connect(root / "docweave.sqlite3")
    old.executescript("""
CREATE TABLE platform_role_grants (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL
    CHECK (role IN ('super_admin','access_admin','support','auditor','api_platform_admin')),
  granted_by TEXT NOT NULL,
  granted_at REAL NOT NULL,
  reason TEXT NOT NULL,
  expires_at REAL,
  revoked_at REAL, revoked_by TEXT, revoke_reason TEXT
);
CREATE UNIQUE INDEX ux_platform_grants_live ON platform_role_grants(user_id, role) WHERE revoked_at IS NULL;
""")
    old.close()
    store = Store(root)
    sql = store.one("SELECT sql FROM sqlite_master WHERE name='platform_role_grants'")["sql"]
    assert "'finance'" in sql and "offer_id" in sql
    names = {r["name"] for r in store.all("SELECT name FROM sqlite_master WHERE tbl_name='platform_role_grants'")}
    assert {"ux_platform_grants_live", "ix_platform_grant_offer"} <= names
    user = Identity(store).create_user("fin-old@example.test", None, status="active", with_personal_workspace=False)
    assert make_platform_admin(store, user, role="finance")
    assert store.one("SELECT role FROM platform_role_grants WHERE user_id=?", (user["id"],))["role"] == "finance"
