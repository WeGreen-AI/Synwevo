"""Real Docs rooms release their object graphs after durable close and failed close.

TEST FIXTURES: one account, a synthetic template, and an isolated Store per iteration.
The failure case uses an SQLite trigger: the real closing write reaches a database that
refuses it. No application/Store/room shutdown method is replaced by the test.
"""
from __future__ import annotations

import json
import logging
import threading
import weakref
import zlib
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from pycrdt import Doc, create_sync_message, create_update_message

from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER
from synwevo_docs import collab, collab_sheet
from synwevo_docs.api.app import create_app
from synwevo_docs.collab_pm import read as read_doc

from .test_collab import DOC, PW, Peer
from .test_sheet_collab import SHEET
from .test_sheet_live import SheetPeer, put


class _TextPeer(Peer):
    """Real protocol peer without a native observer retaining the *test client*.

    The original Peer observer also retained its owner. A state-vector delta sends
    the same real CRDT update, independently of the peer's observer lifecycle.
    """

    def __init__(self, client, tid):
        super().__init__(client, tid)            # a failed hello or sync closes the socket (Peer.connect)
        self.hello = self.connect()

    def edit(self, fn):
        before = self.doc.get_state()
        with self.doc.transaction():
            fn()
        self.ws.send_bytes(create_update_message(self.doc.get_update(before)))
        # FIFO sync reply proves the server applied the preceding edit, including
        # when an already-dirty reopened room sends no new dirty-state notification.
        self.synced = False
        self.ws.send_bytes(create_sync_message(self.doc))
        while not self.synced:
            self.recv(want="sync")


