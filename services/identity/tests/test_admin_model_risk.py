"""Real isolated HTTP administration of the paid-model risk controls; no funds or provider traffic."""
from __future__ import annotations

import uuid

import pytest

from synwevo_core.billing.risk import RiskControls
from synwevo_core.organizations import Organizations

from .platform_fixture import holding_exactly
from .test_admin_operations import PW, post, world  # noqa: F401 - fixture

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
    user = w["ident"].create_user("risk-auditor@example.test", PW, status="active")
    w["store"].execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    PlatformAccess(w["store"]).host_grant(user["id"], "auditor", source="fixture:risk-order",
                                          reason="isolated authorization order fixture")
    auditor = w["login"](user)
    bodies = {"/breaker/open": change(), "/breaker/close": change(), "/suspensions/rsus_fixture/lift": change(),
              "/allowlist": change(payer_kind="user", email="nobody@example.test"),
              "/allowlist/rallow_fixture/remove": change(), "/topups": change(amount_units="1", topped_up_at=0),
              "/reconciliations/run": change(kind="ledger_audit")}
    custodian = holding_exactly(monkeypatch, w["store"], w["ident"].create_user("risk-custodian@example.test", PW, status="active"),
                                {"platform.config.view", "platform.models.credential"})  # FIXTURE, this test only
    credential = w["login"](custodian)
    for path, body in bodies.items():
        for who, client in (("auditor", auditor), ("holder of the credential permission", credential)):
            r = post(client, BASE + path, body)
            assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", (who, path, r.text)
            # authz-6: the risk controls are the pricing permission's, never platform.config.manage
            assert r.json()["detail"]["permission"] == "platform.models.pricing", (who, path)
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


# ---------------------------------------------------------------- a delegated holder (authz-6 review)
PRICING_READER = frozenset({"platform.config.view", "platform.models.pricing"})


def delegate(w, monkeypatch, permissions, name, *, carrier="support"):
    """FIXTURE (`holding_exactly`): the shape D20 says a delegated role needs. Re-authenticated, so
    what a route answers below is about the permission and not the freshness."""
    user = holding_exactly(monkeypatch, w["store"], w["ident"].create_user(f"{name}@example.test", PW, status="active"),
                           permissions, carrier=carrier)
    client = w["login"](user)
    w["reauth"](client)
    return user, client


def trip(w, kind, payer):
    return RiskControls(w["store"], environment="test").record_trip({
        "scope_kind": kind, "scope_id": payer, "reason_code": "payer_velocity", "unit": "FIX_COST",
        "window_s": 600, "threshold_units": 10, "observed_units": 11, "actor_user_id": None})


