"""Write-ahead security-journal intents whose transaction failed: voided only when proven.

A line written ahead of its SQL (journal._intent) gets a fresh `attempt_id` and a receipt in `meta`
that commits with the change. When that transaction does not commit, its rollback queues a void
(Store.after_rollback): a later write transaction that commits finds the receipt absent and writes
`meta['journal_void:'+attempt_id]`, then an `intent_voided` line. A restore skips an attempt either
names. Nothing else voids a line: an attempt the process did not survive is replayed (fail-closed).

Real archives (apps/docs/scripts/snapshot.py) of synthetic state in this test's own directory: the
`recovery` / `withdrawable` fixtures. Failures are injected one at a time and every monkeypatch is
put back with setattr(original). Crashes run in a child process (os._exit) that opens the fixture's
database through Store only.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

import synwevo_core
from synwevo_core import journal, oidc, workspace_lifecycle
from synwevo_core.sessions import Sessions
from synwevo_core.store import Store, new_id, now
from tests.integration.test_security_journal import REASON, recovery  # noqa: F401
from tests.integration.test_workspace_restore import (  # noqa: F401
    _backup_into, _delete, _lifecycle, _recover, _recover_restored, _restore_into, withdrawable)


def _lines(w, kind):
    return [e for e in journal.read(w.data) if e["kind"] == kind]


def _void_row(store, attempt):
    row = store.one("SELECT value FROM meta WHERE key=?", ("journal_void:" + attempt,))
    return row and row["value"]


def _receipt(store, line):
    return store.one("SELECT 1 FROM meta WHERE key=?", ("workspace_withdrawal:" + line["event_id"],))


def _status(store, wid):
    return store.one("SELECT status FROM workspaces WHERE id=?", (wid,))["status"]


@contextmanager
def _directory_fsync_fails_once(monkeypatch):
    original, calls = journal._fsync_dir, []

    def fails_once(directory):
        calls.append(directory)
        if len(calls) == 1:
            raise OSError("fixture: directory fsync failed after the append")
        return original(directory)
    monkeypatch.setattr(journal, "_fsync_dir", fails_once)
    try:
        yield
    finally:
        monkeypatch.setattr(journal, "_fsync_dir", original)


# ---------------------------------------------------------------- each failure after the line
@contextmanager
def _failing(w, failure, monkeypatch):
    """One failure after the deletion's line reached the journal, inside its transaction."""
    if failure == "directory_fsync":
        original, calls = journal._fsync_dir, []

        def fails_once(directory):
            calls.append(directory)
            if len(calls) == 1:
                raise OSError("fixture: directory fsync failed after the append")
            return original(directory)
        monkeypatch.setattr(journal, "_fsync_dir", fails_once)
        try:
            yield OSError
        finally:
            monkeypatch.setattr(journal, "_fsync_dir", original)
    elif failure == "sql":
        original = workspace_lifecycle.withdraw

        def failing(*args, **kwargs):
            raise sqlite3.OperationalError("fixture: disk I/O error at the withdrawals")
        monkeypatch.setattr(workspace_lifecycle, "withdraw", failing)
        try:
            yield sqlite3.OperationalError
        finally:
            monkeypatch.setattr(workspace_lifecycle, "withdraw", original)
    elif failure == "audit":
        # The caller's own record after the deletion (as the console's operation receipt) cannot
        # be written: a TEMP trigger on this thread's connection refuses that one action.
        c = w.store._c()
        c.execute("CREATE TEMP TRIGGER fixture_audit_fails BEFORE INSERT ON audit_events "
                  "WHEN NEW.action='fixture.after_deletion' BEGIN SELECT RAISE(ABORT, 'fixture: audit refused'); END")
        try:
            yield sqlite3.IntegrityError
        finally:
            c.execute("DROP TRIGGER fixture_audit_fails")
    else:
        yield sqlite3.IntegrityError     # "commit": a deferred foreign key refuses the COMMIT


def _attempt(w, failure):
    with w.store.tx() as c:
        if failure == "commit":
            c.execute("PRAGMA defer_foreign_keys=ON")
        _delete(w)
        if failure == "commit":
            # FIXTURE row naming no account: accepted now, refused by the COMMIT.
            c.execute("INSERT INTO account_recoveries (id,user_id,token_hash,created_at,expires_at) "
                      "VALUES ('fixture-orphan','fixture-missing-user','fixture-hash',?,?)", (now(), now() + 60))
        elif failure == "audit":
            w.store.insert("audit_events", {"id": new_id("au"), "at": now(), "actor_kind": "user",
                                            "actor_id": w.a["id"], "workspace_id": w.wid,
                                            "action": "fixture.after_deletion", "object_kind": "workspace",
                                            "object_id": w.wid, "result": "ok", "meta": {}})


@pytest.mark.parametrize("failure", ["directory_fsync", "sql", "commit", "audit"])
def test_a_deletion_that_failed_after_its_line_is_voided_and_never_replayed(withdrawable, monkeypatch, failure):
    w = withdrawable
    older = _backup_into(w, "older")
    with _failing(w, failure, monkeypatch) as expected:
        with pytest.raises(expected, match="fixture|FOREIGN KEY"):
            _attempt(w, failure)
    assert _lifecycle(w.store, w.wid)["status"] == "active"
    assert not w.store.one("SELECT 1 FROM account_recoveries WHERE id='fixture-orphan'")
    (line,) = _lines(w, journal.WORKSPACE_DELETED_KIND)
    (void,) = _lines(w, journal.VOID_KIND)
    assert (void["attempt_id"], void["voided_kind"]) == (line["attempt_id"], journal.WORKSPACE_DELETED_KIND)
    assert void["at"] > line["at"]
    assert not _receipt(w.store, line)
    assert _void_row(w.store, line["attempt_id"]) == journal.WORKSPACE_DELETED_KIND
    for name, archive in (("older", older), ("newer", _backup_into(w, "newer"))):
        restored = _restore_into(w, archive, "restored-" + name)
        try:
            assert _lifecycle(restored, w.wid)["status"] == "active", name
            assert restored.one("SELECT revoked_at FROM invitations WHERE id=?", (w.invitation,))["revoked_at"] is None
            assert _void_row(restored, line["attempt_id"]) == journal.WORKSPACE_DELETED_KIND, name
            assert journal.apply(restored, journal.read(w.data)) == {journal.VOID_KIND: 1}
            assert _lifecycle(restored, w.wid)["status"] == "active"
        finally:
            w.snapshot._close(restored)


def test_a_failed_attempt_a_retry_and_a_recovery(withdrawable, monkeypatch):
    """The reported scenario: back up (b0); a deletion fails after its line; the retry succeeds; the
    workspace is recovered; back up (b1). b1 keeps the recovery. b0 is deleted again with the
    retry's clock — never the failed attempt's — and can still be recovered."""
    w = withdrawable
    b0 = _backup_into(w, "b0")
    with _failing(w, "sql", monkeypatch) as expected:
        with pytest.raises(expected):
            _delete(w)
    _delete(w)
    retried = _lifecycle(w.store, w.wid)
    failed, retry = _lines(w, journal.WORKSPACE_DELETED_KIND)
    (void,) = _lines(w, journal.VOID_KIND)
    assert void["attempt_id"] == failed["attempt_id"] != retry["attempt_id"]
    assert failed["deleted_at"] < retry["deleted_at"] == retried["deleted_at"]
    _recover(w)
    b1 = _backup_into(w, "b1")
    restored = _restore_into(w, b1, "restored-b1")
    try:
        assert _lifecycle(restored, w.wid) == {"status": "active", "deleted_at": None, "recover_until": None,
                                               "previous_status": None}
    finally:
        w.snapshot._close(restored)
    restored = _restore_into(w, b0, "restored-b0")
    try:
        assert _lifecycle(restored, w.wid) == retried
        assert _recover_restored(w, restored)["workspace"]["status"] == "active"
    finally:
        w.snapshot._close(restored)


