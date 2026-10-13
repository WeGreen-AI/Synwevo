"""B1/B2: the application catalogue under platform administration (admin_apps.py) — the counts
read by the rule the session gate runs (`Apps.check_access`, per workspace), the platform-wide
switch, the access policy and the per-workspace switch, each previewed and confirmed (preview →
reason → strict re-authentication → one transaction → audit → security journal for a withdrawal),
every refusal, and who may read and who may change (decision D12: platform scope also needs
`platform.config.manage`).

Synthetic identities in the isolated identity fixture of test_admin_operations (`world`) with the
ordinary delegated administrator of test_admin_ownership (`governed`, role access_admin) and the
fixture's root super administrator for platform-scope changes. Every account is created the
default way, with its personal workspace and Synwevo Docs on in it. Rows the product's routes would
not write here — a suspended account, Docs jobs, the unclaimed historical workspace, a frozen
workspace, a session parked in a workspace, the messages registration, an OAuth client — are
written directly and marked FIXTURE.
"""
from __future__ import annotations

import json
import time
import types
import uuid

import pytest

from synwevo_core import authority, journal
from synwevo_core.apps import (DOCS_APP_ID, MESSAGES_APP_ID, Apps, bootstrap_docs_app, bootstrap_suite_app,
                               suite_unreleased)
from synwevo_core.api_platform import Approvals, enforce_principal
from synwevo_core.auth import Auth
from synwevo_core.errors import ServiceError
from synwevo_core.machine import SCOPE_READ, SCOPE_WRITE
from synwevo_core.oidc import docs_resource
from synwevo_core.oidc import Clients
from synwevo_core.store import LEGACY_WORKSPACE_NAME, now
from synwevo_identity import admin_apps
from synwevo_identity.admin_apps import AppGovernance

from .platform_fixture import make_platform_admin
from .test_admin_operations import PW, post, world  # noqa: F401
from .test_admin_ownership import governed  # noqa: F401

REASON = "Isolated fixture: platform governance of an application"
BASE = "/api/admin/apps"
ADMIN_EMAIL = "governance-admin@example.test"
LIVE = "status='active' AND deleted_at IS NULL"
AWAY_NOTES = ["data_kept", "links_stop", "jobs_continue_or_stop", "webhooks_disabled", "delegations_end_at_refresh"]
BACK_NOTES = ["data_kept", "links_stop", "jobs_continue_or_stop", "integrations_need_reauthorization"]


# ---------------------------------------------------------------- fixtures and helpers
@pytest.fixture()
def cat(governed):
    """Synwevo Docs in ten workspaces — every account's personal one, and `shared` and `other`, both
    owned by `target` — with, in `shared`, a member under the policy alone (`plain`), one with a
    grant (`granted`), one revoked (`revoked`), one who also has a grant in `other` (`both`) and a
    suspended one; four queued or running Docs jobs (a person without a grant, a person with one,
    an API key, pre-identity operator work) and a finished one; a registered Docs OAuth client;
    messages registered; a business application released once and then held back."""
    w = governed
    store, ident, owner = w["store"], w["ident"], w["target"]
    apps = Apps(store, ident)
    w["super"] = w["login"](w["root"])
    w["reauth"](w["super"])

    def person(email):
        u = ident.create_user(email, PW, status="active")               # with a personal workspace
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
        return ident.get_user(u["id"])

    shared = ident.create_workspace("Shared fixture workspace", owner_id=owner["id"], kind="org")["id"]
    other = ident.create_workspace("Other fixture workspace", owner_id=owner["id"], kind="org")["id"]
    people = {name: person(f"{name}@example.test") for name in ("plain", "granted", "revoked", "both", "suspended")}
    for u in people.values():
        ident.add_member(shared, u["id"], "editor", actor_id=owner["id"])
    ident.add_member(other, people["both"]["id"], "viewer", actor_id=owner["id"])
    apps.grant(shared, DOCS_APP_ID, people["granted"]["id"], owner["id"])
    apps.grant(other, DOCS_APP_ID, people["both"]["id"], owner["id"])
    apps.revoke(shared, DOCS_APP_ID, people["revoked"]["id"])
    store.execute("UPDATE users SET status='suspended' WHERE id=?", (people["suspended"]["id"],))  # FIXTURE
    # FIXTURE: Docs jobs, as the job service stores them (a descriptor of who asked).
    _, key = Auth(store).create_key("fixture key", shared)
    descs = {"plain": authority.describe(user=people["plain"], tenant=shared),
             "granted": authority.describe(user=people["granted"], tenant=shared),
             "key": authority.describe({"kind": "api_key", "key_id": key["id"], "lineage": key["id"]}, tenant=shared),
             "operator": None}
    project(store, "apps-project", shared)
    for name, desc in descs.items():
        job(store, f"apps-job-{name}", desc, shared, status="queued" if name == "granted" else "running")
    job(store, "apps-job-done", descs["plain"], shared, status="completed")
    # FIXTURE: Synwevo Docs' sign-in client, as `cli client register --app docweave` writes it.
    Clients(store).register(client_id="docs-web-fixture", app_id=DOCS_APP_ID, name="Fixture Docs web",
                            redirect_uris=["http://docweave.localhost:8765/auth/callback"])
    # FIXTURE: messages registered, as its own process does at start-up.
    apps.register(app_id=MESSAGES_APP_ID, name="Fixture messages", icon="chat",
                  launch_url="http://messages.localhost:8771/")
    # A business application released once and then held back, through the start-up path itself.
    for released in (True, False):
        bootstrap_suite_app(store, ident, app_id="forms", name="Fixture forms", icon="forms",
                            launch_url="http://workplace.localhost:8769/forms", released=released)
    return {**w, "apps": apps, "shared": shared, "other": other, "people": people, "descs": descs,
            "uid": {name: u["id"] for name, u in people.items()}}


def project(store, pid, wid, *, deleted=0):
    """FIXTURE: a Docs project row, as the job runner needs one (JobService._authorized)."""
    store.insert("projects", {"id": pid, "name": "Fixture project", "created_at": now(), "updated_at": now(),
                              "tenant": wid, "deleted": deleted})


