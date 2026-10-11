"""Platform administration, and the claim flow for the historical data.

Most of these tests are about what an administrator *cannot* do. The permission contract says
platform administration is not a superset of workspace access, and the only way that claim
stays true is if something fails when it stops being true.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.platform_access import PlatformAccess
from synwevo_core.store import Store, now

from .platform_fixture import make_platform_admin

PW = "a-sufficiently-long-passphrase"
REASON = "把历史数据交给它原本的所有者"


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

    admin = make_platform_admin(store, ident.create_user("admin@example.com", PW, status="active"))
    alice = ident.create_user("alice@example.com", PW, status="active")

    # The pre-identity data: one workspace, zero members, projects carrying tenant='default'.
    ts = now()
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES ('default','历史数据（待认领）','legacy','pending_claim',?,?)", (ts, ts))
    for i in range(3):
        store.execute("INSERT INTO projects (id,name,tenant,step,rev,mapping_rev,created_at,updated_at,deleted) "
                      "VALUES (?,?,'default','upload',1,1,?,?,0)",
                      (f"p_legacy{i}", f"历史项目 {i}", ts, ts))

    c = TestClient(api)
    post(c, "/api/auth/login", {"email": "admin@example.com", "password": PW})
    plain = TestClient(api)
    post(plain, "/api/auth/login", {"email": "alice@example.com", "password": PW})
    return {"api": api, "c": c, "plain": plain, "store": store, "ident": ident,
            "apps": Apps(store, ident), "admin": admin, "alice": alice}


def post(client, path, body=None):
    """POST with the CSRF token echoed, the way the real page does it.

    Without this every call gets a 403 from the double-submit check before it reaches the route
    — which is the protection working, but it means a test that forgets the header proves
    nothing about the route it meant to exercise.
    """
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    return client.post(path, json=body if body is not None else {},
                       headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


def _reauth(client):
    """Fresh re-authentication, server-side, scoped to administration."""
    r = post(client, "/api/auth/reauth", {"password": PW, "scope": "admin"})
    assert r.status_code == 200, r.text
    return r


def _confirmed_status(client, uid, status, reason):
    """Exercise the same bound preview and per-attempt key as the administration form."""
    import uuid
    preview = post(client, f"/api/admin/users/{uid}/status-preview", {"status": status})
    assert preview.status_code == 200, preview.text
    return post(client, f"/api/admin/users/{uid}/status",
                {"status": status, "reason": reason, "preview_token": preview.json()["preview_token"],
                 "idempotency_key": uuid.uuid4().hex})


# ---------------------------------------------------------------- access
def test_an_ordinary_account_cannot_see_the_admin_backend(env):
    """404, not 403: an ordinary account learns nothing about what exists here."""
    for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/workspaces",
                 "/api/admin/audit"):
        r = env["plain"].get(path)
        assert r.status_code == 404, f"{path} → {r.status_code}"


def test_the_backend_is_not_protected_by_a_hidden_button(env):
    """The route checks, not the UI. Typing the URL must get the same answer as not seeing it."""
    r = post(env["plain"], "/api/admin/workspaces/default/claim",
                          {"user_id": env["alice"]["id"], "reason": REASON})
    assert r.status_code == 404
    assert env["store"].one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id='default'")["n"] == 0


def test_signed_out_is_401(env):
    assert TestClient(env["api"]).get("/api/admin/overview").status_code == 401


# ---------------------------------------------------------------- overview
def test_the_overview_counts_the_unclaimed_data_without_naming_it(env):
    body = env["c"].get("/api/admin/overview").json()
    assert body["users"] == 2 and body["admins"] == 1
    unclaimed = body["unclaimed"]
    assert unclaimed["id"] == "default" and unclaimed["members"] == 0 and unclaimed["projects"] == 3
    blob = str(body)
    assert "历史项目" not in blob, "管理后台显示数量，不显示历史文档的标题"


# ---------------------------------------------------------------- claim
def test_claiming_needs_recent_reauth(env):
    r = post(env["c"], "/api/admin/workspaces/default/claim",
                      {"user_id": env["alice"]["id"], "reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"
    assert env["store"].one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id='default'")["n"] == 0


def test_claiming_needs_a_written_reason(env):
    _reauth(env["c"])
    for reason in ("", "   ", "太短"):
        r = post(env["c"], "/api/admin/workspaces/default/claim",
                          {"user_id": env["alice"]["id"], "reason": reason})
        assert r.status_code == 400 and r.json()["error"] == "missing_reason"


def test_claiming_requires_an_account_that_already_exists(env):
    _reauth(env["c"])
    r = post(env["c"], "/api/admin/workspaces/default/claim",
                      {"user_id": "u_nobody", "reason": REASON})
    assert r.status_code == 400 and r.json()["error"] == "bad_target"


def test_a_successful_claim_opens_exactly_that_workspace(env):
    c, store, ident, apps = env["c"], env["store"], env["ident"], env["apps"]
    assert apps.check_access(env["alice"]["id"], "docweave", "default")[0] is False

    _reauth(c)
    r = post(c, "/api/admin/workspaces/default/claim",
               {"user_id": env["alice"]["id"], "reason": REASON, "role": "owner"})
    assert r.status_code == 200, r.text
    assert r.json()["projects"] == 3

    ws = ident.get_workspace("default")
    assert ws["status"] == "active"
    m = ident.membership("default", env["alice"]["id"])
    assert m["role"] == "owner" and m["can_export"]
    assert apps.check_access(env["alice"]["id"], "docweave", "default")[0] is True

    # ...and nothing else moved.
    assert ident.get_user(env["alice"]["id"])["platform_role"] == "none", "认领不得顺带授予平台管理权"
    assert not PlatformAccess(store).live_grants(env["alice"]["id"]), "认领不得顺带授予任何平台角色"
    assert store.one("SELECT COUNT(*) AS n FROM memberships WHERE user_id=? AND workspace_id<>'default'",
                     (env["alice"]["id"],))["n"] == 1        # only their own personal workspace
    rows = store.all("SELECT tenant FROM projects WHERE id LIKE 'p_legacy%'")
    assert {r["tenant"] for r in rows} == {"default"}, "认领不改写任何一行业务数据"


def test_a_claim_is_audited_with_who_what_and_why(env):
    _reauth(env["c"])
    post(env["c"], "/api/admin/workspaces/default/claim",
                  {"user_id": env["alice"]["id"], "reason": REASON})
    row = env["store"].one("SELECT * FROM audit_events WHERE action='admin.workspace.claim'")
    assert row["actor_id"] == env["admin"]["id"] and row["object_id"] == "default"
    import json
    meta = json.loads(row["meta"])
    assert meta["target_user"] == env["alice"]["id"] and meta["reason"] == REASON
    assert meta["projects"] == 3


def test_a_workspace_cannot_be_claimed_twice(env):
    c = env["c"]
    _reauth(c)
    assert post(c, "/api/admin/workspaces/default/claim",
                  {"user_id": env["alice"]["id"], "reason": REASON}).status_code == 200
    again = post(c, "/api/admin/workspaces/default/claim",
                   {"user_id": env["admin"]["id"], "reason": REASON})
    assert again.status_code == 409 and again.json()["error"] == "not_claimable"


def test_an_ordinary_workspace_is_not_claimable(env):
    """This route exists for the pre-identity data, not as a way into anyone's workspace."""
    c, ident = env["c"], env["ident"]
    theirs = ident.workspaces_for(env["alice"]["id"])[0]["id"]
    _reauth(c)
    r = post(c, f"/api/admin/workspaces/{theirs}/claim",
               {"user_id": env["admin"]["id"], "reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "not_claimable"
    assert ident.membership(theirs, env["admin"]["id"]) is None


def test_claiming_does_not_let_the_admin_read_the_documents(env):
    """The claim gives the *target* access. It gives the administrator nothing."""
    c, apps = env["c"], env["apps"]
    _reauth(c)
    post(c, "/api/admin/workspaces/default/claim",
           {"user_id": env["alice"]["id"], "reason": REASON})
    assert apps.check_access(env["admin"]["id"], "docweave", "default")[0] is False
    assert env["ident"].membership("default", env["admin"]["id"]) is None
    # (Whether the admin's own session can reach the documents through the business API is a
    # cross-module question: the account service does not serve /api/projects at all, so asking
    # it here proved nothing. It is asserted against the composed server in
    # tests/integration/test_members_access.py.)


# ---------------------------------------------------------------- users
def test_suspending_ends_the_existing_sessions_too(env):
    c, plain = env["c"], env["plain"]
    assert plain.get("/api/auth/me").status_code == 200
    _reauth(c)
    r = _confirmed_status(c, env['alice']['id'], "suspended", "离职交接完成，停用账户")
    assert r.status_code == 200 and r.json()["sessions_revoked"] >= 1
    assert plain.get("/api/auth/me").status_code == 401, "停用在下一次请求生效，不是等会话过期"


def test_the_last_administrator_cannot_be_suspended(env):
    c = env["c"]
    _reauth(c)
    # not even by another admin: there is only one, and this is them
    r = post(c, f"/api/admin/users/{env['admin']['id']}/status",
               {"status": "suspended", "reason": "试图停用最后一个管理员"})
    assert r.status_code == 400 and r.json()["error"] == "self_suspend"

    second = make_platform_admin(env["store"], env["ident"].create_user("two@example.com", PW, status="active"))
    # back to one admin, through the real revocation
    grant = PlatformAccess(env["store"]).live_grants(second["id"])[0]
    r = post(c, f"/api/admin/platform-grants/{grant['id']}/revoke", {"reason": "第二位管理员已完成交接"})
    assert r.status_code == 200, r.text
    r = _confirmed_status(c, second['id'], "suspended", "这个不是管理员，可以停用")
    assert r.status_code == 200


def test_revoking_sessions_also_revokes_the_tokens_they_minted(env):
    """An application holding an access token minted by a session that no longer exists would
    otherwise keep working."""
    from synwevo_core import oidc
    store = env["store"]
    store.execute(
        "INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,scope,issued_at,"
        "access_expires_at,family_id) VALUES ('tok_1','docweave-web',?,?,'openid',?,?,'tok_1')",
        (env["alice"]["id"], oidc._sha("live-token"), now(), now() + 900))
    assert oidc.Grants(store).load_access("live-token")

    _reauth(env["c"])
    r = post(env["c"], f"/api/admin/users/{env['alice']['id']}/revoke-sessions",
                      {"reason": "怀疑设备丢失，撤销全部会话"})
    assert r.status_code == 200 and r.json()["tokens_revoked"] == 1
    assert oidc.Grants(store).load_access("live-token") is None


def _admin_routes(api) -> dict:
    """{path: {METHODS}} for every /api/admin route actually mounted.

    Read from the generated schema, not `api.routes`: this FastAPI keeps included routers as
    `_IncludedRouter` entries with no path, so filtering `api.routes` by path found nothing and
    the two route-table tests below passed without checking a single route.
    """
    out = {p: {m.upper() for m in ops} for p, ops in api.openapi()["paths"].items() if p.startswith("/api/admin")}
    assert len(out) >= 8, f"只找到 {len(out)} 条管理路由——路由表读取方式失效了"
    return out


def test_there_is_no_route_that_sets_someone_elses_password(env):
    """Checked against the live route table rather than by reading the file, so a route added
    later has to trip this."""
    paths = set(_admin_routes(env["api"]))
    for p in paths:
        assert "password" not in p, p
        assert "impersonat" not in p and "su" != p.rsplit("/", 1)[-1], p


# ---------------------------------------------------------------- audit
def test_the_audit_log_is_readable_and_filterable(env):
    c = env["c"]
    _reauth(c)
    post(c, "/api/admin/workspaces/default/claim",
           {"user_id": env["alice"]["id"], "reason": REASON})
    items = c.get("/api/admin/audit?action=admin.workspace").json()["items"]
    assert items and items[0]["action"] == "admin.workspace.claim"
    assert items[0]["actor_email"] == "admin@example.com"
    assert items[0]["meta"]["reason"] == REASON


def test_the_audit_log_has_no_write_route(env):
    paths = [(p, m) for p, m in _admin_routes(env["api"]).items() if p.startswith("/api/admin/audit")]
    assert paths, "审计路由必须存在，否则这条测试什么也没检查"
    for path, methods in paths:
        assert methods <= {"GET", "HEAD"}, f"{path} 允许了 {methods}——审计记录不能被应用改写"


def test_the_claimed_workspace_stops_calling_itself_unclaimed(env):
    """The generated label says 待认领, and it is shown in the workspace switcher — so after a
    successful claim it would be telling people their data is still unassigned."""
    c, ident = env["c"], env["ident"]
    assert "待认领" in ident.get_workspace("default")["name"]
    _reauth(c)
    post(c, "/api/admin/workspaces/default/claim",
         {"user_id": env["alice"]["id"], "reason": REASON})
    assert ident.get_workspace("default")["name"] == "历史数据"


def test_a_name_an_administrator_chose_is_not_overwritten(env):
    c, ident, store = env["c"], env["ident"], env["store"]
    store.execute("UPDATE workspaces SET name='财务归档' WHERE id='default'")
    _reauth(c)
    post(c, "/api/admin/workspaces/default/claim",
         {"user_id": env["alice"]["id"], "reason": REASON, "name": "别的名字"})
    assert ident.get_workspace("default")["name"] == "财务归档"


# ---------------------------------------------------------------- F078: suspension ends tokens
def _live_token(env, raw="live-token"):
    from synwevo_core import oidc
    env["store"].execute(
        "INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,refresh_hash,scope,issued_at,"
        "access_expires_at,refresh_expires_at,family_id) VALUES (?,?,?,?,?,'openid offline_access',?,?,?,?)",
        ("tok_" + raw, "docweave-web", env["alice"]["id"], oidc._sha(raw), oidc._sha(raw + "-r"),
         now(), now() + 900, now() + 86400, "tok_" + raw))
    return raw


def test_suspension_revokes_tokens_and_restoring_does_not_bring_them_back(env):
    from synwevo_core import journal, oidc
    raw = _live_token(env)
    _reauth(env["c"])
    r = _confirmed_status(env["c"], env['alice']['id'], "suspended", "账户疑似被盗，先停用")
    assert r.status_code == 200 and r.json()["tokens_revoked"] == 1 and r.json()["journaled"] is True
    assert oidc.Grants(env["store"]).load_access(raw) is None
    r = _confirmed_status(env["c"], env['alice']['id'], "active", "已确认安全，恢复账户")
    assert r.status_code == 200
    assert oidc.Grants(env["store"]).load_access(raw) is None, "恢复账户不能让被盗的令牌复活"
    assert any(e["kind"] == "user_suspended" for e in journal.read(env["store"].root))


def test_an_administrator_must_be_demoted_before_being_suspended(env):
    other = make_platform_admin(env["store"], env["ident"].create_user("two@example.com", PW, status="active"))
    _reauth(env["c"])
    r = post(env["c"], f"/api/admin/users/{other['id']}/status",
             {"status": "suspended", "reason": "另一位管理员离职"})
    assert r.status_code == 409 and r.json()["error"] == "demote_first"
    assert env["ident"].get_user(other["id"])["status"] == "active"


def test_revoking_sessions_is_journalled(env):
    from synwevo_core import journal
    _reauth(env["c"])
    r = post(env["c"], f"/api/admin/users/{env['alice']['id']}/revoke-sessions", {"reason": "怀疑设备丢失，撤销全部会话"})
    assert r.json()["journaled"] is True
    assert any(e["kind"] == "sessions_revoked" and e["user_id"] == env["alice"]["id"]
               for e in journal.read(env["store"].root))


# ---------------------------------------------------------------- F094: one claim wins
def test_concurrent_claims_for_different_accounts_give_the_data_one_owner(env):
    import threading
    c, store = env["c"], env["store"]
    bob = env["ident"].create_user("bob@example.com", PW, status="active")
    _reauth(c)
    results, barrier = [], threading.Barrier(2)

    def claim(target):
        barrier.wait()
        results.append(post(c, "/api/admin/workspaces/default/claim",
                            {"user_id": target, "reason": REASON}).status_code)

    threads = [threading.Thread(target=claim, args=(t,)) for t in (env["alice"]["id"], bob["id"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 409], results
    assert store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id='default'")["n"] == 1


# ---------------------------------------------------------------- freezing a workspace
def test_freezing_stops_a_workspace_and_is_audited_without_content(env):
    from synwevo_core import journal, oidc
    c, store, ident, apps = env["c"], env["store"], env["ident"], env["apps"]
    wid = ident.workspaces_for(env["alice"]["id"])[0]["id"]
    store.execute("INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,scope,workspace_id,issued_at,"
                  "access_expires_at,family_id) VALUES ('t_ws','docweave-web',?,?,'docweave.read',?,?,?,'t_ws')",
                  (env["alice"]["id"], oidc._sha("ws-token"), wid, now(), now() + 900))
    assert apps.check_access(env["alice"]["id"], "docweave", wid)[0] is True

    r = post(c, f"/api/admin/workspaces/{wid}/freeze", {"reason": "收到滥用举报，先冻结调查"})
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"
    _reauth(c)
    assert post(c, f"/api/admin/workspaces/{wid}/freeze", {"reason": "短"}).status_code == 400
    r = post(c, f"/api/admin/workspaces/{wid}/freeze", {"reason": "收到滥用举报，先冻结调查"})
    assert r.status_code == 200 and r.json()["status"] == "frozen" and r.json()["tokens_revoked"] == 1
    assert ident.get_workspace(wid)["status"] == "frozen"
    assert apps.check_access(env["alice"]["id"], "docweave", wid)[0] is False
    assert oidc.Grants(store).load_access("ws-token") is None
    assert any(e["kind"] == "workspace_frozen" for e in journal.read(store.root))
    ev = store.one("SELECT meta FROM audit_events WHERE action='admin.workspace.freeze'")
    assert "reason" in ev["meta"] and "历史项目" not in ev["meta"]
    # the legacy workspace has its own lifecycle
    assert post(c, "/api/admin/workspaces/default/freeze", {"reason": "不应该能冻结待认领空间"}).status_code == 409

    r = post(c, f"/api/admin/workspaces/{wid}/unfreeze", {"reason": "调查结束，恢复使用"})
    assert r.status_code == 200 and ident.get_workspace(wid)["status"] == "active"
    assert apps.check_access(env["alice"]["id"], "docweave", wid)[0] is True


def test_an_ordinary_account_cannot_freeze(env):
    wid = env["ident"].workspaces_for(env["alice"]["id"])[0]["id"]
    assert post(env["plain"], f"/api/admin/workspaces/{wid}/freeze", {"reason": "我自己的空间也不行"}).status_code == 404


# ---------------------------------------------------------------- bounded listings
def test_users_pages_cover_every_match_and_keep_an_insertion_boundary(env):
    store, ident, c = env['store'], env['ident'], env['c']
    users = [ident.create_user(f'page{i:03}@example.test', None, status='active',
                              with_personal_workspace=False) for i in range(207)]
    # Equal timestamps must not leave the order to SQLite's query plan.
    store.execute("UPDATE users SET created_at=123 WHERE email LIKE 'page%@example.test'")
    expected = sorted((u['id'] for u in users), reverse=True)
    first = c.get('/api/admin/users', params={'q': 'page', 'limit': 50}).json()
    assert first['total'] == 207 and first['offset'] == 0 and first['limit'] == 50
    seen = [u['id'] for u in first['items']]
    inserted = ident.create_user('page-new@example.test', None, status='active', with_personal_workspace=False)
    for offset in range(50, 250, 50):
        page = c.get('/api/admin/users', params={'q': 'page', 'limit': 50, 'offset': offset,
                                               'snapshot': first['snapshot']}).json()
        assert page['total'] == 207 and page['offset'] == offset
        seen.extend(u['id'] for u in page['items'])
    assert seen == expected and len(set(seen)) == 207
    fresh = c.get('/api/admin/users', params={'q': 'page', 'limit': 500}).json()
    assert fresh['total'] == 208 and len(fresh['items']) == fresh['limit'] == 200
    assert inserted['id'] in {u['id'] for u in fresh['items']}
    assert len(c.get('/api/admin/users?q=page').json()['items']) == 50
    assert 'email' in first['items'][0] and 'workspaces' in first['items'][0] and 'sessions' in first['items'][0]


def test_user_filters_and_counts_apply_before_pagination(env):
    c, store, ident = env['c'], env['store'], env['ident']
    for n in range(6):
        u = ident.create_user(f'filter{n}@example.test', None, display_name='Matching person',
                              status='active' if n % 2 else 'suspended', with_personal_workspace=False)
        make_platform_admin(store, u, role='auditor')
        if n == 0:
            store.execute('UPDATE users SET deleted_at=? WHERE id=?', (now(), u['id']))
    page = c.get('/api/admin/users', params={'q': 'Matching', 'status': 'active', 'role': 'auditor',
                                           'limit': 2, 'offset': 2}).json()
    assert page['total'] == 3 and len(page['items']) == 1
    item = page['items'][0]
    # a delegated role keeps the rollback mirror at 'none' (a rollback removes it, never widens it)
    assert item['status'] == 'active' and item['platform_roles'] == ['auditor'] and item['platform_role'] == 'none'
    assert c.get('/api/admin/users?q=filter&status=suspended').json()['total'] == 2
    assert c.get('/api/admin/users', params={'q': 'Matching', 'role': 'any'}).json()['total'] == 5
    assert c.get('/api/admin/users', params={'q': 'Matching', 'role': 'none'}).json()['total'] == 0
    assert c.get('/api/admin/users', params={'q': 'Matching', 'role': 'super_admin'}).json()['total'] == 0
    assert c.get('/api/admin/users?q=absent').json()['total'] == 0
    assert c.get('/api/admin/users?role=owner').status_code == 422
    assert c.get('/api/admin/users?role=operator').status_code == 422, "「平台运维」已停用，不再是筛选项"


def test_audit_pages_reach_past_500_and_filter_the_whole_log(env):
    c, store = env['c'], env['store']
    with store.tx() as t:
        for n in range(517):
            t.execute('INSERT INTO audit_events (id,at,actor_kind,actor_id,action,object_id,result,meta) '
                      "VALUES (?,123,'user',?,'admin.user.fixture',?,'ok',?)",
                      (f'fixture_{n:03}', env['admin']['id'], f'fixture_{n:03}', '{"reason":"Synthetic fixture"}'))
    first = c.get('/api/admin/audit?q=fixture_&action=admin.user.&limit=50').json()
    store.execute("INSERT INTO audit_events (id,at,actor_kind,action,object_id,result) "
                  "VALUES ('fixture_new',124,'system','admin.user.fixture','fixture_new','ok')")
    seen = []
    for offset in range(0, 550, 50):
        page = c.get('/api/admin/audit', params={'q': 'fixture_', 'action': 'admin.user.', 'limit': 50,
                                               'offset': offset, 'snapshot': first['snapshot']}).json()
        assert page['total'] == 517
        seen.extend(a['id'] for a in page['items'])
    assert seen == [f'fixture_{n:03}' for n in reversed(range(517))]
    assert first['items'][0]['meta']['reason'] == 'Synthetic fixture'
    assert first['items'][0]['actor_email'] == 'admin@example.com'
    fresh = c.get('/api/admin/audit?q=fixture_&limit=999').json()
    assert fresh['total'] == 518 and len(fresh['items']) == fresh['limit'] == 500
    assert fresh['items'][0]['id'] == 'fixture_new'
    assert c.get('/api/admin/audit?q=admin@example.com&action=admin.user.fixture').json()['total'] == 517
    assert c.get('/api/admin/audit?q=fixture_&action=auth.').json()['total'] == 0
    assert len(c.get('/api/admin/audit?q=fixture_').json()['items']) == 100


def test_workspace_pagination_is_opt_in_for_existing_callers(env):
    c, ident = env['c'], env['ident']
    for n in range(5):
        ident.create_workspace(f'Page space {n}', owner_id=env['alice']['id'])
    old = c.get('/api/admin/workspaces?q=Page').json()
    assert len(old['items']) == old['total'] == 5
    first = c.get('/api/admin/workspaces?q=Page&limit=2').json()
    ident.create_workspace('Page later', owner_id=env['alice']['id'])
    items = []
    for offset in (0, 2, 4):
        page = c.get('/api/admin/workspaces', params={'q': 'Page', 'limit': 2, 'offset': offset,
                                                    'snapshot': first['snapshot']}).json()
        assert page['total'] == 5
        items.extend(page['items'])
    assert [w['id'] for w in items] == [w['id'] for w in old['items']]
    assert c.get('/api/admin/workspaces?q=Page&limit=999').json()['limit'] == 200
    assert c.get('/api/admin/workspaces?q=absent&limit=50').json()['total'] == 0


@pytest.mark.parametrize('listing', ['users', 'audit', 'workspaces'])
def test_page_parameters_are_bounded_and_do_not_bypass_access(env, listing):
    path = f'/api/admin/{listing}'
    assert env['c'].get(path, params={'limit': 0}).json()['limit'] == 1
    for params in ({'offset': -1}, {'snapshot': -1}, {'offset': 2**63}, {'snapshot': 2**63}):
        assert env['c'].get(path, params=params).status_code == 422
    assert env['c'].get(path, params={'limit': 50, 'offset': 9999}).json()['items'] == []
    assert env['plain'].get(path, params={'snapshot': 9999, 'offset': 50}).status_code == 404
    assert TestClient(env['api']).get(path, params={'snapshot': 9999, 'offset': 50}).status_code == 401


def test_account_detail_is_metadata_without_secrets(env):
    """A13: one account's facts — sign-in methods, memberships, sessions, backoff, its slice of
    the audit log — and nothing that could sign in as it or read what it reads."""
    c, store, ident, alice, api = env["c"], env["store"], env["ident"], env["alice"], env["api"]
    ws = ident.create_workspace("Detail fixture", owner_id=env["admin"]["id"])
    ident.add_member(ws["id"], alice["id"], "editor")
    # Three wrong passwords: the account bucket counts them and the log records each refusal.
    stranger = TestClient(api)
    for _ in range(3):
        assert post(stranger, "/api/auth/login", {"email": "alice@example.com", "password": "not-the-passphrase"}).status_code == 401
    r = c.get(f"/api/admin/users/{alice['id']}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["user"]["email"] == "alice@example.com" and d["user"]["status"] == "active"
    assert d["application_identity"] is False and d["outranks_caller"] is False and d["platform_roles"] == []
    assert d["sign_in"]["password_set"] is True and d["sign_in"]["totp"] is False
    assert d["sign_in"]["passkeys"] == [] and d["sign_in"]["external_identities"] == []
    assert [w["role"] for w in d["workspaces"]] == ["owner", "editor"] and d["workspaces"][1]["name"] == "Detail fixture"
    assert d["sessions"] and all(s["id"] for s in d["sessions"]) and d["sessions"][0]["amr"] == "password"
    assert d["backoff"]["failures"] == 3 and isinstance(d["backoff"]["retry_after"], int)
    assert d["audit"]["permitted"] is True
    denied = [e for e in d["audit"]["items"] if e["action"] == "auth.login" and e["result"] == "denied"]
    assert len(denied) == 3 and denied[0]["reason"] and "meta" not in denied[0]
    # No secret material, by value or by field name.
    phc = store.one("SELECT phc FROM user_passwords WHERE user_id=?", (alice["id"],))["phc"]
    assert phc not in r.text and "$argon2" not in r.text

    def names(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from names(v)
        elif isinstance(o, list):
            for v in o:
                yield from names(v)
    assert not {k for k in names(d) if any(w in k for w in ("phc", "secret", "seed", "token", "hash", "cookie"))}


def test_account_detail_names_only_what_the_caller_may_list_and_refuses_unknown_accounts(env):
    c, store, ident, alice, api = env["c"], env["store"], env["ident"], env["alice"], env["api"]
    support = make_platform_admin(store, ident.create_user("support@example.com", PW, status="active"), role="support")
    s = TestClient(api)
    assert post(s, "/api/auth/login", {"email": "support@example.com", "password": PW}).status_code == 200
    d = s.get(f"/api/admin/users/{env['admin']['id']}").json()
    # support reads accounts and the workspace list, not the audit log: no timeline, names kept
    assert d["audit"] == {"permitted": False, "limit": 50, "items": []}
    assert d["outranks_caller"] is True and d["platform_roles"] == ["super_admin"]
    assert d["workspaces"] and all(w["name"] for w in d["workspaces"])
    assert d["backoff"] is None
    finance = make_platform_admin(store, ident.create_user("finance@example.com", PW, status="active"), role="finance")
    f = TestClient(api)
    assert post(f, "/api/auth/login", {"email": "finance@example.com", "password": PW}).status_code == 200
    assert f.get(f"/api/admin/users/{alice['id']}").status_code == 403, "another platform role: refused, audited"
    assert env["plain"].get(f"/api/admin/users/{alice['id']}").status_code == 404, "no platform role: not a page"
    r = c.get("/api/admin/users/u_fixture_missing")
    assert r.status_code == 404 and r.json()["error"] == "no_user"
    gone = ident.create_user("gone@example.com", PW, status="active")
    store.execute("UPDATE users SET deleted_at=? WHERE id=?", (now(), gone["id"]))
    assert c.get(f"/api/admin/users/{gone['id']}").status_code == 404
    assert support["id"] != finance["id"]


def test_an_administrator_ends_one_device_removes_a_passkey_and_unlinks_an_identity(env):
    """A17: per-object actions with a reason and strict re-authentication; the person's own
    lockout rules hold, the person is told, and the other devices keep working."""
    c, store, ident, alice, api = env["c"], env["store"], env["ident"], env["alice"], env["api"]
    other = TestClient(api)
    assert post(other, "/api/auth/login", {"email": "alice@example.com", "password": PW}).status_code == 200
    detail = c.get(f"/api/admin/users/{alice['id']}").json()
    assert len(detail["sessions"]) == 2 and all(s["id"] for s in detail["sessions"])
    _reauth(c)
    r = post(c, f"/api/admin/users/{alice['id']}/sessions/{detail['sessions'][0]['id']}/revoke", {"reason": "怀疑该设备已丢失，结束其登录"})
    assert r.status_code == 200 and r.json()["sessions_revoked"] >= 1 and r.json()["notified"] is True
    assert sorted(client.get("/api/auth/me").status_code for client in (env["plain"], other)) == [200, 401]
    assert post(c, f"/api/admin/users/{alice['id']}/sessions/{detail['sessions'][0]['id']}/revoke", {"reason": "再次结束同一设备的登录"}).status_code == 404
    audit_row = store.one("SELECT meta FROM audit_events WHERE action='admin.user.revoke_session'")
    assert audit_row and "怀疑该设备已丢失" in audit_row["meta"]
    # TEST FIXTURE: a passkey row with public material only (no authenticator is involved).
    store.execute("INSERT INTO passkeys (id,user_id,credential_id,public_key,rp_id,uv_capable,name,created_at) "
                  "VALUES ('pk_fixture',?,'fixture-credential','fixture-public-key','localhost',1,'Fixture key',?)", (alice["id"], now()))
    assert any(k["id"] == "pk_fixture" for k in c.get(f"/api/admin/users/{alice['id']}").json()["sign_in"]["passkeys"])
    r = post(c, f"/api/admin/users/{alice['id']}/passkeys/pk_fixture/remove", {"reason": "用户报告该安全密钥已遗失"})
    assert r.status_code == 200 and store.one("SELECT deleted_at FROM passkeys WHERE id='pk_fixture'")["deleted_at"]
    assert post(c, f"/api/admin/users/{alice['id']}/passkeys/pk_fixture/remove", {"reason": "重复移除同一安全密钥"}).status_code == 404
    # TEST FIXTURE: an external identity row (no provider is configured in this instance).
    store.execute("INSERT INTO external_identities (id,user_id,provider,subject,issuer,email,email_verified,created_at) "
                  "VALUES ('xi_fixture',?,'google','fixture-subject','https://accounts.google.com',?,1,?)", (alice["id"], alice["email"], now()))
    r = post(c, f"/api/admin/users/{alice['id']}/external-identities/xi_fixture/unlink", {"reason": "用户确认不再使用该外部账户"})
    assert r.status_code == 200 and not store.one("SELECT 1 FROM external_identities WHERE id='xi_fixture'")
    kinds = [row["kind"] for row in store.all("SELECT kind FROM notifications WHERE user_id=? ORDER BY rowid", (alice["id"],))]
    assert kinds.count("security.change") >= 3
    # Never the only way in: an account that signs in only through an external identity keeps it.
    sso = ident.create_user("sso@example.com", None, status="active")
    store.execute("INSERT INTO external_identities (id,user_id,provider,subject,issuer,email,email_verified,created_at) "
                  "VALUES ('xi_only',?,'google','fixture-subject-2','https://accounts.google.com',?,1,?)", (sso["id"], sso["email"], now()))
    r = post(c, f"/api/admin/users/{sso['id']}/external-identities/xi_only/unlink", {"reason": "尝试解除唯一登录方式"})
    assert r.status_code == 409 and r.json()["error"] == "last_sign_in_method"
    assert store.one("SELECT 1 FROM external_identities WHERE id='xi_only'")
    # Support reads the detail but may not act; an access administrator may not act on a higher one.
    support = make_platform_admin(store, ident.create_user("support2@example.com", PW, status="active"), role="support")
    s = TestClient(api); post(s, "/api/auth/login", {"email": "support2@example.com", "password": PW})
    assert s.get(f"/api/admin/users/{alice['id']}").status_code == 200
    assert post(s, f"/api/admin/users/{alice['id']}/passkeys/pk_fixture/remove", {"reason": "支持角色尝试移除安全密钥"}).json()["error"] == "platform_permission_denied"
    access_admin = make_platform_admin(store, ident.create_user("access2@example.com", PW, status="active"), role="access_admin")
    a = TestClient(api); post(a, "/api/auth/login", {"email": "access2@example.com", "password": PW}); _reauth(a)
    admin_session = c.get(f"/api/admin/users/{env['admin']['id']}").json()["sessions"][0]["id"]
    r = post(a, f"/api/admin/users/{env['admin']['id']}/sessions/{admin_session}/revoke", {"reason": "较低管理员尝试结束最高管理员的设备"})
    assert r.status_code == 403 and r.json()["error"] == "target_outranks_actor"
    assert support["id"] != access_admin["id"]


def test_pending_deletions_are_visible_and_a_lost_cancellation_link_can_be_re_sent_then_the_sweep_run(env, monkeypatch, tmp_path):
    """A20: an account's deletion request as administration sees it — due time, overdue, whether
    the cancellation link is still valid, the rules that would refuse a request — and the two
    things administration may do about it: send the person a new cancellation link (to their own
    address, expiring with the deletion, never cancelling for them) and run the due sweep now."""
    import json
    import re
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "fixture-mail"))
    c, store, ident, alice, api = env["c"], env["store"], env["ident"], env["alice"], env["api"]
    # The rules a request would meet, read without deciding anything: the last usable super
    # administrator, and a shared workspace's owner with no successor.
    assert c.get(f"/api/admin/users/{env['admin']['id']}").json()["deletion"] == {
        "pending": False, "requested_at": None, "due_at": None, "overdue": False,
        "cancellation_link_valid": False, "blockers": ["last_super_admin"]}
    bob = ident.create_user("bob@example.com", PW, status="active")
    shared = ident.create_workspace("Deletion fixture", owner_id=bob["id"])
    ident.add_member(shared["id"], alice["id"], "editor")
    assert c.get(f"/api/admin/users/{bob['id']}").json()["deletion"]["blockers"] == ["ownership_required"]
    assert c.get(f"/api/admin/users/{alice['id']}").json()["deletion"]["blockers"] == []
    # Alice asks to delete her account: the real self-service request, with her password.
    r = post(env["plain"], "/api/auth/deletion", {"password": PW})
    assert r.status_code == 200, r.text
    due = r.json()["deletion_due_at"]
    token = lambda body: re.search(r"[#?]token=([A-Za-z0-9_-]+)", body)[1]  # noqa: E731
    first = token(store.one("SELECT body FROM mail_outbox WHERE kind='account_recovery' ORDER BY created_at DESC LIMIT 1")["body"])
    row = next(u for u in c.get("/api/admin/users?status=pending_delete").json()["items"] if u["id"] == alice["id"])
    assert row["deletion_due_at"] == due
    d = c.get(f"/api/admin/users/{alice['id']}").json()["deletion"]
    assert d == {"pending": True, "requested_at": d["requested_at"], "due_at": due, "overdue": False,
                 "cancellation_link_valid": True, "blockers": []} and d["requested_at"] <= due
    assert c.get("/api/admin/overview").json()["account_deletions"] == {"pending": 1, "overdue": 0, "withdrawals_pending": 0}
    # Re-sending: strict re-authentication, a reason, a pending request, a minute since the last mail.
    path = f"/api/admin/users/{alice['id']}/deletion/cancellation-mail"
    reason = "用户来电说没有收到取消注销的邮件"
    assert post(c, path, {"reason": reason}).json()["error"] == "reauth_required"
    _reauth(c)
    assert post(c, path, {"reason": "短"}).status_code == 400
    assert post(c, f"/api/admin/users/{env['admin']['id']}/deletion/cancellation-mail", {"reason": reason}).json()["error"] == "not_pending_delete"
    r = post(c, path, {"reason": reason})
    assert r.status_code == 429 and r.json()["error"] == "mail_rate_limited", r.text
    store.execute("UPDATE account_recoveries SET created_at=created_at-120 WHERE user_id=?", (alice["id"],))
    r = post(c, path, {"reason": reason})
    assert r.status_code == 200, r.text
    assert r.json()["due_at"] == due and r.json()["delivery"]["kind"] == "account_recovery"
    assert "token" not in json.dumps(r.json()) and "body" not in r.json()["delivery"], "the link never reaches the administrator"
    mails = store.all("SELECT body FROM mail_outbox WHERE kind='account_recovery' ORDER BY created_at")
    assert len(mails) == 2 and "之前的链接已失效" in mails[1]["body"] and "删除日期不变" in mails[1]["body"]
    anonymous = TestClient(api)
    assert anonymous.post("/api/auth/deletion/recovery/preview", json={"token": first}).status_code == 410, "the earlier link no longer works"
    preview = anonymous.post("/api/auth/deletion/recovery/preview", json={"token": token(mails[1]["body"])})
    assert preview.status_code == 200 and preview.json()["deletion_due_at"] == due, "the new link ends with the deletion, not later"
    audit_row = store.one("SELECT meta FROM audit_events WHERE action='admin.user.deletion_cancellation_mail'")
    assert audit_row and "token" not in audit_row["meta"] and reason in audit_row["meta"]
    # Support may send it; another platform role may not.
    make_platform_admin(store, ident.create_user("support@example.com", PW, status="active"), role="support")
    s = TestClient(api)
    assert post(s, "/api/auth/login", {"email": "support@example.com", "password": PW}).status_code == 200
    _reauth(s)
    store.execute("UPDATE account_recoveries SET created_at=created_at-120 WHERE user_id=?", (alice["id"],))
    assert post(s, path, {"reason": "支持人员按用户请求重发取消链接"}).status_code == 200
    make_platform_admin(store, ident.create_user("finance@example.com", PW, status="active"), role="finance")
    f = TestClient(api)
    assert post(f, "/api/auth/login", {"email": "finance@example.com", "password": PW}).status_code == 200
    _reauth(f)
    assert post(f, path, {"reason": "财务角色不应能重发取消链接"}).json()["error"] == "platform_permission_denied"
    # Past due: overdue in the overview and the detail, no more links; the sweep run from
    # administration finalizes it (users.manage, not support) and the counts return to zero.
    store.execute("UPDATE users SET deletion_due_at=? WHERE id=?", (now() - 1, alice["id"]))
    assert c.get("/api/admin/overview").json()["account_deletions"] == {"pending": 1, "overdue": 1, "withdrawals_pending": 0}
    assert c.get(f"/api/admin/users/{alice['id']}").json()["deletion"]["overdue"] is True
    assert post(c, path, {"reason": "到期后不能再重发取消链接"}).json()["error"] == "not_pending_delete"
    assert post(s, "/api/admin/account-deletions/finalize", {"reason": "支持角色不能运行到期删除"}).json()["error"] == "platform_permission_denied"
    r = post(c, "/api/admin/account-deletions/finalize", {"reason": "手动运行到期删除以处理逾期申请"})
    assert r.status_code == 200, r.text
    assert r.json()["finalized"] == 1 and r.json()["after"] == {"pending": 0, "overdue": 0, "withdrawals_pending": 0}
    assert ident.get_user(alice["id"]) is None and c.get(f"/api/admin/users/{alice['id']}").status_code == 404
    assert store.one("SELECT 1 AS n FROM audit_events WHERE action='admin.accounts.finalize_deletions' AND result='ok'")
    assert post(c, "/api/admin/account-deletions/finalize", {"reason": "再次运行应当没有可处理的申请"}).json()["finalized"] == 0


def test_an_administrator_revokes_every_push_device_of_an_account(env):
    """A17: the account's browser push devices are listed without their endpoints, and one
    confirmed action stops them all; the person is told and may subscribe again."""
    import base64
    import json
    from synwevo_core.push import PushSubscriptions
    c, store, ident, alice, api = env["c"], env["store"], env["ident"], env["alice"], env["api"]
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    sid = store.one("SELECT id FROM sessions WHERE user_id=? AND revoked_at IS NULL", (alice["id"],))["id"]
    subs = PushSubscriptions(store)
    for i in range(2):
        # FIXTURE: synthetic subscriptions (public key material only); the second has no session.
        subs.add(alice["id"], sid if i == 0 else None,
                 {"endpoint": f"https://push.example.test/fixture-{i}", "keys": {"p256dh": b64(b"\x04" + bytes([i + 1]) * 64), "auth": b64(b"\x02" * 16)}},
                 user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/128.0 Safari/537.36")
    d = c.get(f"/api/admin/users/{alice['id']}").json()
    assert len(d["push_subscriptions"]) == 2 and all(p["active"] for p in d["push_subscriptions"])
    assert "endpoint" not in json.dumps(d) and "push.example.test" not in json.dumps(d), "the capability URL never reaches administration"
    path = f"/api/admin/users/{alice['id']}/push-subscriptions/revoke"
    assert post(c, path, {"reason": "用户报告设备丢失，停止全部推送"}).json()["error"] == "reauth_required"
    _reauth(c)
    r = post(c, path, {"reason": "用户报告设备丢失，停止全部推送"})
    assert r.status_code == 200 and r.json() == {"ok": True, "revoked": 2, "notified": True}, r.text
    assert c.get(f"/api/admin/users/{alice['id']}").json()["push_subscriptions"] == [] and not subs.has_active(alice["id"])
    assert post(c, path, {"reason": "再次执行应当没有可撤销的设备"}).json() == {"ok": True, "revoked": 0, "notified": False}
    row = store.one("SELECT meta FROM audit_events WHERE action='admin.user.push_revoke' ORDER BY at LIMIT 1")
    assert row and json.loads(row["meta"])["revoked"] == 2
    note = store.one("SELECT * FROM notifications WHERE user_id=? ORDER BY seq DESC LIMIT 1", (alice["id"],))
    assert note and "push_revoked" in json.dumps(dict(note), default=str)
    assert env["plain"].get("/api/auth/me").status_code == 200, "the person's session is untouched"
    make_platform_admin(store, ident.create_user("support@example.com", PW, status="active"), role="support")
    s = TestClient(api)
    assert post(s, "/api/auth/login", {"email": "support@example.com", "password": PW}).status_code == 200
    _reauth(s)
    assert post(s, path, {"reason": "支持角色不能撤销推送设备"}).json()["error"] == "platform_permission_denied"


def test_the_account_detail_shows_an_agent_suspension_and_an_administrator_lifts_it_from_there(env):
    """A13 remainder: the personal Agent's content-safety suspension is part of the account's facts
    (reason, who, when, the linked report) and is lifted through the same route the safety page
    uses; support reads it, cannot lift it."""
    from synwevo_core.agent_safety import AgentAccess
    from synwevo_core.errors import ServiceError
    c, store, ident, alice, api = env["c"], env["store"], env["ident"], env["alice"], env["api"]
    assert c.get(f"/api/admin/users/{alice['id']}").json()["agent_access"] == {"suspended": False, "suspension": None}
    r = post(c, f"/api/admin/agent-safety/users/{alice['id']}/suspend", {"reason": "合成测试：举报核实前暂停"})
    assert r.status_code == 200, r.text
    a = c.get(f"/api/admin/users/{alice['id']}").json()["agent_access"]
    assert a["suspended"] is True and a["suspension"]["reason"] == "合成测试：举报核实前暂停"
    assert a["suspension"]["created_by_email"] == "admin@example.com" and a["suspension"]["report_id"] is None
    with pytest.raises(ServiceError) as refused:
        AgentAccess(store).require(alice["id"])
    assert refused.value.code == "agent_access_suspended"
    make_platform_admin(store, ident.create_user("support@example.com", PW, status="active"), role="support")
    s = TestClient(api)
    assert post(s, "/api/auth/login", {"email": "support@example.com", "password": PW}).status_code == 200
    assert s.get(f"/api/admin/users/{alice['id']}").json()["agent_access"]["suspended"] is True
    assert post(s, f"/api/admin/agent-safety/users/{alice['id']}/lift", {"reason": "支持角色不能恢复"}).json()["error"] == "platform_permission_denied"
    r = post(c, f"/api/admin/agent-safety/users/{alice['id']}/lift", {"reason": "举报已核实为误报，恢复使用"})
    assert r.status_code == 200 and r.json()["lifted_at"], r.text
    assert c.get(f"/api/admin/users/{alice['id']}").json()["agent_access"] == {"suspended": False, "suspension": None}
    AgentAccess(store).require(alice["id"])
    # Both reasons reach the account's timeline, the way every other administration action's does.
    timeline = c.get(f"/api/admin/users/{alice['id']}").json()["audit"]["items"]
    reasons = {e["action"]: e["reason"] for e in timeline if e["action"].startswith("admin.agent_safety.")}
    assert reasons == {"admin.agent_safety.suspend": "合成测试：举报核实前暂停", "admin.agent_safety.lift": "举报已核实为误报，恢复使用"}


def test_registration_diagnostics_follow_one_address_through_every_stage(env, monkeypatch, tmp_path):
    """A27: a support reader follows an institutional-form address from validation to activation —
    the account row, the throttle (with the refusal now on record), the verification token (never
    its value), the mail, the activation — with the audit events and mail rows only for the roles
    that may read those pages."""
    import json
    import re
    monkeypatch.setenv("SYNWEVO_REGISTRATION", "open")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "fixture-mail"))
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "http://testserver")
    c, store, ident, api = env["c"], env["store"], env["ident"], env["api"]
    address = "1155123456@link.cuhk.edu.hk"
    path = "/api/admin/diagnostics/registration"
    assert c.get(path).json()["error"] == "bad_email" and c.get(path + "?email=not-an-address").status_code == 400
    nothing = c.get(path + "?email=" + address).json()
    assert nothing["account"] is None and nothing["throttle"] is None and nothing["tokens"] == []
    assert [s["state"] for s in nothing["stages"]] == ["valid", "none", "clear", "none", "none", "none", "none"]
    anonymous = TestClient(api)
    register = lambda: anonymous.post("/api/auth/register", json={"email": address, "password": "a long institutional passphrase 2026", "locale": "zh-Hans"})  # noqa: E731
    assert register().status_code == 202
    d = c.get(path + "?email=" + address).json()
    assert d["account"]["status"] == "pending" and d["account"]["created_at"] and d["registration_mode"] == "open"
    assert len(d["tokens"]) == 1 and d["tokens"][0]["used_at"] is None and not d["tokens"][0]["expired"]
    assert d["mail"]["permitted"] is True and d["mail"]["items"][0]["kind"] == "verify" and d["mail"]["items"][0]["status"] == "queued"
    assert d["audit"]["permitted"] is True and [e["action"] for e in d["audit"]["events"]] == ["auth.email.verify_requested", "auth.registration"]
    assert [s["state"] for s in d["stages"]] == ["valid", "none", "clear", "pending", "issued", "queued", "pending"]
    assert all(s["at"] for s in d["stages"] if s["stage"] in ("account", "token", "mail"))
    assert "token" not in json.dumps(d["tokens"]) and not re.search(r"[A-Za-z0-9_-]{30,}", json.dumps(d["tokens"]))
    # Three requests an hour per address: the fourth is answered like the others and refused on record.
    for _ in range(3):
        assert register().status_code == 202
    d = c.get(path + "?email=" + address).json()
    assert d["throttle"]["blocked"] is True and d["throttle"]["count"] >= 3
    refused = [e for e in d["audit"]["events"] if e["action"] == "auth.registration.throttled"]
    assert refused and refused[0]["result"] == "denied" and refused[0]["detail"]["scope"] == "email" and refused[0]["detail"]["retry_after"] >= 1
    assert d["stages"][2]["state"] == "blocked" and d["stages"][2]["at"]
    # Activation through the real link (the token read from the outbox, as the mailbox owner would).
    body = store.one("SELECT body FROM mail_outbox WHERE kind='verify' ORDER BY created_at DESC LIMIT 1")["body"]
    token = re.search(r"[#?]token=([A-Za-z0-9_-]+)", body)[1]
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    r = anonymous.post("/api/auth/email/verify", json={"token": token, "new_password": "the mailbox owner picks a passphrase 2026"},
                       headers={CSRF_HEADER: anonymous.cookies.get(CSRF_COOKIE) or ""})
    assert r.status_code == 200, r.text
    d = c.get(path + "?email=" + address).json()
    assert d["account"]["status"] == "active" and d["stages"][-1] == {"stage": "activation", "state": "active", "at": d["account"]["email_verified_at"]}
    assert d["stages"][4]["state"] == "used" and "auth.email.verified" in [e["action"] for e in d["audit"]["events"]]
    # Support: the stages, the account and the mail rows, not the audit events.
    make_platform_admin(store, ident.create_user("support@example.com", PW, status="active"), role="support")
    s = TestClient(api)
    assert post(s, "/api/auth/login", {"email": "support@example.com", "password": PW}).status_code == 200
    d = s.get(path + "?email=" + address).json()
    assert d["account"]["status"] == "active" and d["audit"] == {"permitted": False, "events": [], "challenges": []}
    # support reads the outbox page, so the mail rows are theirs too (metadata, never a body)
    assert d["mail"]["permitted"] is True and d["mail"]["items"][0]["kind"] == "verify" and "body" not in d["mail"]["items"][0]
    assert d["stages"][5]["state"] in ("queued", "sent") and d["stages"][1]["state"] == "withheld"
    make_platform_admin(store, ident.create_user("finance@example.com", PW, status="active"), role="finance")
    f = TestClient(api)
    assert post(f, "/api/auth/login", {"email": "finance@example.com", "password": PW}).status_code == 200
    assert f.get(path + "?email=" + address).status_code == 403
