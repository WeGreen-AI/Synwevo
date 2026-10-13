"""A44: a workspace deletion survives a restore; a recovery the backup already holds does too.

Real archives (apps/docs/scripts/snapshot.py) of synthetic state in this test's own directory: the
`recovery` fixture of test_security_journal. A deletion is written ahead to the security journal
(`workspace_deleted`) with a receipt in `meta` that commits with it; a restore replays it into a
database without the receipt — deleted again with the original clock, its withdrawals made again —
and leaves alone one that holds it, so a recovery made before the backup stands. Recoveries and
renames are not journalled: restoring a backup taken before the deletion deletes it again — and a
freeze journalled after such a recovery (the platform's, an organization withdrawal's substitute, the
ownerless one) is kept for the restored copy's recovery, which comes back frozen. The credentials
bound to the approvals a deletion revokes end with them, live and on replay.

The platform's freeze is written ahead too (`workspace_frozen` with an `attempt_id`, its receipt in
`meta`): a backup taken between the line and its COMMIT is frozen on restore. An attempt whose
transaction rolled back in this process is voided (`intent_voided`, tests/integration/
test_journal_intents.py); a line nobody voided — the process stopped first — is still replayed.
"""
from __future__ import annotations

import json
import tarfile
from types import SimpleNamespace

import pytest

from synwevo_core import authority, journal, workspace_lifecycle
from synwevo_core.apps import DOCS_APP_ID, Apps
from synwevo_core.auth import Auth
from synwevo_core.identity import Identity
from synwevo_core.organizations import Organizations
from synwevo_core.store import Store, new_id, now
from synwevo_identity.invites import Invites
from tests.integration.test_security_journal import PW, REASON, _login, _post, recovery  # noqa: F401


def _backup_into(w, name):
    """A real archive in its own directory (several per test)."""
    out = w.tmp / name
    assert w.snapshot.create(SimpleNamespace(data=str(w.data), out=str(out))) == 0
    (archive,) = list(out.glob("*.tar.gz"))
    return archive


def _restore_into(w, archive, name):
    into = w.tmp / name
    assert w.snapshot.restore(SimpleNamespace(archive=str(archive), into=str(into), force=False,
                                              journal_from=str(w.data), no_journal=False)) == 0
    return Store(into)


def _delete(w, wid=None):
    return workspace_lifecycle.platform_transition(w.store, wid or w.wid, w.a["id"], "delete", reason=REASON)


def _recover(w, wid=None):
    return workspace_lifecycle.platform_transition(w.store, wid or w.wid, w.a["id"], "recover", reason=REASON)


def _lifecycle(store, wid):
    return store.one("SELECT status, deleted_at, recover_until, previous_status FROM workspaces WHERE id=?", (wid,))


def _entries(w):
    return [e for e in journal.read(w.data) if e["kind"] == journal.WORKSPACE_DELETED_KIND]


def _freeze(w):
    """What the platform's freeze route does (admin_routes._freeze): Identity.set_workspace_frozen
    writes the journal line ahead of the row, with its receipt, in one write transaction."""
    w.ident.set_workspace_frozen(w.wid, True)


def _frozen_lines(w):
    return [e for e in journal.read(w.data) if e["kind"] == journal.WORKSPACE_FROZEN_KIND]


def _status(store, wid):
    return store.one("SELECT status FROM workspaces WHERE id=?", (wid,))["status"]


def _recover_restored(w, restored):
    return workspace_lifecycle.platform_transition(restored, w.wid, w.a["id"], "recover", reason=REASON)


@pytest.fixture()
def withdrawable(recovery):
    """What a deletion withdraws, in the workspace before the backup: a pending invitation and an
    approved API credential request (FIXTURE row; the API platform's own flow is not under test)."""
    w = recovery
    invitation, _ = Invites(w.store, w.ident).create(workspace_id=w.wid, email="invitee@example.test", role="viewer",
                                                     actor_id=w.a["id"])
    ts = now()
    w.store.insert("api_platform_approvals", {"id": "a44-restore-approval", "workspace_id": w.wid,
                                              "requested_by": w.a["id"], "name": "approval fixture",
                                              "credential_kind": "api_key", "requested_scopes": "[]",
                                              "requested_resources": "[]", "status": "approved", "reason": REASON,
                                              "created_at": ts, "expires_at": ts + 3600})
    w.invitation = invitation["id"]
    return w


