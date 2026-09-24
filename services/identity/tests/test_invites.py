"""Invitations, email tokens and the outbox.

The interesting cases are all the ways an invitation stops being valid after it was written:
it expires, it is revoked, it is superseded, the workspace goes away, or the person who issued
it loses the authority they issued it with. And the ones where two things happen at once.
"""
from __future__ import annotations

import threading

import pytest

from synwevo_identity import mail as mail_mod
from synwevo_core.identity import Identity
from synwevo_identity.invites import Invites
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store, now

PW = "a-sufficiently-long-passphrase"
PW2 = "another-sufficiently-long-pass"


@pytest.fixture()
def env(tmp_path):
    store = Store(tmp_path / "d")
    ident = Identity(store)
    inv = Invites(store, ident)
    owner = ident.create_user("owner@example.com", PW, status="active")
    ws = ident.workspaces_for(owner["id"])[0]["id"]
    return {"store": store, "ident": ident, "inv": inv, "owner": owner, "ws": ws}


# ---------------------------------------------------------------- issuing
def test_owner_can_invite_and_the_token_is_not_stored(env):
    inv, ws, owner = env["inv"], env["ws"], env["owner"]
    row, raw = inv.create(workspace_id=ws, email="new@example.com", role="editor", actor_id=owner["id"])
    assert raw and raw not in str(row)
    stored = env["store"].one("SELECT token_hash FROM invitations WHERE id=?", (row["id"],))["token_hash"]
    assert stored != raw and len(stored) == 64


def test_a_non_admin_member_cannot_invite(env):
    ident, inv, ws = env["ident"], env["inv"], env["ws"]
    editor = ident.create_user("ed@example.com", PW, status="active")
    ident.add_member(ws, editor["id"], "editor")
    with pytest.raises(ServiceError) as e:
        inv.create(workspace_id=ws, email="x@example.com", role="viewer", actor_id=editor["id"])
    assert e.value.status == 403


def test_nobody_can_invite_above_their_own_role(env):
    """Otherwise an admin promotes themselves by proxy: invite someone as owner, then ask them."""
    ident, inv, ws = env["ident"], env["inv"], env["ws"]
    admin = ident.create_user("ad@example.com", PW, status="active")
    ident.add_member(ws, admin["id"], "admin")
    with pytest.raises(ServiceError) as e:
        inv.create(workspace_id=ws, email="x@example.com", role="owner", actor_id=admin["id"])
    assert e.value.code == "role_too_high"
    # but the same person may invite at or below their own level
    assert inv.create(workspace_id=ws, email="y@example.com", role="admin", actor_id=admin["id"])[0]


def test_platform_admin_gets_no_workspace_authority_from_it(env):
    """Platform role manages accounts and service state; it is not workspace membership."""
    ident, inv, ws = env["ident"], env["inv"], env["ws"]
    root = ident.create_user("root@example.com", PW, status="active", platform_role="admin")
    with pytest.raises(ServiceError) as e:
        inv.create(workspace_id=ws, email="x@example.com", role="viewer", actor_id=root["id"])
    assert e.value.status == 403


def test_inviting_an_existing_member_is_refused(env):
    ident, inv, ws, owner = env["ident"], env["inv"], env["ws"], env["owner"]
    m = ident.create_user("m@example.com", PW, status="active")
    ident.add_member(ws, m["id"], "editor")
    with pytest.raises(ServiceError) as e:
        inv.create(workspace_id=ws, email="m@example.com", role="editor", actor_id=owner["id"])
    assert e.value.code == "already_member"


def test_reinviting_supersedes_the_previous_token(env):
    """Otherwise revoking "the" invitation leaves an older one in an inbox still working."""
    inv, ws, owner = env["inv"], env["ws"], env["owner"]
    _, first = inv.create(workspace_id=ws, email="n@example.com", role="viewer", actor_id=owner["id"])
    _, second = inv.create(workspace_id=ws, email="n@example.com", role="editor", actor_id=owner["id"])
    assert inv.preview(first)["state"] == "revoked"
    assert inv.preview(second)["state"] == "pending"