def test_a_retry_reusing_the_event_id_is_not_voided_with_the_failed_attempt(recovery):
    """record_account keys its receipt by the event id, which a retry may reuse (a second-factor
    reset's next sweep); the void is keyed by each line's own `attempt_id`. The failed attempt is
    voided, the retry stands: an older copy has the account's sessions revoked again."""
    w = recovery
    uid, event = w.b["id"], "sfr_fixture_reused"
    Sessions(w.store).create(uid, workspace_id=None, amr="password")
    older = Store(w.tmp / "older-copy")
    with w.store.conn() as source, older.conn() as destination:
        source.backup(destination)
    try:
        with pytest.raises(RuntimeError, match="fixture"):
            with w.store.tx():
                journal.record_account(w.store, "sessions_revoked", user_id=uid, event_id=event)
                raise RuntimeError("fixture failure after the line")
        assert not w.store.one("SELECT 1 FROM meta WHERE key=?", ("account_withdrawal:" + event,))
        with w.store.tx():
            journal.record_account(w.store, "sessions_revoked", user_id=uid, event_id=event)
        failed, retry = _lines(w, "sessions_revoked")
        assert failed["account_event_id"] == retry["account_event_id"] == event
        (void,) = _lines(w, journal.VOID_KIND)
        assert void["attempt_id"] == failed["attempt_id"] != retry["attempt_id"]
        assert not _void_row(w.store, retry["attempt_id"])
        assert w.store.one("SELECT 1 FROM meta WHERE key=?", ("account_withdrawal:" + event,))
        assert older.one("SELECT 1 FROM sessions WHERE user_id=? AND revoked_at IS NULL", (uid,))
        assert journal.apply(older, journal.read(w.data, since=now() + 3600)) == {
            journal.VOID_KIND: 1, "sessions_revoked": 1}
        assert not older.one("SELECT 1 FROM sessions WHERE user_id=? AND revoked_at IS NULL", (uid,))
        assert older.one("SELECT 1 FROM meta WHERE key=?", ("account_withdrawal:" + event,))
    finally:
        w.snapshot._close(older)


def test_the_void_refuses_when_the_receipt_is_present(tmp_path):
    """A receipt that committed means the attempt committed: no void row, no void line."""
    store = Store(tmp_path)
    store.execute("INSERT INTO meta (key,value) VALUES ('journal_receipt:ja_fixture', '1')")
    journal._void(store, "ja_fixture", journal.WORKSPACE_FROZEN_KIND, "journal_receipt:ja_fixture")
    assert not store.one("SELECT 1 FROM meta WHERE key='journal_void:ja_fixture'")
    assert not journal.path(tmp_path).exists()
    journal._void(store, "ja_fixture_absent", journal.WORKSPACE_FROZEN_KIND, "journal_receipt:ja_fixture_absent")
    assert store.one("SELECT value FROM meta WHERE key='journal_void:ja_fixture_absent'")["value"] == "workspace_frozen"
    assert [(e["kind"], e["attempt_id"]) for e in journal.read(tmp_path)] == [(journal.VOID_KIND, "ja_fixture_absent")]


def test_a_caller_that_catches_the_failure_and_commits_keeps_the_receipt_and_no_void(recovery):
    """The void belongs to the outermost transaction: an inner failure the caller handles, followed
    by a COMMIT, commits the receipt and the line stands — nothing is voided."""
    w = recovery
    with w.store.tx():
        try:
            with w.store.tx():
                journal.record_workspace_frozen(w.store, workspace_id=w.wid)
                raise RuntimeError("fixture inner failure")
        except RuntimeError:
            pass
    (line,) = _lines(w, journal.WORKSPACE_FROZEN_KIND)
    assert w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],))
    assert not _lines(w, journal.VOID_KIND) and not _void_row(w.store, line["attempt_id"])


def test_the_void_raises_synchronous_for_its_proof_and_puts_back_exactly_what_it_found(tmp_path):
    """The void runs on this thread's long-lived connection: FULL for its own transaction when the
    connection is below it, then the level it found — never NORMAL over a connection opened at
    FULL or EXTRA, which would weaken every later commit on that thread."""
    store = Store(tmp_path)
    c = store._c()
    for level in (0, 1, 2, 3):
        c.execute(f"PRAGMA synchronous={level}")
        attempt = f"ja_fixture_sync_{level}"
        seen = []
        c.set_trace_callback(seen.append)
        try:
            journal._void(store, attempt, journal.WORKSPACE_FROZEN_KIND, "journal_receipt:" + attempt)
        finally:
            c.set_trace_callback(None)
        assert c.execute("PRAGMA synchronous").fetchone()[0] == level
        steps = [s for s in seen if s.startswith("PRAGMA synchronous=") or s in ("BEGIN IMMEDIATE", "COMMIT")]
        assert steps == (["PRAGMA synchronous=FULL", "BEGIN IMMEDIATE", "COMMIT", f"PRAGMA synchronous={level}"]
                         if level < 2 else ["BEGIN IMMEDIATE", "COMMIT"]), level
        assert _void_row(store, attempt) == journal.WORKSPACE_FROZEN_KIND


# ---------------------------------------------------------------- attempt ids and what a void names
def test_an_attempt_id_already_known_to_the_database_is_drawn_again(recovery, monkeypatch):
    """A void skips the lines naming its `attempt_id`, so an id drawn twice must never reach a
    second line. Ids are 128 random bits; and _intent, in the attempt's own transaction, draws
    again over an id this database holds a void or a receipt for. FIXTURE: the generator returns
    the same id twice, twice over. A failed freeze is voided as `ja_fixture_collide`; the next
    freeze draws that id, is refused it and commits as `ja_fixture_fresh` — so a backup from
    before both is frozen on restore, not skipped by the earlier void. An unfreeze and a third
    freeze then draw `ja_fixture_fresh` again (its receipt is here) and commit as the next id."""
    w = recovery
    assert re.fullmatch(r"ja_[0-9a-f]{32}", journal._new_attempt_id())
    b0 = _backup_into(w, "b0")
    original = journal._new_attempt_id
    draws = iter(["ja_fixture_collide", "ja_fixture_collide", "ja_fixture_fresh",
                  "ja_fixture_fresh", "ja_fixture_third"])
    monkeypatch.setattr(journal, "_new_attempt_id", lambda: next(draws))
    try:
        with _directory_fsync_fails_once(monkeypatch):
            with pytest.raises(OSError, match="fixture"):
                w.ident.set_workspace_frozen(w.wid, True)
        assert _status(w.store, w.wid) == "active"
        assert _void_row(w.store, "ja_fixture_collide") == journal.WORKSPACE_FROZEN_KIND
        w.ident.set_workspace_frozen(w.wid, True)
        w.ident.set_workspace_frozen(w.wid, False)
        w.ident.set_workspace_frozen(w.wid, True)
    finally:
        monkeypatch.setattr(journal, "_new_attempt_id", original)
    assert [e["attempt_id"] for e in _lines(w, journal.WORKSPACE_FROZEN_KIND)] == [
        "ja_fixture_collide", "ja_fixture_fresh", "ja_fixture_third"]
    assert [e["attempt_id"] for e in _lines(w, journal.VOID_KIND)] == ["ja_fixture_collide"]
    for attempt in ("ja_fixture_fresh", "ja_fixture_third"):
        assert w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + attempt,))
        assert not _void_row(w.store, attempt)
    restored = _restore_into(w, b0, "restored-b0")
    try:
        assert _status(restored, w.wid) == "frozen", "the committed freeze is not skipped by the earlier void"
    finally:
        w.snapshot._close(restored)


def test_a_void_skips_only_a_line_of_the_kind_it_names(recovery):
    """FIXTURE lines appended by hand, as only an id drawn twice could leave them: a freeze whose
    `attempt_id` an `intent_voided` line names for another kind, and one whose `journal_void:` row
    names another kind. Neither proves the freeze void: both are applied (fail-closed), and the
    void of the right kind still skips its own line."""
    w = recovery
    other = w.ident.create_workspace("Second fixture workspace", owner_id=w.a["id"])["id"]
    third = w.ident.create_workspace("Third fixture workspace", owner_id=w.a["id"])["id"]
    journal.record(w.store, journal.WORKSPACE_FROZEN_KIND, workspace_id=w.wid, attempt_id="ja_fixture_line")
    journal.record(w.store, journal.VOID_KIND, attempt_id="ja_fixture_line", voided_kind=journal.WORKSPACE_DELETED_KIND)
    journal.record(w.store, journal.WORKSPACE_FROZEN_KIND, workspace_id=other, attempt_id="ja_fixture_row")
    w.store.execute("INSERT INTO meta (key,value) VALUES ('journal_void:ja_fixture_row', ?)",
                    (journal.WORKSPACE_DELETED_KIND,))
    journal.record(w.store, journal.WORKSPACE_FROZEN_KIND, workspace_id=third, attempt_id="ja_fixture_void")
    journal.record(w.store, journal.VOID_KIND, attempt_id="ja_fixture_void", voided_kind=journal.WORKSPACE_FROZEN_KIND)
    assert journal.apply(w.store, journal.read(w.data)) == {"workspace_frozen": 2, journal.VOID_KIND: 1}
    assert (_status(w.store, w.wid), _status(w.store, other), _status(w.store, third)) == ("frozen", "frozen", "active")