def test_a_restore_from_before_the_deletion_deletes_it_again_with_the_original_clock(withdrawable):
    w = withdrawable
    archive = _backup_into(w, "before")
    out = _delete(w)
    live = _lifecycle(w.store, w.wid)
    assert live["status"] == "deleted" and out["withdrawn"]["invitations"] == 1 and out["withdrawn"]["approvals"] == 1
    (entry,) = _entries(w)
    restored = _restore_into(w, archive, "restored")
    try:
        assert _lifecycle(restored, w.wid) == live, "deleted again, with the original clock and previous status"
        assert restored.one("SELECT revoked_at FROM invitations WHERE id=?", (w.invitation,))["revoked_at"]
        assert restored.one("SELECT status FROM api_platform_approvals WHERE id='a44-restore-approval'")["status"] == "revoked"
        assert restored.one("SELECT 1 FROM meta WHERE key=?", ("workspace_withdrawal:" + entry["event_id"],))
        ident = Identity(restored)
        assert ident.get_workspace(w.wid) is None
        assert w.wid not in [x["id"] for x in ident.workspaces_for(w.b["id"])]
        assert not Apps(restored, ident).check_access(w.b["id"], DOCS_APP_ID, w.wid)[0]
        assert restored.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=?", (w.wid,))["n"] == 2, "data kept"
        # replaying again changes nothing: the receipt is there now
        assert journal.apply(restored, _entries(w)) == {journal.WORKSPACE_DELETED_KIND: 0}
        assert _lifecycle(restored, w.wid) == live
    finally:
        w.snapshot._close(restored)


def test_a_backup_taken_after_delete_and_recover_keeps_the_recovery(withdrawable):
    w = withdrawable
    _delete(w)
    _recover(w)
    assert _lifecycle(w.store, w.wid)["status"] == "active"
    restored = _restore_into(w, _backup_into(w, "after"), "restored")
    try:
        assert _lifecycle(restored, w.wid) == {"status": "active", "deleted_at": None, "recover_until": None,
                                               "previous_status": None}
        assert Identity(restored).get_workspace(w.wid)["status"] == "active"
    finally:
        w.snapshot._close(restored)


def test_a_backup_older_than_delete_and_recover_deletes_it_again(withdrawable):
    """The recovery is not journalled (the result's `recover_not_journalled`): an older backup
    brings the deletion back, with its first clock — recovered again only while that window lasts."""
    w = withdrawable
    archive = _backup_into(w, "before")
    _delete(w)
    deleted = _lifecycle(w.store, w.wid)
    _recover(w)
    restored = _restore_into(w, archive, "restored")
    try:
        assert _lifecycle(restored, w.wid) == deleted
        assert _lifecycle(w.store, w.wid)["status"] == "active", "the live instance is untouched"
    finally:
        w.snapshot._close(restored)


def test_a_newer_deletion_is_never_replaced_and_the_clock_never_restarts(withdrawable):
    """Deleted, recovered and deleted again before the backup: the first entry has its receipt and
    the second is already in the backup — nothing moves. Replaying the first entry into a database
    holding only the newer deletion (receipts removed: FIXTURE) keeps the newer clock."""
    w = withdrawable
    _delete(w)
    _recover(w)
    _delete(w)
    second = _lifecycle(w.store, w.wid)
    restored = _restore_into(w, _backup_into(w, "after"), "restored")
    try:
        assert _lifecycle(restored, w.wid) == second
        restored.execute("DELETE FROM meta WHERE key LIKE 'workspace_withdrawal:%'")
        first, newer = _entries(w)
        assert journal.apply(restored, [first]) == {journal.WORKSPACE_DELETED_KIND: 0}
        assert _lifecycle(restored, w.wid) == second
        assert journal.apply(restored, [newer]) == {journal.WORKSPACE_DELETED_KIND: 0}, "same clock: not replaced"
    finally:
        w.snapshot._close(restored)


