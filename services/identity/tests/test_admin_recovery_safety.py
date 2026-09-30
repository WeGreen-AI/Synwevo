"""Isolated restore checks for account withdrawals and the last usable administrator."""
from __future__ import annotations

import pytest

from synwevo_core import journal
from synwevo_core.identity import Identity
from synwevo_core.platform_access import PlatformAccess
from synwevo_core.sessions import Sessions
from synwevo_core.store import Store, now
from synwevo_identity.mfa import Tickets

from .test_admin_operations import (REASON, command, execute, post, preview, seed_pending_credentials,
                                    world)  # noqa: F401 -- shared isolated fixture
from .test_platform_grant_restore import backup


def test_session_revocation_restore_ends_old_pending_authentication_and_preserves_later_sign_ins(world, tmp_path):
    pending = seed_pending_credentials(world)
    old_path = backup(world["store"], tmp_path, "before-session-withdrawal")
    uid = world["target"]["id"]
    response = post(world["admin"], f"/api/admin/users/{uid}/revoke-sessions", {"reason": REASON})
    assert response.status_code == 200, response.text
    entries = journal.read(world["store"].root, since=now()+86400)
    assert len(entries) == 1 and entries[0]["kind"] == "sessions_revoked"
    restored = Store(old_path)
    assert Tickets(restored).load(pending["ticket"])
    journal.apply(restored, entries)
    assert Identity(restored).get_user(uid)["status"] == "active"
    for table in ("sessions", "oauth_tokens"):
        assert not restored.one(f"SELECT 1 FROM {table} WHERE user_id=? AND revoked_at IS NULL", (uid,)), table
    for table in ("oauth_codes", "email_tokens"):
        assert not restored.one(f"SELECT 1 FROM {table} WHERE user_id=? AND used_at IS NULL", (uid,)), table
    for table in ("mfa_tickets", "webauthn_challenges"):
        assert not restored.one(f"SELECT 1 FROM {table} WHERE user_id=?", (uid,)), table
    assert Tickets(restored).load(pending["ticket"]) is None
    raw, _ = Sessions(restored).create(uid, workspace_id=None, amr="password")
    new_ticket = Tickets(restored).issue(uid)
    assert journal.apply(restored, entries) == {}
    assert Sessions(restored).load(raw, touch=False)
    assert Tickets(restored).load(new_ticket)


def test_failed_suspension_intent_cannot_lock_out_a_later_authorized_successor(world, tmp_path, monkeypatch):
    body = command(preview(world))
    original = journal.record_account

    def fail_after_fsync(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("fixture failure after fsync")

    with monkeypatch.context() as failing:
        failing.setattr(journal, "record_account", fail_after_fsync)
        response = execute(world, body)
    assert response.status_code == 503 and response.json()["error"] == "journal_unavailable"
    store, root, target = world["store"], world["root"], world["target"]
    assert world["ident"].get_user(target["id"])["status"] == "active"
    intent = journal.read(store.root)[0]
    receipt = "account_withdrawal:" + intent["account_event_id"]
    assert not store.one("SELECT value FROM meta WHERE key=?", (receipt,))
    access = PlatformAccess(store)
    access.host_grant(target["id"], "super_admin", source="fixture:restore", reason=REASON)
    root_grant = access.live_grants(root["id"])[0]
    access.revoke(target["id"], root_grant["id"], REASON)
    restored = Store(backup(store, tmp_path, "after-legitimate-succession"))
    before = restored.all("SELECT * FROM users ORDER BY id")
    with pytest.raises(ValueError, match="no usable super administrator"):
        journal.apply(restored, journal.read(store.root, since=now()+86400))
    assert restored.all("SELECT * FROM users ORDER BY id") == before
    assert [u["id"] for u in PlatformAccess(restored).usable_super_admins()] == [target["id"]]
    assert restored.one("SELECT revoked_at FROM platform_role_grants WHERE id=?", (root_grant["id"],))["revoked_at"]
    assert not restored.one("SELECT value FROM meta WHERE key=?", (receipt,))
    assert not restored.all("SELECT * FROM admin_operations")


@pytest.mark.parametrize("kind", ["user_suspended", "user_deleted"])
def test_old_account_withdrawal_refuses_to_remove_the_only_restored_administrator(world, kind):
    store, uid = world["store"], world["root"]["id"]
    before = store.one("SELECT * FROM users WHERE id=?", (uid,))
    with pytest.raises(ValueError, match="no usable super administrator"):
        journal.apply(store, [{"kind": kind, "user_id": uid, "at": now()}])
    assert store.one("SELECT * FROM users WHERE id=?", (uid,)) == before
    assert PlatformAccess(store).is_usable_super_admin(uid)
    assert world["admin"].get("/api/auth/me").status_code == 200


def test_never_bootstrapped_archive_with_zero_administrators_can_restore_and_initialize(tmp_path):
    store = Store(tmp_path / "never-bootstrapped-fixture")
    identity = Identity(store)
    ordinary = identity.create_user("ordinary@example.test", "isolated-fixture-passphrase", status="active")
    assert PlatformAccess(store).usable_super_admins() == []
    assert journal.apply(store, [{"kind": "user_suspended", "user_id": ordinary["id"], "at": now()}])["user_suspended"] == 1
    assert identity.get_user(ordinary["id"])["status"] == "suspended"
    assert identity.bootstrap_needed()
    root = identity.create_user("bootstrap@example.test", "isolated-fixture-passphrase", status="active")
    PlatformAccess(store).host_grant(root["id"], "super_admin", source="bootstrap:cli", reason="Fixture bootstrap")
    assert PlatformAccess(store).is_usable_super_admin(root["id"])