def test_a_later_release_s_attempted_lines_are_read_rather_than_refused(recovery):
    """Lines appended by hand as `_intent` writes them (FIXTURE), for kinds whose old lines are
    filtered by time — a `member_removed` with an `attempt_id`, and the void of a `role_changed`
    attempt — are accepted, read whatever their time, replayed into a copy without the receipt and
    then receipted, and skipped by the void; a second replay changes nothing."""
    w = recovery
    uid = w.b["id"]
    older = Store(w.tmp / "older-copy")
    with w.store.conn() as source, older.conn() as destination:
        source.backup(destination)
    try:
        with w.store.tx() as c:
            c.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (w.wid, uid))
            c.execute("INSERT INTO meta (key,value) VALUES ('journal_receipt:ja_fixture_next_removed','1')")
            journal.record(w.store, "member_removed", workspace_id=w.wid, user_id=uid,
                           attempt_id="ja_fixture_next_removed")
        journal.record(w.store, "role_changed", workspace_id=w.wid, user_id=uid, role="viewer",
                       attempt_id="ja_fixture_next_failed")
        journal.record(w.store, journal.VOID_KIND, attempt_id="ja_fixture_next_failed", voided_kind="role_changed")
        entries = journal.read(w.data, since=now() + 3600)
        assert [e["kind"] for e in entries] == ["member_removed", "role_changed", journal.VOID_KIND]
        assert older.one("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?", (w.wid, uid))
        assert journal.apply(older, entries) == {"member_removed": 1, journal.VOID_KIND: 1}
        assert not older.one("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?", (w.wid, uid))
        assert older.one("SELECT 1 FROM meta WHERE key='journal_receipt:ja_fixture_next_removed'")
        assert _void_row(older, "ja_fixture_next_failed") == "role_changed"
        assert journal.apply(older, entries) == {"member_removed": 0, journal.VOID_KIND: 1}
        assert journal.apply(w.store, entries) == {"member_removed": 0, journal.VOID_KIND: 1}
    finally:
        w.snapshot._close(older)


# ---------------------------------------------------------------- the process did not survive
CHILD = r'''
import os, sqlite3, sys
from synwevo_core import journal, workspace_lifecycle
from synwevo_core.store import Store
root, wid, actor, phase = sys.argv[1:]
store = Store(root)
if phase == "after_append":
    def crash(*args, **kwargs):
        os._exit(9)
    workspace_lifecycle.withdraw = crash
elif phase == "fsync_dir":
    def crash(directory):
        os._exit(9)
    journal._fsync_dir = crash
elif phase == "void_line":
    def failing(*args, **kwargs):
        raise sqlite3.OperationalError("fixture: failure after the journal line")
    workspace_lifecycle.withdraw = failing
    real = journal.record
    def record(store_, kind, **fields):
        if kind == journal.VOID_KIND:
            os._exit(9)
        return real(store_, kind, **fields)
    journal.record = record
try:
    workspace_lifecycle.platform_transition(store, wid, actor, "delete", reason="fixture crash during a deletion")
except sqlite3.OperationalError:
    pass
os._exit(3)
'''


def _crash(w, phase):
    # The installed core package's parent as cwd, as test_billing_ledger's children: never a
    # sys.path change. The child opens the fixture's database through Store only.
    run = subprocess.run([sys.executable, "-c", CHILD, str(w.data), w.wid, w.a["id"], phase],
                         cwd=Path(synwevo_core.__file__).resolve().parent.parent,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 9, run.stderr


@pytest.mark.parametrize("phase", ["after_append", "fsync_dir"])
def test_an_attempt_the_process_did_not_survive_is_replayed(withdrawable, phase):
    """Fail-closed baseline: the process died after the line (after its append and file fsync; or
    in the directory fsync), inside the transaction. Nothing ran after it, so nothing proved the
    attempt void: a restore from a backup taken afterwards deletes the workspace with the
    attempt's clock — and it can be recovered (workspaces have no purge)."""
    w = withdrawable
    _crash(w, phase)
    assert _lifecycle(w.store, w.wid)["status"] == "active"
    (line,) = _lines(w, journal.WORKSPACE_DELETED_KIND)
    assert not _lines(w, journal.VOID_KIND) and not _void_row(w.store, line["attempt_id"])
    assert not _receipt(w.store, line)
    restored = _restore_into(w, _backup_into(w, "after"), "restored")
    try:
        assert _lifecycle(restored, w.wid) == {"status": "deleted", "deleted_at": line["deleted_at"],
                                               "recover_until": line["recover_until"], "previous_status": "active"}
        assert _recover_restored(w, restored)["workspace"]["status"] == "active"
    finally:
        w.snapshot._close(restored)


def test_a_void_proven_but_not_yet_written_to_the_journal(withdrawable):
    """The process died when the void line was due: the `journal_void:` row committed, the line
    did not. A backup taken afterwards holds the row and skips the attempt; one taken before the
    attempt cannot know of it and replays it (fail-closed)."""
    w = withdrawable
    before = _backup_into(w, "before")
    _crash(w, "void_line")
    assert _lifecycle(w.store, w.wid)["status"] == "active"
    (line,) = _lines(w, journal.WORKSPACE_DELETED_KIND)
    assert not _lines(w, journal.VOID_KIND)
    assert _void_row(w.store, line["attempt_id"]) == journal.WORKSPACE_DELETED_KIND
    restored = _restore_into(w, _backup_into(w, "after"), "restored-after")
    try:
        assert _lifecycle(restored, w.wid)["status"] == "active"
    finally:
        w.snapshot._close(restored)
    restored = _restore_into(w, before, "restored-before")
    try:
        assert _lifecycle(restored, w.wid)["status"] == "deleted"
        assert _lifecycle(restored, w.wid)["deleted_at"] == line["deleted_at"]
    finally:
        w.snapshot._close(restored)


# ---------------------------------------------------------------- Store.after_rollback
def test_after_rollback_runs_once_after_a_rollback_and_never_after_a_commit(tmp_path):
    store = Store(tmp_path)
    ran = []
    with store.tx():
        store.after_rollback(lambda: ran.append("committed"))
    assert ran == []
    with pytest.raises(RuntimeError, match="fixture"):
        with store.tx():
            store.after_rollback(lambda: ran.append("rolled back"))
            raise RuntimeError("fixture rollback")
    assert ran == ["rolled back"]
    with pytest.raises(RuntimeError, match="fixture"):
        with store.tx():
            raise RuntimeError("fixture rollback with nothing queued")
    assert ran == ["rolled back"], "a later transaction starts with nothing queued"


def test_after_rollback_needs_a_transaction(tmp_path):
    with pytest.raises(RuntimeError, match="transaction"):
        Store(tmp_path).after_rollback(lambda: None)


def test_after_rollback_runs_at_depth_zero_outside_the_write_lock_and_may_open_a_transaction(tmp_path):
    store = Store(tmp_path)
    seen = []

    def callback():
        seen.append((store._local.depth, store._c().in_transaction))
        # Another thread can write now: the write lock is released.
        other = threading.Thread(target=lambda: store.execute(
            "INSERT INTO meta (key,value) VALUES ('fixture_other_thread','1')"))
        other.start()
        other.join(10)
        seen.append(other.is_alive())
        with store.tx() as t:
            t.execute("INSERT INTO meta (key,value) VALUES ('fixture_after_rollback','1')")

    with pytest.raises(RuntimeError, match="fixture"):
        with store.tx() as t:
            t.execute("INSERT INTO meta (key,value) VALUES ('fixture_rolled_back','1')")
            store.after_rollback(callback)
            raise RuntimeError("fixture rollback")
    assert seen == [(0, False), False]
    assert store.one("SELECT 1 FROM meta WHERE key='fixture_other_thread'")
    assert store.one("SELECT 1 FROM meta WHERE key='fixture_after_rollback'")
    assert not store.one("SELECT 1 FROM meta WHERE key='fixture_rolled_back'")


def test_a_nested_block_registers_on_the_outermost_transaction(tmp_path):
    store = Store(tmp_path)
    ran = []
    with store.tx():
        try:
            with store.tx():
                store.after_rollback(lambda: ran.append("inner"))
                raise RuntimeError("fixture inner failure")
        except RuntimeError:
            pass
    assert ran == [], "the outermost transaction committed"
    with pytest.raises(RuntimeError, match="fixture"):
        with store.tx():
            with store.tx():
                store.after_rollback(lambda: ran.append(store._local.depth))
            raise RuntimeError("fixture outer failure")
    assert ran == [0]


def test_a_failing_callback_never_hides_the_exception_that_rolled_back(tmp_path, caplog):
    store = Store(tmp_path)
    ran = []

    def broken():
        raise OSError("fixture callback failure")
    with caplog.at_level(logging.ERROR, logger="synwevo.store"):
        with pytest.raises(RuntimeError, match="the original failure"):
            with store.tx():
                store.after_rollback(broken)
                store.after_rollback(lambda: ran.append("next"))
                raise RuntimeError("the original failure")
    assert ran == ["next"]
    assert "after-rollback callback failed" in caplog.text


class _CommitReportsAnError:
    """FIXTURE: this thread's connection, except that its next COMMIT commits and then raises — an
    error reported after SQLite committed (as a KeyboardInterrupt between the COMMIT and its
    bookkeeping would be)."""

    def __init__(self, connection):
        self._connection, self.armed = connection, True

    def execute(self, sql, *args):
        out = self._connection.execute(sql, *args)
        if sql == "COMMIT" and self.armed:
            self.armed = False
            raise sqlite3.OperationalError("fixture: error reported after a real COMMIT")
        return out

    def __getattr__(self, name):
        return getattr(self._connection, name)


def test_a_commit_that_raises_after_committing_runs_the_callbacks_and_the_void_finds_the_receipt(recovery, monkeypatch):
    """after_rollback is not a promise that nothing committed: a COMMIT that raised runs it too, and
    may have committed. The void re-checks the receipt in a transaction of its own, finds it, and
    writes nothing: the freeze stands, live and on a restore from a backup taken before it."""
    w = recovery
    b0 = _backup_into(w, "b0")
    real = w.store._c
    proxy = _CommitReportsAnError(real())
    monkeypatch.setattr(w.store, "_c", lambda: proxy)
    try:
        with pytest.raises(sqlite3.OperationalError, match="cannot rollback") as raised:
            w.ident.set_workspace_frozen(w.wid, True)
    finally:
        monkeypatch.setattr(w.store, "_c", real)
    assert "after a real COMMIT" in str(raised.value.__context__)
    assert not proxy.armed
    assert _status(w.store, w.wid) == "frozen"
    (line,) = _lines(w, journal.WORKSPACE_FROZEN_KIND)
    assert w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],))
    assert not _void_row(w.store, line["attempt_id"]) and not _lines(w, journal.VOID_KIND)
    restored = _restore_into(w, b0, "restored-b0")
    try:
        assert _status(restored, w.wid) == "frozen"
    finally:
        w.snapshot._close(restored)