# ---------------------------------------------------------------- accepting
def test_accept_creates_the_account_and_the_membership(env):
    inv, ws, owner, ident = env["inv"], env["ws"], env["owner"], env["ident"]
    _, raw = inv.create(workspace_id=ws, email="new@example.com", role="editor", actor_id=owner["id"])
    out = inv.accept(raw, password=PW2, display_name="新人")
    assert out["workspace_id"] == ws and out["role"] == "editor"
    m = ident.membership(ws, out["user"]["id"])
    assert m["role"] == "editor" and m["can_export"] == 1
    # arriving through the invitation proves the mailbox, so the address is already verified
    assert out["user"]["email_verified_at"]
    # and they also get their own personal workspace
    assert len(ident.workspaces_for(out["user"]["id"])) == 2


def test_preview_does_not_consume_the_token(env):
    """A mail scanner fetching the link must not accept the invitation."""
    inv, ws, owner = env["inv"], env["ws"], env["owner"]
    _, raw = inv.create(workspace_id=ws, email="new@example.com", role="viewer", actor_id=owner["id"])
    for _ in range(3):
        assert inv.preview(raw)["state"] == "pending"
    assert inv.accept(raw, password=PW2, display_name=None)["workspace_id"] == ws


def test_a_token_can_only_be_used_once(env):
    inv, ws, owner = env["inv"], env["ws"], env["owner"]
    _, raw = inv.create(workspace_id=ws, email="new@example.com", role="viewer", actor_id=owner["id"])
    inv.accept(raw, password=PW2, display_name=None)
    with pytest.raises(ServiceError) as e:
        inv.accept(raw, password=PW2, display_name=None)
    assert e.value.code == "invite_accepted"


def test_concurrent_accepts_produce_exactly_one_membership(env):
    """Four threads race one invitation; exactly one may win.

    This asserts the property, not a particular mechanism: it also passes with a naive
    check-then-act, because Store.tx() is BEGIN IMMEDIATE behind a process write lock. Worth
    keeping regardless — it is the property that matters, and it would catch a future change
    that moved acceptance out of a write transaction.
    """
    inv, ws, owner, store = env["inv"], env["ws"], env["owner"], env["store"]
    _, raw = inv.create(workspace_id=ws, email="race@example.com", role="editor", actor_id=owner["id"])
    results: list = []
    barrier = threading.Barrier(4)

    def run():
        barrier.wait()
        try:
            results.append(("ok", inv.accept(raw, password=PW2, display_name=None)))
        except Exception as e:  # noqa: BLE001
            results.append(("err", e))

    ts = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for k, _ in results if k == "ok") == 1, [r[1] for r in results]
    assert store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=?", (ws,))["n"] == 2


def test_expired_and_revoked_invitations_are_refused(env):
    inv, ws, owner, store = env["inv"], env["ws"], env["owner"], env["store"]
    row, raw = inv.create(workspace_id=ws, email="a@example.com", role="viewer", actor_id=owner["id"])
    store.execute("UPDATE invitations SET expires_at=? WHERE id=?", (now() - 1, row["id"]))
    assert inv.preview(raw)["state"] == "expired"
    with pytest.raises(ServiceError) as e:
        inv.accept(raw, password=PW2, display_name=None)
    assert e.value.code == "invite_expired"

    row2, raw2 = inv.create(workspace_id=ws, email="b@example.com", role="viewer", actor_id=owner["id"])
    inv.revoke(row2["id"], owner["id"])
    with pytest.raises(ServiceError) as e2:
        inv.accept(raw2, password=PW2, display_name=None)
    assert e2.value.code == "invite_revoked"


def test_invitation_dies_when_the_inviter_loses_the_authority_they_used(env):
    """Checked at acceptance, not only at issue: a removed admin must not still add members."""
    ident, inv, ws, owner = env["ident"], env["inv"], env["ws"], env["owner"]
    admin = ident.create_user("ad@example.com", PW, status="active")
    ident.add_member(ws, admin["id"], "admin")
    _, raw = inv.create(workspace_id=ws, email="late@example.com", role="editor", actor_id=admin["id"])

    ident.change_role(ws, admin["id"], "viewer")          # demoted after sending
    with pytest.raises(ServiceError) as e:
        inv.accept(raw, password=PW2, display_name=None)
    assert e.value.code == "inviter_lost_permission"


