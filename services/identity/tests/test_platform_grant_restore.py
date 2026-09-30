"""Isolated SQLite recovery fixtures: a backup must not resurrect a revoked platform role."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from .platform_fixture import legacy_platform_role_row, make_platform_admin
from synwevo_core import journal
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.platform_access import PlatformAccess, SUPER_ADMIN
from synwevo_core.store import Store, now

PW = "isolated-platform-recovery-fixture-passphrase"
REASON = "Isolated fixture: administrator handover completed"


@pytest.fixture()
def world(tmp_path):
    store = Store(tmp_path / "live-fixture")
    ident = Identity(store)
    root = make_platform_admin(store, ident.create_user("root@example.test", PW, status="active"))
    target = ident.create_user("target@example.test", PW, status="active")
    store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), target["id"]))
    return store, root, target


def backup(store, tmp_path, name):
    """Fixture snapshot using SQLite's online backup; never points at live application data."""
    folder = tmp_path / name
    folder.mkdir()
    with store.conn() as source:
        dest = sqlite3.connect(folder / store.db_path.name)
        try:
            source.backup(dest)
        finally:
            dest.close()
    return folder


def grant(world, role="support"):
    store, root, target = world
    return PlatformAccess(store).grant(root["id"], target["id"], role, REASON)["grant"]


def revoke(world, row):
    store, root, _ = world
    return PlatformAccess(store).revoke(root["id"], row["id"], REASON)


def entries(store):
    return [e for e in journal.read(store.root, since=now()+86400) if e["kind"] == journal.PLATFORM_GRANT_KIND]


def test_restoring_an_older_backup_removes_only_the_revoked_grant(world, tmp_path):
    store, _, target = world
    withdrawn = grant(world)
    retained = grant(world, "auditor")
    folder = backup(store, tmp_path, "before-withdrawal")
    revoke(world, withdrawn)
    event = entries(store)[0]
    assert event["grant_id"] == withdrawn["id"]
    assert event["role"] == "support" and event["user_id"] == target["id"]
    serialized = json.dumps(event)
    assert PW not in serialized and target["email"] not in serialized and REASON not in serialized
    restored = Store(folder)
    assert journal.apply(restored, [event])[journal.PLATFORM_GRANT_KIND] == 1
    assert PlatformAccess(restored).roles(target["id"]) == ["auditor"]
    assert PlatformAccess(restored).grant_row(retained["id"])["revoked_at"] is None
    assert Identity(restored).get_user(target["id"])["status"] == "active"
    count = restored.one("SELECT COUNT(*) AS n FROM audit_events")["n"]
    assert journal.apply(restored, [event])[journal.PLATFORM_GRANT_KIND] == 0
    assert restored.one("SELECT COUNT(*) AS n FROM audit_events")["n"] == count
    assert PlatformAccess(Store(folder)).roles(target["id"]) == ["auditor"]


def test_super_admin_restore_keeps_the_rollback_mirror_consistent(world, tmp_path):
    store, root, target = world
    withdrawn = grant(world, SUPER_ADMIN)
    grant(world, "support")
    folder = backup(store, tmp_path, "two-admins")
    revoke(world, withdrawn)
    restored = Store(folder)
    journal.apply(restored, entries(store))
    assert Identity(restored).get_user(target["id"])["platform_role"] == "none"
    assert PlatformAccess(restored).roles(target["id"]) == ["support"]
    assert [u["id"] for u in PlatformAccess(restored).usable_super_admins()] == [root["id"]]
    assert PlatformAccess(Store(folder)).roles(target["id"]) == ["support"]


