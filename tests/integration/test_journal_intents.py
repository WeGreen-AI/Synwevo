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
from synwevo_core import journal, workspace_lifecycle
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
    """Reading is wider than writing (journal.ATTEMPTED): a kind this release still writes with the
    time cutoff, written ahead by a later release (FIXTURE: lines appended by hand as `_intent`
    would) — a `member_removed` with an `attempt_id`, and the void of a `role_changed` attempt — is
    accepted, read whatever its time, replayed into a copy without its receipt and then receipted,
    and skipped by its void; a second replay changes nothing."""
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
