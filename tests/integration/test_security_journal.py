"""Synthetic local recovery inputs; a damaged withdrawal must never disappear silently."""
import json
import base64
import hashlib
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from synwevo_core import authority, journal, oidc
from synwevo_core.apps import bootstrap_docs_app
from synwevo_core.auth import Auth
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store


@pytest.mark.parametrize("bad", [
    '{"at": 1, "kind": "sessions_rev',
    'null', '[]', '"unexpected"',
    '{"at": 1, "kind": "future_revocation", "user_id": "synthetic"}',
    '{"at": "1", "kind": "sessions_revoked", "user_id": "synthetic"}',
    '{"at": NaN, "kind": "sessions_revoked", "user_id": "synthetic"}',
    '{"at": 1, "kind": "sessions_revoked"}',
    '{"at": 1, "kind": "tokens_revoked"}',
    '{"at": 1, "kind": "role_changed", "workspace_id": "w", "user_id": "u", "role": "unknown"}',
    '{"at": 1, "kind": "workspace_unclaimed"}',
    '{"at": 1, "kind": "workspace_unclaimed", "workspace_id": "w", "label_restored": "yes"}',
    '{"at": 1, "kind": "organization_deleted", "event_id": "e", "organization_id": "o", "workspace_ids": [], '
    '"deleted_at": 1, "recover_until": 2, "previous_status": "active"}',
    '{"at": 1, "kind": "intent_voided", "voided_kind": "workspace_frozen"}',
    '{"at": 1, "kind": "intent_voided", "attempt_id": "ja_fixture", "voided_kind": "not_a_kind"}',
    '{"at": 1, "kind": "intent_voided", "attempt_id": "ja_fixture", "voided_kind": "intent_voided"}',
    '{"at": 1, "kind": "workspace_frozen", "workspace_id": "w", "attempt_id": ""}',
    '{"at": 1, "kind": "api_key_revoked", "key_id": "k", "attempt_id": 7}',
])
@pytest.mark.parametrize("suffix", ["", '\n{"at": 3, "kind": "sessions_revoked", "user_id": "after"}\n'])
def test_bad_entries_are_fatal_even_before_the_snapshot(tmp_path, bad, suffix):
    journal.path(tmp_path).write_text(bad + suffix, encoding="utf-8")
    with pytest.raises(ValueError, match="journal.*line 1"):
        journal.read(tmp_path, since=2)


def test_apply_validates_before_any_withdrawal_is_applied(tmp_path):
    store = Store(tmp_path)
    store.execute("INSERT INTO workspaces (id,name,status,created_at,updated_at) VALUES ('w','synthetic','active',1,1)")
    with pytest.raises(ValueError, match="journal"):
        journal.apply(store, [{"at": 1, "kind": "workspace_frozen", "workspace_id": "w"},
                              {"at": 2, "kind": "unknown"}])
    assert store.one("SELECT status FROM workspaces WHERE id='w'")["status"] == "active"


def test_valid_withdrawals_preserve_order_and_filter_by_time(tmp_path):
    entries = [{"at": at, "kind": "sessions_revoked", "user_id": f"synthetic-{at}"} for at in (1, 2, 3)]
    journal.path(tmp_path).write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    assert journal.read(tmp_path, since=1) == entries[1:]


PW = "synthetic-restoration-test-passphrase"
REASON = "synthetic ownership restoration test"
REDIRECT = "http://localhost:8765/auth/callback"


@pytest.fixture()
def recovery(tmp_path, monkeypatch):
    """Real identities and archives, exclusively in this test's synthetic directory."""
    data = tmp_path / "live"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(data))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    from synwevo_server.app import create_app
    api = create_app(str(data), start_jobs=False)
    store = Store(data)
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    a, b = [ident.create_user(f"{name}@example.test", PW, status="active",
                             with_personal_workspace=False) for name in ("old", "new")]
    wid = ident.create_workspace("Synthetic restoration", owner_id=a["id"])["id"]
    ident.add_member(wid, b["id"], "editor")
    path = Path(__file__).resolve().parents[2] / "apps/docs/scripts/snapshot.py"
    spec = importlib.util.spec_from_file_location("_authority_snapshot", path)
    snapshot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snapshot)
    yield SimpleNamespace(data=data, tmp=tmp_path, api=api, store=store, ident=ident,
                          a=a, b=b, wid=wid, snapshot=snapshot)
    snapshot._close(store)


