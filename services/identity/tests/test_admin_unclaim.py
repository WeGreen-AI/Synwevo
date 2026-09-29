"""Undoing the assignment of the historical data (`POST /api/admin/workspaces/{wid}/unclaim`).

Before this existed, an administrator who assigned the pre-identity workspace to the wrong account
had to ask an operator to edit the production database by hand (docs/quality/deployment.md,
2026-09-29). These tests hold the reverse to the claim's own rules — platform administrators only,
recent re-authentication, a written reason, one transaction, an audit record — and to its purpose:
afterwards nobody can read the data again, nothing of it is deleted, and it can be assigned anew.

Fixtures are isolated (a data directory under tmp_path); accounts and rows are fixture data.
"""
from __future__ import annotations

import importlib
import json
import threading

import pytest
from fastapi.testclient import TestClient

from synwevo_core import journal, oidc
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, new_id, now

PW = "a-sufficiently-long-passphrase"
REASON = "指派给了错误的账户，撤销后重新指派"
CLAIM_REASON = "把历史数据交给它原本的所有者"
LEGACY = "default"
GENERATED = "历史数据（待认领）"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    admin = ident.create_user("admin@example.com", PW, status="active", platform_role="admin",
                              display_name="平台管理员甲")
    alice = ident.create_user("alice@example.com", PW, status="active", display_name="Alice")
    bob = ident.create_user("bob@example.com", PW, status="active", display_name="Bob")

    # Fixture: the pre-identity data — one workspace, zero members, rows carrying tenant='default'.
    ts = now()
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES (?,?,'legacy','pending_claim',?,?)", (LEGACY, GENERATED, ts, ts))
    for i in range(3):
        store.execute("INSERT INTO projects (id,name,tenant,step,rev,mapping_rev,created_at,updated_at,deleted) "
                      "VALUES (?,?,?,'upload',1,1,?,?,0)", (f"p_legacy{i}", f"历史项目 {i}", LEGACY, ts, ts))
    for i in range(2):
        store.execute("INSERT INTO templates (id,name,tenant,created_at,updated_at,deleted) VALUES (?,?,?,?,?,0)",
                      (f"t_legacy{i}", f"历史模板 {i}", LEGACY, ts, ts))
    blob = store.put_blob(b"fixture source bytes")
    store.execute("INSERT INTO source_files (id,project_id,filename,blob,size,created_at,updated_at) "
                  "VALUES ('s_legacy','p_legacy0','fixture.xlsx',?,20,?,?)", (blob, ts, ts))
    store.execute("INSERT INTO jobs (id,project_id,status,created_at) VALUES ('j_legacy','p_legacy0','done',?)", (ts,))
    store.execute("INSERT INTO artifacts (id,job_id,filename,blob,size,created_at) "
                  "VALUES ('a_legacy','j_legacy','out.docx',?,20,?)", (blob, ts))

    c = _login(api, "admin@example.com")
    return {"api": api, "c": c, "store": store, "ident": ident, "apps": Apps(store, ident),
            "admin": admin, "alice": alice, "bob": bob, "blob": blob}


def _login(api, email):
    c = TestClient(api)
    r = c.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return c


def post(client, path, body=None):
    return client.post(path, json=body if body is not None else {},
                       headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


def _reauth(client):
    r = post(client, "/api/auth/reauth", {"password": PW, "scope": "admin"})
    assert r.status_code == 200, r.text


def _claim(env, uid, **extra):
    _reauth(env["c"])
    r = post(env["c"], f"/api/admin/workspaces/{LEGACY}/claim", {"user_id": uid, "reason": CLAIM_REASON, **extra})
    assert r.status_code == 200, r.text
    return r


def _unclaim(env, body=None, client=None):
    client = client or env["c"]
    return post(client, f"/api/admin/workspaces/{LEGACY}/unclaim", {"reason": REASON, **(body or {})})


def _preview(env, client=None, wid=LEGACY):
    return (client or env["c"]).get(f"/api/admin/workspaces/{wid}/unclaim-preview")


def _members(env):
    return {r["user_id"] for r in env["store"].all("SELECT user_id FROM memberships WHERE workspace_id=?", (LEGACY,))}


def _ws(env):
    return env["store"].one("SELECT * FROM workspaces WHERE id=?", (LEGACY,))


def _token(env, *, user_id, client_id="docweave-web", raw=None, expires_in=900):
    """Fixture: a platform token acting in the legacy workspace, as the token endpoint stores it."""
    raw = raw or new_id("raw")
    env["store"].execute(
        "INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,scope,workspace_id,issued_at,"
        "access_expires_at,family_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (new_id("tok"), client_id, user_id, oidc._sha(raw), "docweave.read docweave.write", LEGACY, now(),
         now() + expires_in, new_id("fam")))
    return raw


def _snapshot(store):
    """Every row the undo could touch, plus the data it must not."""
    tables = ("workspaces", "memberships", "workspace_apps", "app_entitlements", "invitations",
              "oauth_tokens", "oauth_codes", "projects", "templates", "source_files", "jobs", "artifacts",
              "audit_events", "notifications", "sessions")
    out = {}
    for t in tables:
        rows = store.all(f"SELECT * FROM {t}")
        if t == "sessions":
            rows = [{k: v for k, v in r.items() if k not in ("last_seen_at", "idle_expires_at")} for r in rows]
        out[t] = sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
    return out


def _audit(env, action="admin.workspace.unclaim"):
    rows = env["store"].all("SELECT * FROM audit_events WHERE action=? ORDER BY seq", (action,))
    for r in rows:
        r["meta"] = json.loads(r["meta"] or "{}")
    return rows