def job(store, jid, desc, wid, *, status="running", pid="apps-project"):
    """FIXTURE: a Docs job as the job service stores it (a descriptor of who asked)."""
    store.insert("jobs", {"id": jid, "status": status, "created_at": now(), "tenant": wid, "project_id": pid,
                          "principal": authority.dumps(desc)})


def admin_id(w):
    return w["ident"].by_email(ADMIN_EMAIL)["id"]


def promote_and_approve(w, uid, label):
    """Through the API platform's own service: `uid` an owner of `shared` (a role change writes no
    grant), an API key requested by the root administrator (`api_platform.manage`), approved by
    that owner, minted and bound. Returns (approval id, the key's job descriptor, key id)."""
    store, shared = w["store"], w["shared"]
    if w["ident"].membership(shared, uid)["role"] != "owner":
        w["ident"].add_member(shared, uid, "owner", actor_id=w["target"]["id"])
    approvals = Approvals(store)
    scope = {"scopes": [SCOPE_READ, SCOPE_WRITE], "resources": [docs_resource()]}
    row = approvals.create(w["root"]["id"], {"workspace_id": shared, "name": label, "credential_kind": "api_key", **scope},
                           "Isolated fixture: an API key for a workspace")
    approvals.decide(row["id"], uid, "approve", scope, "Isolated fixture: approved by an owner")
    _, key = Auth(store).create_key(label, shared)
    with store.tx():
        approvals.bind(row["id"], "api_key", key["id"])
    return row["id"], authority.describe({"kind": "api_key", "key_id": key["id"], "lineage": key["id"]}, tenant=shared), key["id"]


def runner_allows(w):
    """What `JobService._authorized` answers for every queued or running job: a live project, then
    `authority.check` of the descriptor (the oracle for `running_jobs`)."""
    store = w["store"]
    return {j["id"]: j["deleted"] == 0 and authority.check(store, authority.loads(j["principal"]), need="write",
                                                           tenant=j["tenant"] or "default")[0]
            for j in store.all("SELECT j.id, j.tenant, j.principal, p.deleted FROM jobs j LEFT JOIN projects p "
                               "ON p.id=j.project_id WHERE j.status IN ('queued','running','cancelling')")}


def _client(w, workspace, client):
    """Platform-scope changes are the super administrator's (D12); a workspace's are the delegated
    administrator's."""
    return client or (w["super"] if workspace is None else w["admin"])


def preview(w, change, *, app=DOCS_APP_ID, workspace=None, client=None):
    body = {"change": change, **({"workspace_id": workspace} if workspace is not None else {})}
    return post(_client(w, workspace, client), f"{BASE}/{app}/preview", body)


def plan(w, change, **kwargs):
    r = preview(w, change, **kwargs)
    assert r.status_code == 200, r.text
    return r.json()


def command(p, *, workspace=None, reason=REASON, key=None):
    body = {"reason": reason, "preview_token": p["preview_token"], "idempotency_key": key or uuid.uuid4().hex}
    if workspace is not None:
        body["workspace_id"] = workspace
    return body


def execute(w, change, body, *, app=DOCS_APP_ID, client=None, headers=None):
    return post(_client(w, body.get("workspace_id"), client), f"{BASE}/{app}/{change}", body, headers=headers or {})


def refused(r, status, code):
    assert r.status_code == status and r.json()["error"] == code, r.text


def catalogue(w, app=DOCS_APP_ID):
    r = w["admin"].get(BASE)
    assert r.status_code == 200, r.text
    return next(item for item in r.json()["items"] if item["id"] == app)


def listed(w, wid, app=DOCS_APP_ID):
    return next(i for i in w["admin"].get(f"{BASE}/{app}/workspaces", params={"limit": 200}).json()["items"] if i["id"] == wid)


def can_open(w, name, app=DOCS_APP_ID, workspace=None):
    return Apps(w["store"], w["ident"]).check_access(w["uid"][name], app, workspace)[0]


def live_people(w):
    return [row["id"] for row in w["store"].all(f"SELECT id FROM users WHERE {LIVE} AND NOT EXISTS "
                                                "(SELECT 1 FROM app_accounts a WHERE a.user_id=users.id)")]


def access_pairs(w, app=DOCS_APP_ID):
    """The oracle: every (live person, workspace of theirs) in which `Apps.check_access` — the
    session gate's own question — lets them open the application."""
    gate = Apps(w["store"], w["ident"])
    return {(uid, ws["id"]) for uid in live_people(w) for ws in w["ident"].workspaces_for(uid)
            if gate.check_access(uid, app, ws["id"])[0]}


def sessions_in(w, pairs):
    t = now()
    return sum(1 for row in w["store"].all("SELECT user_id, workspace_id FROM sessions WHERE revoked_at IS NULL "
                                           "AND idle_expires_at>? AND absolute_expires_at>?", (t, t))
               if (row["user_id"], row["workspace_id"]) in pairs)


def assert_counts_follow_check_access(w):
    """The catalogue's pairs are exactly the gate's, for the application as a whole and in each
    workspace, whatever state the fixture is in."""
    store, ident = w["store"], w["ident"]
    gate = Apps(store, ident)
    governance = AppGovernance(store, ident, None)
    for app_id in (DOCS_APP_ID, MESSAGES_APP_ID, "forms"):
        app = gate.get(app_id)
        pairs = access_pairs(w, app_id)
        if app["status"] == "active":
            assert governance._pairs(app) == pairs, app_id
        else:
            assert pairs == set(), app_id
        people = {uid for uid in live_people(w) if gate.check_access(uid, app_id)[0]}
        assert catalogue(w, app_id)["members_with_access"] == len(people), app_id
        for ws in w["admin"].get(f"{BASE}/{app_id}/workspaces", params={"limit": 200}).json()["items"]:
            assert ws["members_with_access"] == sum(1 for _, wid in pairs if wid == ws["id"]), (app_id, ws["id"])


def rows(w, action, result=None):
    where = " AND result=?" if result else ""
    return w["store"].all(f"SELECT * FROM audit_events WHERE action=?{where} ORDER BY seq",
                          (action, *((result,) if result else ())))