def test_after_rollback_runs_when_the_commit_fails_and_when_the_rollback_raises(tmp_path):
    store = Store(tmp_path)
    ran = []
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with store.tx() as t:
            t.execute("PRAGMA defer_foreign_keys=ON")
            # FIXTURE row naming no account: accepted now, refused by the COMMIT.
            t.execute("INSERT INTO account_recoveries (id,user_id,token_hash,created_at,expires_at) "
                      "VALUES ('fixture-orphan','fixture-missing-user','fixture-hash',1,2)")
            store.after_rollback(lambda: ran.append("commit refused"))
    assert ran == ["commit refused"]
    assert not store.one("SELECT 1 FROM account_recoveries WHERE id='fixture-orphan'")
    # The transaction already ended under the block, so its ROLLBACK raises: that error propagates
    # (with the block's own as its context), and the queued callbacks still run.
    with pytest.raises(sqlite3.OperationalError) as raised:
        with store.tx() as t:
            store.after_rollback(lambda: ran.append("rollback raised"))
            t.execute("ROLLBACK")
            raise RuntimeError("fixture failure")
    assert isinstance(raised.value.__context__, RuntimeError)
    assert ran == ["commit refused", "rollback raised"]
    assert store._local.depth == 0 and not store._c().in_transaction


# ---------------------------------------------------------------- every kind on the generic receipt
# One case per kind whose receipt is `journal_receipt:<attempt_id>`: what the withdrawal targets,
# created in the live database before any copy (FIXTURE rows, inserted directly where the writer
# lives in another package), the line's fields, and whether a database shows the withdrawal.
def _key(w):
    w.store.execute("INSERT INTO api_keys (id,name,tenant,scopes,secret_hash,created_at) "
                    "VALUES ('fixture-key','fixture key',?,'read','fixture-hash',?)", (w.wid, now()))
    return {"key_id": "fixture-key"}, lambda s: s.one(
        "SELECT revoked_at FROM api_keys WHERE id='fixture-key'")["revoked_at"] is not None


def _client(w, fields=None, withdrawn=None):
    t = now()
    w.store.execute("INSERT INTO oauth_clients (client_id,app_id,name,secret_hash,redirect_uris,kind,workspace_id,"
                    "created_at,updated_at) VALUES ('fixture-client','docweave','Fixture client','fixture-old-hash',"
                    "'[]','service',?,?,?)", (w.wid, t, t))
    w.store.execute("INSERT INTO oauth_tokens (id,client_id,user_id,scope,workspace_id,issued_at) "
                    "VALUES ('fixture-token','fixture-client',?,'openid',?,?)", (w.b["id"], w.wid, t))
    return fields or {"client_id": "fixture-client"}, withdrawn or (lambda s: s.one(
        "SELECT disabled_at FROM oauth_clients WHERE client_id='fixture-client'")["disabled_at"] is not None)


def _token_revoked(s):
    return s.one("SELECT revoked_at FROM oauth_tokens WHERE id='fixture-token'")["revoked_at"] is not None


def _user_status(status):
    def case(w):
        return {"user_id": w.b["id"]}, lambda s: s.one("SELECT status FROM users WHERE id=?",
                                                        (w.b["id"],))["status"] == status
    return case


def _sessions(w):
    Sessions(w.store).create(w.b["id"], workspace_id=None, amr="password")
    return {"user_id": w.b["id"]}, lambda s: not s.one(
        "SELECT 1 FROM sessions WHERE user_id=? AND revoked_at IS NULL", (w.b["id"],))


def _password(w):
    return {"user_id": w.b["id"]}, lambda s: bool(s.one(
        "SELECT 1 FROM meta WHERE key=?", ("password_reset_required:" + w.b["id"],)))


def _member(w):
    return {"workspace_id": w.wid, "user_id": w.b["id"]}, lambda s: not s.one(
        "SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?", (w.wid, w.b["id"]))


def _role(w):
    return {"workspace_id": w.wid, "user_id": w.b["id"], "role": "viewer"}, lambda s: s.one(
        "SELECT role FROM memberships WHERE workspace_id=? AND user_id=?", (w.wid, w.b["id"]))["role"] == "viewer"


def _entitlement(w):
    def withdrawn(s):
        row = s.one("SELECT revoked_at FROM app_entitlements WHERE workspace_id=? AND app_id='docweave' AND user_id=?",
                    (w.wid, w.b["id"]))
        return bool(row and row["revoked_at"])
    return {"workspace_id": w.wid, "app_id": "docweave", "user_id": w.b["id"]}, withdrawn


def _frozen(w):
    return {"workspace_id": w.wid}, lambda s: _status(s, w.wid) == "frozen"


def _webhook(w):
    t = now()
    w.store.execute("INSERT INTO webhook_subscriptions (id,tenant,url,events,secret_enc,created_at,updated_at) "
                    "VALUES ('wh_fixture',?,'https://receiver.example.test/hook','[\"*\"]','fixture-sealed',?,?)",
                    (w.wid, t, t))
    return {"subscription_id": "wh_fixture"}, lambda s: s.one(
        "SELECT status FROM webhook_subscriptions WHERE id='wh_fixture'")["status"] == "disabled"


def _passkey(w):
    w.store.execute("INSERT INTO passkeys (id,user_id,credential_id,public_key,rp_id,created_at) "
                    "VALUES ('pk_fixture',?,'fixture-credential','fixture-public-key','localhost',?)", (w.b["id"], now()))
    return {"credential_id": "pk_fixture"}, lambda s: not s.one("SELECT 1 FROM passkeys WHERE id='pk_fixture'")


def _external_identity(w):
    w.store.execute("INSERT INTO external_identities (id,user_id,provider,subject,issuer,email,email_verified,created_at) "
                    "VALUES ('xi_fixture',?,'fixture-provider','fixture-subject','https://idp.example.test',?,1,?)",
                    (w.b["id"], w.b["email"], now()))
    return {"identity_id": "xi_fixture"}, lambda s: not s.one("SELECT 1 FROM external_identities WHERE id='xi_fixture'")


def _workspace_app(w):
    w.store.execute("INSERT OR IGNORE INTO workspace_apps (workspace_id,app_id,enabled_at) VALUES (?,'docweave',?)",
                    (w.wid, now()))
    return {"workspace_id": w.wid, "app_id": "docweave"}, lambda s: not s.one(
        "SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id='docweave'", (w.wid,))


def _app(w):
    return {"app_id": "docweave"}, lambda s: s.one("SELECT status FROM apps WHERE id='docweave'")["status"] == "disabled"


B3_ORGANIZATION = "org_fixture_b3_switch"