def test_a_restored_frozen_workspace_is_recovered_frozen(withdrawable):
    """The backup holds the workspace frozen; it was unfrozen (not journalled) and deleted from
    active afterwards. Replayed, the deletion keeps `previous_status='frozen'`: a recovery must not
    thaw what the restored database had frozen."""
    w = withdrawable
    w.ident.set_workspace_frozen(w.wid, True)
    archive = _backup_into(w, "frozen")
    w.ident.set_workspace_frozen(w.wid, False)
    _delete(w)
    assert _lifecycle(w.store, w.wid)["previous_status"] == "active"
    restored = _restore_into(w, archive, "restored")
    try:
        assert _lifecycle(restored, w.wid)["status"] == "deleted"
        assert _lifecycle(restored, w.wid)["previous_status"] == "frozen"
        out = workspace_lifecycle.platform_transition(restored, w.wid, w.a["id"], "recover", reason=REASON)
        assert out["workspace"]["status"] == "frozen"
    finally:
        w.snapshot._close(restored)


@pytest.mark.parametrize("backup_at", ["before_the_deletion", "while_deleted"])
def test_a_freeze_after_a_recovery_survives_a_restore_that_deletes_again(withdrawable, backup_at):
    """A44-J1 / A44-2: backup, delete, recover, freeze (journalled); restore. The restored row is
    deleted again — replayed, or already in a backup taken while deleted — and the freeze entry,
    which only an active row could have taken, marks it to come back frozen: the recovery of the
    restored copy must not undo the platform's stop switch. The freeze entry is applied once."""
    w = withdrawable
    archive = _backup_into(w, "before") if backup_at == "before_the_deletion" else None
    _delete(w)
    deleted = _lifecycle(w.store, w.wid)
    archive = archive or _backup_into(w, "deleted")
    _recover(w)
    _freeze(w)
    assert _lifecycle(w.store, w.wid)["status"] == "frozen"
    restored = _restore_into(w, archive, "restored")
    try:
        assert _lifecycle(restored, w.wid) == {**deleted, "previous_status": "frozen"}
        assert journal.apply(restored, journal.read(w.data)).get("workspace_frozen") == 0, "applied once"
        assert _recover_restored(w, restored)["workspace"]["status"] == "frozen"
        assert not Apps(restored, Identity(restored)).check_access(w.b["id"], DOCS_APP_ID, w.wid)[0]
    finally:
        w.snapshot._close(restored)


def test_an_organization_withdrawal_keeps_a_deleted_unattached_workspace_frozen(withdrawable):
    """A44-J1 (P6): the backup holds the workspace before its organization adopted it; afterwards
    the workspace and then its organization were deleted. Restored, the workspace is deleted again
    and has no organization link, so the organization's replay substitutes a freeze — kept for the
    recovery, which in the live instance stays refused by the deleted organization. The order of
    the two deletions no longer matters."""
    w = withdrawable
    archive = _backup_into(w, "before-adoption")
    orgs = Organizations(w.store, w.ident)
    oid = orgs.create(w.a["id"], "Restore organization", "Restore organization workspace",
                      "a44-restore-org-0001")["organization"]["id"]
    orgs.adopt_workspace(oid, w.a["id"], w.wid, reason=REASON)
    _delete(w)
    orgs.platform_transition(oid, w.a["id"], "delete", reason=REASON)
    restored = _restore_into(w, archive, "restored")
    try:
        assert restored.one("SELECT organization_id FROM workspaces WHERE id=?", (w.wid,))["organization_id"] is None
        assert _lifecycle(restored, w.wid)["previous_status"] == "frozen"
        assert _recover_restored(w, restored)["workspace"]["status"] == "frozen"
        assert not Apps(restored, Identity(restored)).check_access(w.b["id"], DOCS_APP_ID, w.wid)[0]
    finally:
        w.snapshot._close(restored)


def test_the_ownerless_freeze_of_a_replayed_demotion_keeps_a_deleted_workspace_frozen(withdrawable):
    """A44-J1, the third replayed freeze: a demotion leaves the deleted workspace without a usable
    owner (the promotion of a successor is not journalled), so its recovery comes back frozen."""
    w = withdrawable
    _delete(w)
    out = journal.apply(w.store, [{"at": now(), "kind": "role_changed", "workspace_id": w.wid,
                                   "user_id": w.a["id"], "role": "viewer"}])
    assert out["role_changed_workspaces_frozen"] == 1
    assert _lifecycle(w.store, w.wid)["status"] == "deleted" and _lifecycle(w.store, w.wid)["previous_status"] == "frozen"
    assert _recover(w)["workspace"]["status"] == "frozen"