def test_adding_a_person_by_address_needs_the_account_list_right(world, monkeypatch):
    """The account list is refused to a pricing holder without `platform.users.view`; the allowlist
    must not answer the same question: whether an address has an account, and that account's id."""
    w = world
    user, holder = delegate(w, monkeypatch, PRICING_READER, "pricing-only")
    assert holder.get("/api/admin/users").json()["detail"]["permission"] == "platform.users.view"
    known = post(holder, BASE + "/allowlist", change(payer_kind="user", email=w["target"]["email"]))
    unknown = post(holder, BASE + "/allowlist", change(payer_kind="user", email="nobody-here@example.test"))
    for answer in (known, unknown):
        assert answer.status_code == 403 and answer.json()["error"] == "platform_permission_denied", answer.text
        assert answer.json()["detail"]["permission"] == "platform.users.view"
        assert w["target"]["id"] not in answer.text
    assert not w["store"].all("SELECT * FROM model_risk_allowlist")
    denied = w["store"].all("SELECT object_id FROM audit_events WHERE action='admin.access.denied' AND actor_id=? AND object_id='platform.users.view'",
                            (user["id"],))
    assert len(denied) == 1, "the refused lookup is on the record, and a page that keeps asking cannot flood it"
    # the same refusal before any re-authentication is asked for: the permission comes first
    fresh = w["login"](user)
    refused = post(fresh, BASE + "/allowlist", change(payer_kind="user", email=w["target"]["email"]))
    assert refused.status_code == 403 and refused.json()["error"] == "platform_permission_denied", refused.text
    # an organization goes by its id and looks nothing up by address
    org = Organizations(w["store"], w["ident"]).create(w["target"]["id"], "Fixture payer organization", "Fixture workspace", "fixture-risk-org-1")
    oid = org["organization"]["id"]
    added = post(holder, BASE + "/allowlist", change(payer_kind="organization", organization_id=oid))
    assert added.status_code == 200 and added.json()["item"]["payer_id"] == oid and added.json()["item"]["payer_label"] is None, added.text
    # with the account list the address resolves, and the answer names it
    _, listing = delegate(w, monkeypatch, PRICING_READER | {"platform.users.view"}, "pricing-and-accounts", carrier="auditor")
    found = post(listing, BASE + "/allowlist", change(payer_kind="user", email=w["target"]["email"]))
    assert found.status_code == 200 and found.json()["item"]["payer_id"] == w["target"]["id"], found.text
    assert found.json()["item"]["payer_label"] == w["target"]["email"]
    missing = post(listing, BASE + "/allowlist", change(payer_kind="user", email="nobody-here@example.test"))
    assert missing.status_code == 404 and missing.json()["error"] == "risk_allowlist_payer_not_found"
    # without the pricing permission the account list opens nothing here
    _, reader = delegate(w, monkeypatch, {"platform.config.view", "platform.users.view"}, "accounts-only", carrier="finance")
    refused = post(reader, BASE + "/allowlist", change(payer_kind="user", email=w["target"]["email"]))
    assert refused.status_code == 403 and refused.json()["detail"]["permission"] == "platform.models.pricing", refused.text


def test_a_one_factor_session_is_told_the_missing_permission_before_the_second_factor(world, monkeypatch):
    """The order every other route keeps: 404, 403 by name, step-up, re-authentication."""
    from synwevo_core.sessions import COOKIE_NAME, Sessions

    def one_factor(client):  # FIXTURE, the temporary database only: this session proved one factor.
        session = Sessions(w["store"]).load(client.cookies.get(COOKIE_NAME), touch=False)
        w["store"].execute("UPDATE sessions SET amr='email' WHERE id=?", (session["id"],))

    w = world
    by_address = dict(payer_kind="user", email=w["target"]["email"])
    _, pricing_only = delegate(w, monkeypatch, PRICING_READER, "pricing-one-factor", carrier="support")
    _, accounts_only = delegate(w, monkeypatch, {"platform.config.view", "platform.users.view"}, "accounts-one-factor", carrier="auditor")
    _, both = delegate(w, monkeypatch, PRICING_READER | {"platform.users.view"}, "both-one-factor", carrier="finance")
    for client in (pricing_only, accounts_only, both):
        one_factor(client)
    refused = post(pricing_only, BASE + "/allowlist", change(**by_address))
    assert refused.status_code == 403 and refused.json()["detail"]["permission"] == "platform.users.view", refused.text
    refused = post(accounts_only, BASE + "/allowlist", change(**by_address))
    assert refused.status_code == 403 and refused.json()["detail"]["permission"] == "platform.models.pricing", refused.text
    # holding what the change needs, the second factor comes next
    refused = post(both, BASE + "/allowlist", change(**by_address))
    assert refused.status_code == 403 and refused.json()["error"] == "step_up_required", refused.text
    refused = post(pricing_only, BASE + "/breaker/open", change())
    assert refused.status_code == 403 and refused.json()["error"] == "step_up_required", refused.text
    assert not w["store"].all("SELECT * FROM model_risk_allowlist")