def _organization_app(w):
    """B3: FIXTURE organization row (the switch's own check wants one; the replay does not — the
    table has no foreign key), Synwevo Message on in it until the switch-off."""
    t = now()
    w.store.execute("INSERT OR IGNORE INTO organizations (id,name,status,invitation_policy,created_by,created_at,updated_at,version) "
                    "VALUES (?,'B3 fixture organization','active','admins',?,?,?,1)", (B3_ORGANIZATION, w.a["id"], t, t))
    return {"organization_id": B3_ORGANIZATION, "app_id": "messages"}, lambda s: bool(s.one(
        "SELECT 1 FROM organization_apps WHERE organization_id=? AND app_id='messages' AND status='disabled'", (B3_ORGANIZATION,)))


def _invitation(w):
    t = now()
    w.store.execute("INSERT INTO invitations (id,email_key,workspace_id,role,token_hash,invited_by,created_at,expires_at) "
                    "VALUES ('inv_fixture','invitee@example.test',?,'editor','fixture-token-hash',?,?,?)",
                    (w.wid, w.a["id"], t, t + 3600))
    return {"invitation_id": "inv_fixture"}, lambda s: s.one(
        "SELECT revoked_at FROM invitations WHERE id='inv_fixture'")["revoked_at"] is not None


def _approval(w):
    t = now()
    w.store.execute("INSERT INTO api_platform_approvals (id,workspace_id,requested_by,name,credential_kind,"
                    "requested_scopes,requested_resources,status,reason,created_at,expires_at) "
                    "VALUES ('apa_fixture',?,?,'fixture','api_key','[]','[]','approved','fixture reason',?,?)",
                    (w.wid, w.a["id"], t, t + 3600))
    return {"approval_id": "apa_fixture"}, lambda s: s.one(
        "SELECT status FROM api_platform_approvals WHERE id='apa_fixture'")["status"] == "revoked"


def _consent(w):
    t = now() - 60
    w.store.execute("INSERT INTO oauth_consents (id,user_id,client_id,workspace_id,scopes,granted_at,updated_at) "
                    "VALUES ('cns_fixture',?,'fixture-client',?,'[]',?,?)", (w.b["id"], w.wid, t, t))
    return {"user_id": w.b["id"], "client_id": "fixture-client", "workspace_id": w.wid}, lambda s: s.one(
        "SELECT revoked_at FROM oauth_consents WHERE id='cns_fixture'")["revoked_at"] is not None


def _unclaim(w):
    t = now() - 60
    w.store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                    "VALUES ('ws_fixture_legacy','Fixture legacy data','legacy','active',?,?)", (t, t))
    w.store.execute("INSERT INTO memberships (workspace_id,user_id,role,created_at,updated_at) "
                    "VALUES ('ws_fixture_legacy',?,'editor',?,?)", (w.b["id"], t, t))
    return {"workspace_id": "ws_fixture_legacy", "label_restored": False}, lambda s: not s.one(
        "SELECT 1 FROM memberships WHERE workspace_id='ws_fixture_legacy'")


def _row_exists(table, column, value):
    return lambda s: bool(s.one(f"SELECT 1 FROM {table} WHERE {column}=?", (value,)))


def _dispatch(w):
    return ({"suspension_id": "rsus_fixture", "environment": "test", "scope_kind": "user", "scope_id": w.b["id"]},
            _row_exists("model_risk_suspensions", "id", "rsus_fixture"))


def _breaker(w):
    return {"event_id": "rbe_fixture", "environment": "test"}, _row_exists("model_risk_breaker_events", "id", "rbe_fixture")


def _payer(w):
    w.store.execute("INSERT OR IGNORE INTO billing_units (code,scale) VALUES ('FIXTURE',2)")
    w.store.execute("INSERT INTO billing_accounts (id,environment,unit,owner,kind) "
                    "VALUES ('ba_fixture','test','FIXTURE',?,'wallet')", (w.b["id"],))
    return ({"freeze_id": "fz_fixture", "environment": "test", "account_id": "ba_fixture", "payer_kind": "user",
             "payer_id": w.b["id"], "chargeback_id": "cb_fixture"},
            _row_exists("billing_account_freezes", "id", "fz_fixture"))


def _agent_suspended(w):
    return {"suspension_id": "asp_fixture", "user_id": w.b["id"]}, _row_exists("agent_access_suspensions", "id", "asp_fixture")


def _agent_consent(w):
    w.store.execute("INSERT INTO agent_provider_consents (user_id,provider,version,accepted_at) "
                    "VALUES (?,'anthropic','fixture-version',?)", (w.b["id"], now() - 60))
    return {"user_id": w.b["id"], "provider": "anthropic"}, lambda s: s.one(
        "SELECT withdrawn_at FROM agent_provider_consents WHERE user_id=?", (w.b["id"],))["withdrawn_at"] is not None


def _credential(w):
    w.store.execute("INSERT INTO model_provider_credentials (environment,revision,secret_enc) "
                    "VALUES ('test',1,'fixture-sealed')")
    return {"event_id": "op_fixture_credential", "environment": "test", "credential_revision": 1}, lambda s: s.one(
        "SELECT secret_enc FROM model_provider_credentials WHERE environment='test'")["secret_enc"] is None


def _provider(w):
    w.store.execute("INSERT INTO model_provider_versions (environment,revision,spec_json,credential_revision,"
                    "credential_configured,actor_id,reason,operation_id,created_at) "
                    "VALUES ('test',1,'{\"enabled\":true}',0,0,?,'fixture enabled version','op_fixture_enabled',?)",
                    (w.a["id"], int(now())))
    return {"event_id": "op_fixture_disabled", "environment": "test"}, lambda s: not json.loads(s.one(
        "SELECT spec_json FROM model_provider_versions WHERE environment='test' ORDER BY revision DESC LIMIT 1")
        ["spec_json"])["enabled"]


CASES = {
    "api_key_superseded": _key, "api_key_revoked": _key,
    "api_platform_approval_revoked": _approval,
    "client_disabled": _client,
    "client_secret_rotated": lambda w: _client(w, {"client_id": "fixture-client", "secret_hash": "fixture-new-hash"},
                                               lambda s: s.one("SELECT secret_hash FROM oauth_clients WHERE "
                                                               "client_id='fixture-client'")["secret_hash"] == "fixture-new-hash"),
    "tokens_revoked": lambda w: _client(w, None, _token_revoked),
    "user_suspended": _user_status("suspended"), "user_deleted": _user_status("deleted"),
    "password_changed": _password, "sessions_revoked": _sessions,
    "member_removed": _member, "role_changed": _role, "entitlement_revoked": _entitlement,
    "workspace_frozen": _frozen, "webhook_disabled": _webhook, "passkey_removed": _passkey,
    "external_identity_unlinked": _external_identity,
    "workspace_app_disabled": _workspace_app, "organization_app_disabled": _organization_app,
    "app_disabled": _app, "invitation_revoked": _invitation,
    "consent_withdrawn": _consent, "workspace_unclaimed": _unclaim,
    "model_dispatch_suspended": _dispatch, "model_breaker_opened": _breaker, "payer_frozen": _payer,
    "agent_access_suspended": _agent_suspended, "agent_provider_consent_withdrawn": _agent_consent,
    "model_credential_withdrawn": _credential, "model_provider_disabled": _provider,
}


def _copy(w, name):
    copy = Store(w.tmp / name)
    with w.store.conn() as source, copy.conn() as destination:
        source.backup(destination)
    return copy


def _generic(kind):
    return journal._receipt({"kind": kind, "attempt_id": "ja_x", "event_id": "e"}) == "journal_receipt:ja_x"


def test_every_withdrawal_is_written_ahead_and_every_generic_kind_has_a_case():
    """Every kind but the void is an attempted kind (journal.ATTEMPTED); the ones receipted by
    `journal_receipt:<attempt_id>` are exactly the cases below, so a new kind cannot skip them."""
    assert journal.ATTEMPTED == journal.KINDS - {journal.VOID_KIND}
    assert journal.TIME_FILTERED == journal.KINDS - journal.ALWAYS_REPLAYED
    assert set(CASES) == {k for k in journal.ATTEMPTED if _generic(k)}
    assert {k for k in journal.ATTEMPTED if not _generic(k)} == journal.ORGANIZATION_KINDS | {
        journal.WORKSPACE_DELETED_KIND, journal.PLATFORM_GRANT_KIND, "ai_policy_narrowed", journal.APP_POLICY_KIND}