def test_a_v26_backup_cannot_recreate_the_revoked_highest_administrator(world, tmp_path):
    store, root, target = world
    withdrawn = grant(world, SUPER_ADMIN)
    folder = backup(store, tmp_path, "legacy-v26")
    legacy = Store(folder)
    with legacy.tx() as c:
        c.execute("DROP TABLE platform_role_grants")
        c.execute("DELETE FROM meta WHERE key='platform_grants.migrated'")
        c.execute("UPDATE meta SET value='26' WHERE key='schema_version'")
    revoke(world, withdrawn)
    restored = Store(folder)
    recreated = PlatformAccess(restored).live_grants(target["id"])[0]
    assert recreated["id"] != withdrawn["id"] and recreated["granted_by"] == "migration:v27"
    journal.apply(restored, entries(store))
    assert PlatformAccess(restored).roles(target["id"]) == []
    assert Identity(restored).get_user(target["id"])["platform_role"] == "none"
    assert PlatformAccess(Store(folder)).is_usable_super_admin(root["id"])


def test_regrant_in_a_later_backup_survives_even_when_all_journal_lines_are_read(world, tmp_path):
    store, _, target = world
    old = grant(world, SUPER_ADMIN)
    revoke(world, old)
    newer = grant(world, SUPER_ADMIN)
    folder = backup(store, tmp_path, "after-deliberate-regrant")
    restored = Store(folder)
    journal.apply(restored, entries(store))
    assert PlatformAccess(restored).grant_row(newer["id"])["revoked_at"] is None
    assert PlatformAccess(restored).is_usable_super_admin(target["id"])


def test_precise_grant_id_also_preserves_regrant_without_the_receipt(world, tmp_path):
    store, _, target = world
    old = grant(world, SUPER_ADMIN)
    revoke(world, old)
    newer = grant(world, SUPER_ADMIN)
    folder = backup(store, tmp_path, "missing-receipt-fixture")
    restored = Store(folder)
    restored.execute("DELETE FROM meta WHERE key LIKE 'platform_withdrawal:%'")
    journal.apply(restored, entries(store))
    assert PlatformAccess(restored).grant_row(newer["id"])["revoked_at"] is None
    assert PlatformAccess(restored).is_usable_super_admin(target["id"])


def test_later_legacy_regrant_is_not_confused_with_an_old_migration_grant(world, tmp_path):
    store, _, target = world
    old = grant(world, SUPER_ADMIN)
    revoke(world, old)
    event = entries(store)[0]
    folder = backup(store, tmp_path, "later-legacy-regrant")
    legacy = Store(folder)
    with legacy.tx() as c:
        c.execute("DROP TABLE platform_role_grants")
        c.execute("DELETE FROM meta WHERE key='platform_grants.migrated' OR key LIKE 'platform_withdrawal:%'")
        # The old version's set_platform_role writes updated_at with its deliberate promotion.
        c.execute("UPDATE users SET platform_role='admin',updated_at=? WHERE id=?", (event["at"]+1, target["id"]))
    restored = Store(folder)
    journal.apply(restored, [event])
    assert PlatformAccess(restored).is_usable_super_admin(target["id"])


def test_snapshot_missing_the_authorized_successor_is_refused_atomically(tmp_path):
    store = Store(tmp_path / "live")
    ident = Identity(store)
    former = make_platform_admin(store, ident.create_user("former@example.test", PW, status="active"))
    former_grant = PlatformAccess(store).live_grants(former["id"])[0]
    folder = backup(store, tmp_path, "before-successor")
    successor = make_platform_admin(store, ident.create_user("successor@example.test", PW, status="active"))
    PlatformAccess(store).revoke(successor["id"], former_grant["id"], REASON)
    restored = Store(folder)
    before = restored.one("SELECT COUNT(*) AS n FROM audit_events")["n"]
    with pytest.raises(ValueError, match="no usable super administrator"):
        journal.apply(restored, entries(store))
    assert PlatformAccess(restored).grant_row(former_grant["id"])["revoked_at"] is None
    assert restored.one("SELECT COUNT(*) AS n FROM audit_events")["n"] == before
    assert not restored.one("SELECT 1 FROM meta WHERE key LIKE 'platform_withdrawal:%'")


