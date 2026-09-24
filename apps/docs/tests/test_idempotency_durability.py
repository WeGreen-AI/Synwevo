"""Durability regressions using only synthetic records in pytest's isolated temp stores."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import Request

from synwevo_core.context import as_tenant
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store, now
from synwevo_docs.api import idempotency as I
from synwevo_docs.services import App

ROUTE = "/api/v1/projects"
BODY = {"name": "synthetic durability fixture"}


def _request(key="durability", *, method="POST", principal=None):
    req = Request({"type": "http", "method": method, "path": ROUTE,
                   "headers": [(b"idempotency-key", key.encode("ascii"))]})
    if principal:
        req.state.principal = principal
    return req


@pytest.fixture
def app_(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    app = App(Store(tmp_path / "synthetic-store"))
    with as_tenant("default"):
        yield app
    app.parse_pool.shutdown(wait=True)
    app.store._c().close()


def _create(app, request=None, work=None, **kwargs):
    return I.Idempotency(app.store).run(
        request or _request(), ROUTE, BODY,
        work or (lambda: app.create_project(BODY["name"], origin="test:idempotency-durability")),
        replay_check=lambda out: app.get_project(out["id"]), **kwargs)


def _counts(store):
    return tuple(store.one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
                 for table in ("projects", "mapping_revs", "idempotency"))


def test_response_persistence_failure_rolls_back_all_work(app_):
    store = app_.store
    # A real SQLite trigger rejects the last write, after both service inserts succeeded.
    store.execute("CREATE TRIGGER synthetic_fail_response BEFORE UPDATE OF response_status ON idempotency "
                  "BEGIN SELECT RAISE(ABORT, 'synthetic response persistence failure'); END")
    made = []

    def work():
        out = app_.create_project(BODY["name"], origin="test:idempotency-durability")
        assert _counts(store) == (1, 1, 1)
        made.append(out["id"])
        return out

    with pytest.raises(sqlite3.IntegrityError, match="synthetic response persistence failure"):
        _create(app_, work=work)
    assert len(made) == 1 and _counts(store) == (0, 0, 0)
    store.execute("DROP TRIGGER synthetic_fail_response")
    first = _create(app_)
    again = _create(app_)
    assert first.body == again.body and again.headers[I.REPLAY_HEADER] == "true"
    assert json.loads(first.body)["id"] != made[0] and _counts(store) == (1, 1, 1)


def test_commit_failure_rolls_back_work_and_response(app_):
    store = app_.store
    denied = []

    def fail_commit(action, arg1, arg2, db, trigger):
        if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
            denied.append(arg1)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    store._c().set_authorizer(fail_commit)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            _create(app_)
    finally:
        store._c().set_authorizer(None)
    assert denied == ["COMMIT"] and _counts(store) == (0, 0, 0)
    assert _create(app_).status_code == 201 and _counts(store) == (1, 1, 1)


@pytest.mark.parametrize("failure", ["work", "serialization"])
def test_failure_after_business_writes_releases_key_by_rollback(app_, failure):
    def work():
        out = app_.create_project(BODY["name"], origin="test:idempotency-durability")
        if failure == "work":
            raise ServiceError(400, "synthetic_failure", "synthetic post-write failure")
        return {**out, "cannot_render": float("nan")}

    with pytest.raises(ServiceError if failure == "work" else ValueError):
        _create(app_, work=work)
    assert _counts(app_.store) == (0, 0, 0)
    assert _create(app_).status_code == 201 and _counts(app_.store) == (1, 1, 1)


@pytest.mark.parametrize("body", [BODY, {"name": "changed synthetic payload"}])
def test_replay_callback_checks_deleted_object_without_reexecuting_work(app_, body):
    response = _create(app_)
    pid = json.loads(response.body)["id"]
    app_.delete_project(pid)
    original = app_.store.one("SELECT * FROM idempotency")
    checked = []

    def replay_check(out):
        checked.append(out)
        app_.get_project(out["id"])

    with pytest.raises(ServiceError) as exc:
        I.Idempotency(app_.store).run(_request(), ROUTE, body,
                                     lambda: pytest.fail("deleted result must not be recreated"),
                                     replay_check=replay_check)
    assert exc.value.status == 404
    assert checked == [json.loads(response.body)]
    assert app_.store.one("SELECT * FROM idempotency") == original
    assert _counts(app_.store) == (1, 1, 1)


def test_legacy_complete_response_replays_after_restart_with_owner_scope(app_):
    out = app_.create_project(BODY["name"], origin="test:legacy-response")
    principal = {"kind": "api_key", "key_id": "synthetic-old-key", "lineage": "synthetic-lineage"}
    scope = I.scope_of("POST", ROUTE, "api:synthetic-lineage")
    # Exact pre-repair schema; no new metadata is required to replay a known success.
    app_.store.insert("idempotency", {"tenant": "default", "scope": scope, "key": "durability",
                                     "request_hash": I.fingerprint(BODY), "created_at": now(),
                                     "response_status": 202, "response_body": json.dumps(out, ensure_ascii=False),
                                     "resource_id": out["id"], "principal": "api:synthetic-old-key"})
    restarted = App(Store(app_.store.root))
    try:
        rotated = {**principal, "key_id": "synthetic-rotated-key"}
        replay = _create(restarted, _request(principal=rotated))
        assert replay.status_code == 202 and json.loads(replay.body) == out
        assert replay.headers[I.REPLAY_HEADER] == "true"
        other = _create(restarted, _request(principal={**rotated, "lineage": "synthetic-other"}))
        assert I.REPLAY_HEADER not in other.headers and json.loads(other.body)["id"] != out["id"]
        assert _counts(restarted.store) == (2, 2, 2)
    finally:
        restarted.parse_pool.shutdown(wait=True)
        restarted.store._c().close()


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize("response_status,response_body", [(None, None), (201, None), (None, "{}")])
def test_unknown_legacy_outcome_survives_ttl_and_purge(app_, method, response_status, response_body):
    store = app_.store
    # A real legacy business result may exist even though its response was never persisted.
    app_.create_project(BODY["name"], origin="test:legacy-unknown")
    store.insert("idempotency", {"tenant": "default", "scope": I.scope_of(method, ROUTE, ""),
                                 "key": "durability", "request_hash": I.fingerprint(BODY),
                                 "created_at": now() - I.TTL_S - I.IN_FLIGHT_STALE_S - 60,
                                 "response_status": response_status, "response_body": response_body})
    original = store.one("SELECT * FROM idempotency")
    assert I.purge_expired(store) == 0
    for body in (BODY, {"name": "different"}):
        with pytest.raises(ServiceError) as exc:
            I.Idempotency(store).run(_request(method=method), ROUTE, body,
                                    lambda: pytest.fail("unknown outcome must never run work"),
                                    replay_check=lambda out: pytest.fail("incomplete response cannot be replayed"))
        assert exc.value.status == 409 and exc.value.code == "idempotency_outcome_unknown"
        assert store.one("SELECT * FROM idempotency") == original and _counts(store) == (1, 1, 1)


@pytest.mark.parametrize("method", ["POST", "PUT"])
def test_complete_response_expires_for_lookup_and_purge(app_, method):
    first = _create(app_, _request(method=method))
    app_.store.execute("UPDATE idempotency SET created_at=?", (now() - I.TTL_S - 60,))
    second = _create(app_, _request(method=method))
    assert I.REPLAY_HEADER not in second.headers and first.body != second.body
    assert _counts(app_.store) == (2, 2, 1)
    app_.store.execute("UPDATE idempotency SET created_at=?", (now() - I.TTL_S - 60,))
    assert I.purge_expired(app_.store) == 1 and _counts(app_.store) == (2, 2, 0)


@pytest.mark.parametrize("shared_store", [True, False])
def test_same_body_parallel_waits_and_replays_once(app_, shared_store):
    second_app = App(app_.store if shared_store else Store(app_.store.root))
    observer = Store(app_.store.root)
    start = threading.Barrier(3)
    entered, release = threading.Event(), threading.Event()
    attempted = [threading.Event(), threading.Event()]
    executions = []

    def call(index, app):
        with as_tenant("default"):
            start.wait(timeout=5)
            attempted[index].set()

            def work():
                out = app.create_project(BODY["name"], origin="test:parallel")
                executions.append(out["id"])
                entered.set()
                assert release.wait(5), "test coordinator did not release transaction"
                return out

            return _create(app, work=work)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(call, i, app) for i, app in enumerate((app_, second_app))]
            try:
                start.wait(timeout=5)
                assert entered.wait(5) and all(event.wait(5) for event in attempted)
                # Other connections cannot observe either the business rows or the claim yet.
                assert _counts(observer) == (0, 0, 0)
            finally:
                release.set()
            results = [future.result(timeout=10) for future in futures]
        assert len(executions) == 1 and results[0].body == results[1].body
        assert [r.headers.get(I.REPLAY_HEADER) for r in results].count("true") == 1
        assert all(r.status_code == 201 for r in results) and _counts(observer) == (1, 1, 1)
    finally:
        second_app.parse_pool.shutdown(wait=True)
        observer._c().close()


_CRASH_PROGRAM = r'''
import os
import sys
from pathlib import Path
from fastapi import Request
from synwevo_core.context import as_tenant
from synwevo_core.store import Store
from synwevo_docs.api.idempotency import Idempotency
from synwevo_docs.services import App

root, phase = Path(sys.argv[1]), sys.argv[2]

def crash():
    (root / "synthetic-crash-marker").write_text(phase)
    os._exit(73)  # No Python unwinding, rollback handler or normal connection close.

class CrashStore(Store):
    def execute(self, sql, args=()):
        result = super().execute(sql, args)
        if phase == "after_response" and sql.startswith("UPDATE idempotency SET response_status="):
            assert self.one("SELECT response_status FROM idempotency")["response_status"] == 201
            crash()
        return result

store = CrashStore(root)
app = App(store)
req = Request({"type": "http", "method": "POST", "path": "/api/v1/projects",
               "headers": [(b"idempotency-key", b"durability")]})
body = {"name": "synthetic durability fixture"}
def work():
    out = app.create_project(body["name"], origin="test:process-death")
    assert store.one("SELECT COUNT(*) AS n FROM mapping_revs")["n"] == 1
    if phase == "after_work":
        crash()
    return out
with as_tenant("default"):
    Idempotency(store).run(req, "/api/v1/projects", body, work,
                          replay_check=lambda out: app.get_project(out["id"]))
raise AssertionError("crash injection was not reached")
'''


@pytest.mark.parametrize("phase", ["after_work", "after_response"])
def test_process_exit_before_commit_rolls_back_then_restart_retries_once(tmp_path, phase):
    root = tmp_path / "synthetic-crashed-store"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SYNWEVO_", "DOCWEAVE_"))}
    env.update(SYNWEVO_TEST_INSTANCE="1", SYNWEVO_DATA_DIR=str(root))
    child = subprocess.run([sys.executable, "-c", _CRASH_PROGRAM, str(root), phase],
                           cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert child.returncode == 73, child.stdout + child.stderr
    assert (root / "synthetic-crash-marker").read_text() == phase
    restarted = App(Store(root))
    try:
        assert _counts(restarted.store) == (0, 0, 0)
        with as_tenant("default"):
            first = _create(restarted)
            replay = _create(restarted)
        assert first.body == replay.body and replay.headers[I.REPLAY_HEADER] == "true"
        assert _counts(restarted.store) == (1, 1, 1)
    finally:
        restarted.parse_pool.shutdown(wait=True)
        restarted.store._c().close()


@pytest.mark.parametrize("rollback", [False, True])
def test_source_parser_waits_for_creator_commit_or_rollback(app_, monkeypatch, rollback):
    pid = app_.create_project("synthetic parsing fixture", origin="test:parse-transaction")["id"]
    rev = app_.get_project(pid)["rev"]
    started, finished = threading.Event(), threading.Event()
    original = app_._parse_source

    def observe_parser(sid):
        started.set()
        try:
            original(sid)
        finally:
            finished.set()

    monkeypatch.setattr(app_, "_parse_source", observe_parser)

    def work():
        out = app_.add_source(pid, "synthetic.csv", b"name,value\nexample,1\n")
        assert started.wait(5)
        assert not finished.wait(0.15), "parser treated the uncommitted source as absent"
        if rollback:
            raise ServiceError(400, "synthetic_failure", "synthetic upload rollback")
        return out

    def run():
        return I.Idempotency(app_.store).run(
            _request(), "/api/v1/projects/{pid}/files", {"pid": pid}, work,
            replay_check=lambda out: app_.get_source(out["id"]))

    if rollback:
        with pytest.raises(ServiceError, match="synthetic upload rollback"):
            run()
    else:
        response = run()
    assert finished.wait(5)
    if rollback:
        assert app_.store.one("SELECT COUNT(*) AS n FROM source_files")["n"] == 0
        assert app_.store.one("SELECT COUNT(*) AS n FROM idempotency")["n"] == 0
        assert app_.get_project(pid)["rev"] == rev
    else:
        out = json.loads(response.body)
        assert app_.get_source(out["id"])["status"] == "ready"
        assert app_.get_project(pid)["rev"] == rev + 2
