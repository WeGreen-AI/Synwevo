"""Workspace member governance: listing, roles, removal, entitlements, ownership, invitations.

Driven through the account service's HTTP routes with real sessions. The cases that matter are
the refusals — acting above your own role, removing the last usable owner, a dangerous change
without re-authentication — and what a removal actually takes away: the membership, the
entitlements, the tokens, the session's workspace and the invitations that person sent.
"""
from __future__ import annotations

import importlib
import json
import threading

import pytest

from .platform_fixture import make_platform_admin
from fastapi.testclient import TestClient

from synwevo_core import journal, oidc
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, new_id, now

PW = "a-sufficiently-long-passphrase"
REASON = "离职交接完成，收回空间权限"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "https://docs.example.org")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    people = {}
    for name in ("alice", "bob", "carol", "dave", "erin"):
        people[name] = ident.create_user(f"{name}@example.com", PW, display_name=name.title(), status="active")
    ws = ident.workspaces_for(people["alice"]["id"])[0]["id"]              # alice owns it
    for name, role in (("bob", "admin"), ("carol", "editor"), ("dave", "viewer")):
        ident.add_member(ws, people[name]["id"], role)
        Apps(store, ident).grant(ws, "docweave", people[name]["id"])
    oidc.Clients(store).register(client_id="docweave-web", app_id="docweave", name="Synwevo Docs",
                                 redirect_uris=["http://docweave.localhost:8765/auth/callback"],
                                 scopes="openid profile email docweave.read")
    clients = {n: _login(api, f"{n}@example.com") for n in ("alice", "bob", "carol", "dave")}
    return {"api": api, "store": store, "ident": ident, "ws": ws, "p": people, "c": clients,
            "root": tmp_path / "d", "mailer": api.state.mailer}


def _login(api, email):
    c = TestClient(api)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def _h(c):
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""}


def post(c, path, body=None):
    return c.post(path, json=body if body is not None else {}, headers=_h(c))


def patch(c, path, body):
    return c.patch(path, json=body, headers=_h(c))


def reauth(c):
    assert post(c, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200


def uid(env, name):
    return env["p"][name]["id"]


def _token(env, name, ws=None, client_id="docweave-web"):
    raw = new_id("raw")
    env["store"].execute(
        "INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,scope,workspace_id,issued_at,"
        "access_expires_at,family_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (new_id("tok"), client_id, uid(env, name), oidc._sha(raw), "docweave.read", ws or env["ws"],
         now(), now() + 900, new_id("fam")))
    return raw


def _journal_kinds(env):
    return [e["kind"] for e in journal.read(env["root"])]


# ---------------------------------------------------------------- listing
def test_only_admins_and_owners_can_list_and_outsiders_learn_nothing(env):
    ws = env["ws"]
    assert env["c"]["dave"].get(f"/api/auth/workspaces/{ws}/members").status_code == 403
    assert env["c"]["carol"].get(f"/api/auth/workspaces/{ws}/members").status_code == 403
    outsider = _login(env["api"], "erin@example.com")
    r = outsider.get(f"/api/auth/workspaces/{ws}/members")
    assert r.status_code == 404, "非成员不能得知空间是否存在"
    body = env["c"]["bob"].get(f"/api/auth/workspaces/{ws}/members").json()
    assert body["total"] == 4 and body["caller"]["role"] == "admin"
    assert [i["role"] for i in body["items"]] == ["owner", "admin", "editor", "viewer"]
    assert body["owner_count"] == 1 and body["single_owner"] is True
    mine = next(i for i in body["items"] if i["is_self"])
    assert mine["user_id"] == uid(env, "bob") and mine["manageable"] is False


def test_platform_admin_gets_no_member_view_of_a_workspace_they_are_not_in(env):
    make_platform_admin(env["ident"].store, uid(env, "erin"))
    admin = _login(env["api"], "erin@example.com")
    assert admin.get(f"/api/auth/workspaces/{env['ws']}/members").status_code == 404