@pytest.mark.parametrize("kind", sorted(CASES))
def test_each_kind_is_replayed_without_its_receipt_skipped_with_it_and_skipped_by_its_void(recovery, kind):
    """For one kind: (1) a committed attempt's line, read whatever its time, is replayed into a copy
    taken before it and receipted there, once; (2) skipped by a copy holding its receipt — FIXTURE:
    the attempt commits only its line and receipt, not the change, so a replay would show; (3) a
    failed attempt is voided and skipped by a copy taken before it; (4) an old line without
    `attempt_id` keeps today's rule — filtered by time (TIME_FILTERED) or always read and applied
    by its own ids — and writes no receipt."""
    w = recovery
    fields, withdrawn = CASES[kind](w)
    older, before_void, old_rule = _copy(w, "older"), _copy(w, "before-void"), _copy(w, "old-rule")
    try:
        assert not withdrawn(older)
        with w.store.tx():
            assert journal._intent(w.store, kind, **fields) is True
        (line,) = _lines(w, kind)
        attempt = line["attempt_id"]
        assert re.fullmatch(r"ja_[0-9a-f]{32}", attempt)
        assert w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + attempt,))
        holding = _copy(w, "holding")
        try:
            entries = journal.read(w.data, since=now() + 3600)
            assert entries == [line]
            # (1) replayed into a copy without the receipt, then receipted there: once
            assert kind in journal.apply(older, entries)
            assert withdrawn(older)
            assert older.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + attempt,))
            assert journal.apply(older, entries) == {kind: 0}
            # (2) skipped where the receipt is
            assert journal.apply(holding, entries) == {kind: 0}
            assert not withdrawn(holding)
        finally:
            w.snapshot._close(holding)
        # (3) a failed attempt: voided after its rollback, skipped by its void
        with pytest.raises(RuntimeError, match="fixture"):
            with w.store.tx():
                journal._intent(w.store, kind, **fields)
                raise RuntimeError("fixture: the change failed after its line")
        committed, failed = _lines(w, kind)
        (void,) = _lines(w, journal.VOID_KIND)
        assert committed == line and (void["attempt_id"], void["voided_kind"]) == (failed["attempt_id"], kind)
        assert _void_row(w.store, failed["attempt_id"]) == kind
        assert not w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + failed["attempt_id"],))
        assert journal.read(w.data, since=now() + 3600) == [line, failed, void]
        assert journal.apply(before_void, [failed, void]) == {journal.VOID_KIND: 1}
        assert not withdrawn(before_void)
        assert _void_row(before_void, failed["attempt_id"]) == kind
        # (4) an old line (FIXTURE, by hand, in a journal of its own): today's rule
        old_dir = w.tmp / "old-journal"
        old_dir.mkdir()
        at = now()
        old_line = {"at": at, "kind": kind, **fields}
        journal.path(old_dir).write_text(json.dumps(old_line) + "\n", encoding="utf-8")
        assert journal.read(old_dir, since=at + 1) == ([] if kind in journal.TIME_FILTERED else [old_line])
        assert journal.read(old_dir, since=at - 1) == [old_line]
        receipts = old_rule.one("SELECT COUNT(*) AS n FROM meta WHERE key LIKE 'journal_receipt:%'")["n"]
        assert kind in journal.apply(old_rule, [old_line])
        assert withdrawn(old_rule)
        assert old_rule.one("SELECT COUNT(*) AS n FROM meta WHERE key LIKE 'journal_receipt:%'")["n"] == receipts
    finally:
        for copy in (older, before_void, old_rule):
            w.snapshot._close(copy)


# ---------------------------------------------------------------- the core writers
# Each writer in synwevo_core journals through _intent inside the transaction of its change. Per
# writer: the kind, what it needs (FIXTURE rows), the call, whether a database shows the change,
# and what a failed append does — "strict": raises (the exception), nothing changes, the attempt is
# voided; "stands": the change commits with its receipt (and the exception, if any, is raised after).
def _api_key(w):
    from synwevo_core.auth import Auth
    return Auth(w.store).create_key("fixture key", w.wid, ("read",))[1]["id"]


def _key_revoked(s, w, kid):
    return s.one("SELECT revoked_at FROM api_keys WHERE id=?", (kid,))["revoked_at"] is not None


def _rotate(w, kid):
    from synwevo_core.auth import Auth
    Auth(w.store).rotate_key(kid)


def _revoke_key(w, kid):
    from synwevo_core.auth import Auth
    assert Auth(w.store).revoke_key(kid) == 1


def _apps(w):
    from synwevo_core.apps import Apps
    return Apps(w.store, w.ident)


def _withdraw_consent(w, _):
    from synwevo_core.oauth_consent import Consents
    Consents(w.store).withdraw(w.b["id"], "cns_fixture")


def _accept_provider(w):
    from synwevo_core.agent_safety import PROVIDER_TERMS, ProviderConsents
    ProviderConsents(w.store).accept(w.b["id"], "deepseek", PROVIDER_TERMS["deepseek"].version)


def _withdraw_provider(w, _):
    from synwevo_core.agent_safety import ProviderConsents
    ProviderConsents(w.store).withdraw(w.b["id"], "deepseek")


def _provider_withdrawn(s, w, _):
    return s.one("SELECT withdrawn_at FROM agent_provider_consents WHERE user_id=?", (w.b["id"],))["withdrawn_at"] is not None


def _suspend_agent(w, _):
    from synwevo_core.agent_safety import AgentAccess
    AgentAccess(w.store).suspend(w.b["id"], w.a["id"], reason="fixture suspension reason")


def _agent_suspension(s, w, _):
    return bool(s.one("SELECT 1 FROM agent_access_suspensions WHERE user_id=? AND lifted_at IS NULL", (w.b["id"],)))


def _risk(w):
    from synwevo_core.billing.risk import RiskControls
    return RiskControls(w.store, environment="test")


def _trip(w, _):
    _risk(w).record_trip({"scope_kind": "user", "scope_id": w.b["id"], "reason_code": "payer_velocity"})


def _dispatch_suspended(s, w, _):
    return bool(s.one("SELECT 1 FROM model_risk_suspensions WHERE scope_id=? AND lifted_at IS NULL", (w.b["id"],)))


def _open_breaker(w, _):
    _risk(w).open_breaker(source="balance", reason_code="fixture_balance")


def _breaker_open(s, w, _):
    row = s.one("SELECT state FROM model_risk_breaker_events WHERE environment='test' ORDER BY seq DESC LIMIT 1")
    return bool(row and row["state"] == "open")


def _service_error():
    from synwevo_core.errors import ServiceError
    return ServiceError


def _journal_write_failed():
    from synwevo_core.auth import JournalWriteFailed
    return JournalWriteFailed


def _as_writer(case):
    """A CASES predicate as a writer's: (store, w, ctx) -> bool."""
    def withdrawn(s, w, _):
        return case(w)[1](s)
    return withdrawn


WRITERS = {
    "Auth.rotate_key": ("api_key_superseded", _api_key, _rotate, _key_revoked, "strict", _service_error),
    "Auth.revoke_key": ("api_key_revoked", _api_key, _revoke_key, _key_revoked, "stands", _journal_write_failed),
    "Apps.revoke": ("entitlement_revoked", lambda w: None,
                    lambda w, _: _apps(w).revoke(w.wid, "docweave", w.b["id"]),
                    _as_writer(_entitlement), "stands", lambda: OSError),
    "Apps.disable_for_workspace": ("workspace_app_disabled", _workspace_app,
                                   lambda w, _: _apps(w).disable_for_workspace(w.wid, "docweave"),
                                   lambda s, w, case: case[1](s), "strict", lambda: OSError),
    "Apps.disable_for_organization": ("organization_app_disabled", _organization_app,
                                      lambda w, _: _apps(w).disable_for_organization(B3_ORGANIZATION, "messages"),
                                      lambda s, w, case: case[1](s), "strict", lambda: OSError),
    "Apps.set_status": ("app_disabled", lambda w: None, lambda w, _: _apps(w).set_status("docweave", "disabled"),
                        _as_writer(_app), "strict", lambda: OSError),
    "Consents.withdraw": ("consent_withdrawn", _consent, _withdraw_consent,
                          lambda s, w, case: case[1](s), "strict", lambda: OSError),
    "ProviderConsents.withdraw": ("agent_provider_consent_withdrawn", _accept_provider, _withdraw_provider,
                                  _provider_withdrawn, "strict", lambda: OSError),
    "AgentAccess.suspend": ("agent_access_suspended", lambda w: None, _suspend_agent, _agent_suspension,
                            "strict", lambda: OSError),
    "RiskControls.record_trip": ("model_dispatch_suspended", lambda w: None, _trip, _dispatch_suspended,
                                 "stands", None),
    "RiskControls.open_breaker": ("model_breaker_opened", lambda w: None, _open_breaker, _breaker_open,
                                  "stands", None),
}


