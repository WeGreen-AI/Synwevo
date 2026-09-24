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
from synwevo_core.store import Store, now

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

    admin = ident.create_user("admin@example.com", PW, status="active", platform_role="admin")
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
    r = post(c, f"/api/admin/users/{env['alice']['id']}/status",
               {"status": "suspended", "reason": "离职交接完成，停用账户"})
    assert r.status_code == 200 and r.json()["sessions_revoked"] >= 1
    assert plain.get("/api/auth/me").status_code == 401, "停用在下一次请求生效，不是等会话过期"


def test_the_last_administrator_cannot_be_suspended(env):
    c = env["c"]
    _reauth(c)
    # not even by another admin: there is only one, and this is them
    r = post(c, f"/api/admin/users/{env['admin']['id']}/status",
               {"status": "suspended", "reason": "试图停用最后一个管理员"})
    assert r.status_code == 400 and r.json()["error"] == "self_suspend"

    second = env["ident"].create_user("two@example.com", PW, status="active", platform_role="admin")
    env["ident"].set_platform_role(second["id"], "none")     # back to one admin
    r = post(c, f"/api/admin/users/{second['id']}/status",
               {"status": "suspended", "reason": "这个不是管理员，可以停用"})
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
    r = post(env["c"], f"/api/admin/users/{env['alice']['id']}/status",
             {"status": "suspended", "reason": "账户疑似被盗，先停用"})
    assert r.status_code == 200 and r.json()["tokens_revoked"] == 1 and r.json()["journaled"] is True
    assert oidc.Grants(env["store"]).load_access(raw) is None
    r = post(env["c"], f"/api/admin/users/{env['alice']['id']}/status",
             {"status": "active", "reason": "已确认安全，恢复账户"})
    assert r.status_code == 200
    assert oidc.Grants(env["store"]).load_access(raw) is None, "恢复账户不能让被盗的令牌复活"
    assert any(e["kind"] == "user_suspended" for e in journal.read(env["store"].root))


def test_an_administrator_must_be_demoted_before_being_suspended(env):
    other = env["ident"].create_user("two@example.com", PW, status="active", platform_role="admin")
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
                              status='active' if n % 2 else 'suspended', platform_role='operator',
                              with_personal_workspace=False)
        if n == 0:
            store.execute('UPDATE users SET deleted_at=? WHERE id=?', (now(), u['id']))
    page = c.get('/api/admin/users', params={'q': 'Matching', 'status': 'active', 'role': 'operator',
                                           'limit': 2, 'offset': 2}).json()
    assert page['total'] == 3 and len(page['items']) == 1
    assert page['items'][0]['status'] == 'active' and page['items'][0]['platform_role'] == 'operator'
    assert c.get('/api/admin/users?q=filter&status=suspended').json()['total'] == 2
    assert c.get('/api/admin/users?q=absent').json()['total'] == 0
    assert c.get('/api/admin/users?role=owner').status_code == 422


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
