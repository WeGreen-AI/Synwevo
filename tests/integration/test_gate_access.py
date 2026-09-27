"""Workspace status and application access, re-checked on every Synwevo Docs request.

The browser gate used to check only that a membership row existed. Eligibility — the application
is live, the workspace turned it on, the person's entitlement was not revoked, the workspace is
active — was checked once, in the sign-in callback, and never again. So a revoked person kept
Docs for the rest of a 30-day session, a frozen workspace could not be frozen, and a member could
switch their session into a workspace that never enabled Docs. Machine callers were re-checked on
every call; browser sessions were not. These tests are the browser half.

They also cover the registry itself: an explicit revocation must beat the `members` policy, and
restarting the account service must not undo it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"


def _csrf(c: TestClient) -> dict:
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    d = tmp_path / "d"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    api = create_app(str(d), role="all", start_jobs=False)
    store = Store(d)
    ident = Identity(store)
    apps = Apps(store, ident)
    alice = ident.create_user("alice@example.com", PW, status="active")
    ws = ident.workspaces_for(alice["id"])[0]["id"]
    c = TestClient(api)
    assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    assert c.get("/api/projects").status_code == 200
    return {"api": api, "d": d, "store": store, "ident": ident, "apps": apps, "alice": alice,
            "ws": ws, "c": c}


def _refused(r, code):
    assert r.status_code == 403, (r.status_code, r.text[:300])
    assert r.json()["error"] == code, r.text[:300]


# ---------------------------------------------------------------- per request
def test_revoking_the_entitlement_stops_the_open_session(env):
    """Under the `members` policy most people have no entitlement row, so `revoke` used to
    UPDATE nothing — and even with a row, `members` ignored it."""
    env["apps"].revoke(env["ws"], "docweave", env["alice"]["id"])
    _refused(env["c"].get("/api/projects"), "app_access_revoked")
    _refused(env["c"].post("/api/projects", json={"name": "x"}, headers=_csrf(env["c"])),
             "app_access_revoked")
    # Their account is untouched: it is the application that was withdrawn.
    assert env["c"].get("/api/auth/me").status_code == 200


def test_turning_docs_off_for_the_workspace_stops_the_open_session(env):
    env["apps"].disable_for_workspace(env["ws"], "docweave")
    r = env["c"].get("/api/projects")
    _refused(r, "app_access_revoked")
    assert r.json()["detail"]["workspace_id"] == env["ws"]


def test_turning_docs_off_platform_wide_stops_the_open_session(env):
    env["store"].execute("UPDATE apps SET status='disabled' WHERE id='docweave'")
    _refused(env["c"].get("/api/templates"), "app_access_revoked")


@pytest.mark.parametrize("status", ["frozen", "pending_claim", "deleted"])
def test_only_an_active_workspace_can_be_worked_in(env, status):
    env["store"].execute("UPDATE workspaces SET status=? WHERE id=?", (status, env["ws"]))
    r = env["c"].get("/api/projects")
    _refused(r, "workspace_unavailable")
    assert r.json()["detail"]["workspace_status"] == status
    assert env["c"].get("/api/auth/me").status_code == 200


def test_restoring_access_restores_it(env):
    apps, ws, uid = env["apps"], env["ws"], env["alice"]["id"]
    apps.revoke(ws, "docweave", uid)
    _refused(env["c"].get("/api/projects"), "app_access_revoked")
    apps.grant(ws, "docweave", uid)
    assert env["c"].get("/api/projects").status_code == 200


def test_a_user_with_no_workspace_can_write_nothing(env):
    """Reads see an empty tenant; a write would have created a project in a tenant nobody owns."""
    env["store"].execute("DELETE FROM memberships WHERE user_id=?", (env["alice"]["id"],))
    assert env["c"].get("/api/projects").json() == []
    _refused(env["c"].post("/api/projects", json={"name": "无处安放"}, headers=_csrf(env["c"])),
             "no_workspace")
    assert env["store"].one("SELECT COUNT(*) AS n FROM projects")["n"] == 0


# ---------------------------------------------------------------- switching workspace
def test_switching_into_a_workspace_without_docs_is_refused(env):
    ident, apps, c = env["ident"], env["apps"], env["c"]
    w2 = ident.create_workspace("没有文档应用", owner_id=env["alice"]["id"])["id"]
    apps.disable_for_workspace(w2, "docweave")
    _refused(c.post("/api/auth/workspace", json={"workspace_id": w2}, headers=_csrf(c)),
             "app_access_revoked")
    assert c.get("/api/auth/me").json()["workspace_id"] == env["ws"], "拒绝后会话留在原来的空间"


def test_switching_into_a_frozen_workspace_is_refused(env):
    ident, c = env["ident"], env["c"]
    w2 = ident.create_workspace("冻结的空间", owner_id=env["alice"]["id"])["id"]
    env["store"].execute("UPDATE workspaces SET status='frozen' WHERE id=?", (w2,))
    _refused(c.post("/api/auth/workspace", json={"workspace_id": w2}, headers=_csrf(c)),
             "workspace_unavailable")


def test_switching_into_a_usable_workspace_works_and_is_audited(env):
    ident, c, store = env["ident"], env["c"], env["store"]
    w2 = ident.create_workspace("第二个空间", owner_id=env["alice"]["id"])["id"]
    r = c.post("/api/auth/workspace", json={"workspace_id": w2}, headers=_csrf(c))
    assert r.status_code == 200 and c.get("/api/auth/me").json()["workspace_id"] == w2
    row = store.one("SELECT result, workspace_id FROM audit_events WHERE action='auth.workspace.switch' "
                    "ORDER BY seq DESC LIMIT 1")
    assert row == {"result": "ok", "workspace_id": w2}


def test_switching_into_a_workspace_you_are_not_in_is_not_found(env):
    ident, c = env["ident"], env["c"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    theirs = ident.workspaces_for(bob["id"])[0]["id"]
    r = c.post("/api/auth/workspace", json={"workspace_id": theirs}, headers=_csrf(c))
    assert r.status_code == 404 and r.json()["error"] == "no_workspace"


# ---------------------------------------------------------------- the registry
def test_an_explicit_revocation_beats_the_members_policy(env):
    apps, ws, uid = env["apps"], env["ws"], env["alice"]["id"]
    assert apps.get("docweave")["access_policy"] == "members"
    assert apps.check_access(uid, "docweave", ws)[0] is True
    apps.revoke(ws, "docweave", uid)
    ok, wid, reason = apps.check_access(uid, "docweave", ws)
    assert ok is False and wid is None and "撤销" in reason


def test_revoking_is_journalled(env):
    from synwevo_core import journal
    env["apps"].revoke(env["ws"], "docweave", env["alice"]["id"])
    entries = [e for e in journal.read(env["d"]) if e["kind"] == "entitlement_revoked"]
    assert entries and entries[-1]["user_id"] == env["alice"]["id"] and entries[-1]["workspace_id"] == env["ws"]


def _restore_and_replay(store, backup_rows, since):
    """Put `app_entitlements` back to what a backup held, then replay the journal as a restore
    does. Only the table under test is rolled back; the rest of the database is unchanged."""
    from synwevo_core import journal
    store.execute("DELETE FROM app_entitlements")
    for r in backup_rows:
        store.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by,revoked_at) "
                      "VALUES (?,?,?,?,?,?)", (r["workspace_id"], r["app_id"], r["user_id"], r["granted_at"],
                                               r["granted_by"], r["revoked_at"]))
    return journal.apply(store, journal.read(store.root, since=since))


def test_a_restore_that_had_a_granted_row_replays_the_revocation(env):
    """Alice owns her workspace, so the backup has a (granted) row and replay updates it."""
    import time
    store, apps, ws, uid = env["store"], env["apps"], env["ws"], env["alice"]["id"]
    backup = store.all("SELECT * FROM app_entitlements")
    taken = time.time()
    apps.revoke(ws, "docweave", uid)
    _restore_and_replay(store, backup, taken)
    assert apps.check_access(uid, "docweave", ws)[0] is False, "恢复备份让撤销的资格又回来了"
    _refused(env["c"].get("/api/projects"), "app_access_revoked")


def test_a_restore_without_the_row_cannot_bring_revoked_access_back(env):
    """The case the revoke upsert exists for: under `members` most people have no row at all, so
    the backup has none either and an UPDATE-only replay matches nothing."""
    import time
    store, ident, apps, ws = env["store"], env["ident"], env["apps"], env["ws"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    ident.add_member(ws, bob["id"], "editor")
    assert store.one("SELECT 1 FROM app_entitlements WHERE workspace_id=? AND user_id=?", (ws, bob["id"])) is None
    backup = store.all("SELECT * FROM app_entitlements")
    taken = time.time()
    apps.revoke(ws, "docweave", bob["id"])
    _restore_and_replay(store, backup, taken)
    assert apps.check_access(bob["id"], "docweave", ws)[0] is False, "恢复备份让撤销的资格又回来了"


def test_a_restore_holding_a_stale_revocation_still_replays_the_new_one(env):
    """Bob's row carries a revocation from an earlier membership (stale, so he has access), then
    his access was revoked for real. A backup from between the two must not give it back."""
    import time
    store, ident, apps, ws = env["store"], env["ident"], env["apps"], env["ws"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    ident.add_member(ws, bob["id"], "editor")
    _leave_a_stale_revocation(store, ws, bob["id"])
    assert apps.check_access(bob["id"], "docweave", ws)[0] is True
    backup = store.all("SELECT * FROM app_entitlements")
    taken = time.time()
    apps.revoke(ws, "docweave", bob["id"])
    _restore_and_replay(store, backup, taken)
    assert apps.check_access(bob["id"], "docweave", ws)[0] is False, "恢复备份让撤销的资格又回来了"


def test_turning_docs_off_for_a_workspace_is_journalled_and_survives_a_restore(env):
    import time
    from synwevo_core import journal
    store, apps, ws, uid = env["store"], env["apps"], env["ws"], env["alice"]["id"]
    taken = time.time()
    assert apps.disable_for_workspace(ws, "docweave") is True
    entries = [e for e in journal.read(env["d"]) if e["kind"] == "workspace_app_disabled"]
    assert entries and entries[-1] == {**entries[-1], "workspace_id": ws, "app_id": "docweave"}
    # the restore: the backup still had Docs on for this workspace
    apps.enable_for_workspace(ws, "docweave")
    journal.apply(store, journal.read(env["d"], since=taken))
    assert apps.check_access(uid, "docweave", ws)[0] is False, "恢复备份让停用的应用又被启用了"
    # Turning off something that was not on withdraws nothing and writes nothing.
    n = len(journal.read(env["d"]))
    assert apps.disable_for_workspace(ws, "docweave") is False
    assert len(journal.read(env["d"])) == n


# ---------------------------------------------------------------- leaving and coming back
def _login(api, email):
    c = TestClient(api)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def _leave_a_stale_revocation(store, ws, uid):
    """The row an earlier removal left behind, under a membership created after it.

    Removing someone revokes their entitlement rows; the add-back paths of earlier releases
    (`add_member`, invitation acceptance) cleared nothing, so upgraded databases already hold
    these rows for everyone who was removed and re-added. The write paths now delete them on a
    fresh membership, which does nothing for the rows already there — so the state is built
    directly rather than through a write path that no longer produces it.
    """
    import time
    removed_at = time.time() - 3600
    store.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by,revoked_at) "
                  "VALUES (?,'docweave',?,?,NULL,?) ON CONFLICT(workspace_id,app_id,user_id) DO UPDATE SET "
                  "revoked_at=excluded.revoked_at", (ws, uid, removed_at - 3600, removed_at))
    store.execute("UPDATE memberships SET created_at=? WHERE workspace_id=? AND user_id=?",
                  (removed_at + 60, ws, uid))


@pytest.mark.parametrize("row", ["none", "granted", "adopted"])
def test_a_member_removed_and_added_back_can_use_docs_again(env, row):
    """Removing someone writes explicit revocations; adding them back cleared nothing, and a
    revocation outranks the `members` policy — so a re-added person was locked out of Docs for
    good (403 app_access_revoked on the workspace switch and on every Docs request). On an
    adopted database every member has a row, so every member was exposed."""
    store, ident, apps, ws, api = env["store"], env["ident"], env["apps"], env["ws"], env["api"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    ident.add_member(ws, bob["id"], "editor")
    if row == "granted":
        apps.grant(ws, "docweave", bob["id"])
    elif row == "adopted":
        apps.adopt_existing_members()
    c = _login(api, "bob@example.com")
    assert c.post("/api/auth/workspace", json={"workspace_id": ws}, headers=_csrf(c)).status_code == 200

    ident.remove_member(ws, bob["id"])
    assert c.get("/api/auth/me").json()["workspace_id"] is None
    ident.add_member(ws, bob["id"], "owner")

    r = c.post("/api/auth/workspace", json={"workspace_id": ws}, headers=_csrf(c))
    assert r.status_code == 200, r.text[:300]
    assert c.get("/api/projects").status_code == 200
    assert apps.check_access(bob["id"], "docweave", ws) == (True, ws, "ok")
    # ... and the portal agrees: the card is back. (Synwevo Message is registered by this
    # single-process composition too and is open to every live account, CI-66.)
    assert [a["id"] for a in apps.apps_for(bob["id"], ws)] == ["docweave", "messages"]


def test_a_revocation_left_by_an_earlier_membership_does_not_lock_the_member_out(env):
    """The rows already in upgraded databases. Only the read side can fix those."""
    from synwevo_core.apps import entitlement_state
    store, ident, apps, ws, api = env["store"], env["ident"], env["apps"], env["ws"], env["api"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    ident.add_member(ws, bob["id"], "editor")
    _leave_a_stale_revocation(store, ws, bob["id"])
    assert entitlement_state(store, ws, "docweave", bob["id"]) == "none"
    c = _login(api, "bob@example.com")
    r = c.post("/api/auth/workspace", json={"workspace_id": ws}, headers=_csrf(c))
    assert r.status_code == 200, r.text[:300]
    assert c.get("/api/projects").status_code == 200
    # Under `explicit` a stale revocation is no grant either: coming back needs a new one.
    store.execute("UPDATE apps SET access_policy='explicit' WHERE id='docweave'")
    _refused(c.get("/api/projects"), "app_access_revoked")
    apps.grant(ws, "docweave", bob["id"])
    assert c.get("/api/projects").status_code == 200


def test_a_revocation_after_coming_back_still_holds(env):
    ident, apps, ws, api, store = env["ident"], env["apps"], env["ws"], env["api"], env["store"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    ident.add_member(ws, bob["id"], "editor")
    _leave_a_stale_revocation(store, ws, bob["id"])
    c = _login(api, "bob@example.com")
    assert c.post("/api/auth/workspace", json={"workspace_id": ws}, headers=_csrf(c)).status_code == 200
    apps.revoke(ws, "docweave", bob["id"])
    _refused(c.get("/api/projects"), "app_access_revoked")


def test_a_revocation_at_the_same_instant_as_the_membership_is_in_force(env):
    """On a coarse clock the tie goes to the withdrawal."""
    from synwevo_core.apps import entitlement_state
    store, ws, uid = env["store"], env["ws"], env["alice"]["id"]
    since = store.one("SELECT created_at FROM memberships WHERE workspace_id=? AND user_id=?", (ws, uid))["created_at"]
    store.execute("UPDATE app_entitlements SET revoked_at=? WHERE workspace_id=? AND user_id=?", (since, ws, uid))
    assert entitlement_state(store, ws, "docweave", uid) == "revoked"


def test_being_added_to_a_workspace_does_not_undo_a_revocation_there(env):
    """add_member on someone who is already a member keeps their `created_at`, so a revocation
    of that membership stays in force."""
    ident, apps, ws, uid = env["ident"], env["apps"], env["ws"], env["alice"]["id"]
    apps.revoke(ws, "docweave", uid)
    ident.add_member(ws, uid, "owner")
    assert apps.check_access(uid, "docweave", ws)[0] is False


def test_under_the_explicit_policy_coming_back_needs_a_new_grant(env):
    store, ident, apps, ws = env["store"], env["ident"], env["apps"], env["ws"]
    store.execute("UPDATE apps SET access_policy='explicit' WHERE id='docweave'")
    bob = ident.create_user("bob@example.com", PW, status="active")
    ident.add_member(ws, bob["id"], "editor")
    apps.grant(ws, "docweave", bob["id"])
    assert apps.check_access(bob["id"], "docweave", ws)[0] is True
    ident.remove_member(ws, bob["id"])
    ident.add_member(ws, bob["id"], "editor")
    assert apps.check_access(bob["id"], "docweave", ws)[0] is False, "移除前的授权不应随重新加入自动恢复"
    apps.grant(ws, "docweave", bob["id"])
    assert apps.check_access(bob["id"], "docweave", ws)[0] is True


def test_deferred_work_agrees_with_the_gate_about_a_re_added_member(env):
    from synwevo_core import authority
    store, ident, apps, ws = env["store"], env["ident"], env["apps"], env["ws"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    ident.add_member(ws, bob["id"], "editor")
    _leave_a_stale_revocation(store, ws, bob["id"])
    assert apps.check_access(bob["id"], "docweave", ws)[0] is True
    desc = authority.describe(user={"id": bob["id"]}, tenant=ws)
    assert authority.check(store, desc, need="write", tenant=ws) == (True, None)


def test_a_restart_of_the_account_service_does_not_undo_revocations(env, monkeypatch):
    """Adoption ran on every start and re-granted every member, re-enabling the application in
    every workspace. It now runs once per database."""
    store, ident, apps, ws, uid = env["store"], env["ident"], env["apps"], env["ws"], env["alice"]["id"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    bob_ws = ident.workspaces_for(bob["id"])[0]["id"]
    apps.revoke(ws, "docweave", uid)
    apps.disable_for_workspace(bob_ws, "docweave")

    from synwevo_server.app import create_app
    create_app(str(env["d"]), role="accounts", start_jobs=False)     # a restart of the account service
    create_app(str(env["d"]), role="all", start_jobs=False)

    assert apps.check_access(uid, "docweave", ws)[0] is False, "撤销的资格在重启后又回来了"
    assert apps.check_access(bob["id"], "docweave", bob_ws)[0] is False, "停用的应用在重启后又被启用了"
    assert store.one("SELECT 1 FROM meta WHERE key='apps_adopted:docweave'")


def test_adoption_still_runs_once_on_a_database_that_predates_the_registry(tmp_path):
    """The migration's purpose must survive: members of a pre-registry database keep access."""
    store = Store(tmp_path / "old")
    ident = Identity(store)
    u = ident.create_user("old@example.com", PW, status="active")
    ws = ident.workspaces_for(u["id"])[0]["id"]
    # Make it look like a database from before the registry: no registry rows at all.
    store.execute("DELETE FROM workspace_apps")
    store.execute("DELETE FROM app_entitlements")
    apps = Apps(store, ident)
    bootstrap_docs_app(store, ident)
    assert apps.check_access(u["id"], "docweave", ws)[0] is False
    out = apps.adopt_once()
    assert out and out["workspaces_enabled"] == 1
    assert apps.check_access(u["id"], "docweave", ws)[0] is True
    assert apps.adopt_once() is None, "第二次不应再执行"