def test_write_ahead_intent_survives_a_failed_commit_and_ignores_backup_time_cutoff(world, tmp_path):
    store, _, target = world
    old = grant(world)
    folder = backup(store, tmp_path, "before-commit-fixture")
    with pytest.raises(RuntimeError, match="fixture commit failure"):
        with store.tx():
            revoke(world, old)
            raise RuntimeError("fixture commit failure")
    assert PlatformAccess(store).grant_row(old["id"])["revoked_at"] is None
    assert not store.one("SELECT 1 FROM meta WHERE key LIKE 'platform_withdrawal:%'")
    assert len(entries(store)) == 1
    restored = Store(folder)
    journal.apply(restored, entries(store))
    assert PlatformAccess(restored).roles(target["id"]) == []


def test_journal_failure_aborts_grant_revocation_and_its_audit(world, monkeypatch):
    store, _, target = world
    old = grant(world, SUPER_ADMIN)
    before = store.one("SELECT COUNT(*) AS n FROM audit_events")["n"]
    def unavailable(*args, **kwargs):
        raise OSError("fixture journal unavailable")
    monkeypatch.setattr(journal, "record", unavailable)
    with pytest.raises(OSError, match="fixture journal unavailable"):
        revoke(world, old)
    assert PlatformAccess(store).grant_row(old["id"])["revoked_at"] is None
    assert Identity(store).get_user(target["id"])["platform_role"] == "admin"
    assert store.one("SELECT COUNT(*) AS n FROM audit_events")["n"] == before
    assert entries(store) == []


def test_audit_failure_does_not_leave_a_replayable_withdrawal(world):
    store, root, _ = world
    old = grant(world)
    def unavailable(*args, **kwargs):
        raise RuntimeError("fixture audit unavailable")
    with pytest.raises(RuntimeError, match="fixture audit unavailable"):
        PlatformAccess(store, audit=unavailable).revoke(root["id"], old["id"], REASON)
    assert PlatformAccess(store).grant_row(old["id"])["revoked_at"] is None
    assert entries(store) == []


def test_denied_multi_role_self_demotion_has_no_durable_partial_intent(world):
    store, _, target = world
    make_platform_admin(store, target, role="support")
    make_platform_admin(store, target, role=SUPER_ADMIN)
    with pytest.raises(ServiceError) as exc:
        PlatformAccess(store).revoke_all(target["id"], target["id"], REASON)
    assert exc.value.code == "self_assignment"
    assert set(PlatformAccess(store).roles(target["id"])) == {"support", SUPER_ADMIN}
    assert entries(store) == []


def test_rollback_mirror_demotion_also_records_its_platform_withdrawal(world):
    store, _, target = world
    old = grant(world, SUPER_ADMIN)
    legacy_platform_role_row(store, target, "none")
    reopened = Store(store.root)
    assert PlatformAccess(reopened).roles(target["id"]) == []
    assert entries(reopened)[0]["grant_id"] == old["id"]


@pytest.mark.parametrize("changed", [
    {"role": "unknown-role"}, {"granted_at": float("nan")}, {"granted_at": -1},
    {"granted_at": float("inf")}, {"event_id": ""}, {"grant_id": ""},
])
def test_invalid_platform_withdrawal_refuses_replay_before_any_change(world, tmp_path, changed):
    store, _, _ = world
    old = grant(world)
    folder = backup(store, tmp_path, "bad-entry-fixture")
    revoke(world, old)
    bad = {**copy.deepcopy(entries(store)[0]), **changed}
    restored = Store(folder)
    with pytest.raises(ValueError, match="invalid security journal"):
        journal.apply(restored, [bad])
    assert PlatformAccess(restored).grant_row(old["id"])["revoked_at"] is None


def test_event_cannot_redirect_an_existing_grant_id_to_another_account(world, tmp_path):
    store, root, _ = world
    old = grant(world)
    folder = backup(store, tmp_path, "wrong-subject-fixture")
    revoke(world, old)
    bad = {**entries(store)[0], "user_id": root["id"]}
    restored = Store(folder)
    with pytest.raises(ValueError, match="does not match"):
        journal.apply(restored, [bad])
    assert PlatformAccess(restored).grant_row(old["id"])["revoked_at"] is None