def entries(w, kind):
    return [e for e in journal.read(w["store"].root) if e["kind"] == kind]


def entitlements(w):
    return w["store"].all("SELECT * FROM app_entitlements WHERE app_id=? ORDER BY workspace_id, user_id", (DOCS_APP_ID,))


def operations(w):
    return w["store"].all("SELECT * FROM admin_operations WHERE action LIKE 'admin.app.%' ORDER BY created_at")


def job_allowed(w, name):
    return authority.check(w["store"], w["descs"][name], need="write", tenant=w["shared"])[0]


def park(w, name, wid):
    """FIXTURE: `name` signed in, the session parked in `wid`."""
    w["login"](w["people"][name])
    w["store"].execute("UPDATE sessions SET workspace_id=? WHERE user_id=? AND revoked_at IS NULL", (wid, w["uid"][name]))


# ---------------------------------------------------------------- the catalogue
def test_the_catalogue_counts_by_the_rule_the_gate_runs(cat):
    w = cat
    item = catalogue(w)
    # Everyone live keeps Docs in their personal workspace: root, target, the governance
    # administrator, plain, granted, revoked (revoked in `shared` only), both — not the suspended
    # member (check_access lets them in, but no session can carry them there).
    assert item == {"id": DOCS_APP_ID, "name": item["name"], "status": "active", "access_policy": "members",
                    "launch_url": item["launch_url"], "scope": "workspace", "workspaces_enabled": 10,
                    "members_with_access": 7, "explicit_grants": 12, "explicit_revocations": 1,
                    "running_jobs": 4, "changes": ["disable", "policy_explicit"]}
    assert can_open(w, "suspended"), "check_access leaves the account's own state to the session gate"
    assert can_open(w, "revoked") and not can_open(w, "revoked", workspace=w["shared"])
    messages = catalogue(w, MESSAGES_APP_ID)
    assert messages["scope"] == "organization" and messages["running_jobs"] is None
    assert messages["members_with_access"] == len(live_people(w)) == 7 and messages["changes"] == ["disable"]
    forms = catalogue(w, "forms")
    assert forms["status"] == "disabled" and forms["members_with_access"] == 0 and forms["changes"] == ["policy_explicit"]
    assert forms["running_jobs"] is None
    assert_counts_follow_check_access(w)

    listing = w["admin"].get(f"{BASE}/{DOCS_APP_ID}/workspaces", params={"q": "Shared fixture"}).json()
    assert listing["total"] == 1 and listing["limit"] == 50 and listing["offset"] == 0
    (shared,) = listing["items"]
    assert shared == {"id": w["shared"], "name": "Shared fixture workspace", "kind": "org", "status": "active",
                      "organization_id": None, "enabled": True, "enabled_at": shared["enabled_at"],
                      "members": 6, "members_with_access": 4}
    assert w["admin"].get(f"{BASE}/{DOCS_APP_ID}/workspaces", params={"q": w["other"]}).json()["total"] == 1
    paged = w["admin"].get(f"{BASE}/{DOCS_APP_ID}/workspaces", params={"limit": 2, "offset": 1}).json()
    assert paged["total"] == 10 and len(paged["items"]) == 2
    refused(w["admin"].get(f"{BASE}/no-such-app/workspaces"), 404, "no_app")
    refused(w["admin"].get(f"{BASE}/platform/workspaces"), 404, "no_app")


# ---------------------------------------------------------------- the impact, pair by pair
@pytest.mark.parametrize("change, workspace", [("disable", "shared"), ("disable", None),
                                               ("policy_explicit", None), ("policy_members", None)])
def test_the_impact_is_what_the_gate_answers_in_each_workspace(cat, change, workspace):
    """Every account has Docs in its own personal workspace, so a person who loses a team workspace
    still "has" the application somewhere: the impact is counted per (person, workspace), as the
    session gate decides, and a person counts when they lose (or gain) it in at least one workspace.
    Held to `Apps.check_access(person, app, workspace)` before and after the confirmed change."""
    w = cat
    wid = w[workspace] if workspace else None
    if change == "policy_members":
        assert execute(w, "policy_explicit", command(plan(w, "policy_explicit"))).status_code == 200
    park(w, "plain", w["shared"])
    park(w, "both", w["other"])                       # `both` keeps `other` (a grant) whatever `shared` does
    before = access_pairs(w)
    p = plan(w, change, workspace=wid)
    r = execute(w, change, command(p, workspace=wid))
    assert r.status_code == 200, r.text
    after = access_pairs(w)
    lost, gained = before - after, after - before
    impact = p["impact"]
    assert impact["memberships_losing_access"] == len(lost) and impact["memberships_gaining_access"] == len(gained)
    assert impact["people_losing_access"] == len({u for u, _ in lost})
    assert impact["people_gaining_access"] == len({u for u, _ in gained})
    assert impact["sessions_in_app"] == sessions_in(w, lost), "sessions sitting in a pair that loses access"
    expected = {("disable", "shared"): (4, 4, 0, 0, 1), ("disable", None): (7, 13, 0, 0, 6),
                ("policy_explicit", None): (2, 2, 0, 0, 1), ("policy_members", None): (0, 0, 2, 2, 0)}[(change, workspace)]
    assert (impact["people_losing_access"], impact["memberships_losing_access"], impact["people_gaining_access"],
            impact["memberships_gaining_access"], impact["sessions_in_app"]) == expected
    if (change, workspace) != ("disable", None):
        # Counted "anywhere", each of them would still have it: their own workspace keeps it.
        assert catalogue(w)["members_with_access"] == 7
    assert_counts_follow_check_access(w)