# ---------------------------------------------------------------- who may undo it
def test_an_ordinary_account_gets_the_same_404_as_for_the_rest_of_administration(env):
    _claim(env, env["alice"]["id"])
    plain = _login(env["api"], "bob@example.com")
    assert _preview(env, plain).status_code == 404
    r = _unclaim(env, client=plain)
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert _members(env) == {env["alice"]["id"]} and _ws(env)["status"] == "active"


def test_signed_out_is_401(env):
    _claim(env, env["alice"]["id"])
    assert _preview(env).status_code == 200          # the route exists and answers an administrator
    anon = TestClient(env["api"])
    r = anon.get(f"/api/admin/workspaces/{LEGACY}/unclaim-preview")
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"
    assert anon.post(f"/api/admin/workspaces/{LEGACY}/unclaim", json={"reason": REASON}).status_code == 401
    assert _members(env) == {env["alice"]["id"]}


def test_the_owner_the_claim_created_cannot_undo_it(env):
    """A workspace role — even owner of this very workspace — never opens platform administration."""
    _claim(env, env["alice"]["id"])
    assert _preview(env).json()["members"][0]["user_id"] == env["alice"]["id"]
    owner = _login(env["api"], "alice@example.com")
    _reauth(owner)
    assert _preview(env, owner).status_code == 404
    r = _unclaim(env, client=owner)
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert _ws(env)["status"] == "active" and _audit(env) == []
    assert _unclaim(env).status_code == 200          # the same request from an administrator works


def test_an_organization_owner_cannot_undo_it(env):
    from synwevo_core.organizations import Organizations
    _claim(env, env["alice"]["id"])
    Organizations(env["store"], env["ident"]).create(env["bob"]["id"], "Bob 的组织", "Bob 的空间", "fixture-idem-key-1")
    org_owner = _login(env["api"], "bob@example.com")
    _reauth(org_owner)
    assert _preview(env, org_owner).status_code == 404
    assert _unclaim(env, client=org_owner).status_code == 404
    assert _members(env) == {env["alice"]["id"]} and _audit(env) == []
    assert _unclaim(env).status_code == 200


def test_an_administrator_without_a_fresh_reauthentication_is_refused(env):
    _claim(env, env["alice"]["id"])
    fresh = _login(env["api"], "admin@example.com")        # a new sign-in is not a confirmation
    before = _snapshot(env["store"])
    r = _unclaim(env, client=fresh)
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"
    assert r.json()["detail"] == {"scope": "admin"}
    after = _snapshot(env["store"])
    assert after["memberships"] == before["memberships"] and after["workspaces"] == before["workspaces"]
    # looking needs no re-authentication
    assert _preview(env, fresh).status_code == 200


def test_an_administrator_signed_in_with_one_factor_is_refused(env):
    _claim(env, env["alice"]["id"])
    from synwevo_core.sessions import COOKIE_NAME
    import hashlib
    sid = hashlib.sha256(env["c"].cookies.get(COOKIE_NAME).encode()).hexdigest()
    env["store"].execute("UPDATE sessions SET amr='otp' WHERE id=?", (sid,))
    r = _unclaim(env)
    assert r.status_code == 403 and r.json()["error"] == "step_up_required"
    assert _ws(env)["status"] == "active"


# ---------------------------------------------------------------- refusals
@pytest.mark.parametrize("reason", ["", "   ", "太短了", "x" * 501])
def test_a_written_reason_is_required(env, reason):
    _claim(env, env["alice"]["id"])
    r = post(env["c"], f"/api/admin/workspaces/{LEGACY}/unclaim", {"reason": reason})
    assert r.status_code == 400 and r.json()["error"] == "missing_reason"
    assert _members(env) == {env["alice"]["id"]}


def test_data_nobody_was_assigned_has_nothing_to_undo(env):
    _reauth(env["c"])
    for r in (_unclaim(env), _preview(env)):
        assert r.status_code == 409 and r.json()["error"] == "not_assigned", r.text
        assert r.json()["message"] == "这份历史数据目前没有指派给任何人，无需撤销"
    assert _ws(env)["status"] == "pending_claim"


def test_an_ordinary_workspace_is_not_undone_through_this_route(env):
    theirs = env["ident"].workspaces_for(env["alice"]["id"])[0]["id"]
    _reauth(env["c"])
    for r in (post(env["c"], f"/api/admin/workspaces/{theirs}/unclaim", {"reason": REASON}), _preview(env, wid=theirs)):
        assert r.status_code == 409 and r.json()["error"] == "not_legacy"
    assert env["ident"].membership(theirs, env["alice"]["id"])["role"] == "owner"
    assert post(env["c"], "/api/admin/workspaces/w_nothing/unclaim", {"reason": REASON}).status_code == 404


def test_a_frozen_assignment_is_undone_without_handing_the_data_back_first(env):
    """Freezing is the stop switch an administrator pulls on seeing a wrong assignment. The undo
    works from there: it used to demand an unfreeze first, which gave the wrong account the data
    back for as long as the two steps took (review finding: frozen → unfreeze → access)."""
    apps, alice = env["apps"], env["alice"]["id"]
    _claim(env, alice)
    assert post(env["c"], f"/api/admin/workspaces/{LEGACY}/freeze", {"reason": "指派错了，先停止访问"}).status_code == 200
    assert apps.check_access(alice, "docweave", LEGACY)[0] is False

    plan = _preview(env)
    assert plan.status_code == 200, plan.text
    assert plan.json()["workspace"]["status"] == "frozen"
    assert [m["email"] for m in plan.json()["members"]] == ["alice@example.com"]
    r = _unclaim(env)
    assert r.status_code == 200, r.text
    assert r.json()["previous_status"] == "frozen" and r.json()["status"] == "pending_claim"
    # at no point did the wrong account get the data back
    assert apps.check_access(alice, "docweave", LEGACY)[0] is False
    assert _members(env) == set() and _ws(env)["status"] == "pending_claim"
    assert _audit(env, "admin.workspace.unfreeze") == []
    [row] = _audit(env)
    assert row["meta"]["previous_status"] == "frozen"
    # the freeze does not outlive the undo: a new assignment works the normal way
    _claim(env, env["bob"]["id"])
    assert _ws(env)["status"] == "active" and apps.check_access(env["bob"]["id"], "docweave", LEGACY)[0] is True