def test_the_credentials_bound_to_a_revoked_approval_end_live_and_on_replay(withdrawable):
    """A44-ACC-1 / A44-1: a deletion revokes the workspace's approvals and ends the credentials bound
    to them as `api_platform.revoke_approval` would — the key's lineage revoked, the service account
    disabled — so stored state matches what is refused. Restoring a backup from before the deletion
    makes the same withdrawals; a historical (unbound) key stays paused, not revoked."""
    w = withdrawable
    auth = Auth(w.store)
    _, bound_key = auth.create_key("Bound fixture key", w.wid, ("read",))
    _, historical = auth.create_key("Historical fixture key", w.wid, ("read",))
    ts = now()
    # FIXTURE rows as the console's issue leaves them: the approval bound to the key, and a service
    # account bound to a second approval.
    w.store.insert("api_platform_approvals", {"id": "a44-restore-service-approval", "workspace_id": w.wid,
                                              "requested_by": w.a["id"], "name": "approval fixture",
                                              "credential_kind": "service_account", "requested_scopes": "[]",
                                              "requested_resources": "[]", "status": "approved", "reason": REASON,
                                              "created_at": ts, "expires_at": ts + 3600})
    w.store.insert("oauth_clients", {"client_id": "a44-restore-service", "app_id": DOCS_APP_ID,
                                     "name": "service fixture", "redirect_uris": "[]", "kind": "service",
                                     "workspace_id": w.wid, "created_at": ts, "updated_at": ts})
    for kind, credential, aid in (("api_key", bound_key["lineage"], "a44-restore-approval"),
                                  ("service_account", "a44-restore-service", "a44-restore-service-approval")):
        w.store.insert("api_platform_bindings", {"kind": kind, "credential_id": credential, "approval_id": aid,
                                                 "created_at": ts})
    archive = _backup_into(w, "before")

    def ended(store):
        key = store.one("SELECT revoked_at, revoke_reason FROM api_keys WHERE id=?", (bound_key["id"],))
        kept = store.one("SELECT revoked_at FROM api_keys WHERE id=?", (historical["id"],))
        client = store.one("SELECT disabled_at FROM oauth_clients WHERE client_id='a44-restore-service'")
        return bool(key["revoked_at"]), key["revoke_reason"], bool(client["disabled_at"]), bool(kept["revoked_at"])

    assert _delete(w)["withdrawn"]["approvals"] == 2
    assert ended(w.store) == (True, "approval_revoked", True, False)
    desc = authority.describe({"kind": "api_key", "key_id": bound_key["id"], "lineage": bound_key["lineage"],
                               "tenant": w.wid})
    restored = _restore_into(w, archive, "restored")
    try:
        assert ended(restored) == (True, "approval_revoked", True, False)
        assert restored.one("SELECT status FROM api_platform_approvals WHERE id='a44-restore-service-approval'")[
            "status"] == "revoked"
        _recover_restored(w, restored)
        assert authority.check(restored, desc, need="read", tenant=w.wid)[0] is False
    finally:
        w.snapshot._close(restored)


def test_an_entry_whose_transaction_rolled_back_in_this_process_is_voided(withdrawable):
    """Write-ahead: the entry reached the journal, then the transaction rolled back. The rollback
    proves the attempt never committed (a later committed transaction found no receipt) and voids
    it: a restore — from a backup taken before the attempt or after it — leaves the workspace
    active."""
    w = withdrawable
    before = _backup_into(w, "before")
    ts = now()
    with pytest.raises(RuntimeError):
        with w.store.tx():
            journal.record_workspace(w.store, journal.WORKSPACE_DELETED_KIND, workspace_id=w.wid, deleted_at=ts,
                                     recover_until=ts + workspace_lifecycle.RECOVERY_SECONDS, previous_status="active")
            raise RuntimeError("synthetic rollback after the durable append")
    assert _lifecycle(w.store, w.wid)["status"] == "active"
    (entry,) = _entries(w)
    assert not w.store.one("SELECT 1 FROM meta WHERE key=?", ("workspace_withdrawal:" + entry["event_id"],))
    (void,) = [e for e in journal.read(w.data) if e["kind"] == journal.VOID_KIND]
    assert void["attempt_id"] == entry["attempt_id"] and void["voided_kind"] == journal.WORKSPACE_DELETED_KIND
    assert w.store.one("SELECT value FROM meta WHERE key=?", ("journal_void:" + entry["attempt_id"],))["value"] \
        == journal.WORKSPACE_DELETED_KIND
    for name, archive in (("from-before", before), ("from-after", _backup_into(w, "after"))):
        restored = _restore_into(w, archive, name)
        try:
            assert _lifecycle(restored, w.wid)["status"] == "active", name
            assert restored.one("SELECT revoked_at FROM invitations WHERE id=?", (w.invitation,))["revoked_at"] is None
        finally:
            w.snapshot._close(restored)