def test_invitation_dies_when_the_inviter_is_suspended(env):
    ident, inv, ws = env["ident"], env["inv"], env["ws"]
    admin = ident.create_user("ad@example.com", PW, status="active")
    ident.add_member(ws, admin["id"], "admin")
    _, raw = inv.create(workspace_id=ws, email="late@example.com", role="viewer", actor_id=admin["id"])
    ident.set_status(admin["id"], "suspended")
    with pytest.raises(ServiceError) as e:
        inv.accept(raw, password=PW2, display_name=None)
    assert e.value.code == "inviter_lost_permission"


def test_an_existing_account_must_sign_in_rather_than_set_a_password(env):
    """Otherwise the invitation would be a way to set a stranger's password."""
    ident, inv, ws, owner = env["ident"], env["inv"], env["ws"], env["owner"]
    ident.create_user("known@example.com", PW, status="active")
    _, raw = inv.create(workspace_id=ws, email="known@example.com", role="viewer", actor_id=owner["id"])
    with pytest.raises(ServiceError) as e:
        inv.accept(raw, password="attacker-chosen-password", display_name=None)
    assert e.value.code == "sign_in_required"


def test_accepting_while_signed_in_as_someone_else_is_refused(env):
    """The colleague's browser case: bind to the invited address, not to whoever is logged in."""
    ident, inv, ws, owner = env["ident"], env["inv"], env["ws"], env["owner"]
    other = ident.create_user("other@example.com", PW, status="active")
    _, raw = inv.create(workspace_id=ws, email="target@example.com", role="viewer", actor_id=owner["id"])
    with pytest.raises(ServiceError) as e:
        inv.accept(raw, password=None, display_name=None, as_user_id=other["id"])
    assert e.value.code == "wrong_account" and "target@example.com" in e.value.message


def test_the_invited_account_may_accept_while_signed_in(env):
    ident, inv, ws, owner = env["ident"], env["inv"], env["ws"], env["owner"]
    u = ident.create_user("target@example.com", PW, status="active")
    _, raw = inv.create(workspace_id=ws, email="target@example.com", role="admin", actor_id=owner["id"])
    out = inv.accept(raw, password=None, display_name=None, as_user_id=u["id"])
    assert ident.membership(ws, u["id"])["role"] == "admin"
    assert out["user"]["id"] == u["id"]


def test_preview_does_not_reveal_whether_the_address_is_registered(env):
    """needs_account tells the client what to render; it must not be an existence oracle for a
    *different* address. The only address it speaks about is the one already in the link."""
    inv, ws, owner = env["inv"], env["ws"], env["owner"]
    _, raw = inv.create(workspace_id=ws, email="someone@example.com", role="viewer", actor_id=owner["id"])
    p = inv.preview(raw)
    assert p["email"] == "someone@example.com" and "needs_account" in p
    assert "password" not in str(p) and "token" not in str(p)


def test_an_unknown_token_is_simply_invalid(env):
    with pytest.raises(ServiceError) as e:
        env["inv"].preview("not-a-real-token")
    assert e.value.status == 404


# ---------------------------------------------------------------- email tokens
def test_email_token_is_single_use_and_atomic(env):
    inv, owner = env["inv"], env["owner"]
    raw = inv.issue_email_token(owner["id"], "reset", ttl_s=3600)
    assert inv.peek_email_token(raw, "reset")             # peeking does not spend it
    assert inv.peek_email_token(raw, "reset")
    assert inv.consume_email_token(raw, "reset")["user_id"] == owner["id"]
    with pytest.raises(ServiceError):
        inv.consume_email_token(raw, "reset")


def test_issuing_a_new_token_invalidates_the_previous_one(env):
    inv, owner = env["inv"], env["owner"]
    first = inv.issue_email_token(owner["id"], "reset", ttl_s=3600)
    second = inv.issue_email_token(owner["id"], "reset", ttl_s=3600)
    assert inv.peek_email_token(first, "reset") is None
    assert inv.peek_email_token(second, "reset")