def _login(api, email="old@example.test"):
    c = TestClient(api)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def _post(c, path, body):
    return c.post(path, json=body, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE)})


def _backup(w):
    out = w.tmp / "archives"
    assert w.snapshot.create(SimpleNamespace(data=str(w.data), out=str(out))) == 0
    archives = list(out.glob("*.tar.gz"))
    assert len(archives) == 1
    return archives[0]


def _restore(w, archive, *, no_journal=False):
    into = w.tmp / "restored"
    if no_journal:
        # Exercise the actual explicit CLI contract, including flag parsing and report text.
        run = subprocess.run([sys.executable, w.snapshot.__file__, "restore", "--archive", str(archive),
                              "--into", str(into), "--journal-from", str(w.data), "--no-journal"],
                             capture_output=True, text=True, check=False)
        assert run.returncode == 0, run.stderr
        assert "撤销全部旧 API 密钥" in run.stdout
        assert "停用私密及服务客户端" in run.stdout
    else:
        assert w.snapshot.restore(SimpleNamespace(archive=str(archive), into=str(into), force=False,
                                                  journal_from=str(w.data), no_journal=False)) == 0
    return Store(into)


@pytest.mark.parametrize("other_owner", [None, "active", "suspended"])
@pytest.mark.parametrize("single_transfer", [False, True])
def test_real_restore_honors_owner_demotion_without_inventing_owner(recovery, other_owner, single_transfer):
    w = recovery
    if other_owner:
        c = w.ident.create_user("coowner@example.test", PW, status="active", with_personal_workspace=False)
        w.ident.add_member(w.wid, c["id"], "owner")
        if other_owner == "suspended":
            w.ident.set_status(c["id"], "suspended")
    old, new = _login(w.api), _login(w.api, "new@example.test")
    archive = _backup(w)
    assert _post(old, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    out = _post(old, f"/api/auth/workspaces/{w.wid}/ownership/transfer",
                {"to_user_id": w.b["id"], "demote_self_to": "viewer" if single_transfer else None,
                 "reason": REASON})
    assert out.status_code == 200
    if not single_transfer:
        assert _post(new, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
        out = new.patch(f"/api/auth/workspaces/{w.wid}/members/{w.a['id']}",
                        json={"role": "viewer", "reason": REASON},
                        headers={CSRF_HEADER: new.cookies.get(CSRF_COOKIE)})
        assert out.status_code == 200
    assert out.json()["journaled"] is True
    entries = journal.read(w.data)
    assert [(e["kind"], e.get("role")) for e in entries] == [("role_changed", "viewer")]
    current = w.store.all("SELECT * FROM memberships WHERE workspace_id=? ORDER BY user_id", (w.wid,))
    saved_journal = journal.path(w.data).read_bytes()
    restored = _restore(w, archive)
    try:
        row = restored.one("SELECT role,can_export FROM memberships WHERE workspace_id=? AND user_id=?",
                           (w.wid, w.a["id"]))
        assert row == {"role": "viewer", "can_export": 0}
        assert restored.one("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?",
                            (w.wid, w.b["id"]))["role"] == "editor"
        expected = "active" if other_owner == "active" else "frozen"
        assert restored.one("SELECT status FROM workspaces WHERE id=?", (w.wid,))["status"] == expected
        desc = {"kind": "user", "user_id": w.a["id"], "tenant": w.wid}
        assert authority.check(restored, desc, need="write")[0] is False
        assert authority.check(restored, desc, need="export")[0] is False
        if other_owner == "active":
            assert authority.check(restored, {**desc, "user_id": c["id"]}, need="write") == (True, None)
        # Replaying twice does not revive privileges or need to invent an owner.
        journal.apply(restored, entries)
        assert restored.one("SELECT role,can_export FROM memberships WHERE workspace_id=? AND user_id=?",
                            (w.wid, w.a["id"])) == row
        from synwevo_server.app import create_app
        restored_api = create_app(str(w.tmp / "restored"), start_jobs=False)
        again = _login(restored_api)
        assert again.get(f"/api/auth/workspaces/{w.wid}/members").status_code == 403
        assert w.store.all("SELECT * FROM memberships WHERE workspace_id=? ORDER BY user_id", (w.wid,)) == current
        assert journal.path(w.data).read_bytes() == saved_journal
    finally:
        w.snapshot._close(restored)


def test_demotion_replay_never_promotes_a_more_restricted_member(recovery):
    w = recovery
    w.ident.change_role(w.wid, w.b["id"], "viewer")
    before = w.store.one("SELECT * FROM memberships WHERE workspace_id=? AND user_id=?", (w.wid, w.b["id"]))
    entries = [{"at": 1, "kind": "role_changed", "workspace_id": w.wid, "user_id": uid, "role": "admin"}
               for uid in (w.b["id"], "synthetic-absent")]
    journal.apply(w.store, entries)
    assert w.store.one("SELECT * FROM memberships WHERE workspace_id=? AND user_id=?", (w.wid, w.b["id"])) == before
    assert w.store.one("SELECT 1 FROM memberships WHERE user_id='synthetic-absent'") is None
    assert w.ident.get_workspace(w.wid)["status"] == "active"


def test_viewer_demotion_replay_withdraws_export_even_if_backup_was_already_viewer(recovery):
    w = recovery
    w.ident.change_role(w.wid, w.b["id"], "viewer", can_export=True)
    archive = _backup(w)
    # A post-backup grant followed by a demotion: only the latter is in the journal.
    w.ident.change_role(w.wid, w.b["id"], "editor")
    w.ident.change_role(w.wid, w.b["id"], "viewer")
    journal.record(w.store, "role_changed", workspace_id=w.wid, user_id=w.b["id"], role="viewer")
    restored = _restore(w, archive)
    try:
        assert restored.one("SELECT role,can_export FROM memberships WHERE workspace_id=? AND user_id=?",
                            (w.wid, w.b["id"])) == {"role": "viewer", "can_export": 0}
        before = restored.all("SELECT * FROM memberships")
        journal.apply(restored, journal.read(w.data))
        assert restored.all("SELECT * FROM memberships") == before
    finally:
        w.snapshot._close(restored)


def test_failed_freeze_rolls_back_demotion_and_later_withdrawals(recovery):
    w = recovery
    before = w.store.all("SELECT * FROM memberships")
    w.store.execute("CREATE TEMP TRIGGER fail_restore_freeze BEFORE UPDATE OF status ON workspaces "
                    "WHEN NEW.status='frozen' BEGIN SELECT RAISE(ABORT, 'synthetic freeze failure'); END")
    entries = [{"at": 1, "kind": "role_changed", "workspace_id": w.wid, "user_id": w.a["id"], "role": "viewer"},
               {"at": 2, "kind": "user_suspended", "user_id": w.b["id"]}]
    with pytest.raises(sqlite3.IntegrityError, match="synthetic freeze failure"):
        journal.apply(w.store, entries)
    assert w.store.all("SELECT * FROM memberships") == before
    assert w.ident.get_workspace(w.wid)["status"] == "active"
    assert w.ident.get_user(w.b["id"])["status"] == "active"


def test_freeze_counts_usable_owners_after_all_withdrawals(recovery):
    w = recovery
    w.ident.add_member(w.wid, w.b["id"], "owner")
    out = journal.apply(w.store, [
        {"at": 1, "kind": "role_changed", "workspace_id": w.wid, "user_id": w.a["id"], "role": "viewer"},
        {"at": 2, "kind": "user_suspended", "user_id": w.b["id"]},
    ])
    assert out["role_changed_workspaces_frozen"] == 1
    assert w.ident.get_workspace(w.wid)["status"] == "frozen"


@pytest.mark.parametrize("no_journal", [False, True])
def test_restore_cannot_revive_machine_credentials_and_public_login_survives(recovery, no_journal):
    w = recovery
    auth, clients = Auth(w.store), oidc.Clients(w.store)
    # Generate only fixture material before backup; restoration must preserve it byte-for-byte.
    root = auth.secret()
    signing = oidc.Keys(w.store).active()
    raw, key = auth.create_key("synthetic revoked", w.wid)
    healthy_raw, healthy_key = auth.create_key("synthetic unaffected", w.wid)
    secrets = {}
    for cid, kind, confidential in [("public", "user", False), ("private", "user", True),
                                     ("service", "service", True), ("healthy-service", "service", True)]:
        _, secrets[cid] = clients.register(client_id=cid, app_id="docweave", name="Synthetic client",
                                          redirect_uris=[REDIRECT] if kind == "user" else [],
                                          confidential=confidential, kind=kind,
                                          workspace_id=w.wid if kind == "service" else None,
                                          api_scopes="docweave.read" if kind == "service" else "")
    _login(w.api)
    token = TestClient(w.api).post("/oidc/token", data={"grant_type": "client_credentials",
                    "client_id": "service", "client_secret": secrets["service"]})
    assert token.status_code == 200
    old_access = token.json()["access_token"]
    key_hashes = w.store.all("SELECT id,secret_hash FROM api_keys ORDER BY id")
    client_hashes = w.store.all("SELECT client_id,secret_hash FROM oauth_clients ORDER BY client_id")
    users = w.store.all("SELECT * FROM users ORDER BY id")
    passwords = w.store.all("SELECT * FROM user_passwords ORDER BY user_id")
    archive = _backup(w)
    auth.revoke_key(key["id"])
    assert clients.disable("service")
    journal.record(w.store, "client_disabled", client_id="service")
    rotated = clients.rotate_secret("private")
    journal.record(w.store, "client_secret_rotated", client_id="private", secret_hash=rotated["secret_hash"])
    with pytest.raises(ServiceError):
        auth.verify(raw)
    assert clients.check_secret("service", secrets["service"]) is False
    assert clients.check_secret("private", secrets["private"]) is False
    live_keys = w.store.all("SELECT * FROM api_keys ORDER BY id")
    live_clients = w.store.all("SELECT * FROM oauth_clients ORDER BY client_id")
    restored = _restore(w, archive, no_journal=no_journal)
    try:
        restored_auth, restored_clients = Auth(restored), oidc.Clients(restored)
        try:
            restored_auth.verify(raw)
            old_key_accepted = True
        except ServiceError as revoked:
            assert revoked.code == "key_revoked"
            old_key_accepted = False
        assert {"old_key_accepted": old_key_accepted,
                "old_service_secret_accepted": restored_clients.check_secret("service", secrets["service"]),
                "old_private_secret_accepted": restored_clients.check_secret("private", secrets["private"])} == {
                    "old_key_accepted": False, "old_service_secret_accepted": False,
                    "old_private_secret_accepted": False}
        assert oidc.Grants(restored).load_access(old_access) is None
        assert restored.one("SELECT COUNT(*) n FROM sessions WHERE revoked_at IS NULL")["n"] == 0
        assert restored.one("SELECT COUNT(*) n FROM oauth_tokens WHERE revoked_at IS NULL")["n"] == 0
        assert restored_auth.secret() == root
        assert oidc.Keys(restored).active() == signing
        assert restored.all("SELECT * FROM users ORDER BY id") == users
        assert restored.all("SELECT * FROM user_passwords ORDER BY user_id") == passwords
        assert restored.all("SELECT id,secret_hash FROM api_keys ORDER BY id") == key_hashes
        if no_journal:
            with pytest.raises(ServiceError):
                restored_auth.verify(healthy_raw)
            assert restored.all("SELECT client_id,secret_hash FROM oauth_clients ORDER BY client_id") == client_hashes
            for cid in ("private", "service", "healthy-service"):
                assert restored_clients.get(cid) is None
            key_rows = restored.all("SELECT * FROM api_keys ORDER BY id")
            client_rows = restored.all("SELECT * FROM oauth_clients ORDER BY client_id")
            again = w.snapshot.fence(w.tmp / "restored", since=None, journal_dir=None, no_journal=True)
            assert again["api_keys_revoked_without_journal"] == 0
            assert again["clients_disabled_without_journal"] == 0
            assert restored.all("SELECT * FROM api_keys ORDER BY id") == key_rows
            assert restored.all("SELECT * FROM oauth_clients ORDER BY client_id") == client_rows
        else:
            assert restored_auth.verify(healthy_raw)["key_id"] == healthy_key["id"]
            assert restored_clients.check_secret("private", rotated["secret"]) is True
            assert restored_clients.check_secret("healthy-service", secrets["healthy-service"]) is True
        assert restored_clients.get("public") is not None
        from synwevo_server.app import create_app
        restored_api = create_app(str(w.tmp / "restored"), start_jobs=False)
        http = TestClient(restored_api)
        denied = http.post("/oidc/token", data={"grant_type": "client_credentials", "client_id": "service",
                                               "client_secret": secrets["service"]})
        assert denied.status_code == 401 and denied.json()["error"] == "invalid_client"
        # Real fresh public-client login + PKCE exchange still works after every session ended.
        http = _login(restored_api)
        verifier = "synthetic-pkce-verifier-" * 3
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        response = http.get("/oidc/authorize", params={"client_id": "public", "redirect_uri": REDIRECT,
                            "response_type": "code", "scope": "openid", "state": "synthetic",
                            "code_challenge": challenge, "code_challenge_method": "S256"}, follow_redirects=False)
        assert response.status_code == 303
        code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]
        exchanged = http.post("/oidc/token", data={"grant_type": "authorization_code", "client_id": "public",
                              "redirect_uri": REDIRECT, "code": code, "code_verifier": verifier})
        assert exchanged.status_code == 200 and "id_token" in exchanged.json()
        assert w.store.all("SELECT * FROM api_keys ORDER BY id") == live_keys
        assert w.store.all("SELECT * FROM oauth_clients ORDER BY client_id") == live_clients
    finally:
        w.snapshot._close(restored)


@pytest.mark.parametrize("no_journal", [False, True])
def test_restore_fences_inflight_scoped_passkey_proofs(recovery, no_journal):
    """A synthetic pending ceremony from a backup must not become spendable after restore."""
    from synwevo_core.sessions import COOKIE_NAME, Sessions
    from synwevo_core.store import now
    w = recovery
    client = _login(w.api)
    session = Sessions(w.store).load(client.cookies.get(COOKIE_NAME), touch=False)
    ts = now()
    w.store.execute("INSERT INTO reauth_challenges (id,user_id,session_id,purpose,scope,challenge,rp_id,origin,created_at,expires_at) "
                    "VALUES (?,?,?,'reauth','admin',?,'localhost','http://localhost',?,?)",
                    ("fixture-pending-proof", w.a["id"], session["id"], "fixture-unspent-challenge", ts, ts + 300))
    archive = _backup(w)
    restored = _restore(w, archive, no_journal=no_journal)
    try:
        assert restored.one("SELECT used_at FROM reauth_challenges WHERE id='fixture-pending-proof'")["used_at"]
        assert restored.one("SELECT revoked_at FROM sessions WHERE id=?", (session["id"],))["revoked_at"]
        assert not Sessions(restored).current(session)
        # The restoration touches only the isolated copy, even when the original ceremony is live.
        assert w.store.one("SELECT used_at FROM reauth_challenges WHERE id='fixture-pending-proof'")["used_at"] is None
        assert Sessions(w.store).current(session)
    finally:
        w.snapshot._close(restored)


@pytest.mark.parametrize("no_journal", [False, True])
def test_restore_voids_a_pending_second_factor_reset_and_keeps_closed_ones(recovery, no_journal):
    """A26: a pending reset in the backup may have been cancelled since — by the person, through
    the mailed link — or its announcement may be among the queued mail the fence drops. Completing
    it from the restored copy would remove factors the person refused or never heard of, so the
    fence voids it; closed resets are history and stay. Synthetic rows only."""
    from synwevo_core.store import now
    w = recovery
    ts = now()
    for status in ("pending", "cancelled", "completed"):
        w.store.execute("INSERT INTO second_factor_resets (id,user_id,requested_by,method,reason,include_passkeys,"
                        "cancel_token_hash,created_at,due_at,status) VALUES (?,?,?,?,?,0,?,?,?,?)",
                        ("fixture-reset-" + status, w.b["id"], w.a["id"], "in_person", "Fixture restore intent",
                         "fixture-hash-" + status, ts, ts + 3600, status))
    archive = _backup(w)
    restored = _restore(w, archive, no_journal=no_journal)
    try:
        pending = restored.one("SELECT status,void_reason FROM second_factor_resets WHERE id='fixture-reset-pending'")
        assert pending == {"status": "void", "void_reason": "restored_invalidated"}
        for status in ("cancelled", "completed"):
            assert restored.one("SELECT status FROM second_factor_resets WHERE id=?", ("fixture-reset-" + status,))["status"] == status
        assert w.store.one("SELECT status FROM second_factor_resets WHERE id='fixture-reset-pending'")["status"] == "pending", \
            "the live database is not touched"
    finally:
        w.snapshot._close(restored)


def test_the_restore_review_lists_the_second_factor_resets_it_voided(recovery, capsys):
    """The fence can tell nobody about a voided reset, so its review names them (ids only)."""
    from synwevo_core.store import now
    w = recovery
    ts = now()
    w.store.execute("INSERT INTO second_factor_resets (id,user_id,requested_by,method,reason,include_passkeys,"
                    "cancel_token_hash,created_at,due_at,status) VALUES (?,?,?,'in_person','Fixture restore intent',0,"
                    "'fixture-hash-review',?,?,'pending')", ("fixture-reset-review", w.b["id"], w.a["id"], ts, ts + 3600))
    restored_dir = w.tmp / "restored"
    _restore(w, _backup(w))
    # The restore already voided it; a pending row written into the copy afterwards shows the report.
    copy = Store(restored_dir)
    copy.execute("INSERT INTO second_factor_resets (id,user_id,requested_by,method,reason,include_passkeys,"
                 "cancel_token_hash,created_at,due_at,status) VALUES (?,?,?,'in_person','Fixture restore intent',0,"
                 "'fixture-hash-review-2',?,?,'pending')", ("fixture-reset-review-2", w.b["id"], w.a["id"], ts, ts + 3600))
    w.snapshot._close(copy)
    report = w.snapshot.fence(restored_dir, since=None, journal_dir=None, no_journal=True)
    assert report["second_factor_resets_voided"] == 1
    assert report["review"]["second_factor_resets_voided"] == ["fixture-reset-review-2"]
    capsys.readouterr()
    w.snapshot._print_fence(report, rehearsal=False)
    assert "fixture-reset-review-2" in capsys.readouterr().out


@pytest.mark.parametrize("no_journal", [False, True])
def test_restore_invalidates_pending_super_offers_and_keeps_accepted_history(recovery, no_journal):
    """Synthetic offer rows exercise the real restored-copy fence in both recovery modes."""
    from synwevo_core.store import now
    w = recovery
    ts = now()
    for state in ("pending", "accepted", "ready", "cancelled"):
        w.store.execute("INSERT INTO platform_super_admin_offers(id,proposer_id,proposer_grant_id,recipient_id,reason,"
                        "created_at,expires_at,state) VALUES (?,?,?,?,?,?,?,?)",
                        ("fixture-offer-" + state, w.a["id"], "fixture-source-grant", w.b["id"], "Fixture restore intent", ts, ts+86400, state))
    archive = _backup(w)
    restored = _restore(w, archive, no_journal=no_journal)
    try:
        pending = restored.one("SELECT * FROM platform_super_admin_offers WHERE id='fixture-offer-pending'")
        assert pending["state"] == "invalidated" and pending["closed_reason"] == "restored_invalidated"
        for state in ("accepted", "ready", "cancelled"):
            assert restored.one("SELECT state FROM platform_super_admin_offers WHERE id=?", ("fixture-offer-" + state,))["state"] == state
        assert w.store.one("SELECT state FROM platform_super_admin_offers WHERE id='fixture-offer-pending'")["state"] == "pending"
    finally:
        w.snapshot._close(restored)


def test_restore_keeps_a_platform_revoked_invitation_revoked(recovery):
    """A47: the platform's revoke of an invitation (`POST /api/admin/invitations/{iid}/revoke`) is
    journalled, so a copy restored from a backup taken before it has the link withdrawn again —
    without the workspace's own record of who withdrew it, which the backup predates."""
    from synwevo_identity.invites import Invites
    from tests.integration.platform_fixture import make_platform_admin
    w = recovery
    # Synthetic invitation, issued by the workspace's owner through the invitation service.
    row, raw = Invites(w.store, w.ident).create(workspace_id=w.wid, email="invitee@example.test", role="viewer",
                                                actor_id=w.a["id"])
    root = make_platform_admin(w.store, w.ident.create_user("root@example.test", PW, status="active",
                                                            with_personal_workspace=False))
    archive = _backup(w)
    admin = _login(w.api, root["email"])
    assert _post(admin, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    out = _post(admin, f"/api/admin/invitations/{row['id']}/revoke", {"reason": REASON})
    assert out.status_code == 200, out.text
    assert out.json()["journaled"] is True
    (line,) = [e for e in journal.read(w.data) if e["kind"] == "invitation_revoked"]
    assert line == {"at": pytest.approx(out.json()["invitation"]["revoked_at"], abs=5), "kind": "invitation_revoked",
                    "invitation_id": row["id"], "attempt_id": line["attempt_id"]}
    # written ahead in the revoke's transaction, with the receipt that committed with it
    assert w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],))
    restored = _restore(w, archive)
    try:
        again = Invites(restored, Identity(restored))
        assert restored.one("SELECT revoked_at FROM invitations WHERE id=?", (row["id"],))["revoked_at"]
        assert again.preview(raw)["state"] == "revoked"
        with pytest.raises(ServiceError) as refused:
            again.accept(raw, password=PW, display_name=None)
        assert refused.value.code == "invite_revoked"
        assert again.revoked_by_platform(w.wid) == set(), "the backup predates the workspace's record"
    finally:
        w.snapshot._close(restored)


