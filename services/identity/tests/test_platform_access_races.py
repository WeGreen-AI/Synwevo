"""Regression fixtures for delayed platform requests and concurrent session tightening.

Only isolated Store instances are used. Interleaving hooks schedule real grant/revoke writes
between a session read and its touch; they never substitute an authorization result.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from .platform_fixture import legacy_platform_role_row, make_platform_admin
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.platform_access import PlatformAccess, SUPER_ADMIN, require_platform
from synwevo_core.sessions import ADMIN_IDLE_SECONDS, PERSISTENT_IDLE_SECONDS, REAUTH_SECONDS, Sessions
from synwevo_core.store import Store, now

PW = "isolated-administrator-race-fixture-2026"
REASON = "Isolated fixture: exercise delayed authorization"


@pytest.fixture()
def world(tmp_path):
    store = Store(tmp_path / "fixture")
    ident, sessions = Identity(store), Sessions(store)
    root = make_platform_admin(store, ident.create_user("root@example.test", PW, status="active"))
    target = ident.create_user("target@example.test", PW, status="active")
    store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), target["id"]))
    return store, ident, sessions, root, target


def _admitted(sessions, user):
    raw, row = sessions.create(user["id"], workspace_id=None, amr="password")
    sessions.mark_reauth(row["id"], "admin")
    return SimpleNamespace(state=SimpleNamespace(session=sessions.load(raw, touch=False)))


def test_legacy_bootstrap_admin_remains_usable_after_rollback_and_upgrade(tmp_path):
    store = Store(tmp_path / "fixture")
    user = Identity(store).create_user("legacy-bootstrap@example.test", PW, status="active")
    assert not user["email_verified_at"]
    # A v26 bootstrap during a rollback creates a password-bearing admin without verifying mail.
    legacy_platform_role_row(store, user, "admin")
    reopened = Store(store.root)
    access = PlatformAccess(reopened)
    assert access.roles(user["id"]) == [SUPER_ADMIN]
    assert access.is_usable_super_admin(user["id"])
    assert not Identity(reopened).bootstrap_needed()
    assert access.live_grants(user["id"])[0]["granted_by"] == "reconcile:mirror"
    # A second open is idempotent, and preserving host authority must not invent mailbox proof.
    again = Store(store.root)
    assert again.one("SELECT COUNT(*) AS n FROM platform_role_grants")["n"] == 1
    assert not Identity(again).get_user(user["id"])["email_verified_at"]


def test_reopening_does_not_grant_an_unverified_ordinary_account(tmp_path):
    store = Store(tmp_path / "fixture")
    user = Identity(store).create_user("ordinary@example.test", PW, status="active")
    access = PlatformAccess(Store(store.root))
    assert access.effective_permissions(user["id"]) == frozenset()
    assert access.live_grants(user["id"]) == []


def test_delayed_central_session_creation_rechecks_new_platform_grant(world):
    store, _, sessions, root, target = world
    # issue_session read ordinary permissions, then a grant committed before session insertion.
    earlier_admin = bool(PlatformAccess(store).effective_permissions(target["id"]))
    assert earlier_admin is False
    PlatformAccess(store).grant(root["id"], target["id"], "auditor", REASON)
    _, row = sessions.create(target["id"], workspace_id=None, amr="password",
                             admin=earlier_admin, persistent=True)
    assert row["persistent"] == 0
    assert row["idle_expires_at"] - row["last_seen_at"] <= ADMIN_IDLE_SECONDS
    assert sessions.cookie_max_age(row) is None


def test_relying_party_session_keeps_its_own_idle_policy(world):
    store, _, sessions, root, target = world
    PlatformAccess(store).grant(root["id"], target["id"], "auditor", REASON)
    _, row = sessions.create(target["id"], workspace_id=None, amr="password", persistent=True,
                             oidc_sid="fixture-oidc", central_session_id="fixture-central")
    assert row["persistent"] == 1
    assert row["idle_expires_at"] - row["last_seen_at"] == PERSISTENT_IDLE_SECONDS


@pytest.mark.parametrize("role", ["auditor", SUPER_ADMIN])
def test_inflight_session_touch_cannot_undo_a_platform_grants_shorter_idle_window(world, monkeypatch, role):
    store, _, sessions, root, target = world
    raw, row = sessions.create(target["id"], workspace_id=None, amr="password", persistent=True)
    store.execute("UPDATE sessions SET last_seen_at=last_seen_at-600, idle_expires_at=idle_expires_at-600 WHERE id=?",
                  (row["id"],))
    original, interleaved = store.execute, []

    def after_session_read(sql, args=()):
        if sql.startswith("UPDATE sessions SET last_seen_at=") and not interleaved:
            interleaved.append(True)
            if role == SUPER_ADMIN:
                # Host fixture isolates stale session touching; accepted-role idle policy is
                # covered through actual dedicated signatures in test_admin_handover.
                PlatformAccess(store).host_grant(target["id"], role, source="fixture:idle", reason=REASON)
            else:
                PlatformAccess(store).grant(root["id"], target["id"], role, REASON)
        return original(sql, args)

    monkeypatch.setattr(store, "execute", after_session_read)
    loaded = sessions.load(raw)
    assert interleaved == [True]
    persisted = store.one("SELECT * FROM sessions WHERE id=?", (row["id"],))
    assert persisted["persistent"] == 0
    assert persisted["idle_expires_at"] - persisted["last_seen_at"] <= ADMIN_IDLE_SECONDS
    assert loaded["idle_expires_at"] == persisted["idle_expires_at"]
    assert loaded["persistent"] == 0


def test_inflight_session_touch_returns_no_session_when_revocation_wins(world, monkeypatch):
    store, _, sessions, _, target = world
    raw, row = sessions.create(target["id"], workspace_id=None, amr="password")
    store.execute("UPDATE sessions SET last_seen_at=last_seen_at-600, idle_expires_at=idle_expires_at-600 WHERE id=?",
                  (row["id"],))
    original, interleaved = store.execute, []

    def after_session_read(sql, args=()):
        if sql.startswith("UPDATE sessions SET last_seen_at=") and not interleaved:
            interleaved.append(True)
            sessions.revoke(row["id"], "fixture_lost_device")
        return original(sql, args)

    monkeypatch.setattr(store, "execute", after_session_read)
    assert sessions.load(raw) is None
    assert interleaved == [True]


@pytest.mark.parametrize("change", ["revoked", "deleted", "idle", "absolute"])
def test_stale_admission_cannot_authorize_a_write_after_session_ends(world, change):
    store, _, sessions, root, target = world
    request = _admitted(sessions, root)
    sid = request.state.session["id"]
    if change == "revoked":
        sessions.revoke(sid, "fixture_lost_device")
    elif change == "deleted":
        store.execute("DELETE FROM sessions WHERE id=?", (sid,))
    else:
        field = "idle_expires_at" if change == "idle" else "absolute_expires_at"
        store.execute(f"UPDATE sessions SET {field}=? WHERE id=?", (now()-1, sid))
    # The actor still holds the role: it is this sign-in, not their account, that lost authority.
    assert PlatformAccess(store).is_usable_super_admin(root["id"])
    with pytest.raises(ServiceError) as exc:
        with store.tx():
            actor = require_platform(request, "platform.roles.assign", store=store, dangerous=True)
            PlatformAccess(store).grant(actor["id"], target["id"], "support", REASON)
    assert exc.value.code == "unauthorized"
    assert request.state.session is None
    assert PlatformAccess(store).live_grants(target["id"]) == []


@pytest.mark.parametrize("field,value,error", [
    ("reauth_scope", "mfa", "reauth_required"),
    ("reauth_at", "expired", "reauth_required"),
    ("amr", "email", "step_up_required"),
])
def test_platform_authorization_uses_stored_authentication_proof(world, field, value, error):
    store, _, sessions, root, _ = world
    request = _admitted(sessions, root)
    sid = request.state.session["id"]
    value = now()-REAUTH_SECONDS-1 if value == "expired" else value
    store.execute(f"UPDATE sessions SET {field}=? WHERE id=?", (value, sid))
    with pytest.raises(ServiceError) as exc:
        with store.tx():
            require_platform(request, "platform.roles.assign", store=store, dangerous=True)
    assert exc.value.code == error
    assert request.state.session[field] == value


def test_platform_recheck_does_not_touch_an_idle_clock_or_persist_anything(world):
    store, _, sessions, root, _ = world
    request = _admitted(sessions, root)
    sid = request.state.session["id"]
    store.execute("UPDATE sessions SET last_seen_at=last_seen_at-120, idle_expires_at=idle_expires_at-120 WHERE id=?",
                  (sid,))
    before = store.one("SELECT * FROM sessions WHERE id=?", (sid,))
    with store.tx():
        actor = require_platform(request, "platform.roles.assign", store=store, dangerous=True)
    assert actor["id"] == root["id"]
    assert store.one("SELECT * FROM sessions WHERE id=?", (sid,)) == before
    assert request.state.session == before