@contextmanager
def _copy_between_the_line_and_its_commit(w, monkeypatch, copy):
    """FIXTURE: a backup taken while the writer's transaction is still open, right after its line
    reached the journal — from a connection of its own, which sees only what was committed."""
    original, taken = journal._fsync_dir, []

    def then_copy(directory):
        original(directory)
        if not taken:
            source = sqlite3.connect(w.store.db_path)
            try:
                source.backup(copy._c())
            finally:
                source.close()
            taken.append(directory)
    monkeypatch.setattr(journal, "_fsync_dir", then_copy)
    try:
        yield taken
    finally:
        monkeypatch.setattr(journal, "_fsync_dir", original)


def _writer_receipt(store, line):
    return store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],))


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_a_backup_taken_between_a_writer_s_line_and_its_commit_gets_the_withdrawal(recovery, monkeypatch, name):
    """The race the time cutoff lost: the backup's copy holds neither the change nor the receipt,
    and its time is later than the line. The line is read whatever its time and replayed into it;
    a backup taken after the COMMIT holds the receipt and is left as it is."""
    w = recovery
    kind, setup, call, withdrawn, _, _ = WRITERS[name]
    ctx = setup(w)
    mid = Store(w.tmp / "mid")
    try:
        with _copy_between_the_line_and_its_commit(w, monkeypatch, mid) as taken:
            call(w, ctx)
        assert taken, "the writer appended its line inside its transaction"
        (line,) = _lines(w, kind)
        assert withdrawn(w.store, w, ctx) and _writer_receipt(w.store, line)
        assert not withdrawn(mid, w, ctx) and not _writer_receipt(mid, line)
        entries = journal.read(w.data, since=now() + 3600)
        assert line in entries
        assert kind in journal.apply(mid, entries)
        assert withdrawn(mid, w, ctx) and _writer_receipt(mid, line)
        after = _copy(w, "after")
        try:
            assert journal.apply(after, entries).get(kind) == 0
        finally:
            w.snapshot._close(after)
    finally:
        w.snapshot._close(mid)


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_a_writer_whose_journal_append_fails(recovery, monkeypatch, name):
    """The directory fsync after the append fails once. A strict writer refuses: nothing changes,
    the attempt is voided, and a backup from before it is not given the withdrawal. A writer whose
    withdrawal stands commits it with its receipt — no void — and says so where it raises; a
    backup from before it is given the withdrawal on restore."""
    w = recovery
    kind, setup, call, withdrawn, mode, raised = WRITERS[name]
    ctx = setup(w)
    older = _copy(w, "older")
    try:
        with _directory_fsync_fails_once(monkeypatch):
            if raised is None:
                call(w, ctx)
            else:
                with pytest.raises(raised()):
                    call(w, ctx)
        (line,) = _lines(w, kind)          # the append itself went through; its directory fsync did not
        entries = journal.read(w.data, since=now() + 3600)
        if mode == "strict":
            assert not withdrawn(w.store, w, ctx) and not _writer_receipt(w.store, line)
            (void,) = _lines(w, journal.VOID_KIND)
            assert (void["attempt_id"], void["voided_kind"]) == (line["attempt_id"], kind)
            assert journal.apply(older, entries) == {journal.VOID_KIND: 1}
            assert not withdrawn(older, w, ctx)
        else:
            assert withdrawn(w.store, w, ctx) and _writer_receipt(w.store, line)
            assert not _lines(w, journal.VOID_KIND) and not _void_row(w.store, line["attempt_id"])
            assert kind in journal.apply(older, entries)
            assert withdrawn(older, w, ctx)
    finally:
        w.snapshot._close(older)


# ---------------------------------------------------------------- an attempt older than the backup
# A line older than the backup, without its receipt there: the backup's own race (copied between
# the line and its COMMIT) or an attempt that never committed and was never voided. Real archives
# and restores (snapshot.fence passes the backup's time to journal.apply). The crashed attempt runs
# in a child process that stops between its line and its COMMIT; everything after it is a later,
# legitimate change made through the code that makes it.
ATTEMPT_CHILD = r'''
import json, os, sys
from synwevo_core import journal
from synwevo_core.store import Store
store = Store(sys.argv[1])
with store.tx():
    journal._intent(store, sys.argv[2], **json.loads(sys.argv[3]))
    os._exit(9)          # the process stops between the line and its COMMIT
'''


def _crashed_attempt(w, kind, fields):
    before = len(_lines(w, kind))
    run = subprocess.run([sys.executable, "-c", ATTEMPT_CHILD, str(w.data), kind, json.dumps(fields)],
                         cwd=Path(synwevo_core.__file__).resolve().parent.parent,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 9, run.stderr
    line = _lines(w, kind)[before]
    assert not w.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],))
    assert not _void_row(w.store, line["attempt_id"]) and not _lines(w, journal.VOID_KIND)
    return line


def _member_role(store, w):
    row = store.one("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?", (w.wid, w.b["id"]))
    return row and row["role"]


def _service_client(w):
    """FIXTURE: a confidential service client and its first secret."""
    _, secret = oidc.Clients(w.store).register(client_id="fixture-service", app_id="docweave", name="Fixture service",
                                               redirect_uris=[], confidential=True, kind="service",
                                               workspace_id=w.wid, api_scopes="docweave.read")
    return secret


def _rotate_committed(w):
    """A rotation as the CLI makes it: the new hash and its journal line in one transaction."""
    with w.store.tx():
        out = oidc.Clients(w.store).rotate_secret("fixture-service")
        journal._intent(w.store, "client_secret_rotated", client_id="fixture-service", secret_hash=out["secret_hash"])
    return out["secret"]


def _restored(w, name):
    restored = _restore_into(w, _backup_into(w, "b-" + name), "restored-" + name)
    return restored


def test_a_crashed_removal_does_not_remove_a_member_re_added_before_the_backup(recovery):
    """The removal stopped before its COMMIT; the administrator removed the member again (committed,
    receipted) and later added them back. A backup after that holds the member: the crashed line is
    older than the backup and has no receipt there, but the backup holds a later receipt and a later
    membership — the attempt never committed. Restored, the member stays, and the attempt is voided
    in the restored copy."""
    w = recovery
    line = _crashed_attempt(w, "member_removed", {"workspace_id": w.wid, "user_id": w.b["id"]})
    assert _member_role(w.store, w) == "editor"
    with w.store.tx():
        w.ident.remove_member(w.wid, w.b["id"])
        journal._intent(w.store, "member_removed", workspace_id=w.wid, user_id=w.b["id"])
    w.ident.add_member(w.wid, w.b["id"], "editor")
    restored = _restored(w, "re-added")
    try:
        assert _member_role(restored, w) == "editor"
        assert _void_row(restored, line["attempt_id"]) == "member_removed"
    finally:
        w.snapshot._close(restored)


def test_a_crashed_downgrade_does_not_undo_a_later_promotion(recovery):
    """Nothing else was journalled in between: the backup's own membership row, changed by the
    promotion after the line, is what shows the downgrade never committed."""
    w = recovery
    _crashed_attempt(w, "role_changed", {"workspace_id": w.wid, "user_id": w.b["id"], "role": "viewer"})
    w.ident.change_role(w.wid, w.b["id"], "admin")
    restored = _restored(w, "promoted")
    try:
        assert _member_role(restored, w) == "admin"
    finally:
        w.snapshot._close(restored)


def test_a_crashed_entitlement_revocation_does_not_undo_a_later_grant(recovery):
    from synwevo_core.apps import Apps, entitlement_state
    w = recovery
    _crashed_attempt(w, "entitlement_revoked", {"workspace_id": w.wid, "app_id": "docweave", "user_id": w.b["id"]})
    Apps(w.store, w.ident).grant(w.wid, "docweave", w.b["id"], actor_id=w.a["id"])
    granted = entitlement_state(w.store, w.wid, "docweave", w.b["id"])
    restored = _restored(w, "granted")
    try:
        assert entitlement_state(restored, w.wid, "docweave", w.b["id"]) == granted
    finally:
        w.snapshot._close(restored)


def test_a_crashed_rotation_does_not_replace_the_secret_issued_after_it(recovery):
    """The rotation to a hash nobody holds stopped before its COMMIT; a later rotation committed,
    and its secret is the one the integration uses. Restored, that secret still authenticates."""
    w = recovery
    first = _service_client(w)
    _crashed_attempt(w, "client_secret_rotated", {"client_id": "fixture-service", "secret_hash": "fixture-nobody-holds"})
    current = _rotate_committed(w)
    restored = _restored(w, "rotated")
    try:
        assert oidc.Clients(restored).check_secret("fixture-service", current)
        assert not oidc.Clients(restored).check_secret("fixture-service", first)
    finally:
        w.snapshot._close(restored)


