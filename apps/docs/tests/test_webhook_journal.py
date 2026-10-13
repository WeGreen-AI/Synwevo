"""Webhook stops are journalled ahead, inside the transaction that makes them.

`Subscriptions.delete` (a person removes a subscription) and `WebhookDispatcher.disable_subscription`
(the dispatcher gives up on a destination) write `webhook_disabled` through `journal._intent`: a fresh
`attempt_id`, a receipt in `meta` that commits with the change, and the line appended before the
COMMIT. Three consequences, each tested here per writer:

* a backup taken between the line and the COMMIT holds neither the change nor the receipt, and a
  restore from it disables the subscription (the time cutoff alone would have lost it);
* a delete whose journal append fails is refused (503), nothing changes, and the attempt is voided,
  so no restore applies it; the dispatcher's stop stands without its line, with its receipt;
* a process that stops right after the dispatcher's COMMIT has already written the line (it used to
  be appended after the transaction).

FIXTURES: the `hub` application of test_webhook_support. Backups are real SQLite copies into Stores
of this test's own directory, and a restore replays the journal into them with `journal.apply`.
Failures are injected one at a time; every monkeypatch is put back with setattr(original).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

from synwevo_core import journal
from synwevo_core.store import Store, now

from .test_webhook_support import hub  # noqa: F401  (hub is a fixture)

BASE = "/api/v1/webhooks/subscriptions"
KIND = "webhook_disabled"


# ---------------------------------------------------------------- fixtures
def _copy_into(store, copy: Store) -> Store:
    """FIXTURE: a backup — what is committed, copied by connections of its own (from whichever
    thread the writer runs on) into a Store of this test's directory."""
    source, destination = sqlite3.connect(store.db_path), sqlite3.connect(copy.db_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return copy


def _close(*stores) -> None:
    for s in stores:
        c = getattr(s._local, "c", None)
        if c is not None:
            c.close()
            s._local.c = None


def _lines(store, kind=KIND) -> list[dict]:
    p = journal.path(store.root)
    if not p.exists():
        return []
    return [e for e in (json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()) if e["kind"] == kind]


def _receipt(store, line) -> bool:
    return bool(store.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],)))


def _status(store, sid) -> tuple:
    row = store.one("SELECT status, disabled_reason, deleted_at FROM webhook_subscriptions WHERE id=?", (sid,))
    return row["status"], row["disabled_reason"], row["deleted_at"]


@contextmanager
def _copied_between_the_line_and_its_commit(store, monkeypatch, copy: Store):
    """FIXTURE: a backup taken while the writer's transaction is still open, right after its line
    reached the journal (after the directory fsync), from a connection that sees only what was
    committed."""
    original, taken = journal._fsync_dir, []

    def then_copy(directory):
        original(directory)
        if not taken:
            _copy_into(store, copy)
            taken.append(directory)
    monkeypatch.setattr(journal, "_fsync_dir", then_copy)
    try:
        yield taken
    finally:
        monkeypatch.setattr(journal, "_fsync_dir", original)


@contextmanager
def _directory_fsync_fails_once(monkeypatch):
    """FIXTURE: the line is appended and its file fsynced; the directory fsync after it fails once."""
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


@contextmanager
def _journal_as_a_stop_right_after_the_commit_leaves_it(store):
    """FIXTURE: the journal's lines as they are on disk the moment the transaction that disabled
    the subscription has committed — what a process that stopped there would leave. A TEMP trigger
    on this thread's connection sees the row turn disabled and queues the read for right after that
    transaction's COMMIT (Store.after_commit), before any code after the transaction runs."""
    seen: list[list[dict]] = []
    c = store._c()

    def disabled():
        store.after_commit(lambda: seen.append(_lines(store)))
        return 0
    c.create_function("fixture_disabled", 0, disabled)
    c.execute("CREATE TEMP TRIGGER fixture_stop_after_commit AFTER UPDATE OF status ON webhook_subscriptions "
              "WHEN NEW.status='disabled' AND OLD.status<>'disabled' BEGIN SELECT fixture_disabled(); END")
    try:
        yield seen
    finally:
        c.execute("DROP TRIGGER fixture_stop_after_commit")


def _delete(hub, sid):
    return hub.c.delete(f"{BASE}/{sid}", headers=hub.keys["admin"])


# ---------------------------------------------------------------- a person's delete (strict)
def test_a_backup_taken_between_a_delete_s_line_and_its_commit_is_disabled_on_restore(hub, tmp_path, monkeypatch):
    sid = hub.create(hub.receiver().url)["id"]
    mid = Store(tmp_path / "copies" / "mid")
    after = Store(tmp_path / "copies" / "after")
    try:
        with _copied_between_the_line_and_its_commit(hub.store, monkeypatch, mid) as taken:
            assert _delete(hub, sid).json() == {"deleted": sid}
        assert taken, "the line was appended inside the delete's transaction"
        (line,) = _lines(hub.store)
        assert line["subscription_id"] == sid and _receipt(hub.store, line)
        # the backup in the window: neither the delete nor its receipt…
        assert _status(mid, sid) == ("active", None, None) and not _receipt(mid, line)
        # …and later than the line, so only `attempt_id` gets the line read for it
        entries = journal.read(hub.store.root, since=now() + 3600)
        assert entries == [line]
        assert journal.apply(mid, entries) == {KIND: 1}
        assert _status(mid, sid)[0] == "disabled" and _receipt(mid, line)
        # a backup taken after the COMMIT holds the receipt: left as it is
        _copy_into(hub.store, after)
        assert journal.apply(after, entries) == {KIND: 0}
    finally:
        _close(mid, after)