def test_a_deleted_workspace_is_refused(env):
    _claim(env, env["alice"]["id"])
    env["store"].execute("UPDATE workspaces SET status='deleted', deleted_at=? WHERE id=?", (now(), LEGACY))
    r = _unclaim(env)
    assert r.status_code == 409 and r.json()["error"] == "workspace_deleted"
    assert _members(env) == {env["alice"]["id"]}


def test_a_workspace_that_belongs_to_an_organization_is_refused(env):
    from synwevo_core.organizations import Organizations
    _claim(env, env["alice"]["id"])
    org = Organizations(env["store"], env["ident"]).create(env["alice"]["id"], "某组织", "某空间", "fixture-idem-key-2")
    env["store"].execute("UPDATE workspaces SET organization_id=? WHERE id=?", (org["organization"]["id"], LEGACY))
    for r in (_unclaim(env), _preview(env)):
        assert r.status_code == 409 and r.json()["error"] == "organization_workspace"
    assert _members(env) == {env["alice"]["id"]} and _ws(env)["status"] == "active"


def test_members_who_changed_after_the_confirmation_are_not_removed_unseen(env):
    from synwevo_identity.invites import Invites
    _claim(env, env["alice"]["id"])
    shown = [m["user_id"] for m in _preview(env).json()["members"]]
    _, raw = Invites(env["store"], env["ident"]).create(workspace_id=LEGACY, email="bob@example.com",
                                                        role="editor", actor_id=env["alice"]["id"])
    Invites(env["store"], env["ident"]).accept(raw, password=None, display_name=None, as_user_id=env["bob"]["id"])
    r = _unclaim(env, {"expected_members": shown})
    assert r.status_code == 409 and r.json()["error"] == "assignment_changed"
    assert _members(env) == {env["alice"]["id"], env["bob"]["id"]}
    assert _unclaim(env, {"expected_members": "u_1"}).status_code == 400
    r = _unclaim(env, {"expected_members": [env["bob"]["id"], env["alice"]["id"]]})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- effects
def test_the_undo_withdraws_every_kind_of_access_and_deletes_no_data(env):
    from synwevo_identity.invites import Invites
    store, ident, apps = env["store"], env["ident"], env["apps"]
    alice, bob = env["alice"]["id"], env["bob"]["id"]
    _claim(env, alice)
    # a second member, invited after the claim through the product's own flow
    _, raw = Invites(store, ident).create(workspace_id=LEGACY, email="bob@example.com", role="viewer", actor_id=alice)
    Invites(store, ident).accept(raw, password=None, display_name=None, as_user_id=bob)
    # an invitation nobody has accepted yet
    pending, _ = Invites(store, ident).create(workspace_id=LEGACY, email="carol@example.com", role="editor", actor_id=alice)
    # tokens acting in the workspace: both people's and a service account's
    oidc.Clients(store).register(client_id="docweave-web", app_id="docweave", name="Synwevo Docs",
                                 redirect_uris=["http://docweave.localhost:8765/auth/callback"],
                                 scopes="openid profile email docweave.read docweave.write")
    _, secret = oidc.Clients(store).register(client_id="legacy-batch", app_id="docweave", name="历史数据批处理",
                                             redirect_uris=[], confidential=True, kind="service",
                                             workspace_id=LEGACY, api_scopes="docweave.read")
    r = TestClient(env["api"]).post("/oidc/token", data={"grant_type": "client_credentials", "client_id": "legacy-batch",
                                                         "client_secret": secret, "scope": "docweave.read"})
    assert r.status_code == 200, r.text
    service_raw = r.json()["access_token"]
    alice_raw, bob_raw = _token(env, user_id=alice), _token(env, user_id=bob)
    store.execute("INSERT INTO oauth_codes (code_hash,client_id,user_id,redirect_uri,scope,auth_time,workspace_id,"
                  "created_at,expires_at) VALUES ('code_fixture','docweave-web',?,'x','openid',?,?,?,?)",
                  (alice, now(), LEGACY, now(), now() + 60))
    assert apps.check_access(alice, "docweave", LEGACY)[0] and apps.check_access(bob, "docweave", LEGACY)[0]
    data_before = {t: _snapshot(store)[t] for t in ("projects", "templates", "source_files", "jobs", "artifacts")}

    r = _unclaim(env)
    assert r.status_code == 200, r.text
    out = r.json()

    # memberships: all of them, not only the account the claim named
    assert _members(env) == set()
    assert {m["user_id"] for m in out["removed_members"]} == {alice, bob}
    # entitlements: nothing live is left, and each former member has an explicit revoked row
    assert store.one("SELECT COUNT(*) AS n FROM app_entitlements WHERE workspace_id=? AND revoked_at IS NULL",
                     (LEGACY,))["n"] == 0
    for uid in (alice, bob):
        assert store.one("SELECT revoked_at FROM app_entitlements WHERE workspace_id=? AND app_id='docweave' "
                         "AND user_id=?", (LEGACY, uid))["revoked_at"] is not None
        assert apps.check_access(uid, "docweave", LEGACY)[0] is False
    # applications: an unassigned workspace has none
    assert store.all("SELECT * FROM workspace_apps WHERE workspace_id=?", (LEGACY,)) == []
    assert out["apps_disabled"] == ["docweave"]
    assert apps.check_access_for_workspace(LEGACY, "docweave")[0] is False
    # invitations
    assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (pending["id"],))["revoked_at"] is not None
    assert out["invitations_revoked"] == 1
    # tokens, people's and the service account's; the unexchanged code is spent
    grants = oidc.Grants(store)
    for raw_token in (alice_raw, bob_raw, service_raw):
        assert grants.load_access(raw_token) is None
    assert {r["revoked_reason"] for r in store.all("SELECT revoked_reason FROM oauth_tokens WHERE workspace_id=?",
                                                   (LEGACY,))} == {"workspace_unclaimed"}
    assert out["tokens_revoked"] == 3
    assert store.one("SELECT used_at FROM oauth_codes WHERE code_hash='code_fixture'")["used_at"] is not None
    # the service account itself is not deleted or disabled — it stops, as when a workspace is frozen
    assert store.one("SELECT disabled_at FROM oauth_clients WHERE client_id='legacy-batch'")["disabled_at"] is None
    r = TestClient(env["api"]).post("/oidc/token", data={"grant_type": "client_credentials", "client_id": "legacy-batch",
                                                         "client_secret": secret, "scope": "docweave.read"})
    assert r.status_code >= 400, "an unassigned workspace issues no new service tokens"
    # state
    ws = _ws(env)
    assert ws["status"] == "pending_claim" and ws["name"] == GENERATED
    # nothing of the data is deleted
    assert {t: _snapshot(store)[t] for t in data_before} == data_before
    assert store.has_blob(env["blob"])
    # the undo is journalled (one entry, written ahead of the commit), so an older backup
    # restored later does not bring the access back
    entries = [e for e in journal.read(store.root) if e["kind"] == "workspace_unclaimed"]
    assert [(e["workspace_id"], e["label_restored"]) for e in entries] == [(LEGACY, True)]
    assert out["journaled"] is True
    # the people who lost access are told, the way removing a member tells them — by the
    # administrator's role, not their name or email
    for uid in (alice, bob):
        items = env["api"].state.notifier.list(uid)["items"]
        removed = [i for i in items if i["kind"] == "member.removed"]
        assert removed and "历史数据" in removed[0]["title"] and "由 平台管理员 操作" in removed[0]["body"]
        assert "平台管理员甲" not in removed[0]["body"] and "admin@example.com" not in removed[0]["body"]
    assert out["notified"] == 2
    # platform administration gained nothing
    assert apps.check_access(env["admin"]["id"], "docweave", LEGACY)[0] is False


