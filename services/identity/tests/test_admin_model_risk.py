"""Real isolated HTTP administration of the paid-model risk controls; no funds or provider traffic."""
from __future__ import annotations

import uuid

import pytest

from synwevo_core.billing.risk import RiskControls

from .test_admin_operations import post, world  # noqa: F401 - fixture

BASE = "/api/admin/billing/risk"
REASON = "Fixture: reviewed paid model risk state"


@pytest.fixture(autouse=True)
def billing_environment(monkeypatch):
    monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "test")


def change(**extra):
    return {"reason": REASON, "idempotency_key": uuid.uuid4().hex, **extra}


def audits(w, action):
    return w["store"].all("SELECT * FROM audit_events WHERE action=? ORDER BY seq", (action,))


def test_overview_is_honest_before_any_policy_and_hidden_from_ordinary_accounts(world):
    w = world
    r = w["admin"].get(BASE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["environment"], body["configured"], body["allowlist_required"], body["allowlist_source"]) == (
        "test", False, False, "default")
    assert body["breaker"]["state"] == "closed" and body["balance"] is None
    assert w["ordinary"].get(BASE).status_code == 404
    assert post(w["ordinary"], BASE + "/breaker/open", change()).status_code == 404


def test_authorization_comes_before_configuration(request, monkeypatch):
    # Whether billing is configured is deployment state; a change route answers the caller's
    # authority first. An auditor (platform.config.view only) is refused the permission it lacks,
    # never told "not configured"; an account without any platform role sees 404 as everywhere.
    monkeypatch.delenv("SYNWEVO_BILLING_ENVIRONMENT")
    w = request.getfixturevalue("world")
    from synwevo_core.platform_access import PlatformAccess
    from .test_admin_operations import PW
    user = w["ident"].create_user("risk-auditor@example.test", PW, status="active")
    w["store"].execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    PlatformAccess(w["store"]).host_grant(user["id"], "auditor", source="fixture:risk-order",
                                          reason="isolated authorization order fixture")
    auditor = w["login"](user)
    bodies = {"/breaker/open": change(), "/breaker/close": change(), "/suspensions/rsus_fixture/lift": change(),
              "/allowlist": change(payer_kind="user", email="nobody@example.test"),
              "/allowlist/rallow_fixture/remove": change(), "/topups": change(amount_units="1", topped_up_at=0),
              "/reconciliations/run": change(kind="ledger_audit")}
    for path, body in bodies.items():
        r = post(auditor, BASE + path, body)
        assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", (path, r.text)
        assert post(w["ordinary"], BASE + path, body).status_code == 404, path
    # A platform administrator is told the honest reason once authorized.
    refused = post(w["admin"], BASE + "/breaker/open", change())
    assert refused.status_code == 503 and refused.json()["error"] == "billing_not_configured"


def test_breaker_open_and_close_are_audited_idempotent_and_close_needs_reauth(world):
    w = world
    body = change()
    opened = post(w["admin"], BASE + "/breaker/open", body)
    assert opened.status_code == 200, opened.text
    assert opened.json()["item"]["state"] == "open" and opened.json()["item"]["source"] == "admin"
    assert post(w["admin"], BASE + "/breaker/open", body).json() == opened.json()  # Same key, same result.
    conflict = post(w["admin"], BASE + "/breaker/open", {**body, "reason": REASON + " (changed)"})
    assert conflict.status_code == 409 and conflict.json()["error"] == "idempotency_conflict"
    again = post(w["admin"], BASE + "/breaker/open", change())
    assert again.status_code == 409 and again.json()["error"] == "risk_breaker_open"
    fresh = w["login"](w["root"])  # Signed in, not re-authenticated for administration.
    refused = post(fresh, BASE + "/breaker/close", change())
    assert refused.status_code == 403
    closed = post(w["admin"], BASE + "/breaker/close", change())
    assert closed.status_code == 200, closed.text
    assert closed.json()["item"]["state"] == "closed"
    assert [a["result"] for a in audits(w, "admin.model_risk.breaker_open")] == ["ok", "denied", "denied"]
    assert len(audits(w, "admin.model_risk.breaker_close")) >= 1
    history = w["admin"].get(BASE + "/breaker").json()["items"]
    assert [e["state"] for e in history] == ["closed", "open"]


def test_reason_is_required_and_submitted_values_are_not_echoed(world):
    w = world
    r = post(w["admin"], BASE + "/breaker/open", {"reason": "short", "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 400 and "short" not in r.text


def test_allowlist_add_by_email_and_remove(world):
    w = world
    added = post(w["admin"], BASE + "/allowlist", change(payer_kind="user", email=w["target"]["email"].upper()))
    assert added.status_code == 200, added.text
    item = added.json()["item"]
    assert item["payer_id"] == w["target"]["id"] and item["payer_label"] == w["target"]["email"]
    duplicate = post(w["admin"], BASE + "/allowlist", change(payer_kind="user", email=w["target"]["email"]))
    assert duplicate.status_code == 409 and duplicate.json()["error"] == "risk_allowlist_exists"
    missing = post(w["admin"], BASE + "/allowlist", change(payer_kind="user", email="nobody@example.test"))
    assert missing.status_code == 404
    listed = w["admin"].get(BASE + "/allowlist").json()["items"]
    assert [i["id"] for i in listed] == [item["id"]]
    removed = post(w["admin"], BASE + f"/allowlist/{item['id']}/remove", change())
    assert removed.status_code == 200 and removed.json()["item"]["removed_by"] == w["root"]["id"]
    assert w["admin"].get(BASE + "/allowlist").json()["items"] == []


def test_lifting_a_brake_suspension_records_who_and_why(world):
    w = world
    row = RiskControls(w["store"], environment="test").record_trip({
        "scope_kind": "user", "scope_id": w["target"]["id"], "reason_code": "payer_velocity", "unit": "FIX_COST",
        "window_s": 600, "threshold_units": 10, "observed_units": 11, "actor_user_id": w["target"]["id"]})
    active = w["admin"].get(BASE + "/suspensions").json()["items"]
    assert [(s["id"], s["payer_label"]) for s in active] == [(row["id"], w["target"]["email"])]
    lifted = post(w["admin"], BASE + f"/suspensions/{row['id']}/lift", change())
    assert lifted.status_code == 200, lifted.text
    assert lifted.json()["item"]["lift_reason"] == REASON
    assert w["admin"].get(BASE + "/suspensions").json()["items"] == []
    assert len(w["admin"].get(BASE + "/suspensions?state=all").json()["items"]) == 1
    twice = post(w["admin"], BASE + f"/suspensions/{row['id']}/lift", change())
    assert twice.status_code == 409 and twice.json()["error"] == "risk_suspension_lifted"


def test_reconciliation_run_and_topup_need_a_published_cost_policy(world):
    w = world
    audit = post(w["admin"], BASE + "/reconciliations/run", change(kind="ledger_audit"))
    assert audit.status_code == 200, audit.text
    assert audit.json()["item"]["status"] == "ok" and audit.json()["item"]["trigger"] == "admin"
    daily = post(w["admin"], BASE + "/reconciliations/run", change(kind="daily", day="2020-01-01"))
    assert daily.status_code == 200 and daily.json()["item"]["code"] == "cost_policy_missing"
    topup = post(w["admin"], BASE + "/topups", change(amount_units="100", topped_up_at=1))
    assert topup.status_code == 409 and topup.json()["error"] == "model_cost_policy_required"
    items = w["admin"].get(BASE + "/reconciliations").json()["items"]
    assert sorted(i["kind"] for i in items) == ["daily", "ledger_audit"]  # Same second: order by id.