def test_a_delete_whose_journal_fails_after_the_append_is_refused_and_voided(hub, tmp_path, monkeypatch):
    sid = hub.create(hub.receiver().url)["id"]
    older = _copy_into(hub.store, Store(tmp_path / "copies" / "older"))
    try:
        with _directory_fsync_fails_once(monkeypatch):
            r = _delete(hub, sid)
        assert r.status_code == 503 and r.json()["error"]["code"] == "journal_unavailable", r.text
        # nothing changed, and the attempt the line names is proven never to have committed
        assert _status(hub.store, sid) == ("active", None, None)
        (line,) = _lines(hub.store)
        (void,) = _lines(hub.store, journal.VOID_KIND)
        assert (void["attempt_id"], void["voided_kind"]) == (line["attempt_id"], KIND)
        assert not _receipt(hub.store, line)
        assert hub.store.one("SELECT value FROM meta WHERE key=?", ("journal_void:" + line["attempt_id"],))["value"] == KIND
        # a restore from a backup taken before it does not disable the subscription
        entries = journal.read(hub.store.root, since=now() + 3600)
        assert entries == [line, void]
        assert journal.apply(older, entries) == {journal.VOID_KIND: 1}
        assert _status(older, sid) == ("active", None, None)
        # the retry is journalled with an attempt of its own, and receipted
        assert _delete(hub, sid).json() == {"deleted": sid}
        retried = _lines(hub.store)[-1]
        assert retried["attempt_id"] != line["attempt_id"] and _receipt(hub.store, retried)
    finally:
        _close(older)


# ---------------------------------------------------------------- the dispatcher's stop (stands)
def test_a_backup_taken_between_a_dispatcher_stop_s_line_and_its_commit_is_disabled_on_restore(hub, tmp_path, monkeypatch):
    sid = hub.create(hub.receiver().url)["id"]
    d = hub.dispatcher()
    mid = Store(tmp_path / "copies" / "mid")
    after = Store(tmp_path / "copies" / "after")
    try:
        with _copied_between_the_line_and_its_commit(hub.store, monkeypatch, mid) as taken:
            assert d.disable_subscription(sid, "gone") is True
        assert taken, "the line was appended inside the stop's transaction"
        (line,) = _lines(hub.store)
        assert _status(hub.store, sid)[:2] == ("disabled", "gone") and _receipt(hub.store, line)
        assert _status(mid, sid)[0] == "active" and not _receipt(mid, line)
        entries = journal.read(hub.store.root, since=now() + 3600)
        assert entries == [line]
        assert journal.apply(mid, entries) == {KIND: 1}
        assert _status(mid, sid)[:2] == ("disabled", "journal_replay") and _receipt(mid, line)
        _copy_into(hub.store, after)
        assert journal.apply(after, entries) == {KIND: 0}
        assert _status(after, sid)[:2] == ("disabled", "gone")
        # a second stop changes nothing and writes nothing
        assert d.disable_subscription(sid, "failing") is False and _lines(hub.store) == [line]
    finally:
        _close(mid, after)


def test_a_dispatcher_stop_whose_journal_fails_stands_with_its_receipt(hub, tmp_path, monkeypatch):
    sid = hub.create(hub.receiver().url)["id"]
    d = hub.dispatcher()
    older = _copy_into(hub.store, Store(tmp_path / "copies" / "older"))
    try:
        with _directory_fsync_fails_once(monkeypatch):
            assert d.disable_subscription(sid, "gone") is True
        assert "安全日志写入失败" in d.last_error and sid in d.last_error
        (line,) = _lines(hub.store)                    # appended; its directory fsync failed
        assert _status(hub.store, sid)[:2] == ("disabled", "gone") and _receipt(hub.store, line)
        assert not _lines(hub.store, journal.VOID_KIND)
        assert not hub.store.one("SELECT 1 FROM meta WHERE key=?", ("journal_void:" + line["attempt_id"],))
        entries = journal.read(hub.store.root, since=now() + 3600)
        assert journal.apply(older, entries) == {KIND: 1}
        assert _status(older, sid)[0] == "disabled"
    finally:
        _close(older)


def test_a_dispatcher_stop_is_in_the_journal_when_its_commit_returns(hub, tmp_path):
    """The line used to be appended after the transaction: a process stopping between the COMMIT
    and the append lost it for every older backup. Now the journal already holds it then."""
    sid = hub.create(hub.receiver().url)["id"]
    d = hub.dispatcher()
    older = _copy_into(hub.store, Store(tmp_path / "copies" / "older"))
    try:
        with _journal_as_a_stop_right_after_the_commit_leaves_it(hub.store) as seen:
            assert d.disable_subscription(sid, "failing") is True
        (on_disk,) = seen
        (line,) = on_disk
        assert line["subscription_id"] == sid and line["attempt_id"]
        assert journal.apply(older, on_disk) == {KIND: 1}
        assert _status(older, sid)[:2] == ("disabled", "journal_replay")
    finally:
        _close(older)