def test_the_administrator_who_assigned_it_to_themselves_is_not_notified_by_themselves(env):
    _claim(env, env["admin"]["id"])
    r = _unclaim(env)
    assert r.status_code == 200 and r.json()["notified"] == 0
    assert _members(env) == set()


def test_approvals_stop_counting_by_the_existing_owner_rule_and_are_not_revoked(env):
    """The rule already in force (`Approvals.require_owner`): an approval is in effect only while
    its approver is a current owner with the application. Losing the owners stops it; nothing is
    marked revoked, exactly as when an owner is removed."""
    from synwevo_core.api_platform import Approvals
    from synwevo_core.errors import ServiceError
    from synwevo_core.machine import docs_resource
    alice = env["alice"]["id"]
    _claim(env, alice)
    approvals = Approvals(env["store"])
    row = approvals.create(env["admin"]["id"], {"workspace_id": LEGACY, "name": "历史数据导出", "credential_kind": "api_key",
                                                "scopes": ["docweave.read"], "resources": [docs_resource()]}, "fixture approval")
    approvals.decide(row["id"], alice, "approve", {"scopes": ["docweave.read"], "resources": [docs_resource()]}, "同意导出")
    assert approvals.active(row["id"])["status"] == "approved"
    assert _preview(env).json()["approvals"] == 1

    assert _unclaim(env).status_code == 200
    with pytest.raises(ServiceError) as e:
        approvals.active(row["id"])
    assert e.value.code == "owner_required"
    assert approvals.get(row["id"])["status"] == "approved"


def test_the_name_goes_back_only_over_the_label_the_claim_set(env):
    ident = env["ident"]
    # the claim's default label
    _claim(env, env["alice"]["id"])
    assert _ws(env)["name"] == "历史数据"
    assert _preview(env).json()["name_after"] == GENERATED
    assert _unclaim(env).json()["name"] == GENERATED
    # a name given to the claim
    _claim(env, env["alice"]["id"], name="旧系统资料")
    assert _unclaim(env).status_code == 200 and _ws(env)["name"] == GENERATED
    # a name an administrator chose after the claim stays
    _claim(env, env["alice"]["id"])
    env["store"].execute("UPDATE workspaces SET name='财务归档' WHERE id=?", (LEGACY,))
    plan = _preview(env).json()
    assert plan["name_after"] == "财务归档" and plan["name_restored"] is False
    assert _unclaim(env).status_code == 200 and _ws(env)["name"] == "财务归档"
    # ...and so does one chosen before it, which the claim kept
    _claim(env, env["alice"]["id"])
    assert _ws(env)["name"] == "财务归档"
    assert _unclaim(env).status_code == 200 and _ws(env)["name"] == "财务归档"
    assert ident.get_workspace(LEGACY)["status"] == "pending_claim"