def test_an_entry_nobody_voided_is_replayed(withdrawable):
    """The process stopped between the line and its COMMIT (FIXTURE: the line as `_intent` appends
    it, written with no transaction behind it, so no rollback voids it). The backup holds neither
    the deletion nor the receipt and the entry is older than the backup — replayed anyway, failing
    closed, with the attempt's clock."""
    w = withdrawable
    ts = now()
    journal.record(w.store, journal.WORKSPACE_DELETED_KIND, attempt_id=new_id("ja"), event_id=new_id("wd"),
                   workspace_id=w.wid, deleted_at=ts, recover_until=ts + workspace_lifecycle.RECOVERY_SECONDS,
                   previous_status="active")
    assert _lifecycle(w.store, w.wid)["status"] == "active"
    restored = _restore_into(w, _backup_into(w, "after"), "restored")
    try:
        assert _lifecycle(restored, w.wid) == {"status": "deleted", "deleted_at": ts,
                                               "recover_until": ts + workspace_lifecycle.RECOVERY_SECONDS,
                                               "previous_status": "active"}
        assert restored.one("SELECT revoked_at FROM invitations WHERE id=?", (w.invitation,))["revoked_at"]
    finally:
        w.snapshot._close(restored)


def test_a_backup_from_before_the_workspace_existed_is_left_alone(recovery):
    w = recovery
    archive = _backup_into(w, "before")
    later = w.ident.create_workspace("Created after the backup", owner_id=w.a["id"])["id"]
    _delete(w, later)
    restored = _restore_into(w, archive, "restored")
    try:
        assert restored.one("SELECT 1 FROM workspaces WHERE id=?", (later,)) is None
        assert _lifecycle(restored, w.wid)["status"] == "active", "another workspace is not touched"
        (entry,) = _entries(w)
        assert restored.one("SELECT 1 FROM meta WHERE key=?", ("workspace_withdrawal:" + entry["event_id"],))
    finally:
        w.snapshot._close(restored)


def test_a_replayed_entry_never_touches_the_historical_workspace(tmp_path):
    """Live deletion refuses the legacy workspace; a hand-written entry naming it (FIXTURE) changes
    nothing on replay — its lifecycle is claim/unclaim."""
    store = Store(tmp_path)
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES ('default','历史数据','legacy','active',1,1)")
    entry = {"at": 2.0, "kind": journal.WORKSPACE_DELETED_KIND, "event_id": "wd_fixture", "workspace_id": "default",
             "deleted_at": 2.0, "recover_until": 2.0 + 7 * 24 * 3600, "previous_status": "active"}
    assert journal.apply(store, [entry]) == {journal.WORKSPACE_DELETED_KIND: 0}
    assert _lifecycle(store, "default") == {"status": "active", "deleted_at": None, "recover_until": None,
                                            "previous_status": None}


@pytest.mark.parametrize("bad", [
    {"recover_until": 1.0 + 3600},
    {"previous_status": "deleted"},
    {"previous_status": None},
    {"deleted_at": "1"},
    {"deleted_at": float("nan")},
    {"event_id": ""},
    {"workspace_id": None},
])
def test_a_malformed_workspace_entry_refuses_the_restore(recovery, bad):
    w = recovery
    archive = _backup_into(w, "before")
    entry = {"at": now(), "kind": journal.WORKSPACE_DELETED_KIND, "event_id": "wd_fixture", "workspace_id": w.wid,
             "deleted_at": 1.0, "recover_until": 1.0 + 7 * 24 * 3600, "previous_status": "active", **bad}
    entry = {k: v for k, v in entry.items() if v is not None}
    journal.path(w.data).write_text(json.dumps(entry) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="journal.*line 1"):
        journal.read(w.data)
    with pytest.raises(ValueError, match="journal"):
        w.snapshot.restore(SimpleNamespace(archive=str(archive), into=str(w.tmp / "restored"), force=False,
                                           journal_from=str(w.data), no_journal=False))
    assert not (w.tmp / "restored" / w.snapshot.DB_NAME).exists()


