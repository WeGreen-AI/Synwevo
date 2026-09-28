"""Store.after_commit: listeners that take other locks or wait on other threads run after the
outermost transaction commits, outside the store's write lock — the first link of the process's lock
order (the live editing rooms' hook; review 3 of sheet-live step ②). Probes from the fourth review.

TEST FIXTURES: a store in a temporary directory with a one-table probe schema."""
from __future__ import annotations

import threading

import pytest


@pytest.fixture()
def store(tmp_path):
    from synwevo_core.store import Store
    s = Store(tmp_path / "data")
    with s.tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS probe (k TEXT PRIMARY KEY, v TEXT)")
    return s


def test_it_runs_after_the_outermost_commit_outside_the_write_lock(store):
    held = []
    with store.tx() as c:
        c.execute("INSERT INTO probe VALUES ('a','1')")
        with store.tx():
            store.after_commit(lambda: held.append((store._write_lock._is_owned(), store.one("SELECT v FROM probe WHERE k='a'"))))
        assert held == [], "not while the transaction is open"
    assert held == [(False, {"v": "1"})]


def test_outside_a_transaction_it_runs_now_on_the_caller_s_thread(store):
    seen = []
    store.after_commit(lambda: seen.append(threading.current_thread().name))
    assert seen == [threading.current_thread().name]


def test_a_rollback_drops_it(store):
    seen = []
    with pytest.raises(RuntimeError):
        with store.tx():
            with store.tx():
                store.after_commit(lambda: seen.append("inner"))
            raise RuntimeError("outer fails")
    with store.tx():
        pass
    assert seen == []


def test_an_inner_block_s_error_caught_by_the_outer_one_still_commits_and_runs_it(store):
    """Nested blocks join the outer transaction (no savepoints): what an inner block wrote and queued
    commits with the outer one when its error is caught."""
    seen = []
    with store.tx():
        try:
            with store.tx() as c:
                c.execute("INSERT INTO probe VALUES ('a','1')")
                store.after_commit(lambda: seen.append("inner"))
                raise ValueError("inner fails")
        except ValueError:
            pass
    assert store.one("SELECT v FROM probe WHERE k='a'") == {"v": "1"} and seen == ["inner"]


def test_a_failing_listener_neither_breaks_the_caller_nor_skips_the_others(store):
    seen = []
    with store.tx():
        store.after_commit(lambda: 1 / 0)
        store.after_commit(lambda: seen.append("second"))
    assert seen == ["second"]


def test_a_base_exception_in_a_listener_escapes_after_the_commit_and_the_next_transaction_is_clean(store):
    seen = []

    def boom():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        with store.tx() as c:
            c.execute("INSERT INTO probe VALUES ('b','1')")
            store.after_commit(boom)
            store.after_commit(lambda: seen.append("skipped"))
    assert store.one("SELECT v FROM probe WHERE k='b'") == {"v": "1"} and seen == []
    with store.tx():
        store.after_commit(lambda: seen.append("next tx"))
    assert seen == ["next tx"]


def test_a_listener_may_queue_more_and_open_its_own_transaction(store):
    seen = []

    def first():
        store.after_commit(lambda: seen.append("queued-from-callback"))      # no transaction: runs now
        with store.tx() as c:
            c.execute("INSERT INTO probe VALUES ('c','1')")
            store.after_commit(lambda: seen.append("from-callback-tx"))
        seen.append("first-done")
    with store.tx():
        store.after_commit(first)
        store.after_commit(lambda: seen.append("second"))
    assert seen == ["queued-from-callback", "from-callback-tx", "first-done", "second"]