def test_a_claim_recorded_before_names_were_audited_restores_only_its_default(env):
    """An assignment made before the claim recorded names: only its default label can be told
    apart from a chosen name."""
    store, alice = env["store"], env["alice"]["id"]
    ts = now()
    with store.tx() as t:
        t.execute("UPDATE workspaces SET status='active', name='历史数据' WHERE id=?", (LEGACY,))
        t.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at) "
                  "VALUES (?,?,'owner',1,?,?)", (LEGACY, alice, ts, ts))
        t.execute("INSERT INTO audit_events (id,at,actor_kind,actor_id,workspace_id,action,object_kind,object_id,result,meta) "
                  "VALUES ('ae_old_claim',?,'user',?,?,'admin.workspace.claim','workspace',?,'ok',?)",
                  (ts, env["admin"]["id"], LEGACY, LEGACY,
                   json.dumps({"target_user": alice, "target_email": "alice@example.com", "role": "owner",
                               "reason": "旧版本的认领记录", "projects": 3})))
    plan = _preview(env).json()
    assert plan["name_after"] == GENERATED and plan["assigned"]["to_email"] == "alice@example.com"
    _reauth(env["c"])
    assert _unclaim(env).status_code == 200 and _ws(env)["name"] == GENERATED


# ---------------------------------------------------------------- audit
def test_the_undo_is_audited_with_who_why_and_what_it_withdrew(env):
    from synwevo_identity.invites import Invites
    alice = env["alice"]["id"]
    _claim(env, alice)
    Invites(env["store"], env["ident"]).create(workspace_id=LEGACY, email="carol@example.com", role="viewer", actor_id=alice)
    _token(env, user_id=alice)
    assert _unclaim(env).status_code == 200
    [row] = _audit(env)
    assert row["actor_id"] == env["admin"]["id"] and row["actor_kind"] == "user"
    assert row["object_kind"] == "workspace" and row["object_id"] == LEGACY and row["workspace_id"] == LEGACY
    meta = row["meta"]
    assert meta["reason"] == REASON
    assert meta["removed_members"] == [{"user_id": alice, "email": "alice@example.com", "role": "owner"}]
    assert meta["invitations_revoked"] == 1 and meta["tokens_revoked"] == 1
    assert meta["previous_name"] == "历史数据" and meta["previous_status"] == "active"
    assert meta["name"] == GENERATED and meta["status"] == "pending_claim"
    assert meta["assigned_by"] == env["admin"]["id"]
    assert meta["projects"] == 3 and meta["templates"] == 2
    assert meta["operator_keys"] == 0 and meta["jobs_stopping"] == 0
    # it can say so: the journal entry is written before the commit or the undo does not happen
    assert meta["journaled"] is True
    assert "历史项目" not in row["meta"].__repr__(), "审计只记数量，不记历史文档的名字"
    # the claim now records the names it saw and set, inside its own transaction
    [claim] = _audit(env, "admin.workspace.claim")
    assert claim["meta"]["name_before"] == GENERATED and claim["meta"]["name_after"] == "历史数据"


# ---------------------------------------------------------------- preview
def test_the_preview_names_who_loses_access_and_changes_nothing(env):
    from synwevo_identity.invites import Invites
    store, alice, bob = env["store"], env["alice"]["id"], env["bob"]["id"]
    _claim(env, alice)
    _, raw = Invites(store, env["ident"]).create(workspace_id=LEGACY, email="bob@example.com", role="editor", actor_id=alice)
    Invites(store, env["ident"]).accept(raw, password=None, display_name=None, as_user_id=bob)
    Invites(store, env["ident"]).create(workspace_id=LEGACY, email="carol@example.com", role="viewer", actor_id=alice)
    _token(env, user_id=alice)
    _token(env, user_id=bob, expires_in=-60)        # expired: nobody can use it
    fresh = _login(env["api"], "admin@example.com")  # looking needs no re-authentication
    before = _snapshot(store)

    r = _preview(env, fresh)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["workspace"] == {"id": LEGACY, "name": "历史数据", "status": "active"}
    assert plan["name_after"] == GENERATED and plan["name_restored"] is True
    assert plan["members"] == [
        {"user_id": alice, "email": "alice@example.com", "display_name": "Alice", "role": "owner"},
        {"user_id": bob, "email": "bob@example.com", "display_name": "Bob", "role": "editor"}]
    assert plan["invitations"] == 1 and plan["tokens"] == 1
    assert plan["projects"] == 3 and plan["templates"] == 2
    assigned = plan["assigned"]
    assert assigned["by_email"] == "admin@example.com" and assigned["to_email"] == "alice@example.com"
    assert assigned["role"] == "owner" and assigned["reason"] == CLAIM_REASON and assigned["at"] > 0
    assert "历史项目" not in r.text and "历史模板" not in r.text, "预览只给数量，不给历史文档的名字"
    assert _snapshot(store) == before, "预览没有任何副作用"


# ---------------------------------------------------------------- concurrency
def _race(*calls):
    results, barrier = [None] * len(calls), threading.Barrier(len(calls))

    def run(i, fn):
        barrier.wait()
        results[i] = fn()

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_two_undos_at_once_one_wins_and_the_other_is_told_there_is_nothing_to_undo(env):
    _claim(env, env["alice"]["id"])
    a, b = _race(lambda: _unclaim(env), lambda: _unclaim(env))
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    loser = a if a.status_code == 409 else b
    assert loser.json()["error"] == "not_assigned"
    assert len(_audit(env)) == 1 and _ws(env)["status"] == "pending_claim"


def _consistent(env):
    """No half state: either assigned (members, the application, their grants) or not (none)."""
    store = env["store"]
    ws, members = _ws(env), _members(env)
    apps_on = store.all("SELECT app_id FROM workspace_apps WHERE workspace_id=?", (LEGACY,))
    live = store.one("SELECT COUNT(*) AS n FROM app_entitlements WHERE workspace_id=? AND revoked_at IS NULL", (LEGACY,))["n"]
    if ws["status"] == "pending_claim":
        assert members == set() and apps_on == [] and live == 0, (ws, members, apps_on, live)
    else:
        assert ws["status"] == "active" and len(members) >= 1 and apps_on and live >= 1, (ws, members, apps_on, live)
    return ws["status"], members