class _FormattedLogs(logging.Handler):
    """Keep error evidence as strings, never traceback frames holding the room."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(self.format(record))


@contextmanager
def _closing_logs():
    # Pytest's captured exception records themselves retain Room/Store through the
    # expected SQLite failure's traceback. Format this logger's evidence eagerly.
    logger = logging.getLogger("synwevo.docs.collab")
    handler = _FormattedLogs()
    previous = logger.propagate
    logger.addHandler(handler)
    logger.propagate = False
    try:
        yield handler.messages
    finally:
        logger.removeHandler(handler)
        logger.propagate = previous
        handler.close()


def _open(client, tid, kind, expected):
    peer = _TextPeer(client, tid) if kind == "text" else SheetPeer(client, tid)
    try:
        if kind == "text":
            assert peer.texts() == ["客户：" + expected]
        else:
            assert peer.hello["live"] is True
            assert peer.cells().get("A2", {}).get("v", "") == expected
        return peer
    except BaseException:
        peer.close()
        raise


def _edit(peer, kind, value):
    if kind == "text":
        peer.append_text(value)
    else:
        assert peer.do(put(peer, "A2", value))["ok"] is True


def _assert_draft(store, key, kind, expected):
    row = store.one("SELECT * FROM collab_docs WHERE room=?", (key,))
    assert row is not None, "the last editor's close must persist the real draft"
    assert row["head"] == 1 and row["dirty"] == 1
    if kind == "text":
        doc = Doc()
        doc.apply_update(bytes(row["state"]))
        content = read_doc(doc)["body"]["content"][0]["content"]
        assert content[0]["text"] == "客户：" + expected
    else:
        state = json.loads(zlib.decompress(bytes(row["state"])))
        editor = collab_sheet.project(state)
        assert editor["sheets"][0]["cells"]["A2"]["v"] == expected


def _room_refs(hub, key, label):
    room = hub.rooms[key]
    return {f"{label}.Room": weakref.ref(room), f"{label}.Doc": weakref.ref(room.doc)}


def _close(peer, hub, key):
    peer.close()
    # Wait on actual work, not GC timing. settled() signals inside the closing
    # thread's finally block; join that thread too so its final frame is gone.
    hub.settled(key, timeout=5)
    assert key not in hub.rooms
    assert key not in hub._closing
    for thread in threading.enumerate():
        if thread.name == f"collab-close-{key.partition(':')[2]}":
            thread.join(timeout=5)
            assert not thread.is_alive(), "the completed closing write left its thread alive"


def _exercise(data_dir, kind, fail_close):
    """Return only weak references, after every client and application lifespan ends."""
    api = create_app(str(data_dir), start_jobs=False)
    store, hub = api.state.app.store, api.state.collab
    refs = {"API": weakref.ref(api), "Store": weakref.ref(store), "Hub": weakref.ref(hub)}
    user = api.state.identity.create_user("room-owner@example.test", PW, status="active")
    workspace = api.state.identity.workspaces_for(user["id"])[0]["id"]
    raw, _ = api.state.sessions.create(user["id"], workspace_id=workspace, amr="password")
    with TestClient(api) as client:
        client.cookies.set(COOKIE_NAME, raw)
        client.cookies.set(CSRF_COOKIE, "fixture-csrf")
        client.headers[CSRF_HEADER] = "fixture-csrf"
        response = client.post("/api/templates/builtin", json={
            "kind": "builtin_doc" if kind == "text" else "builtin_sheet",
            "editor": DOC if kind == "text" else SHEET,
            "name": "Room lifecycle fixture",
        })
        assert response.status_code == 200, response.text
        tid = response.json()["id"]
        key = f"template:{tid}"

        first = _open(client, tid, kind, "")
        refs.update(_room_refs(hub, key, "first"))
        try:
            _edit(first, kind, "persisted")
        finally:
            _close(first, hub, key)
        _assert_draft(store, key, kind, "persisted")

        reopened = _open(client, tid, kind, "persisted")
        refs.update(_room_refs(hub, key, "reopened"))
        assert refs["reopened.Room"]() is not refs["first.Room"]()
        try:
            _edit(reopened, kind, "-second" if kind == "text" else "persisted-second")
            if fail_close:
                # TEST-ONLY database fault: the actual INSERT/UPSERT must fail.
                store.execute("CREATE TRIGGER fixture_refuse_room_close BEFORE INSERT ON collab_docs "
                              "BEGIN SELECT RAISE(ABORT, 'fixture: closing draft refused'); END")
        finally:
            with _closing_logs() as errors:
                _close(reopened, hub, key)

        if fail_close:
            assert any("fixture: closing draft refused" in message for message in errors), errors
            _assert_draft(store, key, kind, "persisted")
            store.execute("DROP TRIGGER fixture_refuse_room_close")
            recovered = _open(client, tid, kind, "persisted")
            refs.update(_room_refs(hub, key, "after-failed-close"))
            try:
                _edit(recovered, kind, "-recovered" if kind == "text" else "persisted-recovered")
            finally:
                _close(recovered, hub, key)
            _assert_draft(store, key, kind, "persisted-recovered")
        else:
            assert not errors, errors
            _assert_draft(store, key, kind, "persisted-second")
    return refs


@pytest.mark.parametrize("kind", ["text", "table"])
@pytest.mark.parametrize("fail_close", [False, True], ids=["durable-close", "failed-close-and-reopen"])
def test_six_real_rooms_release_their_graph_after_close(
    tmp_path, monkeypatch, release_factory_resources, kind, fail_close,
):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", "1")
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "unused-default"))
    # TEST FIXTURE: make final close, not a debounce timer, responsible for the draft.
    monkeypatch.setattr(collab, "PERSIST_DELAY_S", 3600)
    monkeypatch.setattr(collab, "PERSIST_MAX_WAIT_S", 3600)
    retained = []
    for index in range(6):
        refs = _exercise(tmp_path / str(index), kind, fail_close)
        release_factory_resources()
        retained.extend(f"iteration {index}: {name}" for name, ref in refs.items() if ref() is not None)
    assert not retained, "Completed room graphs remain alive: " + ", ".join(retained)