def test_the_console_deletion_is_what_a_restore_replays(recovery):
    """End to end through the console: preview, confirmation, journal entry, restore."""
    from tests.integration.platform_fixture import make_platform_admin
    w = recovery
    root = make_platform_admin(w.store, w.ident.create_user("root@example.test", PW, status="active",
                                                            with_personal_workspace=False))
    archive = _backup_into(w, "before")
    admin = _login(w.api, root["email"])
    assert _post(admin, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    preview = _post(admin, f"/api/admin/workspaces/{w.wid}/lifecycle/preview", {"action": "delete"})
    assert preview.status_code == 200, preview.text
    out = _post(admin, f"/api/admin/workspaces/{w.wid}/lifecycle/delete",
                {"reason": REASON, "preview_token": preview.json()["preview_token"],
                 "idempotency_key": "a44_restore_console_0001"})
    assert out.status_code == 200, out.text
    deleted = out.json()["workspace"]
    restored = _restore_into(w, archive, "restored")
    try:
        assert _lifecycle(restored, w.wid) == {k: deleted[k] for k in ("status", "deleted_at", "recover_until",
                                                                        "previous_status")}
    finally:
        w.snapshot._close(restored)


# ---------------------------------------------------------------- the platform's freeze, written ahead
def _snapshot_at(archive) -> float:
    with tarfile.open(archive) as tar:
        return json.load(tar.extractfile("manifest.json"))["snapshot_at"]


def test_a_backup_taken_between_the_freeze_line_and_its_commit_is_frozen_on_restore(recovery):
    """The window the write-ahead line closes: the backup reads the database while the freeze's
    transaction is still open — after its line, before its COMMIT — so it holds neither the freeze
    nor its receipt, and the backup's time is later than the line's. Replayed anyway (the line has
    an `attempt_id`), and its receipt is written with it."""
    w = recovery
    with w.store.tx():
        w.ident.set_workspace_frozen(w.wid, True)
        archive = _backup_into(w, "mid")
    assert _status(w.store, w.wid) == "frozen"
    (line,) = _frozen_lines(w)
    assert line["at"] < _snapshot_at(archive), "older than the backup: the time cutoff alone would drop it"
    assert w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],))
    restored = _restore_into(w, archive, "restored")
    try:
        assert _status(restored, w.wid) == "frozen"
        assert restored.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],))
        assert not Apps(restored, Identity(restored)).check_access(w.b["id"], DOCS_APP_ID, w.wid)[0]
    finally:
        w.snapshot._close(restored)


def test_a_backup_holding_the_freeze_keeps_a_later_unfreeze(recovery):
    """Freeze, unfreeze (not journalled), back up: the backup holds the receipt, so the line is
    skipped and the unfreeze stands."""
    w = recovery
    _freeze(w)
    w.ident.set_workspace_frozen(w.wid, False)
    restored = _restore_into(w, _backup_into(w, "after"), "restored")
    try:
        assert _status(restored, w.wid) == "active"
        assert journal.apply(restored, _frozen_lines(w)) == {"workspace_frozen": 0}
        assert _status(restored, w.wid) == "active"
    finally:
        w.snapshot._close(restored)


def test_a_backup_from_before_the_freeze_is_frozen_once(recovery):
    w = recovery
    archive = _backup_into(w, "before")
    _freeze(w)
    restored = _restore_into(w, archive, "restored")
    try:
        assert _status(restored, w.wid) == "frozen"
        assert journal.apply(restored, journal.read(w.data)) == {"workspace_frozen": 0}, "applied once: its receipt"
    finally:
        w.snapshot._close(restored)


