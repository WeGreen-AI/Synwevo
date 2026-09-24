"""The account CLI's credential lifecycle, root-secret migration and audit trail.

Run in-process against a temporary data directory (`--data`), never the real one. The cases are
the operator mistakes the old CLI turned into security problems: a "rotated" secret that was
never stored, a client show that crashed, advice that silently broke every authenticator, and
privileged changes that left no record.
"""
from __future__ import annotations

import json
import sys

import pyotp
import pytest

from synwevo_core import journal, oidc, sealed
from synwevo_core.apps import bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    d = tmp_path / "d"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.delenv("SYNWEVO_SECRET", raising=False)
    store = Store(d)
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    alice = ident.create_user("alice@example.com", PW, status="active")
    ws = ident.workspaces_for(alice["id"])[0]["id"]
    return {"d": str(d), "store": store, "ident": ident, "alice": alice, "ws": ws, "mp": monkeypatch}


def cli(world, capsys, *argv) -> tuple[int, str, str]:
    from synwevo_identity import cli as cli_mod
    world["mp"].setattr(sys, "argv", ["synwevo_identity.cli", "--data", world["d"], *argv])
    code = cli_mod.main()
    out = capsys.readouterr()
    return code, out.out, out.err


def _secret_from(out: str) -> str:
    return next(line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("client_secret"))


def _audit(world, action):
    return world["store"].all("SELECT * FROM audit_events WHERE action=? ORDER BY at", (action,))


def _register_service(world, capsys):
    code, out, _ = cli(world, capsys, "client", "register", "--service", "--client-id", "nightly",
                       "--workspace", world["ws"], "--api-scopes", "docweave.read docweave.write")
    assert code == 0, out
    return _secret_from(out)


# ---------------------------------------------------------------- F006/F054/F315: rotation
def test_re_registering_keeps_the_secret_and_says_so(world, capsys):
    first = _register_service(world, capsys)
    code, out, _ = cli(world, capsys, "client", "register", "--service", "--client-id", "nightly",
                       "--workspace", world["ws"], "--api-scopes", "docweave.read")
    assert code == 0 and "client_secret  :" not in out and "保持不变" in out
    assert oidc.Clients(world["store"]).check_secret("nightly", first) is True
    assert _audit(world, "cli.client.update") and _audit(world, "cli.client.register")


def test_rotate_secret_replaces_it_revokes_tokens_and_is_recorded(world, capsys):
    store = world["store"]
    old = _register_service(world, capsys)
    tok = oidc.Grants(store).issue_for_service(client=oidc.Clients(store).get("nightly"), scope="docweave.read")
    code, out, _ = cli(world, capsys, "client", "rotate-secret", "nightly")
    assert code == 0, out
    new = _secret_from(out)
    clients = oidc.Clients(store)
    assert clients.check_secret("nightly", old) is False and clients.check_secret("nightly", new) is True
    assert oidc.Grants(store).load_access(tok["access_token"]) is None
    ev = _audit(world, "cli.client.rotate_secret")
    assert ev and ev[0]["actor_kind"] == "system" and json.loads(ev[0]["meta"])["via"] == "cli"
    assert new not in ev[0]["meta"] and old not in ev[0]["meta"], "审计里不能出现密钥"
    entries = [e for e in journal.read(world["d"]) if e["kind"] == "client_secret_rotated"]
    assert entries and entries[-1]["secret_hash"] == store.one(
        "SELECT secret_hash FROM oauth_clients WHERE client_id='nightly'")["secret_hash"]


def test_rotate_with_a_grace_period(world, capsys):
    old = _register_service(world, capsys)
    code, out, _ = cli(world, capsys, "client", "rotate-secret", "nightly", "--grace-minutes", "30")
    assert code == 0 and "之前仍然有效" in out
    assert oidc.Clients(world["store"]).check_secret("nightly", old) is True
    code, _, err = cli(world, capsys, "client", "rotate-secret", "nightly", "--grace-minutes", "5000")
    assert code == 2 and "最多" in err


def test_a_public_client_has_nothing_to_rotate(world, capsys):
    cli(world, capsys, "client", "register", "--client-id", "web", "--redirect", "http://docweave.localhost:8765/cb")
    code, _, err = cli(world, capsys, "client", "rotate-secret", "web")
    assert code == 2 and "公共客户端" in err


def test_disable_revokes_tokens_and_enable_restores_the_client_only(world, capsys):
    store = world["store"]
    secret = _register_service(world, capsys)
    tok = oidc.Grants(store).issue_for_service(client=oidc.Clients(store).get("nightly"), scope="docweave.read")
    code, out, _ = cli(world, capsys, "client", "disable", "nightly")
    assert code == 0 and "撤销了 1 个" in out
    assert oidc.Grants(store).load_access(tok["access_token"]) is None
    assert oidc.Clients(store).check_secret("nightly", secret) is False
    assert any(e["kind"] == "client_disabled" for e in journal.read(world["d"]))
    code, out, _ = cli(world, capsys, "client", "show", "nightly")          # F066: used to NameError
    assert code == 0 and json.loads(out)["disabled_at"] and "secret_hash" not in out
    assert cli(world, capsys, "client", "enable", "nightly")[0] == 0
    assert oidc.Clients(store).check_secret("nightly", secret) is True
    assert oidc.Grants(store).load_access(tok["access_token"]) is None, "重新启用不会让撤销的令牌复活"
    assert _audit(world, "cli.client.disable") and _audit(world, "cli.client.enable")