def test_a_token_of_one_kind_does_not_work_as_another(env):
    inv, owner = env["inv"], env["owner"]
    raw = inv.issue_email_token(owner["id"], "verify", ttl_s=3600)
    assert inv.peek_email_token(raw, "reset") is None
    with pytest.raises(ServiceError):
        inv.consume_email_token(raw, "reset")


def test_expired_email_token_is_refused(env):
    inv, owner, store = env["inv"], env["owner"], env["store"]
    raw = inv.issue_email_token(owner["id"], "verify", ttl_s=1)
    store.execute("UPDATE email_tokens SET expires_at=? WHERE user_id=?", (now() - 1, owner["id"]))
    assert inv.peek_email_token(raw, "verify") is None


# ---------------------------------------------------------------- outbox
def test_mail_refuses_to_pretend_when_unconfigured(env, monkeypatch):
    monkeypatch.delenv("SYNWEVO_SMTP_HOST", raising=False)
    monkeypatch.delenv("SYNWEVO_MAIL_MODE", raising=False)
    m = mail_mod.Mailer(env["store"], start=False)
    assert mail_mod.mode() == "disabled"
    with pytest.raises(ServiceError) as e:
        m.enqueue("a@example.com", "s", "b", kind="invite")
    assert e.value.code == "mail_not_configured"


def test_file_mode_writes_a_clearly_labelled_file(env, monkeypatch, tmp_path):
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    m = mail_mod.Mailer(env["store"], start=False)
    m.enqueue("a@example.com", "主题", "正文 https://example.com/invite?token=x", kind="invite", ref_id="iv1")
    assert m.drain_once() == 1
    files = list((tmp_path / "mail").glob("*.eml"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "LOCAL FILE SINK" in text and "没有真正寄出" in text
    assert m.status_for("iv1")["status"] == "sent"


def test_a_failed_send_is_recorded_and_retried_not_reported_as_sent(env, monkeypatch):
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    m = mail_mod.Mailer(env["store"], start=False)
    monkeypatch.setattr(m, "_deliver", lambda row: (_ for _ in ()).throw(OSError("连接被拒绝")))
    m.enqueue("a@example.com", "s", "b", kind="invite", ref_id="iv2")
    assert m.drain_once() == 0
    st = m.status_for("iv2")
    assert st["status"] == "queued" and st["attempts"] == 1 and "连接被拒绝" in st["last_error"]
    # still due only in the future, so a tight loop does not hammer a dead server
    row = env["store"].one("SELECT next_attempt_at FROM mail_outbox WHERE ref_id='iv2'")
    assert row["next_attempt_at"] > now()


def test_links_use_the_configured_public_url_not_a_request_host(monkeypatch):
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "https://docs.example.org")
    subject, body = mail_mod.invitation_mail(workspace="W", inviter="I", role_label="编辑成员",
                                             token="tok123", expires_h=168)
    assert "https://docs.example.org/invite?token=tok123" in body
    assert "确认" in body, "邮件应说明打开链接后仍需确认"


# ---------------------------------------------------------------- over HTTP, end to end
@pytest.fixture()
def http(tmp_path, monkeypatch):
    """A real server with the file mail sink, so the link is read out of a real message."""
    import importlib
    from fastapi.testclient import TestClient
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
    owner = ident.create_user("owner@example.com", PW, status="active")
    c = TestClient(api)
    c.post("/api/auth/login", json={"email": "owner@example.com", "password": PW})
    ws = c.get("/api/auth/me").json()["workspaces"][0]["id"]
    return {"api": api, "c": c, "store": store, "ident": ident, "owner": owner, "ws": ws,
            "maildir": tmp_path / "mail", "mailer": api.state.mailer}


def _csrf(c):
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}