def test_a_freeze_line_without_an_attempt_id_keeps_the_time_cutoff(recovery):
    """Lines written before this release (FIXTURE: appended by hand, without `attempt_id` or a
    receipt) keep today's rule on both sides of the backup's time: one older than the backup is
    already in it and is not replayed; one newer is."""
    w = recovery
    other = w.ident.create_workspace("Second fixture workspace", owner_id=w.a["id"])["id"]
    journal.record(w.store, "workspace_frozen", workspace_id=w.wid)
    archive = _backup_into(w, "between")
    journal.record(w.store, "workspace_frozen", workspace_id=other)
    older, newer = _frozen_lines(w)
    assert "attempt_id" not in older and older["at"] < _snapshot_at(archive) < newer["at"]
    restored = _restore_into(w, archive, "restored")
    try:
        assert _status(restored, w.wid) == "active", "older than the backup: filtered out by time"
        assert _status(restored, other) == "frozen", "newer than the backup: replayed"
    finally:
        w.snapshot._close(restored)


def test_the_freeze_route_refuses_a_line_it_cannot_make_durable_and_voids_it(recovery, monkeypatch):
    """The directory fsync fails after the line reached the journal: 503, nothing frozen, no audit
    record — and the attempt is voided before the answer, so no restore freezes the workspace
    from it. The answer still does not say 操作未提交 (a void that could not be written would leave
    the line for a restore to apply). The retry freezes and journals as usual."""
    from tests.integration.platform_fixture import make_platform_admin
    w = recovery
    root = make_platform_admin(w.store, w.ident.create_user("root@example.test", PW, status="active",
                                                            with_personal_workspace=False))
    admin = _login(w.api, root["email"])
    assert _post(admin, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    original, calls = journal._fsync_dir, []

    def fails_once(directory):
        calls.append(directory)
        if len(calls) == 1:
            raise OSError("fixture: directory fsync failed after the append")
        return original(directory)
    monkeypatch.setattr(journal, "_fsync_dir", fails_once)
    r = _post(admin, f"/api/admin/workspaces/{w.wid}/freeze", {"reason": REASON})
    monkeypatch.setattr(journal, "_fsync_dir", original)
    assert r.status_code == 503 and r.json()["error"] == "journal_unavailable", r.text
    assert "fixture" not in r.text
    # Not 操作未提交: the line was written, and were its void not proven, a restore would apply it.
    from synwevo_identity.admin_routes import FREEZE_JOURNAL_UNAVAILABLE
    message = r.json()["message"]
    assert message == FREEZE_JOURNAL_UNAVAILABLE and "备份" in message and "未提交" not in message
    assert _status(w.store, w.wid) == "active"
    assert not w.store.one("SELECT 1 FROM audit_events WHERE action='admin.workspace.freeze'")
    (line,) = _frozen_lines(w)
    assert not w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],))
    (void,) = [e for e in journal.read(w.data) if e["kind"] == journal.VOID_KIND]
    assert (void["attempt_id"], void["voided_kind"]) == (line["attempt_id"], "workspace_frozen")
    assert w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_void:" + line["attempt_id"],))
    archive = _backup_into(w, "after")
    restored = _restore_into(w, archive, "restored")
    try:
        assert _status(restored, w.wid) == "active"
    finally:
        w.snapshot._close(restored)
    r = _post(admin, f"/api/admin/workspaces/{w.wid}/freeze", {"reason": REASON})
    assert r.status_code == 200 and r.json()["journaled"] is True, r.text
    failed, retry = _frozen_lines(w)
    assert failed == line and w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + retry["attempt_id"],))
    assert w.store.one("SELECT 1 FROM audit_events WHERE action='admin.workspace.freeze' AND result='ok'")
    # The unfreeze's note also names the attempt nobody voided (the process stopped first): it
    # freezes the workspace on a restore from any backup, a later one included.
    from synwevo_identity.admin_routes import UNFREEZE_RESTORE_NOTE
    r = _post(admin, f"/api/admin/workspaces/{w.wid}/unfreeze", {"reason": REASON})
    assert r.status_code == 200 and r.json()["restore_note"] == UNFREEZE_RESTORE_NOTE, r.text
    assert "早于此刻的备份" in UNFREEZE_RESTORE_NOTE and "冻结尝试" in UNFREEZE_RESTORE_NOTE