def test_adoption_itself_never_clears_a_revocation(env):
    apps, ws, uid = env["apps"], env["ws"], env["alice"]["id"]
    apps.revoke(ws, "docweave", uid)
    apps.adopt_existing_members()
    assert apps.check_access(uid, "docweave", ws)[0] is False


def test_startup_registration_keeps_the_operators_access_policy(env):
    store, ident = env["store"], env["ident"]
    store.execute("UPDATE apps SET access_policy='explicit' WHERE id='docweave'")
    bootstrap_docs_app(store, ident)
    from synwevo_server.app import create_app
    create_app(str(env["d"]), role="all", start_jobs=False)
    assert Apps(store, ident).get("docweave")["access_policy"] == "explicit", "重启把逐人授权改回了成员即可用"


def test_the_docs_process_registers_its_own_address(tmp_path, monkeypatch):
    """Only the account service registered the app, with its own address deliberately ignored,
    so in a split deployment the portal card pointed at the built-in development URL."""
    d = tmp_path / "d"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_APP_URL", "https://docs.example.test/")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    create_app(str(d), role="app", start_jobs=False)
    store = Store(d)
    assert Apps(store, Identity(store)).get("docweave")["launch_url"] == "https://docs.example.test/"


# ---------------------------------------------------------------- the portal
def test_the_portal_shows_what_is_usable_in_the_current_workspace(tmp_path, monkeypatch):
    """The workspace switcher on the portal changed nothing: the cards were computed across
    every workspace. They are now computed for the workspace the session is in."""
    d = tmp_path / "p"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    api = create_app(str(d), role="portal", start_jobs=False)
    store = Store(d)
    ident, sessions = Identity(store), Sessions(store)
    bootstrap_docs_app(store, ident)
    u = ident.create_user("pat@example.com", PW, status="active")
    w1 = ident.workspaces_for(u["id"])[0]["id"]
    w2 = ident.create_workspace("没有文档应用", owner_id=u["id"])["id"]
    Apps(store, ident).disable_for_workspace(w2, "docweave")

    raw, _ = sessions.create(u["id"], workspace_id=w1, amr="password")    # a portal session
    c = TestClient(api)
    c.cookies.set(COOKIE_NAME, raw)
    c.cookies.set(CSRF_COOKIE, "t")
    assert [a["id"] for a in c.get("/api/portal/home").json()["apps"]] == ["docweave"]
    assert c.post("/api/portal/workspace", json={"workspace_id": w2},
                  headers={CSRF_HEADER: "t"}).status_code == 200
    home = c.get("/api/portal/home").json()
    assert home["current_workspace_id"] == w2 and home["apps"] == []