# ---------------------------------------------------------------- F075: bootstrap token
def test_no_bootstrap_token_once_an_administrator_exists(world, capsys):
    world["ident"].create_user("root@example.com", PW, status="active", platform_role="admin")
    for extra in ((), ("--force",)):
        code, out, err = cli(world, capsys, "bootstrap", "token", *extra)
        assert code == 1 and "bootstrap admin" in err
        assert "一次性引导令牌" not in out


# ---------------------------------------------------------------- app entitlements
def test_revoking_an_app_entitlement_is_explicit_and_journalled(world, capsys):
    from synwevo_core import authority
    ident, ws, store = world["ident"], world["ws"], world["store"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    ident.add_member(ws, bob["id"], "editor")
    code, out, _ = cli(world, capsys, "app", "revoke", "docweave", "--workspace", ws, "--user", "bob@example.com")
    assert code == 0, out
    assert store.one("SELECT revoked_at FROM app_entitlements WHERE workspace_id=? AND user_id=?",
                     (ws, bob["id"]))["revoked_at"]
    assert authority.check(store, {"kind": "user", "user_id": bob["id"], "tenant": ws}, need="read")[1] == \
        "entitlement_revoked"
    assert any(e["kind"] == "entitlement_revoked" for e in journal.read(world["d"]))
    assert _audit(world, "cli.app.revoke")


# ---------------------------------------------------------------- F055: the root secret
def _enrol_totp(world) -> str:
    from synwevo_identity.mfa import Mfa
    mfa = Mfa(world["store"])
    b = mfa.begin_totp(world["alice"]["id"], "alice@example.com")
    mfa.confirm_totp(world["alice"]["id"], pyotp.TOTP(b["secret"]).now())
    return b["secret"]


def test_info_names_the_migration_step(world, capsys):
    code, out, _ = cli(world, capsys, "info")
    assert code == 0 and "secret rekey" in out


def test_rekey_moves_seeds_to_the_external_secret(world, capsys):
    from synwevo_core.auth import Auth, RootSecretMismatch
    from synwevo_identity.mfa import Mfa, decrypt_seed
    store = world["store"]
    seed = _enrol_totp(world)
    world["mp"].setenv("SYNWEVO_SECRET", "an-external-root-secret-0123456789")
    with pytest.raises(RootSecretMismatch):
        Auth(store).secret()                        # just setting the variable is refused, loudly
    code, out, err = cli(world, capsys, "secret", "rekey", "--to", "env", "--yes")
    assert code == 0, err
    assert "totp_secrets.secret_enc=1" in out
    row = store.one("SELECT secret_enc FROM totp_secrets")
    assert decrypt_seed(store, row["secret_enc"]) == seed
    assert Mfa(store).verify_totp(world["alice"]["id"], pyotp.TOTP(seed).at(int(__import__("time").time()) + 30))
    ev = _audit(world, "cli.secret.rekey")
    assert ev and "an-external-root-secret" not in ev[-1]["meta"]
    # and back again
    code, out, err = cli(world, capsys, "secret", "rekey", "--to", "db", "--yes")
    assert code == 0, err
    world["mp"].delenv("SYNWEVO_SECRET")
    assert decrypt_seed(store, store.one("SELECT secret_enc FROM totp_secrets")["secret_enc"]) == seed


def test_rekey_refuses_an_old_root_that_does_not_open_the_data(world, capsys):
    store = world["store"]
    _enrol_totp(world)
    before = store.one("SELECT secret_enc FROM totp_secrets")["secret_enc"]
    world["mp"].setenv("SYNWEVO_SECRET", "the-new-external-root-secret-0000")
    world["mp"].setenv("OLD_ROOT_GUESS", "a-guess-that-is-not-the-old-root")
    code, _, err = cli(world, capsys, "secret", "rekey", "--to", "env", "--from-env", "OLD_ROOT_GUESS", "--yes")
    assert code == 2 and "拒绝迁移" in err
    assert store.one("SELECT secret_enc FROM totp_secrets")["secret_enc"] == before, "拒绝时一行都不能改"
    assert sealed.opens_with(store, bytes.fromhex(
        store.one("SELECT value FROM meta WHERE key='server_secret'")["value"])) is True


def test_rekey_needs_confirmation_when_not_interactive(world, capsys):
    _enrol_totp(world)
    world["mp"].setenv("SYNWEVO_SECRET", "an-external-root-secret-0123456789")
    code, _, err = cli(world, capsys, "secret", "rekey", "--to", "env")
    assert code == 2 and "--yes" in err


def test_secret_status_reports_a_mismatch(world, capsys):
    _enrol_totp(world)
    world["mp"].setenv("SYNWEVO_SECRET", "an-external-root-secret-0123456789")
    code, out, _ = cli(world, capsys, "secret", "status")
    assert code == 0 and "服务会拒绝启动" in out and "totp_secrets.secret_enc=1" in out