def test_search_and_filters(env):
    ws, c = env["ws"], env["c"]["alice"]
    assert [i["email"] for i in c.get(f"/api/auth/workspaces/{ws}/members?q=CAROL").json()["items"]] == \
        ["carol@example.com"]
    assert {i["role"] for i in c.get(f"/api/auth/workspaces/{ws}/members?role=viewer").json()["items"]} == {"viewer"}
    env["ident"].set_entitlement(ws, uid(env, "dave"), "docweave", grant=False)
    revoked = c.get(f"/api/auth/workspaces/{ws}/members?entitlement=revoked").json()
    assert [i["user_id"] for i in revoked["items"]] == [uid(env, "dave")]
    env["ident"].set_status(uid(env, "carol"), "suspended")
    susp = c.get(f"/api/auth/workspaces/{ws}/members?status=suspended").json()
    assert [i["user_id"] for i in susp["items"]] == [uid(env, "carol")]
    page = c.get(f"/api/auth/workspaces/{ws}/members?limit=2").json()
    assert len(page["items"]) == 2 and page["next_offset"] == 2 and page["total"] == 4
    assert c.get(f"/api/auth/workspaces/{ws}/members?q=100%25").json()["total"] == 0, "LIKE 通配符要转义"


# ---------------------------------------------------------------- removal
def test_removal_needs_reauth_and_a_reason(env):
    ws, c = env["ws"], env["c"]["alice"]
    r = post(c, f"/api/auth/workspaces/{ws}/members/{uid(env, 'carol')}/remove", {"reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"
    reauth(c)
    r = post(c, f"/api/auth/workspaces/{ws}/members/{uid(env, 'carol')}/remove", {"reason": "短"})
    assert r.status_code == 400 and r.json()["error"] == "missing_reason"
    assert env["ident"].membership(ws, uid(env, "carol"))


def test_removing_a_member_takes_everything_that_membership_held_open(env):
    store, ident, ws = env["store"], env["ident"], env["ws"]
    carol = uid(env, "carol")
    # carol also belongs to her own workspace; that must be untouched
    own_ws = [w["id"] for w in ident.workspaces_for(carol) if w["id"] != ws][0]
    here, elsewhere = _token(env, "carol"), _token(env, "carol", ws=own_ws)
    env["c"]["carol"].post("/api/auth/workspace", json={"workspace_id": ws}, headers=_h(env["c"]["carol"]))
    # an invitation carol sent (as if she had been an admin when she sent it)
    store.execute("INSERT INTO invitations (id,email_key,workspace_id,role,can_export,token_hash,invited_by,"
                  "created_at,expires_at) VALUES ('iv_c','x@example.com',?,'viewer',0,'h1',?,?,?)",
                  (ws, carol, now(), now() + 3600))
    c = env["c"]["alice"]
    reauth(c)
    r = post(c, f"/api/auth/workspaces/{ws}/members/{carol}/remove", {"reason": REASON})
    assert r.status_code == 200, r.text
    out = r.json()["removed"]
    assert out["tokens_revoked"] == 1 and out["invitations_cancelled"] == 1 and out["sessions_moved"] >= 1
    assert ident.membership(ws, carol) is None
    assert store.one("SELECT revoked_at FROM app_entitlements WHERE workspace_id=? AND user_id=?",
                     (ws, carol))["revoked_at"]
    grants = oidc.Grants(store)
    assert grants.load_access(here) is None
    assert grants.load_access(elsewhere) is not None, "只收回这个空间的令牌，不影响她的其他空间"
    assert store.one("SELECT revoked_at FROM invitations WHERE id='iv_c'")["revoked_at"]
    assert env["c"]["carol"].get("/api/auth/me").json()["workspace_id"] != ws
    audit = store.one("SELECT * FROM audit_events WHERE action='member.remove'")
    assert audit["object_id"] == carol and json.loads(audit["meta"])["reason"] == REASON
    assert "member_removed" in _journal_kinds(env)


def test_an_admin_cannot_remove_an_owner_or_another_admin(env):
    ws, bob = env["ws"], env["c"]["bob"]
    env["ident"].add_member(ws, uid(env, "erin"), "admin")
    reauth(bob)
    for target in ("alice", "erin"):
        r = post(bob, f"/api/auth/workspaces/{ws}/members/{uid(env, target)}/remove", {"reason": REASON})
        assert r.status_code == 403 and r.json()["error"] == "role_too_high", target
    assert post(bob, f"/api/auth/workspaces/{ws}/members/{uid(env, 'dave')}/remove",
                {"reason": REASON}).status_code == 200


def test_bulk_removal_reports_every_object(env):
    ws, bob = env["ws"], env["c"]["bob"]
    reauth(bob)
    r = post(bob, f"/api/auth/workspaces/{ws}/members/remove",
             {"user_ids": [uid(env, "carol"), uid(env, "alice"), "u_nobody", uid(env, "bob")], "reason": REASON})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["succeeded"] == 1 and body["failed"] == 3, "部分失败不能报成整体成功"
    codes = {x["id"]: x["code"] for x in body["results"]}
    assert codes == {uid(env, "carol"): "ok", uid(env, "alice"): "role_too_high",
                     "u_nobody": "no_member", uid(env, "bob"): "use_leave"}
    # it names the control that exists on the page (CI-85)
    assert {x["id"]: x["message"] for x in body["results"]}[uid(env, "bob")] == "要退出这个工作空间，请使用「退出工作空间」"
    assert env["ident"].membership(ws, uid(env, "alice"))


def test_the_last_usable_owner_cannot_leave_even_with_a_suspended_co_owner(env):
    ws, ident = env["ws"], env["ident"]
    ident.add_member(ws, uid(env, "erin"), "owner")
    ident.set_status(uid(env, "erin"), "suspended")
    r = post(env["c"]["alice"], f"/api/auth/workspaces/{ws}/leave")
    assert r.status_code == 409 and r.json()["error"] == "last_owner"
    with pytest.raises(Exception) as e:
        ident.change_role(ws, uid(env, "alice"), "editor")
    assert getattr(e.value, "code", "") == "last_owner"
    # a member may leave freely
    assert post(env["c"]["carol"], f"/api/auth/workspaces/{ws}/leave").status_code == 200
    assert ident.membership(ws, uid(env, "carol")) is None


# ---------------------------------------------------------------- roles
def test_role_changes_respect_the_ceiling_and_self(env):
    ws, bob, alice = env["ws"], env["c"]["bob"], env["c"]["alice"]
    r = patch(bob, f"/api/auth/workspaces/{ws}/members/{uid(env, 'bob')}", {"role": "owner"})
    assert r.status_code == 403 and r.json()["error"] == "self_role"
    # refused for what it is, before any password prompt: re-authenticating would not help
    r = patch(bob, f"/api/auth/workspaces/{ws}/members/{uid(env, 'carol')}", {"role": "owner", "reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "role_too_high"
    r = patch(bob, f"/api/auth/workspaces/{ws}/members/{uid(env, 'alice')}", {"role": "editor", "reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "role_too_high"
    r = patch(bob, f"/api/auth/workspaces/{ws}/members/{uid(env, 'dave')}", {"role": "editor"})
    assert r.status_code == 200 and r.json()["after"]["role"] == "editor"
    # demoting an admin is dangerous: needs the owner's re-authentication and a reason
    r = patch(alice, f"/api/auth/workspaces/{ws}/members/{uid(env, 'bob')}", {"role": "viewer"})
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"
    reauth(alice)
    r = patch(alice, f"/api/auth/workspaces/{ws}/members/{uid(env, 'bob')}", {"role": "viewer", "reason": REASON})
    assert r.status_code == 200, r.text
    assert r.json()["before"]["role"] == "admin" and r.json()["after"] == {"role": "viewer", "can_export": False}
    assert r.json()["journaled"] is True and "role_changed" in _journal_kinds(env)


def test_can_export_is_kept_unless_changed(env):
    ws, ident = env["ws"], env["ident"]
    ident.add_member(ws, uid(env, "erin"), "viewer", can_export=True)
    out = ident.change_role(ws, uid(env, "erin"), "editor")
    assert out["can_export"] == 1 and out["before"] == {"role": "viewer", "can_export": True}
    ident.change_role(ws, uid(env, "erin"), "editor", can_export=False)
    assert ident.change_role(ws, uid(env, "erin"), "admin")["can_export"] == 0, "显式关掉的导出权不能被角色变化悄悄打开"
    assert ident.change_role(ws, uid(env, "erin"), "viewer", can_export=True)["can_export"] == 1


def test_bulk_role_change_asks_for_reauth_up_front_when_any_item_is_dangerous(env):
    ws, alice = env["ws"], env["c"]["alice"]
    body = {"changes": [{"user_id": uid(env, "dave"), "role": "editor"},
                        {"user_id": uid(env, "bob"), "role": "editor"}], "reason": REASON}
    r = post(alice, f"/api/auth/workspaces/{ws}/members/roles", body)
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"
    assert env["ident"].membership(ws, uid(env, "dave"))["role"] == "viewer", "要求重认证时一项都不能先改"
    reauth(alice)
    body["changes"].append({"user_id": "u_nobody", "role": "viewer"})
    r = post(alice, f"/api/auth/workspaces/{ws}/members/roles", body).json()
    assert [x["ok"] for x in r["results"]] == [True, True, False] and r["ok"] is False


# ---------------------------------------------------------------- entitlements
def test_revoking_an_entitlement_is_explicit_journalled_and_ends_tokens(env):
    from synwevo_core import authority
    ws, store, alice = env["ws"], env["store"], env["c"]["alice"]
    dave = uid(env, "dave")
    tok = _token(env, "dave")
    # a person with no row at all, under the `members` policy: revoking still writes a row
    store.execute("DELETE FROM app_entitlements WHERE user_id=?", (dave,))
    r = post(alice, f"/api/auth/workspaces/{ws}/members/{dave}/entitlements", {"app_id": "docweave", "grant": False})
    assert r.status_code == 200 and r.json()["before"] == "none" and r.json()["after"] == "revoked"
    assert r.json()["tokens_revoked"] == 1 and oidc.Grants(store).load_access(tok) is None
    assert authority.check(store, {"kind": "user", "user_id": dave, "tenant": ws}, need="read") == \
        (False, "entitlement_revoked")
    assert "entitlement_revoked" in _journal_kinds(env)
    r = post(alice, f"/api/auth/workspaces/{ws}/members/{dave}/entitlements", {"grant": True})
    assert r.status_code == 200 and r.json()["after"] == "active"
    assert authority.check(store, {"kind": "user", "user_id": dave, "tenant": ws}, need="read") == (True, None)
    r = post(alice, f"/api/auth/workspaces/{ws}/members/{uid(env, 'alice')}/entitlements", {"grant": False})
    assert r.status_code == 403 and r.json()["error"] == "self_entitlement"


def test_bulk_entitlements_report_per_member(env):
    ws, bob = env["ws"], env["c"]["bob"]
    r = post(bob, f"/api/auth/workspaces/{ws}/members/entitlements",
             {"changes": [{"user_id": uid(env, "carol"), "grant": False},
                          {"user_id": uid(env, "alice"), "grant": False},
                          {"user_id": uid(env, "dave"), "grant": "yes"}]}).json()
    assert [(x["ok"], x["code"]) for x in r["results"]] == [(True, "ok"), (False, "role_too_high"),
                                                           (False, "bad_grant")]


# ---------------------------------------------------------------- ownership
@pytest.mark.parametrize("demote", ["admin", "editor", "viewer"])
def test_ownership_transfer(env, demote):
    ws, alice, ident, store = env["ws"], env["c"]["alice"], env["ident"], env["store"]
    body = {"to_user_id": uid(env, "carol"), "demote_self_to": demote, "reason": REASON}
    before = journal.read(env["root"])
    assert post(alice, f"/api/auth/workspaces/{ws}/ownership/transfer", body).status_code == 403
    assert journal.read(env["root"]) == before, "No withdrawal before strict reauthentication"
    reauth(alice)
    ident.set_status(uid(env, "dave"), "suspended")
    r = post(alice, f"/api/auth/workspaces/{ws}/ownership/transfer", {**body, "to_user_id": uid(env, "dave")})
    assert r.status_code == 409 and r.json()["error"] == "target_unavailable"
    assert journal.read(env["root"]) == before, "A refused transfer must not create a replayable demotion"
    assert ident.membership(ws, uid(env, "alice"))["role"] == "owner"
    r = post(alice, f"/api/auth/workspaces/{ws}/ownership/transfer", body)
    assert r.status_code == 200, r.text
    assert r.json()["journaled"] is True and r.json()["self_role"] == demote
    assert ident.membership(ws, uid(env, "carol"))["role"] == "owner"
    assert ident.membership(ws, uid(env, "alice"))["role"] == demote
    entries = journal.read(env["root"])
    assert entries[:-1] == before
    assert {k: v for k, v in entries[-1].items() if k not in ("at", "attempt_id")} == {
        "kind": "role_changed", "workspace_id": ws, "user_id": uid(env, "alice"), "role": demote}
    # written ahead in the transfer's transaction, with the receipt that committed with it
    assert store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + entries[-1]["attempt_id"],))
    assert isinstance(entries[-1]["at"], (int, float)), "Only the withdrawal, not the promotion, is journalled"
    ev = store.all("SELECT meta FROM audit_events WHERE action='workspace.ownership.transfer'")
    assert len(ev) == 1 and json.loads(ev[0]["meta"])["from"] == uid(env, "alice") \
        and json.loads(ev[0]["meta"])["to"] == uid(env, "carol")
    assert json.loads(ev[0]["meta"])["journaled"] is True
    # a non-owner cannot transfer
    reauth(env["c"]["bob"])
    r = post(env["c"]["bob"], f"/api/auth/workspaces/{ws}/ownership/transfer", {**body, "to_user_id": uid(env, "bob")})
    assert r.status_code == 403 and r.json()["error"] == "not_owner"
    assert journal.read(env["root"]) == entries


@pytest.mark.parametrize("explicit_null", [False, True])
def test_ownership_transfer_without_self_demotion_has_no_withdrawal(env, explicit_null):
    ws, alice, ident = env["ws"], env["c"]["alice"], env["ident"]
    body = {"to_user_id": uid(env, "carol"), "reason": REASON}
    if explicit_null:
        body["demote_self_to"] = None
    before = journal.read(env["root"])
    reauth(alice)
    response = post(alice, f"/api/auth/workspaces/{ws}/ownership/transfer", body)
    assert response.status_code == 200, response.text
    assert response.json()["journaled"] is None and response.json()["self_role"] == "owner"
    assert ident.membership(ws, uid(env, "alice"))["role"] == "owner"
    assert ident.membership(ws, uid(env, "carol"))["role"] == "owner"
    assert journal.read(env["root"]) == before
    event = env["store"].one("SELECT meta FROM audit_events WHERE action='workspace.ownership.transfer'")
    assert json.loads(event["meta"])["journaled"] is None


def test_ownership_transfer_reports_journal_failure_without_undoing_demotion(env, capsys):
    ws, alice, ident = env["ws"], env["c"]["alice"], env["ident"]
    # Real isolated filesystem failure: a directory cannot be opened as the append-only journal.
    journal.path(env["root"]).mkdir()
    reauth(alice)
    response = post(alice, f"/api/auth/workspaces/{ws}/ownership/transfer",
                    {"to_user_id": uid(env, "carol"), "demote_self_to": "viewer", "reason": REASON})
    assert response.status_code == 200, response.text
    assert response.json()["journaled"] is False
    assert ident.membership(ws, uid(env, "alice"))["role"] == "viewer"
    assert ident.membership(ws, uid(env, "carol"))["role"] == "owner"
    event = env["store"].one("SELECT meta FROM audit_events WHERE action='workspace.ownership.transfer'")
    assert json.loads(event["meta"])["journaled"] is False
    assert "security journal write failed for role_changed" in capsys.readouterr().out


def test_concurrent_transfers_and_removals_never_leave_no_owner(env):
    """R11: 20 threads mixing transfer and remove; the owner count never reaches zero."""
    ident, ws, store = env["ident"], env["ws"], env["store"]
    for name in ("carol", "dave"):
        ident.change_role(ws, uid(env, name), "editor")
    names = ["alice", "carol", "dave"]
    errors, ownerless = [], []

    def worker(i):
        a, b = names[i % 3], names[(i + 1) % 3]
        try:
            if i % 2:
                ident.transfer_ownership(ws, uid(env, a), uid(env, b), demote_self_to="editor")
            else:
                if ident.membership(ws, uid(env, a)) is None:
                    ident.add_member(ws, uid(env, a), "editor")
                ident.remove_member(ws, uid(env, b))
        except Exception as e:  # noqa: BLE001 - refusals are the expected outcome for many
            errors.append(getattr(e, "code", type(e).__name__))
        n = store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=? AND role='owner'", (ws,))["n"]
        if n < 1:
            ownerless.append(i)          # collected, not asserted: an assert in a thread is lost

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not ownerless, f"these steps saw a workspace with no owner: {ownerless}"
    assert store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=? AND role='owner'", (ws,))["n"] >= 1
    assert set(errors) <= {"last_owner", "not_owner", "target_unavailable", "same_user"}, errors


# ---------------------------------------------------------------- invitations
def test_invitation_rate_limit_answers_429_with_retry_after(env):
    ws, store = env["ws"], env["store"]
    for i in range(50):
        store.execute("INSERT INTO invitations (id,email_key,workspace_id,role,can_export,token_hash,invited_by,"
                      "created_at,expires_at) VALUES (?,?,?,'viewer',0,?,?,?,?)",
                      (f"iv_{i}", f"p{i}@example.com", ws, f"h{i}", uid(env, "alice"), now() - 60, now() + 3600))
    r = post(env["c"]["alice"], f"/api/auth/workspaces/{ws}/invitations", {"email": "late@example.com"})
    assert r.status_code == 429 and r.json()["error"] == "invite_rate_limited"
    assert int(r.headers["Retry-After"]) > 0
    assert r.json()["detail"]["limit"] == 50, "the sentence's number, for wording it in the reader's language (CI-85)"


@pytest.mark.parametrize("action", ["create", "resend"])
def test_bulk_invitation_rate_limit_preserves_per_item_retry_after(env, action):
    """Real quota exhaustion partway through a batch must not hide its successes or cooldown."""
    ws, store, alice = env["ws"], env["store"], env["c"]["alice"]
    oldest = now() - 60
    with store.tx() as t:
        for i in range(49):
            t.execute("INSERT INTO invitations (id,email_key,workspace_id,role,can_export,token_hash,"
                      "invited_by,created_at,expires_at) VALUES (?,?,?,'viewer',0,?,?,?,?)",
                      (f"iv_quota_{i}", f"quota{i}@example.test", ws, f"quota_hash_{i}",
                       uid(env, "alice"), oldest, now() + 3600))
    if action == "create":
        path = f"/api/auth/workspaces/{ws}/invitations/batch"
        ids = ["first@example.test", "limited@example.test", "not-an-email"]
        body = {"invitations": [{"email": email} for email in ids]}
        other_error = "bad_email"
    else:
        path = f"/api/auth/workspaces/{ws}/invitations/bulk"
        ids = ["iv_quota_0", "iv_quota_1", "iv_missing"]
        body = {"action": "resend", "ids": ids}
        other_error = "no_invite"
    before = now()
    response = post(alice, path, body)
    after = now()
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["ok"] is False and out["succeeded"] == 1 and out["failed"] == 2
    success, limited, invalid = out["results"]
    assert [(item["id"], item["ok"], item["code"]) for item in out["results"]] == [
        (ids[0], True, "ok"), (ids[1], False, "invite_rate_limited"), (ids[2], False, other_error)]
    assert limited["message"] and type(limited["retry_after"]) is int
    # The value comes from the real 24-hour quota window, not a UI default or a test double.
    assert int(oldest + 86400 - after) + 1 <= limited["retry_after"] <= int(oldest + 86400 - before) + 1
    assert "retry_after" not in success and "retry_after" not in invalid
    assert limited["detail"] == {"limit": 50} and "detail" not in invalid, "only the number a sentence quotes (CI-85)"
    assert "Retry-After" not in response.headers, "A per-item limit is not a blanket batch refusal"
    assert success["invitation"]["mail"] == "queued"
    assert store.one("SELECT COUNT(*) AS n FROM invitations WHERE workspace_id=?", (ws,))["n"] == 50
    assert store.one("SELECT COUNT(*) AS n FROM mail_outbox WHERE kind='invite'")["n"] == 1
    groups = alice.get(f"/api/auth/workspaces/{ws}/invitations").json()["groups"]
    assert groups["pending"] == (50 if action == "create" else 49)
    assert groups["revoked"] == (0 if action == "create" else 1)
    if action == "resend":
        assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (ids[1],))["revoked_at"] is None


def test_invitation_expiry_is_chosen_within_bounds(env):
    ws, c = env["ws"], env["c"]["alice"]
    r = post(c, f"/api/auth/workspaces/{ws}/invitations", {"email": "n@example.com", "expires_in_days": 2})
    assert r.status_code == 200 and 2 * 86400 - 60 < r.json()["expires_at"] - now() <= 2 * 86400
    r = post(c, f"/api/auth/workspaces/{ws}/invitations", {"email": "m@example.com", "expires_in_days": 90})
    assert r.status_code == 400 and r.json()["error"] == "bad_expiry"


def test_editing_an_invitation(env):
    ws, bob, alice = env["ws"], env["c"]["bob"], env["c"]["alice"]
    iid = post(alice, f"/api/auth/workspaces/{ws}/invitations", {"email": "n@example.com", "role": "editor"}).json()["id"]
    r = patch(bob, f"/api/auth/invitations/{iid}", {"role": "owner"})
    assert r.status_code == 403 and r.json()["error"] == "role_too_high"
    r = patch(bob, f"/api/auth/invitations/{iid}", {"role": "viewer"})
    assert r.status_code == 200 and r.json()["invitation"]["role"] == "viewer"
    assert r.json()["invitation"]["can_export"] is False
    env["store"].execute("UPDATE invitations SET accepted_at=? WHERE id=?", (now(), iid))
    r = patch(alice, f"/api/auth/invitations/{iid}", {"role": "editor"})
    assert r.status_code == 409 and r.json()["error"] == "invite_not_pending"
    # a member of another workspace cannot touch it
    outsider = _login(env["api"], "erin@example.com")
    assert patch(outsider, f"/api/auth/invitations/{iid}", {"role": "viewer"}).status_code == 404


def test_resending_an_expired_invitation_kills_the_old_link(env):
    ws, alice, store = env["ws"], env["c"]["alice"], env["store"]
    iid = post(alice, f"/api/auth/workspaces/{ws}/invitations", {"email": "n@example.com"}).json()["id"]
    body = store.one("SELECT body FROM mail_outbox WHERE ref_id=?", (iid,))["body"]
    old_token = body.split("token=")[1].split()[0]
    guest = TestClient(env["api"])
    # New policy: an immediate resend is refused, and must not burn the old link
    # or enqueue another message. Previously this test assumed immediate resends.
    cooldown = post(alice, f"/api/auth/invitations/{iid}/resend")
    assert cooldown.status_code == 429 and cooldown.json()["error"] == "invite_cooldown"
    assert int(cooldown.headers["Retry-After"]) > 0
    assert guest.get(f"/api/auth/invitations/preview?token={old_token}").json()["state"] == "pending"
    assert store.one("SELECT COUNT(*) AS n FROM mail_outbox WHERE ref_id=?", (iid,))["n"] == 1
    # Isolated clock fixture: beyond the resend cooldown, and the old link expired.
    store.execute("UPDATE invitations SET created_at=?,expires_at=? WHERE id=?", (now() - 61, now() - 1, iid))
    r = post(alice, f"/api/auth/invitations/{iid}/resend")
    assert r.status_code == 200, r.text
    new_id_ = r.json()["invitation"]["id"]
    assert new_id_ != iid
    assert guest.get(f"/api/auth/invitations/preview?token={old_token}").json()["state"] == "revoked"
    assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (iid,))["revoked_at"]
    items = {i["id"]: i["state"] for i in alice.get(f"/api/auth/workspaces/{ws}/invitations").json()["items"]}
    assert items[new_id_] == "pending"
    new_body = store.one("SELECT body FROM mail_outbox WHERE ref_id=?", (new_id_,))["body"]
    new_token = new_body.split("token=")[1].split()[0]
    assert new_token != old_token
    old = guest.post("/api/auth/invitations/accept", json={"token": old_token, "password": PW, "display_name": "fixture"})
    assert old.status_code == 409 and old.json()["error"] == "invite_revoked"
    accepted = guest.post("/api/auth/invitations/accept", json={"token": new_token, "password": PW, "display_name": "fixture"})
    assert accepted.status_code == 200, accepted.text
    assert store.one("SELECT accepted_at FROM invitations WHERE id=?", (new_id_,))["accepted_at"]


def test_bulk_invitation_actions_report_per_invitation(env):
    ws, alice, store = env["ws"], env["c"]["alice"], env["store"]
    a = post(alice, f"/api/auth/workspaces/{ws}/invitations", {"email": "a@example.com"}).json()["id"]
    b = post(alice, f"/api/auth/workspaces/{ws}/invitations", {"email": "b@example.com"}).json()["id"]
    store.execute("UPDATE invitations SET accepted_at=? WHERE id=?", (now(), b))
    r = post(alice, f"/api/auth/workspaces/{ws}/invitations/bulk", {"action": "revoke", "ids": [a, b, "iv_nope"]}).json()
    assert [(x["id"], x["code"]) for x in r["results"]] == [(a, "ok"), (b, "invite_accepted"), ("iv_nope", "no_invite")]
    listing = alice.get(f"/api/auth/workspaces/{ws}/invitations").json()
    assert listing["groups"]["revoked"] == 1 and listing["groups"]["accepted"] == 1
    assert {i["state"] for i in alice.get(f"/api/auth/workspaces/{ws}/invitations?state=revoked").json()["items"]} \
        == {"revoked"}


def test_batch_invite_reports_each_address(env):
    ws, bob = env["ws"], env["c"]["bob"]
    r = post(bob, f"/api/auth/workspaces/{ws}/invitations/batch",
             {"invitations": [{"email": "new@example.com", "role": "editor"},
                              {"email": "carol@example.com", "role": "viewer"},
                              {"email": "boss@example.com", "role": "owner"},
                              {"email": "not-an-email"}]}).json()
    assert [(x["id"], x["code"]) for x in r["results"]] == [
        ("new@example.com", "ok"), ("carol@example.com", "already_member"),
        ("boss@example.com", "role_too_high"), ("not-an-email", "bad_email")]


# ---------------------------------------------------------------- frozen workspaces
def test_a_frozen_workspace_sheds_access_but_gains_none(env):
    ws, ident, alice = env["ws"], env["ident"], env["c"]["alice"]
    ident.set_workspace_frozen(ws, True)
    r = patch(alice, f"/api/auth/workspaces/{ws}/members/{uid(env, 'dave')}", {"role": "editor"})
    assert r.status_code == 409 and r.json()["error"] == "workspace_frozen"
    r = post(alice, f"/api/auth/workspaces/{ws}/members/{uid(env, 'dave')}/entitlements", {"grant": False})
    assert r.status_code == 200, "冻结期间仍然可以收回权限"
    reauth(alice)
    assert post(alice, f"/api/auth/workspaces/{ws}/members/{uid(env, 'carol')}/remove",
                {"reason": REASON}).status_code == 200


def test_a_former_member_invited_back_starts_fresh(env):
    """Removal writes revoked entitlement rows; a re-invited member must not inherit them and
    silently be unable to open the application — nor get their old grants back implicitly."""
    from synwevo_identity.invites import Invites
    ws, ident, store = env["ws"], env["ident"], env["store"]
    carol = uid(env, "carol")
    ident.remove_member(ws, carol)
    assert Apps(store, ident).check_access(carol, "docweave", ws)[0] is False
    inv = Invites(store, ident)
    _, raw = inv.create(workspace_id=ws, email="carol@example.com", role="viewer", actor_id=uid(env, "alice"))
    inv.accept(raw, password=None, display_name=None, as_user_id=carol)
    assert ident.membership(ws, carol)["role"] == "viewer"
    assert Apps(store, ident).check_access(carol, "docweave", ws)[0] is True
    assert store.one("SELECT COUNT(*) AS n FROM app_entitlements WHERE workspace_id=? AND user_id=? "
                     "AND revoked_at IS NOT NULL", (ws, carol))["n"] == 0