def test_an_undo_racing_a_new_claim_leaves_one_whole_state(env):
    alice, bob = env["alice"]["id"], env["bob"]["id"]
    for _ in range(3):
        _claim(env, alice)
        undo, claim = _race(lambda: _unclaim(env),
                            lambda: post(env["c"], f"/api/admin/workspaces/{LEGACY}/claim",
                                         {"user_id": bob, "reason": CLAIM_REASON}))
        assert undo.status_code == 200, undo.text
        status, members = _consistent(env)
        if claim.status_code == 200:          # the undo went first, then the claim
            assert status == "active" and members == {bob}
            assert _unclaim(env).status_code == 200
        else:                                 # the claim found the data still assigned
            assert claim.json()["error"] == "not_claimable" and status == "pending_claim"
        _consistent(env)


def test_an_undo_racing_an_invitation_being_accepted_leaves_nobody_behind(env):
    from synwevo_identity.invites import Invites
    alice, bob = env["alice"]["id"], env["bob"]["id"]
    _claim(env, alice)
    _, raw = Invites(env["store"], env["ident"]).create(workspace_id=LEGACY, email="bob@example.com",
                                                        role="editor", actor_id=alice)

    def accept():
        try:
            Invites(env["store"], env["ident"]).accept(raw, password=None, display_name=None, as_user_id=bob)
            return "joined"
        except Exception as e:  # noqa: BLE001 - the refusal is the outcome being tested
            return getattr(e, "code", repr(e))

    undo, joined = _race(lambda: _unclaim(env), accept)
    assert undo.status_code == 200, undo.text
    assert _members(env) == set() and _ws(env)["status"] == "pending_claim"
    removed = {m["user_id"] for m in undo.json()["removed_members"]}
    if joined == "joined":
        assert removed == {alice, bob}
    else:
        assert joined in ("invite_revoked", "workspace_unavailable", "inviter_lost_permission") and removed == {alice}
    assert env["apps"].check_access(bob, "docweave", LEGACY)[0] is False


# ---------------------------------------------------------------- again and again
def test_claim_undo_claim_works_repeatedly(env):
    apps, alice, bob = env["apps"], env["alice"]["id"], env["bob"]["id"]
    for target in (alice, bob, alice):
        _claim(env, target)
        assert apps.check_access(target, "docweave", LEGACY) == (True, LEGACY, "ok")
        assert env["ident"].membership(LEGACY, target)["role"] == "owner"
        overview = env["c"].get("/api/admin/overview").json()["unclaimed"]
        assert overview["status"] == "active" and overview["members"] == 1
        assert _unclaim(env).status_code == 200
        assert apps.check_access(target, "docweave", LEGACY)[0] is False
        overview = env["c"].get("/api/admin/overview").json()["unclaimed"]
        assert overview["status"] == "pending_claim" and overview["members"] == 0 and overview["apps"] == []
        assert overview["projects"] == 3
    assert [e["meta"]["removed_members"][0]["user_id"] for e in _audit(env)] == [alice, bob, alice]


# ---------------------------------------------------------------- what the confirmation must say
def test_the_preview_counts_what_keeps_working_and_what_stops(env):
    """Two things the undo does not do, said before it is confirmed: an operator's `dw_` key keeps
    working (the `pending_claim` rule), and queued or running work of the people and credentials
    that lose access is cancelled by the runner (`authority.check`). The counts must agree with
    what the runner and the key checker then decide."""
    from synwevo_core import authority
    from synwevo_core.api_platform import Approvals
    from synwevo_core.auth import Auth
    from synwevo_core.machine import docs_resource
    store, alice = env["store"], env["alice"]["id"]
    _claim(env, alice)
    auth = Auth(store)
    _, operator_key = auth.create_key("运维脚本", LEGACY, ("read", "write"))
    _, revoked_key = auth.create_key("停用的脚本", LEGACY, ("read",))
    auth.revoke_key(revoked_key["id"])
    _, elsewhere = auth.create_key("别的空间", env["ident"].workspaces_for(env["bob"]["id"])[0]["id"], ("read",))
    approvals = Approvals(store)
    row = approvals.create(env["admin"]["id"], {"workspace_id": LEGACY, "name": "历史导出", "credential_kind": "api_key",
                                                "scopes": ["docweave.write"], "resources": [docs_resource()]}, "fixture approval")
    approvals.decide(row["id"], alice, "approve", {"scopes": ["docweave.write"], "resources": [docs_resource()]}, "同意导出")
    with store.tx():
        _, bound_key = auth.create_key("历史导出", LEGACY, ("write",))
        approvals.bind(row["id"], "api_key", bound_key["id"])
    oidc.Clients(store).register(client_id="svc_legacy", app_id="docweave", name="批处理", redirect_uris=[],
                                 confidential=True, kind="service", workspace_id=LEGACY, api_scopes="docweave.write")
    jobs = {
        "j_person": ("running", {"kind": "user", "user_id": alice, "tenant": LEGACY}),
        "j_delegated": ("queued", {"kind": "delegated", "client_id": "docweave-web", "user_id": alice,
                                   "family_id": "fam_x", "tenant": LEGACY}),
        "j_service": ("queued", {"kind": "service_account", "client_id": "svc_legacy", "tenant": LEGACY}),
        "j_approved_key": ("queued", {"kind": "api_key", "key_id": bound_key["id"], "lineage": bound_key["id"], "tenant": LEGACY}),
        "j_operator_key": ("running", {"kind": "api_key", "key_id": operator_key["id"], "lineage": operator_key["id"],
                                       "tenant": LEGACY}),
        "j_operator": ("queued", None),                                   # the pre-identity operator modes
        "j_finished": ("completed", {"kind": "user", "user_id": alice, "tenant": LEGACY}),
    }
    for jid, (status, desc) in jobs.items():
        store.execute("INSERT INTO jobs (id,project_id,status,created_at,tenant,principal) VALUES (?,?,?,?,?,?)",
                      (jid, "p_legacy0", status, now(), LEGACY, authority.dumps(desc)))

    plan = _preview(env).json()
    assert plan["operator_keys"] == 1, "只数没有被所有者审批绑定、未撤销、属于这个空间的运维密钥"
    assert plan["jobs_stopping"] == 4
    out = _unclaim(env).json()
    assert out["operator_keys"] == 1 and out["jobs_stopping"] == 4
    # ...and that is what the rules then decide
    stopped = {jid for jid, (status, desc) in jobs.items() if status != "completed"
               and not authority.check(store, desc, need="write", tenant=LEGACY)[0]}
    assert stopped == {"j_person", "j_delegated", "j_service", "j_approved_key"}
    assert elsewhere["tenant"] != LEGACY


