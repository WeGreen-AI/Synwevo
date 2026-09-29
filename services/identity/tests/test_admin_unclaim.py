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


def test_a_frozen_assignment_is_unfrozen_first(env):
    _claim(env, env["alice"]["id"])
    assert post(env["c"], f"/api/admin/workspaces/{LEGACY}/freeze", {"reason": "先冻结调查一下这个空间"}).status_code == 200
    for r in (_unclaim(env), _preview(env)):
        assert r.status_code == 409 and r.json()["error"] == "workspace_frozen"
        assert "请先解冻" in r.json()["message"]
    assert _ws(env)["status"] == "frozen" and _members(env) == {env["alice"]["id"]}
    assert post(env["c"], f"/api/admin/workspaces/{LEGACY}/unfreeze", {"reason": "调查结束，恢复使用"}).status_code == 200
    assert _unclaim(env).status_code == 200


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
    # the removals are journalled, so an older backup restored later does not bring them back
    entries = journal.read(store.root)
    assert {e["user_id"] for e in entries if e["kind"] == "member_removed" and e["workspace_id"] == LEGACY} == {alice, bob}
    assert any(e["kind"] == "invitation_revoked" and e["invitation_id"] == pending["id"] for e in entries)
    assert any(e["kind"] == "workspace_app_disabled" and e["workspace_id"] == LEGACY for e in entries)
    assert out["journaled"] is True
    # the people who lost access are told, the way removing a member tells them
    for uid in (alice, bob):
        items = env["api"].state.notifier.list(uid)["items"]
        removed = [i for i in items if i["kind"] == "member.removed"]
        assert removed and "历史数据" in removed[0]["title"] and "平台管理员甲" in removed[0]["body"]
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