# ---------------------------------------------------------------- platform-wide switch
def test_switching_an_application_off_and_on_platform_wide(cat):
    w = cat
    store = w["store"]
    grants_before = entitlements(w)
    park(w, "plain", w["shared"])
    p = plan(w, "disable")
    assert p["change"] == "disable" and p["scope"] == "platform" and p["action"] == "admin.app.disable"
    assert p["target"]["id"] == DOCS_APP_ID and p["target"]["members_with_access"] == 7 and "workspace" not in p["target"]
    # The jobs of a person, a person with a grant and a key stop, the operator's does not; every
    # live session sits in a pair that loses access (root twice, target, the administrator, plain).
    assert p["impact"] == {"people_losing_access": 7, "people_gaining_access": 0, "memberships_losing_access": 13,
                           "memberships_gaining_access": 0, "workspaces_affected": 10, "running_jobs": 3,
                           "credentials_stopping": 0, "sessions_in_app": sessions_in(w, access_pairs(w))}
    assert p["impact"]["sessions_in_app"] == 5
    assert p["notes"] == AWAY_NOTES
    assert store.one("SELECT status FROM apps WHERE id=?", (DOCS_APP_ID,))["status"] == "active", "a preview changes nothing"

    r = execute(w, "disable", command(p))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["state"] == "succeeded" and out["journaled"] is True and out["change"] == "disable"
    assert out["app"] == catalogue(w) and out["app"]["status"] == "disabled" and out["app"]["members_with_access"] == 0
    assert out["app"]["changes"] == ["enable", "policy_explicit"]
    assert not can_open(w, "plain") and not can_open(w, "granted") and access_pairs(w) == set()
    assert Apps(store, w["ident"]).check_access(w["uid"]["plain"], DOCS_APP_ID)[2] == "该应用已停用"
    assert [job_allowed(w, n) for n in ("plain", "granted", "key", "operator")] == [False, False, False, True]
    assert authority.check(store, w["descs"]["key"], need="write", tenant=w["shared"]) == (False, "app_disabled")
    assert [e["app_id"] for e in entries(w, "app_disabled")] == [DOCS_APP_ID]
    (row,) = rows(w, "admin.app.disable", "ok")
    meta = json.loads(row["meta"])
    assert row["actor_id"] == w["root"]["id"] and row["object_kind"] == "app" and row["object_id"] == DOCS_APP_ID
    assert row["workspace_id"] is None, "a platform act is filed under no workspace"
    assert meta["before"] == {"status": "active"} and meta["after"] == {"status": "disabled"}
    assert meta["reason"] == REASON and meta["impact"] == p["impact"] and meta["operation_id"] == out["operation_id"]
    (op,) = operations(w)
    assert op["action"] == "admin.app.disable" and json.loads(op["result_json"]) == out
    assert entitlements(w) == grants_before
    assert_counts_follow_check_access(w)

    back = plan(w, "enable")
    assert back["impact"]["people_gaining_access"] == 7 and back["impact"]["memberships_gaining_access"] == 13
    assert back["impact"]["people_losing_access"] == 0 and back["impact"]["running_jobs"] == 0
    assert back["impact"]["sessions_in_app"] == 0
    assert back["notes"] == BACK_NOTES + ["explicit_grants_kept"]
    out = execute(w, "enable", command(back)).json()
    assert out["app"] == catalogue(w) and out["app"]["status"] == "active" and out["journaled"] is None
    assert can_open(w, "plain", workspace=w["shared"]) and can_open(w, "both", workspace=w["shared"])
    assert not can_open(w, "revoked", workspace=w["shared"])
    assert entitlements(w) == grants_before, "grants and revocations untouched both ways"
    assert [e["app_id"] for e in entries(w, "app_disabled")] == [DOCS_APP_ID], "enabling is not journalled"
    assert len(rows(w, "admin.app.enable", "ok")) == 1 and len(operations(w)) == 2
    assert_counts_follow_check_access(w)


# ---------------------------------------------------------------- the access policy
def test_tightening_and_loosening_the_access_policy(cat):
    w = cat
    store = w["store"]
    grants_before = entitlements(w)
    park(w, "plain", w["shared"])
    p = plan(w, "policy_explicit")
    # In `shared`, plain (no grant anywhere) and both (whose grant is in `other`) lose it; the
    # owners and `granted` hold grants, `revoked` could not open it there anyway.
    assert p["impact"] == {"people_losing_access": 2, "people_gaining_access": 0, "memberships_losing_access": 2,
                           "memberships_gaining_access": 0, "workspaces_affected": 10, "running_jobs": 1,
                           "credentials_stopping": 0, "sessions_in_app": 1}
    assert p["notes"] == AWAY_NOTES + ["explicit_grants_kept", "revocations_kept_on_loosen"]
    before = {name: can_open(w, name, workspace=w["shared"]) for name in w["uid"]}
    out = execute(w, "policy_explicit", command(p)).json()
    assert out["app"] == catalogue(w) and out["app"]["access_policy"] == "explicit"
    assert out["app"]["members_with_access"] == 7, "each keeps their own workspace"
    after = {name: can_open(w, name, workspace=w["shared"]) for name in w["uid"]}
    # The suspended member passes check_access too and loses it the same way; no session can
    # carry them, so the count (and the preview) is the live people.
    assert [n for n in w["uid"] if before[n] and not after[n]] == ["plain", "both", "suspended"]
    assert after["granted"] and can_open(w, "both", workspace=w["other"])
    # Only the person without a grant has their job stopped; a key is not entitlement-checked.
    assert [job_allowed(w, n) for n in ("plain", "granted", "key", "operator")] == [False, True, True, True]
    assert authority.check(store, w["descs"]["plain"], need="write", tenant=w["shared"]) == (False, "no_entitlement")
    (entry,) = entries(w, "app_policy_tightened")
    assert entry["app_id"] == DOCS_APP_ID and entry["event_id"]
    assert store.one("SELECT 1 FROM meta WHERE key=?", ("app_policy_tightened:" + entry["event_id"],)), "its receipt"
    (row,) = rows(w, "admin.app.policy_explicit", "ok")
    assert json.loads(row["meta"])["after"] == {"access_policy": "explicit"}
    # Every service start registers Synwevo Docs again; it keeps the policy chosen here.
    bootstrap_docs_app(store, w["ident"])
    assert store.one("SELECT access_policy FROM apps WHERE id=?", (DOCS_APP_ID,))["access_policy"] == "explicit"
    assert_counts_follow_check_access(w)

    loose = plan(w, "policy_members")
    assert loose["impact"]["people_gaining_access"] == 2 and loose["impact"]["memberships_gaining_access"] == 2
    assert loose["impact"]["people_losing_access"] == 0 and loose["impact"]["running_jobs"] == 0
    assert loose["notes"] == BACK_NOTES + ["explicit_grants_kept", "revocations_kept_on_loosen"]
    out = execute(w, "policy_members", command(loose)).json()
    assert out["app"] == catalogue(w) and out["app"]["access_policy"] == "members" and out["journaled"] is None
    assert can_open(w, "plain", workspace=w["shared"]) and not can_open(w, "revoked", workspace=w["shared"]), \
        "a revocation outlives loosening"
    assert entitlements(w) == grants_before
    assert len(entries(w, "app_policy_tightened")) == 1, "loosening is not journalled"
    assert_counts_follow_check_access(w)