# ---------------------------------------------------------------- the notice former members get
def test_former_members_are_told_by_the_role_never_by_the_administrator_s_email(env):
    """An administrator without a display name used to be named by their email in the inbox
    and the mail of every former member — including an account the data was assigned to by
    mistake. The notice names the role, in each language, and stores no identity of theirs."""
    store, alice = env["store"], env["alice"]["id"]
    env["ident"].create_user("root-admin@example.com", PW, status="active", platform_role="admin")
    root = _login(env["api"], "root-admin@example.com")
    _claim(env, alice)
    _reauth(root)
    assert _unclaim(env, client=root).status_code == 200
    row = store.one("SELECT * FROM notifications WHERE user_id=? AND kind='member.removed'", (alice,))
    assert "root-admin" not in row["params"] and "@" not in row["params"]
    notifier = env["api"].state.notifier
    body = {loc: notifier.render(row, loc)["body"] for loc in ("zh-Hans", "zh-Hant", "en")}
    assert body["zh-Hans"].startswith("由 平台管理员 操作。")
    assert body["zh-Hant"].startswith("由 平台管理員 操作。")
    assert body["en"].startswith("Done by a platform administrator.")
    # the marker is its own key: a person whose name reads like it is still named as themselves
    spoof = {**row, "params": json.dumps({"workspace": "某空间", "actor": "platform_admin"})}
    assert notifier.render(spoof, "en")["body"].startswith("Done by platform_admin.")


# ---------------------------------------------------------------- the security journal
def test_the_undo_is_journalled_before_it_commits(env, monkeypatch):
    """Write-ahead: when the entry is written, the undo is not yet visible to anyone else. A line
    written after the COMMIT could be lost to a crash in between, and an older backup restored
    later would then give the former members their access back."""
    import sqlite3
    _claim(env, env["alice"]["id"])
    seen = []
    real = journal.record

    def spy(store, kind, **fields):
        # what everyone else sees at the moment each journal line is written: committed rows only
        other = sqlite3.connect(f"file:{store.db_path}?mode=ro", uri=True)
        try:
            seen.append((kind, other.execute("SELECT status FROM workspaces WHERE id=?", (LEGACY,)).fetchone()[0],
                         other.execute("SELECT COUNT(*) FROM memberships WHERE workspace_id=?", (LEGACY,)).fetchone()[0]))
        finally:
            other.close()
        return real(store, kind, **fields)

    monkeypatch.setattr(journal, "record", spy)
    assert _unclaim(env).status_code == 200
    assert seen == [("workspace_unclaimed", "active", 1)], "每一行日志都写在撤销提交之前"


def test_an_undo_that_cannot_be_journalled_is_not_performed(env, monkeypatch):
    """No undo that a restore would silently reverse: nothing changes, the administrator is told
    why in a sentence, and the attempt is audited as an error."""
    _claim(env, env["alice"]["id"])
    before = _snapshot(env["store"])
    real = journal.record

    def broken(store, kind, **fields):
        raise OSError("fixture: the journal's disk is full")

    monkeypatch.setattr(journal, "record", broken)
    r = _unclaim(env)
    assert r.status_code == 503 and r.json()["error"] == "journal_unavailable", r.text
    assert "没有执行" in r.json()["message"] and "冻结" in r.json()["message"]
    after = _snapshot(env["store"])
    assert {t: v for t, v in after.items() if t != "audit_events"} == {t: v for t, v in before.items() if t != "audit_events"}
    [row] = _audit(env)
    assert row["result"] == "error" and row["meta"] == {"code": "journal_unavailable", "reason": REASON}
    assert env["apps"].check_access(env["alice"]["id"], "docweave", LEGACY)[0] is True
    # the audited claim is still the assignment the overview shows
    assert _preview(env).json()["assigned"]["to_email"] == "alice@example.com"
    monkeypatch.setattr(journal, "record", real)
    assert _unclaim(env).status_code == 200
    assert [a["result"] for a in _audit(env)] == ["error", "ok"]


def _backup(env, tmp_path, name):
    """Fixture: a copy of the database as a backup taken now would hold it (SQLite's own backup)."""
    import sqlite3
    from pathlib import Path
    dest = tmp_path / name
    dest.mkdir()
    src = sqlite3.connect(env["store"].db_path)
    dst = sqlite3.connect(dest / Path(env["store"].db_path).name)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return Store(dest)


