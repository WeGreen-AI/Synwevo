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