# ---------------------------------------------------------------- one workspace
def test_switching_an_application_off_and_on_for_one_workspace(cat):
    w = cat
    store, shared = w["store"], w["shared"]
    park(w, "plain", shared)
    p = plan(w, "disable", workspace=shared)
    assert p["scope"] == "workspace" and p["action"] == "admin.app.workspace_disable"
    assert p["target"]["workspace"] == listed(w, shared) and p["target"]["workspace"]["members_with_access"] == 4
    # target, plain, granted and both lose it in `shared` (each keeps their own workspace).
    assert p["impact"] == {"people_losing_access": 4, "people_gaining_access": 0, "memberships_losing_access": 4,
                           "memberships_gaining_access": 0, "workspaces_affected": 1, "running_jobs": 3,
                           "credentials_stopping": 0, "sessions_in_app": 1}
    assert p["notes"] == AWAY_NOTES
    # Confirming without the workspace is another act (platform-wide), not this plan.
    refused(execute(w, "disable", command(p), client=w["super"]), 409, "admin_preview_invalid")
    out = execute(w, "disable", command(p, workspace=shared)).json()
    assert out["workspace"] == listed(w, shared) and out["workspace"]["enabled"] is False
    assert out["workspace"]["members_with_access"] == 0 and out["workspace"]["enabled_at"] is None
    assert out["app"] == catalogue(w) and out["app"]["workspaces_enabled"] == 9 and out["journaled"] is True
    assert not can_open(w, "plain", workspace=shared) and can_open(w, "plain"), "their own workspace keeps it"
    assert can_open(w, "both", workspace=w["other"])
    assert authority.check(store, w["descs"]["key"], need="write", tenant=shared) == (False, "app_not_enabled_for_workspace")
    (entry,) = entries(w, "workspace_app_disabled")
    assert (entry["workspace_id"], entry["app_id"]) == (shared, DOCS_APP_ID)
    assert not entries(w, "app_disabled")
    (row,) = rows(w, "admin.app.workspace_disable", "ok")
    assert row["actor_id"] == admin_id(w) and row["workspace_id"] == shared
    meta = json.loads(row["meta"])
    assert meta["workspace_id"] == shared and meta["before"] == {"enabled": True} and meta["after"] == {"enabled": False}
    assert_counts_follow_check_access(w)

    back = plan(w, "enable", workspace=shared)
    assert back["impact"]["people_gaining_access"] == 4 and back["impact"]["running_jobs"] == 0
    assert back["notes"] == BACK_NOTES + ["explicit_grants_kept"]
    out = execute(w, "enable", command(back, workspace=shared)).json()
    assert out["workspace"] == listed(w, shared) and out["workspace"]["enabled"] is True
    assert out["workspace"]["members_with_access"] == 4 and out["app"] == catalogue(w)
    assert store.one("SELECT enabled_by FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                     (shared, DOCS_APP_ID))["enabled_by"] == admin_id(w)
    assert can_open(w, "plain", workspace=shared) and not can_open(w, "revoked", workspace=shared)
    (row,) = rows(w, "admin.app.workspace_enable", "ok")
    assert row["workspace_id"] == shared
    assert len(entries(w, "workspace_app_disabled")) == 1, "enabling is not journalled"
    assert_counts_follow_check_access(w)


def test_the_historical_and_unavailable_workspaces_are_refused(cat):
    w = cat
    store, ident = w["store"], w["ident"]
    # FIXTURE: the unclaimed historical workspace, as Store seeds it over pre-identity data.
    store.insert("workspaces", {"id": "default", "name": LEGACY_WORKSPACE_NAME, "kind": "legacy",
                                "status": "pending_claim", "created_at": now(), "updated_at": now()})
    for change in ("enable", "disable"):
        refused(preview(w, change, workspace="default"), 409, "legacy_workspace")
        refused(execute(w, change, {"reason": REASON, "workspace_id": "default"}), 409, "legacy_workspace")
    listing = w["admin"].get(f"{BASE}/{DOCS_APP_ID}/workspaces").json()["items"]
    assert listing[0]["id"] == "default" and listing[0]["enabled"] is False, "listed first, as the workspace page does"
    frozen = ident.create_workspace("Frozen fixture workspace", owner_id=w["target"]["id"], kind="org")["id"]
    w["apps"].disable_for_workspace(frozen, DOCS_APP_ID)
    store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (frozen,))   # FIXTURE
    r = preview(w, "enable", workspace=frozen)
    refused(r, 409, "workspace_unavailable")
    assert r.json()["detail"]["workspace_status"] == "frozen"
    refused(preview(w, "disable", workspace=frozen), 409, "no_change")
    assert not store.one("SELECT 1 FROM workspace_apps WHERE workspace_id IN ('default', ?)", (frozen,))