def _token_from_mail(h) -> str:
    """Pull the link out of the delivered message the way a recipient's client would.

    Parsed rather than regexed over the raw file: the Chinese body is base64-encoded on the
    wire, so matching the raw .eml would silently find nothing — and a test that cannot see the
    link cannot tell a missing link from an encoded one.
    """
    import email
    import re
    h["mailer"].drain_once()
    files = sorted(h["maildir"].glob("*.eml"))
    assert files, "没有任何邮件被写出"
    msg = email.message_from_string(files[-1].read_text(encoding="utf-8"))
    body = msg.get_payload(decode=True).decode("utf-8")
    assert "确认" in body, "邮件必须说明打开链接后仍需确认"
    m = re.search(r"https://docs\.example\.org/invite\?token=([A-Za-z0-9_-]+)", body)
    assert m, body[:400]
    return m.group(1)


def test_full_invitation_journey_over_http(http):
    from fastapi.testclient import TestClient
    c, ws = http["c"], http["ws"]
    r = c.post(f"/api/auth/workspaces/{ws}/invitations",
               json={"email": "new@example.com", "role": "editor"}, headers=_csrf(c))
    assert r.status_code == 200 and r.json()["mail"] == "queued"

    token = _token_from_mail(http)

    # A stranger's browser: the landing page renders without a session and changes nothing.
    guest = TestClient(http["api"])
    pv = guest.get(f"/api/auth/invitations/preview?token={token}").json()
    assert pv["state"] == "pending" and pv["needs_account"] is True and pv["role"] == "editor"
    assert guest.get(f"/api/auth/invitations/preview?token={token}").json()["state"] == "pending"

    acc = guest.post("/api/auth/invitations/accept",
                     json={"token": token, "password": PW2, "display_name": "新人"})
    assert acc.status_code == 200
    me = guest.get("/api/auth/me").json()          # the accept signed them in
    assert me["email"] == "new@example.com"
    assert ws in [w["id"] for w in me["workspaces"]]
    assert next(w for w in me["workspaces"] if w["id"] == ws)["role"] == "editor"

    # the invitation is spent
    assert guest.get(f"/api/auth/invitations/preview?token={token}").json()["state"] == "accepted"


def test_invitation_list_shows_real_mail_state(http):
    c, ws = http["c"], http["ws"]
    c.post(f"/api/auth/workspaces/{ws}/invitations",
           json={"email": "new@example.com", "role": "viewer"}, headers=_csrf(c))
    before = c.get(f"/api/auth/workspaces/{ws}/invitations").json()["items"][0]
    assert before["mail"] == "queued", "未送出前不能显示成已发送"
    http["mailer"].drain_once()
    after = c.get(f"/api/auth/workspaces/{ws}/invitations").json()["items"][0]
    assert after["mail"] == "sent" and after["state"] == "pending"


def test_an_invitation_is_withdrawn_when_mail_cannot_be_queued(http, monkeypatch):
    """No delivery means no invitation — a link nobody receives is not an invitation."""
    c, ws, store = http["c"], http["ws"], http["store"]
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "disabled")
    r = c.post(f"/api/auth/workspaces/{ws}/invitations",
               json={"email": "nope@example.com", "role": "viewer"}, headers=_csrf(c))
    assert r.status_code == 503 and r.json()["error"] == "mail_not_configured"
    row = store.one("SELECT revoked_at FROM invitations WHERE email_key='nope@example.com'")
    assert row and row["revoked_at"], "无法送达时邀请应当撤回，而不是留一条没人收得到的链接"


def test_accepting_while_signed_in_as_the_wrong_account_is_refused_over_http(http):
    from fastapi.testclient import TestClient
    c, ws, ident = http["c"], http["ws"], http["ident"]
    c.post(f"/api/auth/workspaces/{ws}/invitations",
           json={"email": "target@example.com", "role": "viewer"}, headers=_csrf(c))
    token = _token_from_mail(http)

    ident.create_user("bystander@example.com", PW, status="active")
    other = TestClient(http["api"])
    other.post("/api/auth/login", json={"email": "bystander@example.com", "password": PW})
    r = other.post("/api/auth/invitations/accept", json={"token": token}, headers=_csrf(other))
    assert r.status_code == 409 and r.json()["error"] == "wrong_account"
    assert "target@example.com" in r.json()["message"]
