"""The account service's withdrawals are journalled inside the transaction of their change.

Every route and CLI command below withdraws access and writes its security-journal line through
`withdrawals.record` (or `journal._intent` directly, for the strict ones) inside the write
transaction that makes the change: a fresh `attempt_id`, and a receipt in `meta` that commits with
the change. Per writer, three things are held to:

* a backup taken between the line and its COMMIT — a database copy from a connection of its own,
  right after the append — holds neither the change nor the receipt, and a restore gives it the
  withdrawal whatever the backup's time; a copy taken after the COMMIT holds the receipt and is
  left as it is;
* a process that died right after the COMMIT — where the line used to be appended, for the writers
  that journalled after it — leaves the line in the journal already: the journal as it stands at
  the instant of the COMMIT restores the withdrawal into a backup from before it;
* a transaction that fails after its line rolls back and voids the line, so no restore applies a
  withdrawal that never happened; a journal append that fails is refused by a strict writer
  (nothing changes, the attempt is voided) and reported by the others (`journaled: false`: the
  change stands with its receipt).

Isolated data directory under tmp_path, file mail; accounts, platform roles, sessions, invitations,
passkeys, external identities, clients and tokens are FIXTURE data. Every monkeypatch is put back
with setattr(original).
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from synwevo_core import journal
from synwevo_core.apps import bootstrap_docs_app
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store, new_id, now

PW = "isolated-withdrawal-fixture-passphrase-123"
NEW_PW = "another-isolated-fixture-passphrase-456"
REASON = "fixture reason for the journal writer test"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    data = tmp_path / "fixture-data"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(data))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "fixture-mail"))
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "http://testserver")
    from synwevo_identity.app import create_app
    app = create_app(str(data), start_jobs=False)
    ident = app.state.identity
    store = ident.store
    bootstrap_docs_app(store, ident)
    a = ident.create_user("owner@example.test", PW, display_name="Fixture owner", status="active",
                          with_personal_workspace=False)
    b = ident.create_user("editor@example.test", PW, display_name="Fixture editor", status="active",
                          with_personal_workspace=False)
    for u in (a, b):
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
    wid = ident.create_workspace("Fixture workspace", owner_id=a["id"])["id"]
    ident.add_member(wid, b["id"], "editor")
    w = SimpleNamespace(app=app, ident=ident, store=store, data=data, tmp=tmp_path, a=a, b=b, wid=wid,
                        mp=monkeypatch, copies=[])
    w.c = _login(w, a["email"])
    yield w
    for copy in w.copies:                  # this thread's connection to each copy
        c = getattr(copy._local, "c", None)
        if c is not None:
            c.close()
            copy._local.c = None


def _login(w, email, *, raise_server_exceptions=True):
    c = TestClient(w.app, raise_server_exceptions=raise_server_exceptions)
    r = c.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return c


def _send(c, method, path, body=None):
    kwargs = {"headers": {CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""}}
    if body is not None:
        kwargs["json"] = body
    return c.request(method, path, **kwargs)


def _reauth(c, scope):
    r = _send(c, "POST", "/api/auth/reauth", {"password": PW, "scope": scope})
    assert r.status_code == 200, r.text


def _session(w, uid):
    """FIXTURE: another signed-in device of this account."""
    return Sessions(w.store).create(uid, workspace_id=None, amr="password")[1]["id"]


def _invitation(w, email="invitee@example.test"):
    """FIXTURE: a pending invitation, sent long enough ago for a re-invitation to supersede it."""
    t = now() - 120
    iid = new_id("iv")
    w.store.execute("INSERT INTO invitations (id,email_key,workspace_id,role,can_export,token_hash,invited_by,"
                    "created_at,expires_at) VALUES (?,?,?,'editor',1,?,?,?,?)",
                    (iid, email, w.wid, new_id("fixture-hash"), w.a["id"], t, t + 86400))
    return iid


def _copy(store, dest: Path) -> Path:
    """FIXTURE: the database as a backup would copy it — SQLite's own backup, from a connection of its
    own, which sees only what is committed."""
    dest.mkdir()
    src, dst = sqlite3.connect(store.db_path), sqlite3.connect(dest / Path(store.db_path).name)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return dest


def _open(w, path: Path) -> Store:
    s = Store(path)
    w.copies.append(s)
    return s


def _receipt(store, attempt):
    return bool(store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + attempt,)))


def _void_row(store, attempt):
    row = store.one("SELECT value FROM meta WHERE key=?", ("journal_void:" + attempt,))
    return row and row["value"]


def _line(w, attempt):
    (line,) = [e for e in journal.read(w.data) if e.get("attempt_id") == attempt]
    return line


def _voids(w):
    return [e for e in journal.read(w.data) if e["kind"] == journal.VOID_KIND]


@contextmanager
def _watching(w, kind):
    """FIXTURE, around the writer's first line of `kind`: `mid`, the database copied from a connection
    of its own right after the append — a backup taken between the line and its COMMIT — and
    `crashed`, the journal as it stands at the instant that transaction has committed: all a process
    that died right after the COMMIT leaves behind."""
    original = journal.record
    seen = SimpleNamespace(attempt=None, in_transaction=None, mid=None, crashed=None)

    def watching(store, k, **fields):
        original(store, k, **fields)
        if k != kind or seen.attempt is not None:
            return
        seen.attempt = fields.get("attempt_id")
        with store.conn() as c:
            seen.in_transaction = c.in_transaction
        seen.mid = _copy(store, w.tmp / "mid")

        def at_commit():
            crashed = w.tmp / "crashed"
            crashed.mkdir()
            shutil.copyfile(journal.path(store.root), journal.path(crashed))
            seen.crashed = crashed
        store.after_commit(at_commit)
    w.mp.setattr(journal, "record", watching)
    try:
        yield seen
    finally:
        w.mp.setattr(journal, "record", original)


@contextmanager
def _failing_after_the_line(w, kind):
    """FIXTURE: the writer's transaction fails right after its line of `kind` reached the journal
    (the void's own line goes through)."""
    original, failed = journal.record, []

    def failing(store, k, **fields):
        original(store, k, **fields)
        if k == kind and not failed:
            failed.append(fields.get("attempt_id"))
            raise RuntimeError("fixture: the transaction fails after its journal line")
    w.mp.setattr(journal, "record", failing)
    try:
        yield failed
    finally:
        w.mp.setattr(journal, "record", original)


@contextmanager
def _directory_fsync_fails_once(w):
    """FIXTURE: the append went through, its directory fsync did not."""
    original, calls = journal._fsync_dir, []

    def fails_once(directory):
        calls.append(directory)
        if len(calls) == 1:
            raise OSError("fixture: directory fsync failed after the append")
        return original(directory)
    w.mp.setattr(journal, "_fsync_dir", fails_once)
    try:
        yield
    finally:
        w.mp.setattr(journal, "_fsync_dir", original)


# ---------------------------------------------------------------- the writers
# name -> (kind, setup(w) -> ctx, call(w, ctx, client) -> response | exit code, withdrawn(store, w, ctx),
#          mode). `mode` "stands": a failed append is reported and the change commits with its
# receipt; "strict": a failed append refuses the change with 503. `client` is None for the CLI.
def _role(store, w, ctx):
    return store.one("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?", (w.wid, ctx["uid"]))["role"]


def _not_member(store, w, ctx):
    return not store.one("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?", (w.wid, w.b["id"]))


def _entitlement_revoked(store, w, ctx):
    row = store.one("SELECT revoked_at FROM app_entitlements WHERE workspace_id=? AND app_id='docweave' AND user_id=?",
                    (w.wid, w.b["id"]))
    return bool(row and row["revoked_at"])


def _invitation_revoked(store, w, ctx):
    return store.one("SELECT revoked_at FROM invitations WHERE id=?", (ctx["iid"],))["revoked_at"] is not None


def _session_revoked(store, w, ctx):
    return store.one("SELECT revoked_at FROM sessions WHERE id=?", (ctx["sid"],))["revoked_at"] is not None


def _left_active(store, w, ctx):
    return store.one("SELECT status FROM users WHERE id=?", (w.b["id"],))["status"] != "active"


def _passkey_gone(store, w, ctx):
    return not store.one("SELECT 1 FROM passkeys WHERE id=? AND deleted_at IS NULL", (ctx["pid"],))


def _change_role(w, ctx, c):
    return _send(c, "PATCH", f"/api/auth/workspaces/{w.wid}/members/{w.b['id']}", {"role": "viewer"})


def _remove(w, ctx, c):
    _reauth(c, "admin")
    return _send(c, "POST", f"/api/auth/workspaces/{w.wid}/members/{w.b['id']}/remove", {"reason": REASON})


def _revoke_entitlement(w, ctx, c):
    return _send(c, "POST", f"/api/auth/workspaces/{w.wid}/members/{w.b['id']}/entitlements",
                 {"app_id": "docweave", "grant": False})


def _transfer(w, ctx, c):
    _reauth(c, "admin")
    return _send(c, "POST", f"/api/auth/workspaces/{w.wid}/ownership/transfer",
                 {"to_user_id": w.b["id"], "demote_self_to": "editor", "reason": REASON})


def _leave(w, ctx, c):
    return _send(ctx["editor"], "POST", f"/api/auth/workspaces/{w.wid}/leave", {})


def _reinvite(w, ctx, c):
    r = _send(c, "POST", f"/api/auth/workspaces/{w.wid}/invitations/batch",
              {"invitations": [{"email": "invitee@example.test", "role": "editor"}]})
    if r.status_code == 200:
        assert r.json()["results"][0]["ok"], r.text
    return r


def _bulk_revoke(w, ctx, c):
    r = _send(c, "POST", f"/api/auth/workspaces/{w.wid}/invitations/bulk", {"action": "revoke", "ids": [ctx["iid"]]})
    if r.status_code == 200:
        assert r.json()["results"][0]["ok"], r.text
    return r


def _revoke_invite(w, ctx, c):
    return _send(c, "DELETE", f"/api/auth/invitations/{ctx['iid']}")


def _revoke_session(w, ctx, c):
    return _send(c, "DELETE", f"/api/auth/sessions/{ctx['sid']}")


def _revoke_others(w, ctx, c):
    return _send(c, "POST", "/api/auth/sessions/revoke-others", {})


def _reset_token(w):
    from synwevo_identity.invites import Invites
    sid = _session(w, w.b["id"])
    raw = Invites(w.store, w.ident).issue_email_token(w.b["id"], "reset", ttl_s=3600)
    return {"sid": sid, "token": raw}


def _reset(w, ctx, c):
    anonymous = TestClient(w.app, raise_server_exceptions=getattr(c, "_raise", True))
    return _send(anonymous, "POST", "/api/auth/reset", {"token": ctx["token"], "password": NEW_PW})


def _pending_user(w):
    from synwevo_identity.invites import Invites
    u = w.ident.create_user("pending@example.test", PW, status="pending", with_personal_workspace=False)
    sid = _session(w, u["id"])
    raw = Invites(w.store, w.ident).issue_email_token(u["id"], "verify", ttl_s=3600, new_email=u["email"])
    return {"sid": sid, "token": raw}


def _verify(w, ctx, c):
    anonymous = TestClient(w.app, raise_server_exceptions=getattr(c, "_raise", True))
    return _send(anonymous, "POST", "/api/auth/verify-email", {"token": ctx["token"], "new_password": NEW_PW})


def _passkey(w):
    w.store.execute("INSERT INTO passkeys (id,user_id,credential_id,public_key,rp_id,created_at) "
                    "VALUES ('pk_fixture',?,'fixture-credential','fixture-public-key','localhost',?)", (w.a["id"], now()))
    return {"pid": "pk_fixture"}


def _declinable(w):
    """FIXTURE: an invitation to an account of its own (active, the invited address)."""
    from synwevo_identity.invites import Invites
    u = w.ident.create_user("declining@example.test", PW, display_name="Fixture invitee", status="active",
                            with_personal_workspace=False)
    w.store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
    inv, token = Invites(w.store, w.ident).create(workspace_id=w.wid, email=u["email"], role="viewer",
                                                  actor_id=w.a["id"])
    return {"iid": inv["id"], "token": token, "email": u["email"]}


def _decline(w, ctx, c):
    invitee = _login(w, ctx["email"], raise_server_exceptions=getattr(c, "_raise", True))
    return _send(invitee, "POST", "/api/auth/invitations/decline", {"token": ctx["token"]})


def _remove_passkey(w, ctx, c):
    _reauth(c, "mfa")
    return _send(c, "DELETE", f"/api/auth/passkeys/{ctx['pid']}")


def _administrator(w):
    """FIXTURE: the owner is made a platform super administrator (platform_fixture)."""
    from .platform_fixture import make_platform_admin
    make_platform_admin(w.store, w.a)


def _admin_client(w, c):
    admin = _login(w, w.a["email"], raise_server_exceptions=getattr(c, "_raise", True))
    _reauth(admin, "admin")
    return admin


def _editor_passkey(w):
    """FIXTURE: an administrator, and a passkey of the editor's (who also has a password)."""
    _administrator(w)
    w.store.execute("INSERT INTO passkeys (id,user_id,credential_id,public_key,rp_id,created_at) "
                    "VALUES ('pk_editor',?,'fixture-editor-credential','fixture-public-key','localhost',?)",
                    (w.b["id"], now()))
    return {"pid": "pk_editor"}


def _admin_remove_passkey(w, ctx, c):
    return _send(_admin_client(w, c), "POST", f"/api/admin/users/{w.b['id']}/passkeys/{ctx['pid']}/remove",
                 {"reason": REASON})


def _editor_identity(w, *, administrator):
    """FIXTURE: an external identity of the editor's (no provider is configured in this instance)."""
    if administrator:
        _administrator(w)
    w.store.execute("INSERT INTO external_identities (id,user_id,provider,subject,issuer,email,email_verified,created_at) "
                    "VALUES ('xi_editor',?,'fixture-provider','fixture-subject','https://idp.example.test',?,1,?)",
                    (w.b["id"], w.b["email"], now()))
    return {"iid": "xi_editor"}


def _unlinked(store, w, ctx):
    return not store.one("SELECT 1 FROM external_identities WHERE id=?", (ctx["iid"],))


def _admin_unlink(w, ctx, c):
    return _send(_admin_client(w, c), "POST", f"/api/admin/users/{w.b['id']}/external-identities/{ctx['iid']}/unlink",
                 {"reason": REASON})


def _unlink_own(w, ctx, c):
    editor = _login(w, w.b["email"], raise_server_exceptions=getattr(c, "_raise", True))
    _reauth(editor, "mfa")
    return _send(editor, "DELETE", f"/api/auth/external/identities/{ctx['iid']}")


def _change_password(w, ctx, c):
    return _send(c, "POST", "/api/auth/password", {"current_password": PW, "new_password": NEW_PW})


def _email_change(w):
    editor = _login(w, w.b["email"])
    r = _send(editor, "POST", "/api/auth/email-change", {"email": "editor-new@example.test", "password": PW})
    assert r.status_code == 200, r.text
    body = w.store.one("SELECT body FROM mail_outbox WHERE kind='email_change' ORDER BY created_at DESC LIMIT 1")["body"]
    return {"sid": _session(w, w.b["id"]), "token": re.search(r"[#?]token=([A-Za-z0-9_-]+)", body)[1],
            "editor": editor}


def _confirm_email(w, ctx, c):
    editor = ctx["editor"]
    if not getattr(c, "_raise", True):
        editor = _login(w, w.b["email"], raise_server_exceptions=False)
    return _send(editor, "POST", "/api/auth/email-change/confirm", {"token": ctx["token"], "password": PW})


def _request_deletion(w, ctx, c):
    editor = _login(w, w.b["email"], raise_server_exceptions=getattr(c, "_raise", True))
    return _send(editor, "POST", "/api/auth/deletion", {"password": PW})


def _cli(w, *argv):
    from synwevo_identity import cli as cli_mod
    original = sys.argv
    w.mp.setattr(sys, "argv", ["synwevo_identity.cli", "--data", str(w.data), *argv])
    try:
        return cli_mod.main()
    finally:
        w.mp.setattr(sys, "argv", original)


def _service_client(w):
    """FIXTURE: a confidential service client registered through the CLI, with one live token."""
    assert _cli(w, "client", "register", "--service", "--client-id", "fixture-service",
                "--workspace", w.wid, "--api-scopes", "docweave.read") == 0
    w.store.execute("INSERT INTO oauth_tokens (id,client_id,user_id,scope,workspace_id,issued_at) "
                    "VALUES ('fixture-token','fixture-service',NULL,'docweave.read',?,?)", (w.wid, now()))
    return {"secret_hash": w.store.one("SELECT secret_hash FROM oauth_clients WHERE client_id='fixture-service'")["secret_hash"]}


def _token_revoked(store, w, ctx):
    return store.one("SELECT revoked_at FROM oauth_tokens WHERE id='fixture-token'")["revoked_at"] is not None


WRITERS = {
    "members.change_role": ("role_changed", lambda w: {"uid": w.b["id"]}, _change_role,
                            lambda s, w, ctx: _role(s, w, ctx) == "viewer", "stands"),
    "members.remove": ("member_removed", lambda w: {}, _remove, _not_member, "stands"),
    "members.entitlement": ("entitlement_revoked", lambda w: {}, _revoke_entitlement, _entitlement_revoked, "stands"),
    "members.transfer": ("role_changed", lambda w: {"uid": w.a["id"]}, _transfer,
                         lambda s, w, ctx: _role(s, w, ctx) == "editor", "stands"),
    "members.leave": ("member_removed", lambda w: {"editor": _login(w, w.b["email"])}, _leave, _not_member, "stands"),
    "members.issue_invitation": ("invitation_revoked", lambda w: {"iid": _invitation(w)}, _reinvite,
                                 _invitation_revoked, "stands"),
    "members.invitations_bulk": ("invitation_revoked", lambda w: {"iid": _invitation(w)}, _bulk_revoke,
                                 _invitation_revoked, "stands"),
    "auth.revoke_invite": ("invitation_revoked", lambda w: {"iid": _invitation(w)}, _revoke_invite,
                           _invitation_revoked, "stands"),
    "invites.decline": ("invitation_revoked", _declinable, _decline, _invitation_revoked, "strict"),
    "auth.revoke_session": ("sessions_revoked", lambda w: {"sid": _session(w, w.a["id"])}, _revoke_session,
                            _session_revoked, "stands"),
    "auth.revoke_others": ("sessions_revoked", lambda w: {"sid": _session(w, w.a["id"])}, _revoke_others,
                           _session_revoked, "stands"),
    "auth.reset": ("password_changed", _reset_token, _reset, _session_revoked, "stands"),
    "auth.verify_email": ("password_changed", _pending_user, _verify, _session_revoked, "stands"),
    "auth.passkey_remove": ("passkey_removed", _passkey, _remove_passkey, _passkey_gone, "strict"),
    "admin.passkey_remove": ("passkey_removed", _editor_passkey, _admin_remove_passkey, _passkey_gone, "strict"),
    "admin.external_unlink": ("external_identity_unlinked", lambda w: _editor_identity(w, administrator=True),
                              _admin_unlink, _unlinked, "strict"),
    "external.unlink": ("external_identity_unlinked", lambda w: _editor_identity(w, administrator=False),
                        _unlink_own, _unlinked, "strict"),
    "account.password": ("password_changed", lambda w: {"sid": _session(w, w.a["id"])}, _change_password,
                         _session_revoked, "stands"),
    "account.email_change": ("sessions_revoked", _email_change, _confirm_email, _session_revoked, "stands"),
    "account.deletion": ("user_suspended", lambda w: {}, _request_deletion, _left_active, "stands"),
    "cli.client_disable": ("client_disabled", _service_client,
                           lambda w, ctx, c: _cli(w, "client", "disable", "fixture-service"),
                           lambda s, w, ctx: s.one("SELECT disabled_at FROM oauth_clients WHERE "
                                                   "client_id='fixture-service'")["disabled_at"] is not None, "stands"),
    "cli.revoke_tokens": ("tokens_revoked", _service_client,
                          lambda w, ctx, c: _cli(w, "client", "revoke-tokens", "fixture-service"), _token_revoked, "stands"),
    "cli.rotate_secret": ("client_secret_rotated", _service_client,
                          lambda w, ctx, c: _cli(w, "client", "rotate-secret", "fixture-service"),
                          lambda s, w, ctx: s.one("SELECT secret_hash FROM oauth_clients WHERE "
                                                  "client_id='fixture-service'")["secret_hash"] != ctx["secret_hash"],
                          "stands"),
    "cli.app_revoke": ("entitlement_revoked", lambda w: {},
                       lambda w, ctx, c: _cli(w, "app", "revoke", "docweave", "--workspace", w.wid,
                                              "--user", w.b["email"]),
                       _entitlement_revoked, "stands"),
}


def _ok(out):
    if isinstance(out, int):
        return out == 0
    return out.status_code == 200


def _client(w, name, *, raise_server_exceptions=True):
    if name.startswith("cli."):
        return None
    c = w.c if raise_server_exceptions else _login(w, w.a["email"], raise_server_exceptions=False)
    c._raise = raise_server_exceptions
    return c


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_a_backup_inside_the_window_and_a_crash_after_the_commit_keep_the_withdrawal(world, name):
    """The line is appended while the change's transaction is still open: a backup copied right after
    it holds neither the change nor the receipt and is given the withdrawal on restore; the journal
    as it stands at the COMMIT already holds the line, so a process that died there — before the
    point a class-D writer used to append it — loses nothing; a copy after the COMMIT is left alone."""
    w = world
    kind, setup, call, withdrawn, _ = WRITERS[name]
    ctx = setup(w)
    older = _open(w, _copy(w.store, w.tmp / "older"))
    assert not withdrawn(older, w, ctx)
    client = _client(w, name)
    with _watching(w, kind) as seen:
        out = call(w, ctx, client)
    assert _ok(out), getattr(out, "text", out)
    assert seen.in_transaction is True, "the line is appended inside the change's transaction"
    line = _line(w, seen.attempt)
    assert withdrawn(w.store, w, ctx) and _receipt(w.store, seen.attempt)
    entries = journal.read(w.data, since=now() + 3600)
    assert line in entries
    # a backup taken between the line and its COMMIT
    mid = _open(w, seen.mid)
    assert not withdrawn(mid, w, ctx) and not _receipt(mid, seen.attempt)
    assert kind in journal.apply(mid, entries)
    assert withdrawn(mid, w, ctx) and _receipt(mid, seen.attempt)
    # the process died right after the COMMIT: only what the journal held then
    assert seen.crashed is not None, "the transaction committed"
    at_commit = journal.read(seen.crashed, since=now() + 3600)
    assert line in at_commit
    assert kind in journal.apply(older, at_commit)
    assert withdrawn(older, w, ctx)
    # a backup taken after the COMMIT holds the receipt: the line is skipped there
    after = _open(w, _copy(w.store, w.tmp / "after"))
    assert journal.apply(after, entries).get(kind) == 0


@pytest.mark.parametrize("name", sorted(n for n in WRITERS if not n.startswith("cli.")))
def test_a_writer_whose_transaction_fails_after_its_line_is_voided(world, name):
    """The change rolls back after its line reached the journal: no change, no receipt, a void line
    and row naming the attempt, and a restore from before it skips the line."""
    w = world
    kind, setup, call, withdrawn, _ = WRITERS[name]
    ctx = setup(w)
    older = _open(w, _copy(w.store, w.tmp / "older"))
    client = _client(w, name, raise_server_exceptions=False)
    with _failing_after_the_line(w, kind) as failed:
        out = call(w, ctx, client)
    assert failed, "the writer reached its journal line"
    assert out.status_code == 500, out.text
    (attempt,) = failed
    assert not withdrawn(w.store, w, ctx) and not _receipt(w.store, attempt)
    assert _void_row(w.store, attempt) == kind
    assert [(v["attempt_id"], v["voided_kind"]) for v in _voids(w)] == [(attempt, kind)]
    counts = journal.apply(older, journal.read(w.data, since=now() + 3600))
    assert counts.get(journal.VOID_KIND) == 1 and not counts.get(kind)
    assert not withdrawn(older, w, ctx)


@pytest.mark.parametrize("name", sorted(n for n in WRITERS if n.startswith("cli.")))
def test_a_cli_command_whose_transaction_fails_after_its_line_is_voided(world, name):
    w = world
    kind, setup, call, withdrawn, _ = WRITERS[name]
    ctx = setup(w)
    older = _open(w, _copy(w.store, w.tmp / "older"))
    with _failing_after_the_line(w, kind) as failed, pytest.raises(RuntimeError, match="fixture"):
        call(w, ctx, None)
    (attempt,) = failed
    assert not withdrawn(w.store, w, ctx) and not _receipt(w.store, attempt)
    assert _void_row(w.store, attempt) == kind
    assert journal.apply(older, journal.read(w.data, since=now() + 3600)).get(journal.VOID_KIND) == 1
    assert not withdrawn(older, w, ctx)


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_a_writer_whose_journal_append_fails(world, name):
    """The directory fsync after the append fails once. A strict writer refuses (503): nothing
    changes and the attempt is voided. The others keep the withdrawal and say `journaled: false`
    where they answer it: the change commits with its receipt, no void, and the line that did reach
    the file still restores the withdrawal into a backup from before it."""
    w = world
    kind, setup, call, withdrawn, mode = WRITERS[name]
    ctx = setup(w)
    older = _open(w, _copy(w.store, w.tmp / "older"))
    client = _client(w, name)
    with _directory_fsync_fails_once(w):
        out = call(w, ctx, client)
    (line,) = [e for e in journal.read(w.data) if e["kind"] == kind and e.get("attempt_id")][:1]
    entries = journal.read(w.data, since=now() + 3600)
    if mode == "strict":
        assert out.status_code == 503 and out.json()["error"] == "journal_unavailable", out.text
        assert not withdrawn(w.store, w, ctx) and not _receipt(w.store, line["attempt_id"])
        assert [(v["attempt_id"], v["voided_kind"]) for v in _voids(w)] == [(line["attempt_id"], kind)]
        assert journal.apply(older, entries) == {journal.VOID_KIND: 1}
        assert not withdrawn(older, w, ctx)
        return
    assert _ok(out), getattr(out, "text", out)
    if not isinstance(out, int) and "journaled" in out.json():
        assert out.json()["journaled"] is False
    assert withdrawn(w.store, w, ctx) and _receipt(w.store, line["attempt_id"])
    assert not _voids(w) and not _void_row(w.store, line["attempt_id"])
    assert kind in journal.apply(older, entries)
    assert withdrawn(older, w, ctx)


def test_a_mailed_reset_hashes_the_new_password_before_its_transaction(world):
    """The policy check and the hash (and the wait for a hashing slot) run before the reset's write
    transaction: the writer lock every other write waits on is not held while a password hashes."""
    from synwevo_core import passwords
    w = world
    ctx = _reset_token(w)
    original, seen = passwords.hash_password, []

    def watching(password):
        with w.store.conn() as c:            # the handler's own connection, in the handler's thread
            seen.append(c.in_transaction)
        return original(password)
    w.mp.setattr(passwords, "hash_password", watching)
    try:
        out = _reset(w, ctx, w.c)
    finally:
        w.mp.setattr(passwords, "hash_password", original)
    assert out.status_code == 200, out.text
    assert seen == [False]
    assert _session_revoked(w.store, w, ctx)


def test_a_weak_reset_password_is_refused_before_anything_changes(world):
    w = world
    ctx = _reset_token(w)
    anonymous = TestClient(w.app)
    r = _send(anonymous, "POST", "/api/auth/reset", {"token": ctx["token"], "password": "short"})
    assert r.status_code == 400 and r.json()["error"] == "weak_password" and r.json()["detail"]["link_spent"], r.text
    assert not _session_revoked(w.store, w, ctx)
    assert not [e for e in journal.read(w.data) if e["kind"] == "password_changed"]


# ---------------------------------------------------------------- the deletion sweep (profile.py)
def _due(w):
    """FIXTURE: the editor's deletion request, past its grace period."""
    w.store.execute("UPDATE users SET status='pending_delete', deletion_requested_at=?, deletion_due_at=? WHERE id=?",
                    (now() - 10, now() - 1, w.b["id"]))


def _deleted(store, w):
    return store.one("SELECT status FROM users WHERE id=?", (w.b["id"],))["status"] == "deleted"


def _markers(store):
    return store.one("SELECT COUNT(*) AS n FROM meta WHERE key GLOB 'account-deletion-journal:*'")["n"]


def test_a_finalized_deletion_is_journalled_inside_its_transaction_and_owes_nothing(world):
    """`user_deleted` is written ahead in the deletion's own transaction: a backup copied right after
    the line has neither the deletion nor the receipt and is given it on restore; the journal at the
    COMMIT holds the line, where it used to be appended only after it; no retry marker is left."""
    from synwevo_identity.profile import finalize_deletions
    w = world
    _due(w)
    older = _open(w, _copy(w.store, w.tmp / "older"))
    with _watching(w, "user_deleted") as seen:
        assert finalize_deletions(w.store) == 1
    assert seen.in_transaction is True and _deleted(w.store, w) and _receipt(w.store, seen.attempt)
    assert _markers(w.store) == 0, "the line made it: no withdrawal record is owed"
    line = _line(w, seen.attempt)
    mid = _open(w, seen.mid)
    assert not _deleted(mid, w) and not _receipt(mid, seen.attempt)
    assert "user_deleted" in journal.apply(mid, journal.read(w.data, since=now() + 3600))
    assert _deleted(mid, w)
    at_commit = journal.read(seen.crashed, since=now() + 3600)
    assert line in at_commit
    journal.apply(older, at_commit)
    assert _deleted(older, w)


def test_a_deletion_whose_line_cannot_be_written_stands_with_its_retry_marker(world):
    """The deletion stands when its line fails: it commits with its receipt and a retry marker, and
    the records still owed are written later, each with the marker's removal in one transaction."""
    from synwevo_identity.profile import finalize_deletions, record_deletion_withdrawals
    w = world
    _due(w)
    older = _open(w, _copy(w.store, w.tmp / "older"))
    with _directory_fsync_fails_once(w):
        assert finalize_deletions(w.store, withdrawals=False) == 1
    (first,) = [e for e in journal.read(w.data) if e["kind"] == "user_deleted"]
    assert _deleted(w.store, w) and _receipt(w.store, first["attempt_id"]) and not _voids(w)
    assert _markers(w.store) == 1, "the withdrawal record is still owed"
    record_deletion_withdrawals(w.store)
    assert _markers(w.store) == 0
    first_again, retry = [e for e in journal.read(w.data) if e["kind"] == "user_deleted"]
    assert first_again == first and _receipt(w.store, retry["attempt_id"])
    journal.apply(older, journal.read(w.data, since=now() + 3600))
    assert _deleted(older, w)


def test_the_sweep_commits_each_deletion_on_its_own(world):
    """One transaction per due account: the second account's failure after its line rolls back that
    deletion only (voided); the first stays deleted with its receipt, and the next sweep finalizes
    the second. A deletion cancelled after the sweep listed it is not finalized."""
    from synwevo_identity.profile import finalize_deletions
    w = world
    _due(w)
    c = w.ident.create_user("third@example.test", PW, display_name="Fixture third", status="active",
                            with_personal_workspace=False)
    w.store.execute("UPDATE users SET status='pending_delete', deletion_requested_at=?, deletion_due_at=? WHERE id=?",
                    (now() - 10, now() - 1, c["id"]))
    original, lines = journal.record, []

    def failing_on_the_second(store, k, **fields):
        original(store, k, **fields)
        if k == "user_deleted":
            lines.append(fields)
            if len(lines) == 2:
                raise RuntimeError("fixture: the second deletion fails after its line")
    w.mp.setattr(journal, "record", failing_on_the_second)
    try:
        with pytest.raises(RuntimeError, match="fixture"):
            finalize_deletions(w.store)
    finally:
        w.mp.setattr(journal, "record", original)
    first, second = lines
    status = {u: w.store.one("SELECT status FROM users WHERE id=?", (u,))["status"] for u in (w.b["id"], c["id"])}
    assert status[first["user_id"]] == "deleted" and _receipt(w.store, first["attempt_id"])
    assert status[second["user_id"]] == "pending_delete" and _void_row(w.store, second["attempt_id"]) == "user_deleted"
    assert finalize_deletions(w.store) == 1
    assert w.store.one("SELECT status FROM users WHERE id=?", (second["user_id"],))["status"] == "deleted"
    # listed as due, cancelled before its own transaction: kept
    d = w.ident.create_user("fourth@example.test", PW, status="active", with_personal_workspace=False)
    w.store.execute("UPDATE users SET status='pending_delete', deletion_requested_at=?, deletion_due_at=? WHERE id=?",
                    (now() - 10, now() - 1, d["id"]))
    real_all = w.store.all

    def listed_then_cancelled(sql, args=()):
        rows = real_all(sql, args)
        if "pending_delete" in sql and rows:
            w.store.execute("UPDATE users SET status='active', deletion_requested_at=NULL, deletion_due_at=NULL "
                            "WHERE id=?", (d["id"],))
        return rows
    w.mp.setattr(w.store, "all", listed_then_cancelled)
    try:
        assert finalize_deletions(w.store, withdrawals=False) == 0
    finally:
        w.mp.setattr(w.store, "all", real_all)
    assert w.store.one("SELECT status FROM users WHERE id=?", (d["id"],))["status"] == "active"


def test_a_deletion_sweep_that_fails_after_its_line_is_voided(world):
    from synwevo_identity.profile import finalize_deletions
    w = world
    _due(w)
    older = _open(w, _copy(w.store, w.tmp / "older"))
    with _failing_after_the_line(w, "user_deleted") as failed, pytest.raises(RuntimeError, match="fixture"):
        finalize_deletions(w.store)
    (attempt,) = failed
    assert not _deleted(w.store, w) and _markers(w.store) == 0 and not _receipt(w.store, attempt)
    assert _void_row(w.store, attempt) == "user_deleted"
    assert journal.apply(older, journal.read(w.data, since=now() + 3600)) == {journal.VOID_KIND: 1}
    assert not _deleted(older, w)
    assert finalize_deletions(w.store) == 1                     # the next sweep finalizes it
    assert _deleted(w.store, w)


# ---------------------------------------------------------------- the member import (member_import_routes.py)
from .test_member_import import base as _import_base, login as _import_login, org, upload as _import_upload  # noqa: E402,F401


def _reinviting_import(c):
    """FIXTURE: a confirmed one-row import inviting an address, and a pending invitation for that
    address sent by hand after the import was planned (long enough ago for no cooldown): the
    import's invitation supersedes the pending link."""
    h = _import_login(c, "owner")
    r = _import_upload(c, h, "姓名,邮箱,空间角色\nNina New,nina.new@example.test,editor\n")
    assert r.status_code == 201, r.text
    imp = r.json()
    mapping = {"mode": "table", "table": 0, "header_row": True, "columns": imp["mapping"]["columns"],
               "defaults": {"workspace_role": "viewer", "org_role_ids": []}}
    plan = c.put(f"{_import_base(c)}/{imp['id']}/mapping", headers=h, json={"workspace_id": c.wid, "mapping": mapping}).json()
    assert plan["summary"]["counts"]["invite"] == 1, plan
    r = c.post(f"{_import_base(c)}/{imp['id']}/confirm", headers=h,
               json={"version": plan["version"], "reason": "fixture import supersedes a pending link"})
    assert r.status_code == 200, r.text
    t, iid = now() - 120, new_id("iv")
    c.ident.store.execute("INSERT INTO invitations (id,email_key,workspace_id,role,can_export,token_hash,invited_by,"
                          "created_at,expires_at) VALUES (?,'nina.new@example.test',?,'viewer',0,?,?,?,?)",
                          (iid, c.wid, new_id("fixture-hash"), c.users["owner"]["id"], t, t + 86400))
    return iid


def test_an_import_journals_the_link_it_supersedes_inside_the_invitation_s_transaction(org, tmp_path, monkeypatch):
    c, store = org, org.ident.store
    iid = _reinviting_import(c)
    revoked = lambda s: s.one("SELECT revoked_at FROM invitations WHERE id=?", (iid,))["revoked_at"] is not None  # noqa: E731
    older = Store(_copy(store, tmp_path / "older"))
    with _watching(SimpleNamespace(tmp=tmp_path, mp=monkeypatch), "invitation_revoked") as seen:
        assert c.api.state.member_import_runner.run_once() == 1
    assert seen.in_transaction is True and revoked(store) and _receipt(store, seen.attempt)
    (line,) = [e for e in journal.read(store.root) if e.get("attempt_id") == seen.attempt]
    mid = Store(seen.mid)
    assert not revoked(mid) and not _receipt(mid, seen.attempt)
    assert "invitation_revoked" in journal.apply(mid, journal.read(store.root, since=now() + 3600))
    assert revoked(mid)
    at_commit = journal.read(seen.crashed, since=now() + 3600)
    assert line in at_commit
    journal.apply(older, at_commit)
    assert revoked(older)


def test_an_import_row_that_fails_after_its_line_is_voided(org, tmp_path, monkeypatch):
    c, store = org, org.ident.store
    iid = _reinviting_import(c)
    older = Store(_copy(store, tmp_path / "older"))
    with _failing_after_the_line(SimpleNamespace(mp=monkeypatch), "invitation_revoked") as failed:
        assert c.api.state.member_import_runner.run_once() == 1          # the row fails on its own
    (attempt,) = failed
    assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (iid,))["revoked_at"] is None
    assert not store.one("SELECT 1 FROM invitations WHERE email_key='nina.new@example.test' AND id<>?", (iid,))
    assert _void_row(store, attempt) == "invitation_revoked"
    assert journal.apply(older, journal.read(store.root, since=now() + 3600)) == {journal.VOID_KIND: 1}
    assert older.one("SELECT revoked_at FROM invitations WHERE id=?", (iid,))["revoked_at"] is None