def _app_policy(w, change, key):
    """A platform-scope policy change through the console, by a super administrator (D12)."""
    from synwevo_core.apps import DOCS_APP_ID
    from tests.integration.platform_fixture import make_platform_admin
    root = w.store.one("SELECT * FROM users WHERE email_key='root@example.test'") or make_platform_admin(
        w.store, w.ident.create_user("root@example.test", PW, status="active", with_personal_workspace=False))
    admin = _login(w.api, root["email"])
    assert _post(admin, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    preview = _post(admin, f"/api/admin/apps/{DOCS_APP_ID}/preview", {"change": change})
    assert preview.status_code == 200, preview.text
    out = _post(admin, f"/api/admin/apps/{DOCS_APP_ID}/{change}",
                {"reason": REASON, "preview_token": preview.json()["preview_token"], "idempotency_key": key})
    assert out.status_code == 200, out.text
    return preview.json()


def test_restore_keeps_a_tightened_application_policy(recovery):
    """B1/B2: tightening an application to explicit grants from the console (`POST
    /api/admin/apps/{id}/policy_explicit`) is journalled with a receipt, so a copy restored from a
    backup taken before it comes back `explicit`: the member without a grant is still refused, the
    owner's grant still counts. Grants themselves are not journalled and need not be — the backup
    has them."""
    from synwevo_core.apps import DOCS_APP_ID, Apps
    w = recovery
    assert Apps(w.store, w.ident).check_access(w.b["id"], DOCS_APP_ID)[0], "a member, under the members policy"
    archive = _backup(w)
    preview = _app_policy(w, "policy_explicit", "restore_policy_fixture_0001")
    assert preview["impact"]["people_losing_access"] == 1 and preview["impact"]["memberships_losing_access"] == 1
    assert [e["app_id"] for e in journal.read(w.data) if e["kind"] == "app_policy_tightened"] == [DOCS_APP_ID]
    restored = _restore(w, archive)
    try:
        assert restored.one("SELECT access_policy FROM apps WHERE id=?", (DOCS_APP_ID,))["access_policy"] == "explicit"
        gate = Apps(restored, Identity(restored))
        assert gate.check_access(w.b["id"], DOCS_APP_ID)[0] is False
        assert gate.check_access(w.a["id"], DOCS_APP_ID)[0] is True
        # Replaying it again changes nothing: the entry is "make explicit true".
        assert journal.apply(restored, [e for e in journal.read(w.data) if e["kind"] == "app_policy_tightened"]) \
            == {"app_policy_tightened": 0}
    finally:
        w.snapshot._close(restored)


def test_restore_keeps_a_loosening_the_backup_already_holds(recovery):
    """The tightening's entry is replayed whatever the backup's time, but only into a database
    without its receipt: a backup taken after the tightening and a later loosening holds both, and
    the loosening stands."""
    from synwevo_core.apps import DOCS_APP_ID, Apps
    w = recovery
    _app_policy(w, "policy_explicit", "restore_policy_fixture_0002")
    _app_policy(w, "policy_members", "restore_policy_fixture_0003")
    archive = _backup(w)
    restored = _restore(w, archive)
    try:
        assert restored.one("SELECT access_policy FROM apps WHERE id=?", (DOCS_APP_ID,))["access_policy"] == "members"
        assert Apps(restored, Identity(restored)).check_access(w.b["id"], DOCS_APP_ID)[0] is True
    finally:
        w.snapshot._close(restored)


def test_restore_tightens_when_the_entry_outran_its_commit(recovery):
    """The restore window the receipt closes: a tightening whose entry reached the journal but whose
    transaction never committed (or committed after the backup read the database) — the backup holds
    neither the change nor the receipt, and the entry is older than the backup's time. It is replayed
    anyway, failing closed."""
    from synwevo_core.apps import DOCS_APP_ID, Apps
    w = recovery
    # FIXTURE: the entry as Apps.set_access_policy appends it, with no committed change or receipt.
    journal.record(w.store, "app_policy_tightened", event_id="apt_fixture_uncommitted", app_id=DOCS_APP_ID)
    assert w.store.one("SELECT access_policy FROM apps WHERE id=?", (DOCS_APP_ID,))["access_policy"] == "members"
    archive = _backup(w)
    restored = _restore(w, archive)
    try:
        assert restored.one("SELECT access_policy FROM apps WHERE id=?", (DOCS_APP_ID,))["access_policy"] == "explicit"
        assert Apps(restored, Identity(restored)).check_access(w.b["id"], DOCS_APP_ID)[0] is False
        assert restored.one("SELECT 1 FROM meta WHERE key='app_policy_tightened:apt_fixture_uncommitted'")
    finally:
        w.snapshot._close(restored)


def _forms(store, released):
    from synwevo_core.apps import bootstrap_suite_app
    return bootstrap_suite_app(store, Identity(store), app_id="forms", name="Fixture forms", icon="forms",
                               launch_url="http://workplace.localhost:8769/forms", released=released)


@pytest.mark.parametrize("held_back", ["disabled", "enabled_by_command_line"])
def test_restore_keeps_an_operator_switch_off_of_an_application_the_deployment_held_back(recovery, held_back):
    """A backup holding the deployment's hold on a business application (`suite_unreleased:`
    marker) — switched off by the start-up, or switched on since by `cli app enable`, which leaves
    the marker — and an operator's later platform-wide switch-off: the replayed `app_disabled` ends
    the hold too, so the restored deployment's next start that releases the application keeps it
    off instead of turning it back on."""
    from synwevo_core.apps import Apps, suite_unreleased
    w = recovery
    apps = Apps(w.store, w.ident)
    _forms(w.store, True)
    _forms(w.store, False)                                   # held back: disabled + marker
    if held_back == "enabled_by_command_line":
        assert apps.set_status("forms", "active") and suite_unreleased(w.store, "forms")
    archive = _backup(w)
    if held_back == "disabled":
        _forms(w.store, True)                                # released again: active, no marker
    assert apps.set_status("forms", "disabled")              # the operator's switch-off (journalled)
    _forms(w.store, True)
    assert w.store.one("SELECT status FROM apps WHERE id='forms'")["status"] == "disabled"
    restored = _restore(w, archive)
    try:
        assert restored.one("SELECT status FROM apps WHERE id='forms'")["status"] == "disabled"
        assert not suite_unreleased(restored, "forms"), "the replayed switch-off ended the hold"
        _forms(restored, True)                               # the restored deployment starts, Forms released
        assert restored.one("SELECT status FROM apps WHERE id='forms'")["status"] == "disabled"
    finally:
        w.snapshot._close(restored)


def test_restore_from_before_an_application_was_registered_keeps_its_restrictions(recovery):
    """A backup taken before the application had a registry row: the replayed tightening and
    switch-off write a placeholder row that carries them (journal._app_row) — rather than changing
    nothing and consuming the tightening's receipt — and the next start-up registration fills in
    the rest while keeping status and policy."""
    from synwevo_core.apps import Apps
    w = recovery
    assert not w.store.one("SELECT 1 FROM apps WHERE id='forms'"), "precondition: not registered yet"
    archive = _backup(w)
    _forms(w.store, True)
    apps = Apps(w.store, w.ident)
    assert apps.set_access_policy("forms", "explicit") and apps.set_status("forms", "disabled")
    restored = _restore(w, archive)
    try:
        row = restored.one("SELECT name, status, access_policy, launch_url FROM apps WHERE id='forms'")
        assert row == {"name": "forms", "status": "disabled", "access_policy": "explicit", "launch_url": ""}
        assert restored.one("SELECT 1 FROM meta WHERE key LIKE 'app_policy_tightened:%'"), "the receipt, with its change"
        _forms(restored, True)
        row = restored.one("SELECT name, status, access_policy, launch_url FROM apps WHERE id='forms'")
        assert row == {"name": "Fixture forms", "status": "disabled", "access_policy": "explicit",
                       "launch_url": "http://workplace.localhost:8769/forms"}
    finally:
        w.snapshot._close(restored)