# ---------------------------------------------------------------- running jobs
def test_running_jobs_counts_only_what_the_change_stops(cat):
    """A job already refused — its workspace frozen, its submitter suspended, its project deleted —
    is not stopped by the change, and pre-identity operator work is never refused."""
    w = cat
    store, ident = w["store"], w["ident"]
    frozen = ident.create_workspace("Frozen jobs fixture", owner_id=w["target"]["id"], kind="org")["id"]
    project(store, "apps-project-frozen", frozen)
    project(store, "apps-project-deleted", w["shared"], deleted=1)
    job(store, "apps-job-frozen", authority.describe(user=w["target"], tenant=frozen), frozen, pid="apps-project-frozen")
    job(store, "apps-job-suspended", authority.describe(user=w["people"]["suspended"], tenant=w["shared"]), w["shared"])
    job(store, "apps-job-deleted-project", w["descs"]["plain"], w["shared"], pid="apps-project-deleted")
    job(store, "apps-job-no-project", w["descs"]["plain"], w["shared"], pid="apps-project-missing")
    store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (frozen,))    # FIXTURE
    assert authority.check(store, authority.describe(user=w["target"], tenant=frozen), need="write",
                           tenant=frozen) == (False, "workspace_frozen")
    assert authority.check(store, authority.describe(user=w["people"]["suspended"], tenant=w["shared"]),
                           need="write", tenant=w["shared"]) == (False, "account_unavailable")
    assert authority.check(store, w["descs"]["plain"], need="write", tenant=w["shared"])[0], \
        "the submitter is fine; only the project is gone — the runner refuses it first"
    assert catalogue(w)["running_jobs"] == 8, "the list counts every queued or running job"
    assert plan(w, "disable")["impact"]["running_jobs"] == 3
    assert plan(w, "disable", workspace=w["shared"])["impact"]["running_jobs"] == 3
    assert plan(w, "policy_explicit")["impact"]["running_jobs"] == 1
    assert plan(w, "disable", app=MESSAGES_APP_ID)["impact"]["running_jobs"] is None


def test_approved_credentials_answer_through_their_approver(cat):
    """An API key bound to an API-platform approval is not entitlement-checked itself, but its
    approval is: `Approvals.active` asks `check_access` of the approving owner. An owner promoted
    without a grant takes the keys they approved down when Docs is tightened — their queued work
    and every live call — which the preview counts (`credentials_stopping`, `running_jobs`) and
    says (`approved_credentials_stop`)."""
    w = cat
    store, shared = w["store"], w["shared"]
    _, plain_key, plain_key_id = promote_and_approve(w, w["uid"]["plain"], "Key approved by plain")
    _, owner_key, owner_key_id = promote_and_approve(w, w["target"]["id"], "Key approved by target")
    job(store, "apps-job-plain-key", plain_key, shared)
    job(store, "apps-job-owner-key", owner_key, shared)
    for change, wid, n in (("policy_explicit", None, 1), ("disable", None, 2), ("disable", shared, 2)):
        impact_notes = plan(w, change, workspace=wid)
        assert impact_notes["impact"]["credentials_stopping"] == n, (change, wid)
        assert "approved_credentials_stop" in impact_notes["notes"], (change, wid)
    assert plan(w, "disable", workspace=w["other"])["impact"]["credentials_stopping"] == 0
    assert "approved_credentials_stop" not in plan(w, "disable", workspace=w["other"])["notes"]
    before = runner_allows(w)
    p = plan(w, "policy_explicit")
    assert execute(w, "policy_explicit", command(p)).status_code == 200
    after = runner_allows(w)
    stopped = sorted(j for j in before if before[j] and not after[j])
    assert stopped == ["apps-job-plain", "apps-job-plain-key"]
    assert p["impact"]["running_jobs"] == len(stopped)
    assert authority.check(store, plain_key, need="write", tenant=shared) == (False, "approver_unavailable")
    assert authority.check(store, owner_key, need="write", tenant=shared) == (True, None)
    with pytest.raises(ServiceError) as refused_call:              # a live call with the key
        enforce_principal(store, {"kind": "api_key", "key_id": plain_key_id, "lineage": plain_key_id,
                                  "tenant": shared, "scopes": ("read", "write")})
    assert refused_call.value.code == "approver_unavailable"
    assert enforce_principal(store, {"kind": "api_key", "key_id": owner_key_id, "lineage": owner_key_id,
                                     "tenant": shared, "scopes": ("read", "write")})["approval_id"]
    loose = plan(w, "policy_members")
    assert loose["impact"]["credentials_stopping"] == 0 and "approved_credentials_stop" not in loose["notes"]


# ---------------------------------------------------------------- refusals
def test_every_refusal_of_the_target(cat):
    w = cat
    store = w["store"]
    refused(preview(w, "explode"), 400, "bad_change")
    refused(post(w["super"], f"{BASE}/{DOCS_APP_ID}/preview", {}), 400, "bad_change")
    refused(preview(w, "policy_explicit", workspace=w["shared"]), 400, "bad_change")
    r = preview(w, "disable", workspace="")
    refused(r, 400, "bad_change")
    assert r.json()["detail"]["field"] == "workspace_id", "a blank workspace never becomes the whole platform"
    r = preview(w, "policy_explicit", app=MESSAGES_APP_ID)
    refused(r, 400, "bad_change")
    assert r.json()["detail"]["scope"] == "organization"
    refused(preview(w, "disable", app=MESSAGES_APP_ID, workspace=w["shared"]), 400, "bad_change")
    for change in ("disable", "enable", "policy_explicit", "policy_members"):
        refused(preview(w, change, app="platform"), 409, "portal_required")
    refused(preview(w, "disable", app="no-such-app"), 404, "no_app")
    refused(preview(w, "disable", workspace="w_fixture_missing"), 404, "no_workspace")
    refused(preview(w, "enable"), 409, "no_change")
    refused(preview(w, "policy_members"), 409, "no_change")
    refused(preview(w, "enable", workspace=w["shared"]), 409, "no_change")
    refused(preview(w, "enable", app="forms"), 409, "app_not_released")
    refused(preview(w, "disable", app="forms"), 409, "no_change")
    # Messages may be switched off platform-wide; it is counted per person, and it has no links,
    # jobs or OAuth client here to speak of.
    m = plan(w, "disable", app=MESSAGES_APP_ID)
    assert m["impact"] == {"people_losing_access": 7, "people_gaining_access": 0, "memberships_losing_access": None,
                           "memberships_gaining_access": None, "workspaces_affected": None, "running_jobs": None,
                           "credentials_stopping": None, "sessions_in_app": None}
    assert m["notes"] == ["data_kept"]
    # The same refusals at confirmation, before any token is looked at.
    refused(execute(w, "explode", {"reason": REASON}), 400, "bad_change")
    refused(execute(w, "enable", {"reason": REASON}), 409, "no_change")
    refused(execute(w, "disable", {"reason": REASON}, app="platform"), 409, "portal_required")
    refused(execute(w, "disable", {"reason": REASON}, app="no-such-app"), 404, "no_app")
    refused(execute(w, "disable", {"reason": REASON, "workspace_id": "w_fixture_missing"}), 404, "no_workspace")
    refused(execute(w, "enable", {"reason": REASON}, app="forms"), 409, "app_not_released")
    refused(execute(w, "disable", {"reason": "short"}), 400, "missing_reason")
    refused(execute(w, "disable", {"reason": REASON}), 400, "admin_confirmation_required")
    refused(execute(w, "disable", {"reason": REASON, "preview_token": "x", "idempotency_key": "short"}),
            400, "admin_confirmation_required")
    assert store.one("SELECT status FROM apps WHERE id=?", (DOCS_APP_ID,))["status"] == "active"
    assert not operations(w) and not entries(w, "app_disabled")
    # Refused acts are recorded under the action they named, with a code and never their input:
    # an application id the registry does not hold is not kept as the object.
    denied = rows(w, "admin.app.disable", "denied")
    assert denied and all(json.loads(r["meta"])["code"] for r in denied)
    assert {r["object_id"] for r in denied} == {DOCS_APP_ID, "forms", "platform", None}
    assert any(json.loads(r["meta"])["code"] == "no_app" and r["object_id"] is None for r in denied)
    assert not rows(w, "admin.app.disable", "ok")