def test_a_crashed_webhook_disable_does_not_stop_a_subscription_when_a_later_withdrawal_committed(recovery):
    """The subscription was not touched again; an unrelated withdrawal committed after the crashed
    line (its receipt is in the backup) shows the backup was taken after the attempt had ended."""
    w = recovery
    t = now()
    w.store.execute("INSERT INTO webhook_subscriptions (id,tenant,url,events,secret_enc,created_at,updated_at) "
                    "VALUES ('wh_fixture',?,'https://receiver.example.test/hook','[\"*\"]','fixture-sealed',?,?)",
                    (w.wid, t, t))
    _crashed_attempt(w, "webhook_disabled", {"subscription_id": "wh_fixture"})
    Sessions(w.store).create(w.a["id"], workspace_id=None, amr="password")
    with w.store.tx():
        Sessions(w.store).revoke_all_for_user(w.a["id"], "fixture_sign_out")
        journal._intent(w.store, "sessions_revoked", user_id=w.a["id"])
    restored = _restored(w, "webhook")
    try:
        assert restored.one("SELECT status FROM webhook_subscriptions WHERE id='wh_fixture'")["status"] == "active"
    finally:
        w.snapshot._close(restored)


def test_a_rotation_whose_void_failed_does_not_replace_the_secret_issued_after_it(recovery, monkeypatch):
    """The attempt rolled back in this process, but its void could not be written (the disk that
    failed the transaction was still full): an unvoided line, as after a crash. The later rotation
    and the backup decide it, as above."""
    w = recovery
    _service_client(w)
    original = journal._void

    def full_disk(*args, **kwargs):
        raise sqlite3.OperationalError("fixture: database or disk is full")
    monkeypatch.setattr(journal, "_void", full_disk)
    try:
        with pytest.raises(RuntimeError, match="fixture"):
            with w.store.tx():
                journal._intent(w.store, "client_secret_rotated", client_id="fixture-service",
                                secret_hash="fixture-nobody-holds")
                raise RuntimeError("fixture: the rotation failed after its line")
    finally:
        monkeypatch.setattr(journal, "_void", original)
    (dead,) = _lines(w, "client_secret_rotated")
    assert not _void_row(w.store, dead["attempt_id"]) and not _lines(w, journal.VOID_KIND)
    current = _rotate_committed(w)
    restored = _restored(w, "void-failed")
    try:
        assert oidc.Clients(restored).check_secret("fixture-service", current)
    finally:
        w.snapshot._close(restored)


def test_a_crashed_removal_nothing_came_after_still_lands_and_is_named_for_review(recovery, capsys):
    """Fail-closed residual: nothing in the backup is later than the crashed line — no change to the
    membership, no other withdrawal — so it cannot be told apart from the backup's own race and is
    replayed, once (a backup of the restored copy holds its receipt). The report names it."""
    w = recovery
    line = _crashed_attempt(w, "member_removed", {"workspace_id": w.wid, "user_id": w.b["id"]})
    capsys.readouterr()
    restored = _restored(w, "residual")
    try:
        out = capsys.readouterr().out
        assert _member_role(restored, w) is None
        assert restored.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],))
        assert "早于备份时间、备份却没有回执的预写撤销 1 条（member_removed 1）" in out, out
    finally:
        w.snapshot._close(restored)


def test_a_backup_inside_the_race_window_is_still_given_the_withdrawal(recovery, monkeypatch):
    """The backup's own race, through the real fence: its time (taken after the line) is later than
    the line, it holds neither the change nor the receipt, and nothing in it is later than the line.
    The withdrawal is replayed into it."""
    w = recovery
    original, taken = journal._fsync_dir, []

    def then_back_up(directory):
        original(directory)
        if not taken:
            taken.append(_backup_into(w, "b-race"))
    monkeypatch.setattr(journal, "_fsync_dir", then_back_up)
    try:
        with w.store.tx():
            w.ident.remove_member(w.wid, w.b["id"])
            journal._intent(w.store, "member_removed", workspace_id=w.wid, user_id=w.b["id"])
    finally:
        monkeypatch.setattr(journal, "_fsync_dir", original)
    (archive,) = taken
    restored = _restore_into(w, archive, "restored-race")
    try:
        assert _member_role(restored, w) is None
    finally:
        w.snapshot._close(restored)


def test_the_proof_counts_only_what_is_later_than_the_line_and_no_later_than_the_backup(recovery):
    """journal._never_committed, on hand-made lines (FIXTURE): a receipt of a line written after the
    backup's time, or a change to the line's object made after it (as the restore's own writes
    are), proves nothing about the backup; the same evidence no later than the backup does."""
    w = recovery
    t0 = now()
    line = {"at": t0, "kind": "member_removed", "workspace_id": w.wid, "user_id": w.b["id"],
            "attempt_id": "ja_fixture_line"}
    later = {"at": t0 + 200, "kind": "role_changed", "workspace_id": w.wid, "user_id": w.a["id"], "role": "admin",
             "attempt_id": "ja_fixture_later"}
    w.store.execute("INSERT INTO meta (key,value) VALUES ('journal_receipt:ja_fixture_later','1')")

    def held(key):
        return bool(w.store.one("SELECT 1 FROM meta WHERE key=?", (key,)))
    c = w.store._c()
    assert journal._never_committed(c, [line, later], t0 + 100, held) == set()
    assert journal._never_committed(c, [line, later], t0 + 200, held) == {"ja_fixture_line"}
    w.store.execute("UPDATE memberships SET updated_at=? WHERE workspace_id=? AND user_id=?", (t0 + 50, w.wid, w.b["id"]))
    assert journal._never_committed(c, [line], t0 + 40, held) == set()
    assert journal._never_committed(c, [line], t0 + 50, held) == {"ja_fixture_line"}
    # an attempt newer than the backup, or one the backup holds the receipt of, is not in question
    assert journal._never_committed(c, [line], t0 - 1, held) == set()
    w.store.execute("INSERT INTO meta (key,value) VALUES ('journal_receipt:ja_fixture_line','1')")
    assert journal._never_committed(c, [line], t0 + 50, held) == set()


# ---------------------------------------------------------------- a restore without the journal
def test_an_abandoned_history_s_rotation_stays_dead_after_a_restore_without_the_journal(tmp_path, monkeypatch):
    """1. a service client with secret S1, backup b0; 2. rotated to S2 (committed, receipted);
    3. b0 restored in place with --no-journal — the live journal, S2's line included, stays beside
    it; 4. the operator re-enables the client and re-keys it to S3, backup b1; 5. a journalled
    restore of b1. S3 authenticates and S2 does not: the abandoned history's rotation is not
    replayed over the new history's re-key (journal.EPOCH_KEY; the later receipted rotation and the
    client's later change prove it too). Synthetic instance under tmp_path only."""
    import importlib.util
    from types import SimpleNamespace
    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.identity import Identity
    path = Path(__file__).resolve().parents[2] / "apps/docs/scripts/snapshot.py"
    spec = importlib.util.spec_from_file_location("_epoch_snapshot", path)
    snapshot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snapshot)
    data = tmp_path / "live"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(data))
    store = Store(data)
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    owner = ident.create_user("epoch-owner@example.test", "synthetic-epoch-passphrase-123", status="active",
                              with_personal_workspace=False)
    wid = ident.create_workspace("Fixture epoch workspace", owner_id=owner["id"])["id"]
    _, s1 = oidc.Clients(store).register(client_id="fixture-service", app_id="docweave", name="Fixture service",
                                         redirect_uris=[], confidential=True, kind="service", workspace_id=wid,
                                         api_scopes="docweave.read")

    def rotate():
        with store.tx():
            out = oidc.Clients(store).rotate_secret("fixture-service")
            journal._intent(store, "client_secret_rotated", client_id="fixture-service", secret_hash=out["secret_hash"])
        return out["secret"]

    def backup(name):
        snapshot._close(store)
        out = tmp_path / name
        assert snapshot.create(SimpleNamespace(data=str(data), out=str(out))) == 0
        (archive,) = list(out.glob("*.tar.gz"))
        return archive

    b0 = backup("b0")
    s2 = rotate()
    snapshot._close(store)
    assert snapshot.restore(SimpleNamespace(archive=str(b0), into=str(data), force=True, journal_from=None,
                                            no_journal=True)) == 0
    assert [e["kind"] for e in journal.read(data)] == ["client_secret_rotated"]
    assert oidc.Clients(store).enable("fixture-service")
    s3 = rotate()
    b1 = backup("b1")
    into = tmp_path / "restored"
    assert snapshot.restore(SimpleNamespace(archive=str(b1), into=str(into), force=False, journal_from=str(data),
                                            no_journal=False)) == 0
    restored = Store(into)
    try:
        clients = oidc.Clients(restored)
        assert clients.check_secret("fixture-service", s3)
        assert not clients.check_secret("fixture-service", s2) and not clients.check_secret("fixture-service", s1)
    finally:
        snapshot._close(restored)