def test_a_restore_from_before_the_undo_applies_it_whatever_the_backup_s_time(env, tmp_path):
    """A backup taken between the journal line and the COMMIT has neither the undo nor — by its
    cutoff — a reason to replay the line. The undo's entry is replayed on every restore and
    applied as of its own time."""
    from synwevo_identity.invites import Invites
    store, ident, alice, bob = env["store"], env["ident"], env["alice"]["id"], env["bob"]["id"]
    _claim(env, alice)
    _, raw = Invites(store, ident).create(workspace_id=LEGACY, email="bob@example.com", role="editor", actor_id=alice)
    Invites(store, ident).accept(raw, password=None, display_name=None, as_user_id=bob)
    pending, _ = Invites(store, ident).create(workspace_id=LEGACY, email="carol@example.com", role="viewer", actor_id=alice)
    _token(env, user_id=alice)
    restored = _backup(env, tmp_path, "assigned")
    assert _unclaim(env).status_code == 200

    journal.apply(restored, journal.read(store.root, since=now() + 60))     # a cutoff later than every line
    assert restored.one("SELECT status, name FROM workspaces WHERE id=?", (LEGACY,)) == \
        {"status": "pending_claim", "name": GENERATED}
    assert restored.all("SELECT * FROM memberships WHERE workspace_id=?", (LEGACY,)) == []
    assert restored.all("SELECT * FROM workspace_apps WHERE workspace_id=?", (LEGACY,)) == []
    assert restored.one("SELECT COUNT(*) AS n FROM app_entitlements WHERE workspace_id=? AND revoked_at IS NULL",
                        (LEGACY,))["n"] == 0
    assert restored.one("SELECT revoked_at FROM invitations WHERE id=?", (pending["id"],))["revoked_at"] is not None
    assert restored.one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE workspace_id=? AND revoked_at IS NULL",
                        (LEGACY,))["n"] == 0
    for uid in (alice, bob):
        assert Apps(restored, Identity(restored)).check_access(uid, "docweave", LEGACY)[0] is False
    # applying it again changes nothing
    once = _snapshot(restored)
    journal.apply(restored, journal.read(store.root))
    assert _snapshot(restored) == once


def test_replaying_an_undo_never_takes_back_a_later_assignment(env, tmp_path):
    """Grants are not journalled, so a restore replays every undo; whatever was assigned after
    one — the membership, the application, the grant, tokens, invitations — must survive it."""
    from synwevo_identity.invites import Invites
    store, ident, alice, bob = env["store"], env["ident"], env["alice"]["id"], env["bob"]["id"]
    _claim(env, alice)
    assert _unclaim(env).status_code == 200
    _claim(env, bob)
    bob_token = _token(env, user_id=bob)
    pending, _ = Invites(store, ident).create(workspace_id=LEGACY, email="carol@example.com", role="viewer", actor_id=bob)
    restored = _backup(env, tmp_path, "reassigned")

    journal.apply(restored, journal.read(store.root))                        # every line: the oldest cutoff there is
    assert restored.one("SELECT status FROM workspaces WHERE id=?", (LEGACY,))["status"] == "active"
    assert {r["user_id"] for r in restored.all("SELECT user_id FROM memberships WHERE workspace_id=?", (LEGACY,))} == {bob}
    assert [r["app_id"] for r in restored.all("SELECT app_id FROM workspace_apps WHERE workspace_id=?", (LEGACY,))] == ["docweave"]
    assert Apps(restored, Identity(restored)).check_access(bob, "docweave", LEGACY) == (True, LEGACY, "ok")
    assert oidc.Grants(restored).load_access(bob_token) is not None
    assert restored.one("SELECT revoked_at FROM invitations WHERE id=?", (pending["id"],))["revoked_at"] is None


# ---------------------------------------------------------------- a new claim after an undo
def test_a_new_claim_records_its_own_grant_not_the_undo_s(env):
    """The undo writes an explicit revoked row per former member; claiming the data for one of
    them later must record that claim's time and administrator (`entitlement_granted_at`)."""
    from synwevo_identity.invites import Invites
    store, ident, alice, bob = env["store"], env["ident"], env["alice"]["id"], env["bob"]["id"]
    _claim(env, alice)
    _, raw = Invites(store, ident).create(workspace_id=LEGACY, email="bob@example.com", role="editor", actor_id=alice)
    Invites(store, ident).accept(raw, password=None, display_name=None, as_user_id=bob)
    other = ident.create_user("admin2@example.com", PW, status="active", platform_role="admin")
    second = _login(env["api"], "admin2@example.com")
    _reauth(second)
    assert _unclaim(env, client=second).status_code == 200
    _claim(env, bob)
    row = store.one("SELECT granted_at, granted_by, revoked_at FROM app_entitlements WHERE workspace_id=? "
                    "AND app_id='docweave' AND user_id=?", (LEGACY, bob))
    member = store.one("SELECT created_at FROM memberships WHERE workspace_id=? AND user_id=?", (LEGACY, bob))
    assert row["revoked_at"] is None
    assert row["granted_by"] == env["admin"]["id"] != other["id"]
    assert row["granted_at"] == member["created_at"], "同一次认领写下的成员关系与使用资格"


def test_an_undo_racing_a_freeze_always_ends_unassigned(env):
    """Freeze first: the undo takes the frozen assignment. Undo first: there is nothing left to
    freeze. Either way nobody is left holding the data."""
    for _ in range(3):
        _claim(env, env["alice"]["id"])
        undo, freeze = _race(lambda: _unclaim(env),
                             lambda: post(env["c"], f"/api/admin/workspaces/{LEGACY}/freeze", {"reason": "指派错了，先停止访问"}))
        assert undo.status_code == 200, undo.text
        assert freeze.status_code in (200, 409), freeze.text
        if freeze.status_code == 409:
            assert freeze.json()["error"] == "not_active"
        assert _consistent(env)[0] == "pending_claim"