@pytest.mark.parametrize("alteration", ["change", "actor", "origin", "signature", "expired", "app"])
def test_a_preview_binds_change_actor_origin_application_and_time(cat, monkeypatch, alteration):
    w = cat
    p, change, app, headers = plan(w, "disable"), "disable", DOCS_APP_ID, {}
    if alteration == "change":
        change = "policy_explicit"
    elif alteration == "app":
        app = MESSAGES_APP_ID
    elif alteration == "actor":
        second = make_platform_admin(w["store"], w["ident"].create_user("second-admin@example.test", PW, status="active"))
        client = w["login"](second)
        w["reauth"](client)
        p = plan(w, "disable", client=client)                     # signed for the second administrator
    elif alteration == "origin":
        headers = {"host": "localhost"}
    elif alteration == "signature":
        p = {**p, "preview_token": p["preview_token"] + "tampered"}
    else:
        monkeypatch.setattr(admin_apps, "time", types.SimpleNamespace(time=lambda: p["expires_at"] + 1))
    r = execute(w, change, command(p), app=app, headers=headers)
    refused(r, 409, "admin_preview_expired" if alteration == "expired" else "admin_preview_invalid")
    assert {a["status"] for a in w["store"].all("SELECT status FROM apps WHERE id IN (?, ?)",
                                                (DOCS_APP_ID, MESSAGES_APP_ID))} == {"active"}
    assert w["store"].one("SELECT access_policy FROM apps WHERE id=?", (DOCS_APP_ID,))["access_policy"] == "members"
    assert not operations(w) and not entries(w, "app_disabled") and not entries(w, "app_policy_tightened")


@pytest.mark.parametrize("change", ["member_joins", "grant_given", "state_changed", "member_joins_workspace",
                                    "approval_given"])
def test_a_changed_application_or_membership_makes_the_preview_stale(cat, change):
    w = cat
    store, ident = w["store"], w["ident"]
    act_change = "disable" if change.startswith("member_joins") else "policy_explicit"
    wid = w["shared"] if change == "member_joins_workspace" else None
    p = plan(w, act_change, workspace=wid)
    if change.startswith("member_joins"):
        # A default account: it has Docs in its own workspace already; joining `shared` is a new pair.
        late = ident.create_user("latecomer@example.test", PW, status="active")
        ident.add_member(w["shared"], late["id"], "viewer", actor_id=w["target"]["id"])
    elif change == "grant_given":
        w["apps"].grant(w["shared"], DOCS_APP_ID, w["uid"]["plain"], w["target"]["id"])
    elif change == "approval_given":
        # `plain` made an owner (a role, no grant) and approving a key: the same people lose
        # access, but one more approved credential would stop.
        promote_and_approve(w, w["uid"]["plain"], "approval given meanwhile")
    else:
        w["apps"].set_status(DOCS_APP_ID, "disabled")              # by the command line, meanwhile
    refused(execute(w, act_change, command(p, workspace=wid)), 409, "admin_preview_stale")
    assert store.one("SELECT access_policy FROM apps WHERE id=?", (DOCS_APP_ID,))["access_policy"] == "members"
    assert store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?", (w["shared"], DOCS_APP_ID))
    assert not operations(w) and not entries(w, "app_policy_tightened") and not entries(w, "workspace_app_disabled")


def test_identical_retries_return_the_stored_result_and_write_nothing_twice(cat, monkeypatch):
    w = cat
    body = command(plan(w, "disable"))
    first = execute(w, "disable", body)
    assert first.status_code == 200, first.text
    monkeypatch.setattr(admin_apps, "time", types.SimpleNamespace(time=lambda: time.time() + 3600))
    retry = execute(w, "disable", {**body, "reason": "  " + REASON + "  "})
    assert retry.status_code == 200 and retry.json() == first.json()
    assert len(operations(w)) == len(rows(w, "admin.app.disable", "ok")) == len(entries(w, "app_disabled")) == 1
    refused(execute(w, "disable", {**body, "reason": REASON + " changed"}), 409, "idempotency_conflict")
    refused(execute(w, "disable", {**body, "workspace_id": w["shared"]}, client=w["super"]), 409, "idempotency_conflict")
    assert len(operations(w)) == 1