def test_a_delegated_holder_does_not_release_their_own_paid_calls(world, monkeypatch):
    """The brake and the staged rollout hold every payer to someone else's judgment. A delegated
    holder neither lifts a suspension of their own account or an organization they belong to, nor
    puts either on the allowlist; another payer's they can. The page is told which rows those are."""
    w = world
    user, holder = delegate(w, monkeypatch, PRICING_READER | {"platform.users.view"}, "pricing-holder")
    orgs = Organizations(w["store"], w["ident"])
    own_org = orgs.create(user["id"], "Fixture own organization", "Fixture own workspace", "fixture-risk-own-1")["organization"]["id"]
    other_org = orgs.create(w["target"]["id"], "Fixture other organization", "Fixture other workspace", "fixture-risk-other-1")["organization"]["id"]
    own, theirs, member_of, foreign = trip(w, "user", user["id"]), trip(w, "user", w["target"]["id"]), trip(w, "organization", own_org), trip(w, "organization", other_org)
    route = trip(w, "route", "fixture-route")
    listed = {row["id"]: row["refused_own_payer"] for row in holder.get(BASE + "/suspensions").json()["items"]}
    assert listed == {own["id"]: True, member_of["id"]: True, theirs["id"]: False, foreign["id"]: False, route["id"]: False}
    for row in (own, member_of):
        refused = post(holder, BASE + f"/suspensions/{row['id']}/lift", change())
        assert refused.status_code == 403 and refused.json()["error"] == "risk_own_payer", refused.text
        assert w["store"].one("SELECT lifted_at FROM model_risk_suspensions WHERE id=?", (row["id"],))["lifted_at"] is None
    for row in (theirs, foreign, route):
        lifted = post(holder, BASE + f"/suspensions/{row['id']}/lift", change())
        assert lifted.status_code == 200 and lifted.json()["item"]["lifted_by"] == user["id"], lifted.text
    for body in (dict(payer_kind="user", email=user["email"]), dict(payer_kind="organization", organization_id=own_org)):
        refused = post(holder, BASE + "/allowlist", change(**body))
        assert refused.status_code == 403 and refused.json()["error"] == "risk_own_payer", refused.text
    assert not w["store"].all("SELECT * FROM model_risk_allowlist")
    assert [a["result"] for a in audits(w, "admin.model_risk.allowlist_add")] == ["denied", "denied"]
    assert '"risk_own_payer"' in audits(w, "admin.model_risk.suspension_lift")[0]["meta"]
    for body in (dict(payer_kind="user", email=w["target"]["email"]), dict(payer_kind="organization", organization_id=other_org)):
        assert post(holder, BASE + "/allowlist", change(**body)).status_code == 200
    # taking oneself off a list, or stopping everything, releases nothing
    entry = next(row for row in w["store"].all("SELECT * FROM model_risk_allowlist") if row["payer_kind"] == "user")
    assert post(holder, BASE + f"/allowlist/{entry['id']}/remove", change()).status_code == 200


def test_a_super_administrator_may_release_their_own_paid_calls_on_the_record(world):
    """The whole catalogue, and possibly nobody else who could: the rule would lock the owner of a
    single-administrator platform out of their own product, so it binds delegated holders, and the
    audit row says when an administrator acted on their own payer."""
    w = world
    org = Organizations(w["store"], w["ident"]).create(w["root"]["id"], "Fixture root organization", "Fixture root workspace", "fixture-risk-root-1")
    suspension = trip(w, "user", w["root"]["id"])
    assert w["admin"].get(BASE + "/suspensions").json()["items"][0]["refused_own_payer"] is False, "offered: the server would not refuse it"
    assert post(w["admin"], BASE + f"/suspensions/{suspension['id']}/lift", change()).status_code == 200
    assert post(w["admin"], BASE + "/allowlist", change(payer_kind="user", email=w["root"]["email"])).status_code == 200
    assert post(w["admin"], BASE + "/allowlist", change(payer_kind="organization", organization_id=org["organization"]["id"])).status_code == 200
    import json
    assert json.loads(audits(w, "admin.model_risk.suspension_lift")[-1]["meta"])["own_payer"] is True
    assert [json.loads(a["meta"])["own_payer"] for a in audits(w, "admin.model_risk.allowlist_add")] == [True, True]
    other = trip(w, "user", w["target"]["id"])
    assert post(w["admin"], BASE + f"/suspensions/{other['id']}/lift", change()).status_code == 200
    assert "own_payer" not in json.loads(audits(w, "admin.model_risk.suspension_lift")[-1]["meta"])


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