def test_a_journal_that_cannot_be_written_commits_nothing(cat, monkeypatch):
    w = cat
    store = w["store"]

    def unavailable(*args, **kwargs):
        raise OSError("fixture: security journal unavailable")

    for change, workspace in (("disable", None), ("policy_explicit", None), ("disable", w["shared"])):
        p = plan(w, change, workspace=workspace)
        original = journal.record
        monkeypatch.setattr(journal, "record", unavailable)
        r = execute(w, change, command(p, workspace=workspace))
        # Restore only this function: undo() would also revert the fixture's environment.
        monkeypatch.setattr(journal, "record", original)
        refused(r, 503, "journal_unavailable")
        assert "fixture: security journal unavailable" not in r.text
        action = admin_apps.ACTIONS[("workspace" if workspace else "platform", change)]
        assert not rows(w, action, "ok"), "the platform record rolled back with the change"
        assert json.loads(rows(w, action, "error")[-1]["meta"])["code"] == "journal_unavailable"
    app = store.one("SELECT status, access_policy FROM apps WHERE id=?", (DOCS_APP_ID,))
    assert (app["status"], app["access_policy"]) == ("active", "members")
    assert store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?", (w["shared"], DOCS_APP_ID))
    assert not store.one("SELECT 1 FROM meta WHERE key LIKE 'app_policy_tightened:%'"), "no receipt without the change"
    assert not operations(w) and can_open(w, "plain", workspace=w["shared"])


def test_a_platform_switch_off_ends_the_deployments_hold_on_the_application(cat):
    """The command line can switch on an application the deployment holds back; switched off
    again from the console, the switch-off is the operator's, and a later release does not turn it
    back on (`Apps.set_status` deletes the marker)."""
    w = cat
    store, ident = w["store"], w["ident"]
    assert w["apps"].set_status("forms", "active") and suite_unreleased(store, "forms")   # `cli app enable forms`
    out = execute(w, "disable", command(plan(w, "disable", app="forms")), app="forms").json()
    assert not suite_unreleased(store, "forms")
    assert out["app"] == catalogue(w, "forms") and out["app"]["changes"] == ["enable", "policy_explicit"]
    bootstrap_suite_app(store, ident, app_id="forms", name="Fixture forms", icon="forms",
                        launch_url="http://workplace.localhost:8769/forms", released=True)
    assert store.one("SELECT status FROM apps WHERE id='forms'")["status"] == "disabled"
    forms = plan(w, "enable", app="forms")
    assert forms["notes"] == ["data_kept", "links_stop", "explicit_grants_kept"], "its fill links are gated"


# ---------------------------------------------------------------- who may read, who may change
@pytest.mark.parametrize("role", ["auditor", "support"])
def test_readers_read_and_are_refused_every_change(cat, role):
    w = cat
    holder = make_platform_admin(w["store"], w["ident"].create_user(f"{role}@example.test", PW, status="active"), role=role)
    c = w["login"](holder)
    w["reauth"](c)
    assert c.get(BASE).status_code == 200
    assert c.get(f"{BASE}/{DOCS_APP_ID}/workspaces").status_code == 200
    p = plan(w, "disable", workspace=w["shared"])            # a token another administrator could hold
    for r in (preview(w, "disable", client=c), preview(w, "disable", workspace=w["shared"], client=c),
              execute(w, "disable", command(p, workspace=w["shared"]), client=c)):
        refused(r, 403, "platform_permission_denied")
        assert r.json()["detail"]["permission"] == "platform.apps.manage"
    assert w["store"].one("SELECT status FROM apps WHERE id=?", (DOCS_APP_ID,))["status"] == "active"
    assert not operations(w)


def test_platform_scope_changes_also_need_platform_configuration_authority(cat):
    """D12: the access administrator switches applications for a workspace; switching one for the
    whole platform or changing its policy is the super administrator's (`platform.config.manage`)."""
    w = cat
    p = plan(w, "disable")                                    # the super administrator's preview
    for change in ("disable", "policy_explicit"):
        r = preview(w, change, client=w["admin"])
        refused(r, 403, "platform_permission_denied")
        assert r.json()["detail"]["permission"] == "platform.config.manage"
    r = execute(w, "disable", command(p), client=w["admin"])
    refused(r, 403, "platform_permission_denied")
    assert r.json()["detail"]["permission"] == "platform.config.manage"
    denied = w["store"].all("SELECT object_id FROM audit_events WHERE action='admin.access.denied' AND actor_id=?",
                            (admin_id(w),))
    assert [d["object_id"] for d in denied] == ["platform.config.manage"], "recorded once per window"
    assert w["store"].one("SELECT status FROM apps WHERE id=?", (DOCS_APP_ID,))["status"] == "active"
    # The same administrator's workspace switch goes through.
    assert plan(w, "disable", workspace=w["shared"], client=w["admin"])["scope"] == "workspace"


def test_without_a_platform_permission_the_catalogue_does_not_exist(cat):
    w = cat
    c = w["ordinary"]                                     # `target`: owner of shared and other
    for r in (c.get(BASE), c.get(f"{BASE}/{DOCS_APP_ID}/workspaces"), preview(w, "disable", client=c),
              preview(w, "disable", workspace=w["shared"], client=c),
              execute(w, "disable", {"reason": REASON}, client=c)):
        refused(r, 404, "not_found")


def test_a_change_needs_strict_re_authentication_before_its_body(cat):
    w = cat
    fresh = make_platform_admin(w["store"], w["ident"].create_user("fresh-admin@example.test", PW, status="active"),
                                role="access_admin")
    c = w["login"](fresh)
    p = plan(w, "disable", workspace=w["shared"], client=c)   # previewing is not dangerous
    assert p["impact"]["memberships_losing_access"] == 4
    for change, body in (("disable", command(p, workspace=w["shared"])), ("disable", {"workspace_id": w["shared"]}),
                         ("explode", {"workspace_id": 7})):
        refused(execute(w, change, body, client=c), 403, "reauth_required")
    # A platform-scope body meets the missing permission first, re-authenticated or not.
    r = execute(w, "disable", {}, client=c)
    refused(r, 403, "platform_permission_denied")
    w["reauth"](c)
    r = execute(w, "disable", command(p, workspace=w["shared"]), client=c)
    assert r.status_code == 200, r.text
    assert rows(w, "admin.app.workspace_disable", "ok")[0]["actor_id"] == fresh["id"]
