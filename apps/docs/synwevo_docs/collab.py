"""Live co-editing rooms for built-in document templates.

One room per template (``template:<tid>``) holds a shared document (``pycrdt.Doc``) with the three
parts of the editor's document as XML fragments (see ``collab_pm``). Browsers speak the Yjs
sync/awareness protocol to it over a WebSocket at ``/api/collab/templates/{tid}``; the server is
the authority on **who** may do **what**, which no document format can be:

* the handshake runs the platform's own session gate (cookie session, workspace, application
  access, role) on a request built from the socket's scope, and the browser's ``Origin`` has to be
  this host — a cookie alone never opens a room from another site;
* a viewer joins, sees everyone and follows every change, but their document updates are dropped
  (and they are told) rather than applied; the role is re-checked every ``COLLAB_RECHECK_S``
  seconds, so a demotion turns the connection read-only and a removal closes it (4403);
  an expired or revoked session closes it too (4401) — an idle tab does not keep a session alive;
* awareness (name, colour, caret) is only accepted for the identity the connection proved;
* the live document is persisted whole into the single database (``collab_docs``) a second after
  the last change and when the room empties, so a crash or restart loses at most that second;
* every applied update is attributed to its sender and summarised into ``collab_changes`` at the
  granularity of a block ("edited body paragraph 3", with an excerpt), coalescing a person's
  consecutive keystrokes in one block.

Versions stay explicit: 保存模板 turns the live document into a new template version (the room is
told and marks itself clean); restoring, undoing or redoing a version replaces the live document
for everyone in the room; a save from outside the room (the machine API) does the same.

A table template's room carries presence and version frames only — unless the switch
``SYNWEVO_SHEET_LIVE`` is on, when it holds the live table state instead (``collab_sheet_room``,
docs-sheet-live.md B1 step ②): frames of type ``MESSAGE_SHEET`` carry operations the server checks,
orders and attributes.

Locks, in the one order they are ever nested (review 3 of sheet-live step ②): the store's write
lock (a transaction) → ``CollabHub._rooms_mu`` → a room's ``mu`` → a room's queue lock. Under
``_rooms_mu`` nothing waits: no SQL, no other thread, no heavy work — it is taken by the event loop on
every join and leave. A draft write takes the write lock first and checks the room's registration
under ``_rooms_mu`` inside its transaction. The template service calls ``on_template_change`` after
its transaction has committed (``Store.after_commit``), and the hook never waits for anything.

The room state lives in this process. The single-process deployment is the one this repository
ships; a multi-worker deployment needs sticky routing of ``/api/collab/`` to one process, which the
platform record spells out rather than hides.
"""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import json
import os
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

from pycrdt import (Awareness, Decoder, Doc, YMessageType, YSyncMessageType, create_awareness_message,
                    create_sync_message, create_update_message, handle_sync_message, read_message, write_message)
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from synwevo_core import env
from synwevo_core.context import actor as current_actor, as_tenant
from synwevo_core.errors import ServiceError
from synwevo_core.http import WRITE_ROLES
from synwevo_core.errors import not_found
from synwevo_core.store import Store, new_id, now

from .collab_pm import PARTS, block_texts, read, seed, strip_nulls

log = logging.getLogger("synwevo.docs.collab")

MESSAGE_META = 100                 # this server's own frames beside sync (0), awareness (1), query awareness (3)
MESSAGE_SHEET = 101                # a live table room's operations and patches (collab_sheet_room)
MESSAGE_QUERY_AWARENESS = 3
CLOSE_BAD_REQUEST, CLOSE_UNAUTHORIZED, CLOSE_FORBIDDEN, CLOSE_NOT_FOUND, CLOSE_GONE, CLOSE_ERROR = 4400, 4401, 4403, 4404, 4410, 1011
#: A tab reconnecting with a document from a previous seeding of the room (CI-115): merging it would
#: put back what a restore replaced, next to the restored text.
CLOSE_STALE = 4409
PERSIST_DELAY_S = 1.0
# The largest frame a client may send. A keystroke is bytes, a paste a few KB, a whole document's
# sync step well under a megabyte for the templates this editor holds; uvicorn's own ceiling is
# 16 MiB, which is a lot of memory and fan-out to hand to any writer. Over the limit the connection
# is closed with a 44xx code the client does not retry (it works alone and says why).
MAX_FRAME_BYTES = int(os.environ.get("SYNWEVO_COLLAB_MAX_FRAME_BYTES") or 2 * 1024 * 1024)
COALESCE_S = 120.0
KEEP_CHANGES = 500
EXCERPT = 80


def recheck_seconds() -> float:
    return float(env.get("COLLAB_RECHECK_S") or 30)


def sheet_live_enabled() -> bool:
    """``SYNWEVO_SHEET_LIVE``: a table template's room holds the live table state (docs-sheet-live.md
    B1, rolled out step by step; off by default). Read when a room opens."""
    return env.flag("SHEET_LIVE")


#: The live table room whose save is being written on this thread (collab_sheet_room.SheetRoom.save):
#: the version it writes is the room's own, not a change from outside to fold in.
OWN_LIVE_SAVE = threading.local()


def peer_color(user_id: str) -> int:
    """A stable 1–8 colour slot per person, the same in every room and session."""
    return int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16) % 8 + 1


def display_name(user: dict | None) -> str:
    if not user:
        return "—"
    return user.get("display_name") or (user.get("email") or "").split("@")[0] or "—"


def decode_awareness(update: bytes) -> list[tuple[int, int, Any]]:
    """(client id, clock, state or None) entries of an awareness update."""
    d = Decoder(update)
    out = []
    for _ in range(d.read_var_uint()):
        cid = d.read_var_uint()
        clock = d.read_var_uint()
        raw = d.read_var_string()
        out.append((cid, clock, json.loads(raw) if raw else None))
    return out


def meta_frame(payload: dict) -> bytes:
    return bytes([MESSAGE_META]) + write_message(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())


@dataclass(eq=False)
class Conn:
    ws: WebSocket
    user_id: str
    name: str
    color: int
    readonly: bool
    workspace_id: str
    request: Request                       # the handshake as a request: what the re-check re-runs
    path: str
    client_ids: set[int] = field(default_factory=set)
    refused_told: bool = False
    dropped: int = 0
    # a live table room: the tab's client id, the per-person key its messages are numbered under, the
    # newest sequence number it has said it has seen; the event loop serving the socket
    client: str | None = None
    client_key: str | None = None
    seen: int = 0
    loop: Any = None
    rate_until: float = 0.0                # told to wait until then (monotonic): frames before it are refused unread

    async def send(self, data: bytes) -> None:
        try:
            await self.ws.send_bytes(data)
        except Exception:                                   # the receive loop notices the close
            pass

    def post(self, data: bytes) -> None:
        """Send without waiting, from any thread: on the connection's own event loop, in the order
        posted (a frame posted from another thread no longer lands on a loop that may be gone)."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        loop = self.loop or running
        if loop is None:
            return
        if loop is running:
            asyncio.ensure_future(self.send(data))
        elif loop.is_running():
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.send(data)))

    def close_later(self, code: int, reason: str) -> None:
        """Close the socket from any thread; its receive loop then leaves the room."""
        async def close():
            with contextlib.suppress(Exception):
                await self.ws.close(code=code, reason=reason)
        loop = self.loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(close()))

    async def meta(self, payload: dict) -> None:
        await self.send(meta_frame(payload))


class Room:
    #: a table template's room with the live-table switch on (collab_sheet_room.SheetRoom)
    sheet = False

    def __init__(self, hub: "CollabHub", tid: str, tenant: str, *, kind: str = "template", writable: bool | None = None):
        self.hub = hub
        self.tid = tid
        self.kind = kind
        self.key = f"{kind}:{tid}"
        self.tenant = tenant
        # A project room, and a table template's room, carry presence and frames only: no shared
        # document (a table is merged cell by cell on save instead).
        self.writable = (kind == "template") if writable is None else writable
        self.doc = Doc()
        self.awareness = Awareness(self.doc)
        self.recovered = False                     # the persisted draft was unreadable and the head version was used instead
        #: which seeding this live document continues: new whenever the room starts from a version
        #: instead of its own persisted state (CI-115)
        self.epoch: str | None = None
        self.clients: set[Conn] = set()
        self.head: int | None = None
        self.dirty = False
        self.pending: list[bytes] = []
        self.sender: Conn | None = None
        self.persist_handle: asyncio.TimerHandle | None = None
        self.activity_handle: asyncio.TimerHandle | None = None
        self.changes_rev = 0
        self.blocks = block_texts(self.doc)
        self.doc.observe(lambda event: self.pending.append(event.update))
        #: registered in the hub while its state is still being read (``CollabHub._room``): version
        #: changes that land meanwhile wait in the queue instead of missing the room
        self.loading = False
        #: the template was deleted: nobody joins it any more
        self.gone = False
        #: left live mode, or its template is gone: nothing of this room is written again
        self.dropped = False
        #: which registration of its template this room is (CollabHub._room): a room's write after it
        #: closed is allowed only while no newer room has been registered for the template
        self.gen = 0
        self._final_state: bytes | None = None
        # the room's work queue (``enqueue``/``pump``): changes from other threads, run on the room's
        # loop in arrival order, never while the room is held (opening, or folding a version in)
        self._qlock = threading.Lock()
        self._queued: list[Callable[[], None]] = []
        self._holds = 0

    # ---- the work queue ----------------------------------------------------------------------
    def hold(self) -> None:
        with self._qlock:
            self._holds += 1

    def holding(self) -> bool:
        return self._holds > 0

    def release(self) -> None:
        """End one hold (on the room's loop) and run what waited for it."""
        with self._qlock:
            self._holds = max(0, self._holds - 1)
        self.pump()

    def enqueue(self, fn: Callable[[], None], *, first: bool = False) -> None:
        """From any thread: run ``fn`` on the room's event loop after everything queued before it
        (``first``: before it), and not while the room is held."""
        with self._qlock:
            if first:
                self._queued.insert(0, fn)
            else:
                self._queued.append(fn)
        loop = self.work_loop()
        if loop is not None:
            loop.call_soon_threadsafe(self.pump)

    def pump(self) -> None:
        while True:
            with self._qlock:
                if self._holds or not self._queued:
                    return
                fn = self._queued.pop(0)
            try:
                fn()
            except Exception:                                # noqa: BLE001 — one change must not stop the ones after it
                log.exception("collab %s: a queued change failed", self.key)

    # ---- state -------------------------------------------------------------------------------
    def load(self, template: dict, row: dict | None, editor_of_head: Callable[[], dict | None]) -> None:
        """Persisted live state if it belongs to the current head, else the head version."""
        if row and row.get("head") == template["head"] and row.get("state"):
            try:
                self.doc.apply_update(bytes(row["state"]))
                self.dirty = bool(row.get("dirty"))
                self.epoch = row.get("epoch") or new_id("ep")      # a draft from before epochs: this seeding from now on
            except Exception:                                # noqa: BLE001 — a corrupt draft must not keep the room from opening
                log.exception("collab %s: the persisted live state could not be read; reseeding from v%s (the draft is lost)", self.key, template["head"])
                self.doc = Doc()
                self.awareness = Awareness(self.doc)
                seed(self.doc, editor_of_head())
                self.dirty = False
                self.recovered = True
                self.epoch = new_id("ep")
        else:
            if row:
                log.info("collab %s: persisted live state was for v%s, head is v%s — reseeding", self.key, row.get("head"), template["head"])
            seed(self.doc, editor_of_head())
            self.dirty = False
            self.epoch = new_id("ep")
        self.head = template["head"]
        self.pending.clear()                       # seeding/loading is not a change anyone made
        self.blocks = block_texts(self.doc)

    def peers(self, exclude: Conn | None = None) -> list[dict]:
        seen: dict[str, dict] = {}
        for c in self.clients:
            if c is not exclude and c.user_id not in seen:
                seen[c.user_id] = {"id": c.user_id, "name": c.name, "color": c.color, "readonly": c.readonly}
        return list(seen.values())

    def state_payload(self) -> dict:
        return {"type": "state", "head": self.head, "dirty": self.dirty, "changes_rev": self.changes_rev, "online": len(self.peers())}

    def hello(self, conn: Conn) -> dict:
        return {"type": "hello", "kind": self.kind, "writable": self.writable, "recovered": self.recovered,
                "user": {"id": conn.user_id, "name": conn.name, "color": conn.color}, "readonly": conn.readonly,
                "head": self.head, "dirty": self.dirty, "changes_rev": self.changes_rev, "peers": self.peers(exclude=conn),
                "epoch": self.epoch, "recheck_s": recheck_seconds()}

    def admit(self, conn: Conn, requested_client: str | None = None) -> bytes:
        """Add the connection and say hello (a live table room does both at one sequence number)."""
        self.clients.add(conn)
        return meta_frame(self.hello(conn))

    def persists(self) -> bool:
        """Whether the room holds a live state that is written to ``collab_docs``."""
        return self.writable

    def closing_snapshot(self) -> None:
        """On the event loop, right after the room is unregistered: what its closing write writes."""
        self._final_state = self.doc.get_update()

    def close_final(self) -> None:
        """The closing write (a thread of its own, see CollabHub._leave)."""
        if self._final_state is not None:
            self.hub.write_state(self, self._final_state, final=True)

    def live(self) -> bool:
        return False

    def last_writer(self) -> str | None:
        return None

    def on_sheet_frame(self, conn: Conn, data: bytes) -> None:
        """A live table frame in a room that holds no live table: ignored like any unknown kind."""

    def work_loop(self) -> asyncio.AbstractEventLoop | None:
        """An event loop serving this room (in production the only one), for work that arrives on
        another thread and must be ordered with the room's frames."""
        for c in list(self.clients):
            if c.loop is not None and c.loop.is_running():
                return c.loop
        loop = self.hub.loop
        return loop if loop is not None and loop.is_running() else None

    # ---- sending -------------------------------------------------------------------------------
    def broadcast(self, data: bytes, exclude: Conn | None = None) -> None:
        for c in list(self.clients):
            if c is not exclude:
                c.post(data)

    def broadcast_meta(self, payload: dict, exclude: Conn | None = None) -> None:
        self.broadcast(meta_frame(payload), exclude)

    async def flush(self, sender: Conn | None) -> None:
        """Send the document updates the last operation produced, persist soon, log the change."""
        updates, self.pending = self.pending, []
        if not updates:
            return
        for u in updates:
            self.broadcast(create_update_message(u), exclude=sender)
        was_dirty = self.dirty
        if sender is not None:
            self.dirty = True
        self.schedule_persist()
        if sender is not None:
            await self.log_changes(sender)
        if self.dirty != was_dirty:
            self.broadcast_meta(self.state_payload())

    # ---- persistence -----------------------------------------------------------------------------
    def schedule_persist(self) -> None:
        loop = asyncio.get_running_loop()
        if self.persist_handle is not None:
            self.persist_handle.cancel()
        self.persist_handle = loop.call_later(PERSIST_DELAY_S, lambda: asyncio.ensure_future(self.persist()))

    async def persist(self) -> None:
        self.persist_handle = None
        try:
            state = self.doc.get_update()
            await run_in_threadpool(self.hub.write_state, self, state)
        except Exception:                                    # noqa: BLE001 — the next change retries
            log.exception("collab %s: persisting the live document failed", self.key)

    def persist_now(self) -> None:
        """The last one out writes synchronously: the handler may be cancelled (shutdown, test
        client) and an awaited write would never finish."""
        try:
            self.hub.write_state(self, self.doc.get_update())
        except Exception:                                    # noqa: BLE001
            log.exception("collab %s: persisting the live document on close failed", self.key)

    # ---- change log ----------------------------------------------------------------------------
    async def log_changes(self, conn: Conn) -> None:
        after = block_texts(self.doc)
        before, self.blocks = self.blocks, after
        rows: list[tuple[str, str, int, str, str, str | None]] = []
        for part in PARTS:
            a, b = before.get(part, []), after.get(part, [])
            if a == b:
                continue
            sm = difflib.SequenceMatcher(None, [t for _, t in a], [t for _, t in b], autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    continue
                if tag == "delete":
                    rows.append((part, "delete", i1, a[i1][0], a[i1][1][:EXCERPT], None))
                elif tag == "replace":
                    # what the block said before the edit travels with the row (bounded like the
                    # excerpt), so the log can show the insertions and deletions
                    rows.append((part, "edit", j1, b[j1][0], b[j1][1][:EXCERPT], a[i1][1][:EXCERPT]))
                else:
                    rows.append((part, "insert", j1, b[j1][0], b[j1][1][:EXCERPT], None))
        if not rows:
            return
        try:
            await run_in_threadpool(self.hub.record_changes, self, conn.user_id, rows)
        except Exception:                                    # noqa: BLE001
            log.exception("collab %s: recording changes failed", self.key)
            return
        self.changes_rev += 1
        loop = asyncio.get_running_loop()
        if self.activity_handle is None:
            self.activity_handle = loop.call_later(1.5, self._send_activity)

    def _send_activity(self) -> None:
        self.activity_handle = None
        self.broadcast_meta({"type": "activity", "changes_rev": self.changes_rev})

    # ---- outside changes (version operations) ---------------------------------------------------
    def apply_head_change(self, head: int | None, editor: dict | None, reason: str, actor: str | None, snapshot: bool, actor_id: str | None = None) -> None:
        """Runs on the event loop. A collaborator's save is a snapshot of this document; anything
        else (restore, undo, redo, a save from the machine API) replaces the live document."""
        if not snapshot and self.writable:
            seed(self.doc, editor)
            self.blocks = block_texts(self.doc)
            updates, self.pending = self.pending, []
            for u in updates:
                self.broadcast(create_update_message(u))
        self.head = head
        # A collaborator's save is what their page held when they pressed it; someone else may have
        # typed since. Only a live document equal to the saved one is clean (CI-114: it used to be
        # marked saved regardless, so a later restore dropped the newer text without asking).
        self.dirty = bool(snapshot and self.writable and not same_document(self.doc, editor))
        if self.writable:
            self.schedule_persist()
        self.broadcast_meta({**self.state_payload(), "type": "head", "reason": reason, "by": actor, "by_id": actor_id})


def same_document(ydoc, editor: dict | None) -> bool:
    """Does the live document hold exactly the saved editor model (its three parts, compared in
    the form a round trip through the shared types preserves)?"""
    try:
        live = strip_nulls(read(ydoc))
    except Exception:  # noqa: BLE001 — unreadable: not provably equal, so not clean
        return False
    empty = {"type": "doc", "content": []}
    for part in PARTS:
        saved = strip_nulls((editor or {}).get(part) or empty)
        have = live.get(part) or empty
        if not have.get("content") and not saved.get("content"):
            continue
        if have != saved:
            return False
    return True


_ROOM = object()          # write_state: "the room's own value"


class CollabHub:
    def __init__(self, store: Store, app_, identity, sessions, gate: Callable[[Request, str], Any]):
        self.store = store
        self.app = app_
        self.identity = identity
        self.sessions = sessions
        self.gate = gate
        self.rooms: dict[str, Room] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._acting: dict[str, tuple[str | None, float]] = {}
        self._lock = threading.Lock()
        self._room_locks: dict[str, asyncio.Lock] = {}
        #: registering a room, joining it and leaving it happen under this lock, so a join never lands
        #: in a room its last person has just closed (it opens the room again instead); every write of a
        #: live draft is checked and made under it too (``write_state``)
        self._rooms_mu = threading.RLock()
        #: rooms closed by their last person whose closing write is still running (done, room): the next
        #: opener of the same template waits for it before reading the draft; the guard reads the room
        self._closing: dict[str, tuple[threading.Event, Room]] = {}
        #: how many rooms each template has had registered (Room.gen)
        self._key_gen: dict[str, int] = {}
        from synwevo_core.http import RateBuckets
        from .collab_sheet_room import ops_per_minute
        #: live table operations per person per room (docs-sheet-live.md §4)
        self.sheet_rate = RateBuckets(ops_per_minute(), name="sheet-live")

    # ---- routes call these ----------------------------------------------------------------------
    @contextlib.contextmanager
    def acting(self, tid: str, user_id: str | None):
        """Who is performing a version operation on ``tid`` in this request (for attribution and
        for telling a collaborator's save from an outside one)."""
        with self._lock:
            self._acting[tid] = (user_id, time.monotonic() + 30)
        try:
            yield
        finally:
            with self._lock:
                self._acting.pop(tid, None)

    def actor(self, tid: str) -> str | None:
        with self._lock:
            entry = self._acting.get(tid)
        if entry and entry[1] > time.monotonic():
            return entry[0]
        return current_actor()

    def _who(self, user_id: str | None) -> dict | None:
        if not user_id:
            return None
        return {"id": user_id, "name": display_name(self.identity.get_user(user_id)), "color": peer_color(user_id)}

    def on_project_change(self, pid: str, kind: str, detail: dict) -> None:
        """Called by the project service after a change, from any thread: recorded as attributed
        activity, then pushed to everyone who has the project open."""
        uid = current_actor()
        row = {"id": new_id("pa"), "tenant": (self.store.one("SELECT tenant FROM projects WHERE id=?", (pid,)) or {}).get("tenant") or "",
               "project_id": pid, "user_id": uid, "kind": kind, "detail": json.dumps(detail, ensure_ascii=False), "at": now()}
        try:
            self.store.insert("project_activity", row)
            self.store.execute("DELETE FROM project_activity WHERE project_id=? AND id NOT IN (SELECT id FROM project_activity WHERE project_id=? ORDER BY at DESC LIMIT ?)",
                               (pid, pid, KEEP_CHANGES))
        except Exception:                                    # noqa: BLE001
            log.exception("project activity for %s (%s) not recorded", pid, kind)
        self._push_project_change(pid, kind, detail, uid, row["at"])

    def _push_project_change(self, pid: str, kind: str, detail: dict, uid: str | None, at: float) -> None:
        room = self.rooms.get(f"project:{pid}")
        if room is None or self.loop is None:
            return
        payload = {"type": "change", "kind": kind, "detail": detail, "by": self._who(uid), "at": at}
        self.loop.call_soon_threadsafe(room.broadcast_meta, payload)

    def activity(self, pid: str, limit: int = 100) -> dict:
        rows = self.store.all("SELECT * FROM project_activity WHERE project_id=? ORDER BY at DESC LIMIT ?", (pid, max(1, min(int(limit), KEEP_CHANGES))))
        who: dict[str, dict | None] = {}
        items = []
        for r in rows:
            uid = r.get("user_id")
            if uid not in who:
                who[uid] = self._who(uid)
            try:
                detail = json.loads(r.get("detail") or "{}")
            except Exception:                                # noqa: BLE001
                detail = {}
            items.append({"id": r["id"], "kind": r["kind"], "detail": detail, "by": who[uid], "at": r["at"]})
        room = self.rooms.get(f"project:{pid}")
        return {"items": items, "online": room.peers() if room else []}

    def on_template_change(self, tid: str, head: int | None, editor: dict | None, reason: str, *,
                           previous: int | None = None, source: int | None = None, discard_live: bool = False) -> None:
        """Called by the template service after a version operation, from any thread. The history
        row says what the document became and what it was before: a restore names the version it
        copied (`source`) and the one it replaced (`previous`); undo/redo name where the head moved
        from; a save made outside the editor names the version it followed. `discard_live`: the
        caller confirmed that unsaved live edits may go (a live table room then starts afresh)."""
        key = f"template:{tid}"
        actor = self.actor(tid)
        with self._rooms_mu:
            room = self.rooms.get(key)
            closing = self._closing.get(key)
        if reason != "delete":
            for prow in self.store.all("SELECT id FROM projects WHERE template_id=? AND deleted=0", (tid,)):
                self.on_project_change(prow["id"], f"template.{reason}", {"template_id": tid, "head": head})
        if reason == "delete":
            if room is not None:
                with self._rooms_mu:
                    room.gone = True                     # nobody joins it any more (serve opens it again: 404)
                    if not room.clients and not room.loading and self.rooms.get(key) is room:
                        del self.rooms[key]              # nobody left to close it (a room being opened: _room drops it)
                if room.sheet:
                    room.forget_draft()                  # the last one out must not write it back
            self.store.execute("DELETE FROM collab_docs WHERE room=?", (key,))
            self.store.execute("DELETE FROM collab_changes WHERE room=?", (key,))
            if room is not None:
                for c in list(room.clients):
                    c.close_later(CLOSE_GONE, "template_deleted")
            return
        if room is None or self.loop is None:
            # Nobody is in the room: the head is the truth now; the next opener seeds from it.
            if closing is None:
                self.store.execute("DELETE FROM collab_docs WHERE room=?", (key,))
                return
            # A room whose last person just left is still writing its draft — never waited for here.
            # Its unsaved table edits are kept (the next opener folds this version into them) unless
            # the caller discarded them; otherwise a tombstone at this head makes the draft write,
            # based on an older version, refuse itself when it lands (write_state), whichever of the
            # two commits first.
            closed = closing[1]
            if closed.sheet and closed.dirty and closed.persists() and not discard_live:
                return
            self.store.execute(
                "INSERT INTO collab_docs (room, tenant, template_id, state, head, dirty, epoch, model, updated_at) VALUES (?,?,?,?,?,0,NULL,'void',?) "
                "ON CONFLICT(room) DO UPDATE SET state=excluded.state, head=excluded.head, dirty=0, epoch=NULL, model='void', updated_at=excluded.updated_at",
                (key, closed.tenant, tid, b"", head, now()))
            return
        # A room still being opened (``_room``) read the template after it was registered: a change it
        # already read is not applied twice (versions only ever get new numbers, so an equal head is it)
        loading = room.loading
        actor_name = display_name(self.identity.get_user(actor)) if actor else None
        if room.sheet:
            if reason == "save" and getattr(OWN_LIVE_SAVE, "room", None) is room:
                return                                   # the room's own save: it marks itself saved (SheetRoom.save)
            room.enqueue(lambda: None if (loading and room.head == head) else
                         room.outside_change(head, editor, reason, actor, actor_name, discard_live))
            return
        snapshot = reason == "save" and actor is not None and any(c.user_id == actor for c in room.clients)
        if room.writable and (reason != "save" or not snapshot):
            became = f"v{source}" if reason == "restore" and source else f"v{head}"
            self.record_changes(room, actor, [("body", "restore" if reason in ("restore", "undo", "redo") else "replace", 0, "", became,
                                               f"v{previous}" if previous else None)], coalesce=False)
            room.changes_rev += 1
        room.enqueue(lambda: None if (loading and room.head == head) else
                     room.apply_head_change(head, editor, reason, actor_name, snapshot, actor))

    def live_state(self, tid: str) -> dict:
        """What a restore, undo or redo would throw away: whether the live document carries edits
        not yet saved as a version, who is in the room with write access, and who made the last
        recorded change. Read from the room when it is open, from the persisted draft otherwise."""
        key = f"template:{tid}"
        with self._rooms_mu:
            room = self.rooms.get(key)
            closing = self._closing.get(key)
        opened = room is not None and not room.loading
        dirty, sheet = self._dirty(key, room)
        online = [p["name"] for p in room.peers() if not p.get("readonly")] if opened else []
        online_ids = sorted({c.user_id for c in room.clients if not c.readonly}) if opened else []
        last_by = None
        if dirty:
            uid = None
            if sheet:
                source = room if opened else (closing[1] if closing is not None and room is None else None)
                uid = source.last_writer() if source is not None else self._draft_last_writer(key)
            else:
                last = self.store.one("SELECT user_id FROM collab_changes WHERE room=? ORDER BY updated_at DESC LIMIT 1", (key,))
                uid = (last or {}).get("user_id")
            if uid:
                last_by = display_name(self.identity.get_user(uid))
        # live_table: an open live table room folds an outside version in rather than replacing its
        # edits — which it can only do for a table it can hold (services._guard_live_edits)
        return {"dirty": dirty, "online": online, "online_ids": online_ids, "last_by": last_by,
                "live_table": bool(opened and room.sheet and room.live())}

    def settled(self, key: str, timeout: float = 30) -> None:
        """Wait for the closing write of the room of ``key`` its last person just left, if any. Never
        from inside a transaction or on the event loop (``_room`` waits in a worker thread)."""
        with self._rooms_mu:
            closing = self._closing.get(key)
        if closing is not None:
            closing[0].wait(timeout)

    def _dirty(self, key: str, room: Room | None) -> tuple[bool, bool]:
        """(unsaved live edits?, is it a live table?) — from the open room; from a room just closed
        whose closing write is still running (what it is writing, without waiting for it); else from
        the persisted draft (also while a room is still being opened: its state is not read yet)."""
        if room is not None and not room.loading:
            return bool(room.dirty and room.persists()), room.sheet
        if room is None:
            with self._rooms_mu:
                closing = self._closing.get(key)
            if closing is not None:
                closed = closing[1]
                dirty = bool(closed.dirty and closed.persists()) and (not closed.sheet or sheet_live_enabled())
                return dirty, closed.sheet
        row = self.store.one("SELECT dirty, model FROM collab_docs WHERE room=?", (key,))
        sheet = bool(row and row.get("model") == "sheet")
        # a live table draft counts while the switch is on; with it off the room cannot be opened to
        # save it, and the next version change drops it (docs-sheet-live.md §9, rollback)
        return bool(row and row["dirty"] and (not sheet or sheet_live_enabled())), sheet

    def _draft_last_writer(self, key: str) -> str | None:
        """Who last changed a persisted live table draft (nobody is in its room)."""
        import zlib
        from .collab_sheet_room import last_writer
        row = self.store.one("SELECT state FROM collab_docs WHERE room=?", (key,))
        try:
            return last_writer(json.loads(zlib.decompress(bytes(row["state"])))) if row and row.get("state") else None
        except Exception:                                    # noqa: BLE001 — an unreadable draft names nobody
            return None

    def live_sheet_room(self, tid: str) -> Room | None:
        """The open live table room of ``tid`` in this process, if any."""
        room = self.rooms.get(f"template:{tid}")
        return room if room is not None and room.sheet and room.live() else None

    async def save_live(self, tid: str, user_id: str | None, note) -> dict:
        """POST /api/templates/{tid}/collab/save: the live table as a new version (SheetRoom.save)."""
        room = self.live_sheet_room(tid)
        if room is None:
            raise ServiceError(409, "not_live", "这张表格没有正在进行的实时协同", {"template_id": tid})
        return await run_in_threadpool(room.save, user_id, note)

    def shutdown(self) -> None:
        """Process shutdown: write every live document now. The delayed persist runs on the event
        loop, which is about to stop; a save that waits for it would be lost with the process. The
        final writes of rooms closed just before are waited for."""
        with self._rooms_mu:
            rooms = list(self.rooms.values())
            closing = list(self._closing.values())
        for room in rooms:
            if room.persists() and room.clients:
                room.persist_now()
        for done, _room in closing:
            done.wait(30)

    def changes(self, tid: str, limit: int = 100) -> dict:
        key = f"template:{tid}"
        rows = self.store.all("SELECT * FROM collab_changes WHERE room=? ORDER BY updated_at DESC LIMIT ?", (key, max(1, min(int(limit), KEEP_CHANGES))))
        names: dict[str, str] = {}
        items = []
        for r in rows:
            uid = r.get("user_id")
            if uid and uid not in names:
                names[uid] = display_name(self.identity.get_user(uid))
            items.append({"id": r["id"], "user": {"id": uid, "name": names.get(uid, "—"), "color": peer_color(uid)} if uid else None, "part": r["part"], "kind": r["kind"],
                          "block_no": r.get("block_no"), "node_type": r.get("node_type"), "excerpt": r.get("excerpt") or "", "before": r.get("before"),
                          "count": r.get("count") or 1, "started_at": r["started_at"], "updated_at": r["updated_at"]})
        room = self.rooms.get(key)
        return {"items": items, "dirty": self._dirty(key, room)[0],
                "online": room.peers() if room else [], "changes_rev": room.changes_rev if room else 0}

    # ---- persistence (worker threads) -----------------------------------------------------------
    def write_state(self, room: Room, state: bytes, *, model: str | None = None, head: Any = _ROOM, dirty: Any = _ROOM, epoch: Any = _ROOM,
                    final: bool = False) -> bool:
        """Write a room's live draft; ``False`` when it may not. ``head``/``dirty``/``epoch`` default to the
        room's; a live table room passes the values of the serialisation it writes. ``model``: 'sheet'
        for a live table (NULL: a document).

        Only the registered room of a template writes its draft — checked here, under the lock that
        also registers and closes rooms, so the check and the write are one step. A room that is no
        longer registered (its last person left; a fold or a save marked later finishing after that)
        would otherwise write its old state over the draft of the room opened after it; a gone room
        (template deleted) or a dropped one never writes. ``final``: the closing write of a room just
        unregistered (``_leave``), allowed once while no other room has been opened for the template.
        And the statement itself refuses a template marked deleted (Q19): a write racing the delete
        cannot bring the draft back."""
        with self.store.tx() as c:                       # the write lock first (the process's lock order) ...
            with self._rooms_mu:                         # ... then a brief check: no SQL, no waiting under it
                allowed = self._may_write(room, final)
            if not allowed:
                return False
            return self._upsert_draft(c, room, state, model, room.head if head is _ROOM else head,
                                      1 if (room.dirty if dirty is _ROOM else dirty) else 0, room.epoch if epoch is _ROOM else epoch)

    def _may_write(self, room: Room, final: bool) -> bool:
        """Under ``_rooms_mu``: may ``room`` write its draft now? The registered room may; a room after
        it closed (its closing write, a save marked afterwards) only while no newer room has been
        registered for the template; a gone or dropped room never."""
        if room.gone or room.dropped:
            return False
        current = self.rooms.get(room.key)
        if current is room:
            return True
        return final and current is None and self._key_gen.get(room.key, 0) == room.gen

    @staticmethod
    def _upsert_draft(c, room: Room, state: bytes, model, head, dirty: int, epoch) -> bool:
        """The draft row itself, in the caller's transaction. Refused for a template marked deleted
        (Q19) and, over a tombstone (``model='void'``, see on_template_change), for a draft based on
        an older version than the tombstone's."""
        cur = c.execute(
            "INSERT INTO collab_docs (room, tenant, template_id, state, head, dirty, epoch, model, updated_at) "
            "SELECT ?,?,?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM templates WHERE id=? AND COALESCE(deleted,0)=0) "
            "ON CONFLICT(room) DO UPDATE SET state=excluded.state, head=excluded.head, dirty=excluded.dirty, epoch=excluded.epoch, "
            "model=excluded.model, updated_at=excluded.updated_at "
            "WHERE NOT (collab_docs.model = 'void' AND COALESCE(collab_docs.head, 0) > COALESCE(excluded.head, 0))",
            (room.key, room.tenant, room.tid, state, head, dirty, epoch, model, now(), room.tid))
        return bool(cur.rowcount)

    def record_changes(self, room: Room, user_id: str | None, rows: list[tuple], *, coalesce: bool = True) -> None:
        t = now()
        with self.store.tx() as c:
            last = c.execute("SELECT * FROM collab_changes WHERE room=? ORDER BY updated_at DESC LIMIT 1", (room.key,)).fetchone()
            for row in rows:
                part, kind, block_no, node_type, excerpt = row[:5]
                before = row[5] if len(row) > 5 else None
                if (coalesce and last and last["user_id"] == user_id and last["part"] == part and last["kind"] == kind == "edit"
                        and last["block_no"] == block_no and t - last["updated_at"] <= COALESCE_S):
                    # consecutive typing in one block: the row keeps the text from before the first
                    # keystroke and takes the latest excerpt, so its diff spans the whole run
                    c.execute("UPDATE collab_changes SET excerpt=?, node_type=?, count=count+1, updated_at=? WHERE id=?", (excerpt, node_type, t, last["id"]))
                    last = {**dict(last), "excerpt": excerpt, "updated_at": t}
                    continue
                cid = new_id("cc")
                # never for a template marked deleted (Q19): a change log row racing the delete stays out
                c.execute("INSERT INTO collab_changes (id, room, tenant, user_id, part, kind, block_no, node_type, excerpt, before, count, started_at, updated_at) "
                          "SELECT ?,?,?,?,?,?,?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM templates WHERE id=? AND COALESCE(deleted,0)=0)",
                          (cid, room.key, room.tenant, user_id, part, kind, block_no, node_type, excerpt, before, 1, t, t, room.tid))
                last = {"id": cid, "user_id": user_id, "part": part, "kind": kind, "block_no": block_no, "updated_at": t}
            c.execute("DELETE FROM collab_changes WHERE room=? AND id NOT IN (SELECT id FROM collab_changes WHERE room=? ORDER BY updated_at DESC LIMIT ?)",
                      (room.key, room.key, KEEP_CHANGES))

    # ---- the socket -----------------------------------------------------------------------------
    def _shim_request(self, ws: WebSocket) -> tuple[Request, str]:
        scope = dict(ws.scope)
        scope["type"] = "http"
        scope["method"] = "GET"
        headers = list(scope.get("headers") or [])
        wid = ws.query_params.get("workspace")
        if wid:
            headers.append((b"x-synwevo-workspace", wid.encode()))
        scope["headers"] = headers
        return Request(scope), scope.get("path") or ""

    @staticmethod
    def _origin_ok(ws: WebSocket) -> bool:
        origin = ws.headers.get("origin")
        host = ws.headers.get("host")
        if not origin or not host:
            return False
        try:
            o = urlsplit(origin)
        except ValueError:
            return False
        if o.scheme not in ("http", "https") or not o.netloc:
            return False
        if o.netloc.lower() == host.lower():
            return True
        own = (env.get("APP_URL") or "").strip()
        return bool(own) and o.netloc.lower() == urlsplit(own).netloc.lower()

    async def _gate(self, req: Request, path: str) -> tuple[int, str] | tuple[None, dict]:
        """(close code, reason) when refused, else (None, {tenant, user, role, wid})."""
        outcome = await run_in_threadpool(self.gate, req, path)
        if isinstance(outcome, JSONResponse):
            try:
                body = json.loads(outcome.body)
                reason = body.get("error") if isinstance(body.get("error"), str) else (body.get("error") or {}).get("code") or "refused"
            except Exception:                                # noqa: BLE001
                reason = "refused"
            code = {401: CLOSE_UNAUTHORIZED, 403: CLOSE_FORBIDDEN, 404: CLOSE_NOT_FOUND}.get(outcome.status_code, CLOSE_FORBIDDEN)
            return code, str(reason)[:120]
        tenant = str(outcome)
        user = getattr(req.state, "user", None)
        wid = getattr(req.state, "workspace_id", None)
        if not user or not wid or tenant.startswith("__"):
            return CLOSE_FORBIDDEN, "no_workspace"
        role = getattr(req.state, "workspace_role", None)
        return None, {"tenant": tenant, "user": user, "role": role, "wid": wid}

    async def _project_room(self, pid: str, tenant: str) -> Room | None:
        key = f"project:{pid}"
        lock = self._room_locks.setdefault(key, asyncio.Lock())
        async with lock:
            room = self.rooms.get(key)
            if room is not None:
                if (room.tenant or "default") != (tenant or "default"):
                    not_found("项目")                        # an open room is no way into another workspace (CI-118)
                return room

            def read():
                with as_tenant(tenant):
                    return self.app.get_project(pid)
            await run_in_threadpool(read)                    # raises the service's 404 for a project not in this workspace
            room = Room(self, pid, tenant, kind="project")
            room.pending.clear()
            with self._rooms_mu:
                self.rooms[key] = room
            return room

    async def _room(self, tid: str, tenant: str) -> Room | None:
        key = f"template:{tid}"
        lock = self._room_locks.setdefault(key, asyncio.Lock())
        async with lock:
            with self._rooms_mu:
                room = self.rooms.get(key)
                closing = self._closing.get(key)
            if room is None and closing is not None:
                # the previous room's last write is still running: read the draft after it
                await run_in_threadpool(closing[0].wait, 60)
            if room is not None and not room.gone:
                # The room was opened by someone in the template's workspace; a person signed into
                # another workspace used to be let in while it was open — reading and writing the live
                # document of a template their own workspace cannot see (CI-118). Same answer as closed.
                if (room.tenant or "default") != (tenant or "default"):
                    not_found("模板")
                return room

            def kind_of():
                with as_tenant(tenant):
                    return self.app.get_template(tid).get("kind")
            kind = await run_in_threadpool(kind_of)          # the service's 404 for a template not in this workspace
            if kind == "builtin_sheet" and sheet_live_enabled():
                # SYNWEVO_SHEET_LIVE: the live table (collab_sheet_room), or presence when it cannot be held
                from .collab_sheet_room import SheetRoom
                room = SheetRoom(self, tid, tenant)
            elif kind == "builtin_sheet":
                # A table template: who is here and what they are on, plus version frames; the
                # cells themselves are merged on save (builtin_service.merge_sheets).
                room = Room(self, tid, tenant, kind="template", writable=False)
            elif kind == "builtin_doc":
                room = Room(self, tid, tenant)
            else:
                return None
            # Registered *before* its state is read: a version written meanwhile either happened before
            # the read (the read sees it) or finds the room and waits in its queue until it is loaded.
            # Registered after, such a version found no room, the room opened at the old head and could
            # never save again — and a reopen reseeded it, losing what was typed (review item 3).
            room.loading = True
            room.hold()
            with self._rooms_mu:
                self._key_gen[key] = room.gen = self._key_gen.get(key, 0) + 1
                self.rooms[key] = room
            try:
                await self._load(room, tid, tenant, kind)
            except BaseException:
                with self._rooms_mu:
                    if self.rooms.get(key) is room:
                        del self.rooms[key]
                raise
            room.loading = False
            room.pending.clear()                             # loading is not a change anyone made
            if room.gone:
                # the template was deleted after the read: nobody will join (or close) this room
                with self._rooms_mu:
                    if self.rooms.get(key) is room:
                        del self.rooms[key]
                not_found("模板")
            catch_up = getattr(room, "catch_up", None)
            if catch_up is not None:
                # before the versions that arrived while it opened: those are newer than the head it read
                room.catch_up = None
                room.enqueue(lambda: room.outside_change(catch_up[0], catch_up[1], "save", None, None, False), first=True)
            room.release()
            return room

    async def _load(self, room: Room, tid: str, tenant: str, kind: str) -> None:
        """Read a room's state: the head version and its persisted draft."""
        key = room.key

        def head_editor(t: dict) -> dict | None:
            with as_tenant(tenant):
                return self.app.editor_of(tid, self.app.get_version(tid, t["head"]), t.get("kind"))

        def read():
            with as_tenant(tenant):
                t = self.app.get_template(tid)
                return t, (self.store.one("SELECT * FROM collab_docs WHERE room=?", (key,)) if room.sheet or room.writable else None)
        if room.sheet:
            def load():
                t, row = read()
                room.load_sheet(t, row, lambda: head_editor(t))
            await run_in_threadpool(load)
            return
        t, row = await run_in_threadpool(read)
        if not room.writable:
            room.head = t["head"]
            return
        # Read lazily: when a matching draft exists the head version is normally not needed, but a
        # draft that turns out to be unreadable falls back to it (Room.load).
        editor = None if (row and row.get("head") == t["head"] and row.get("state")) else (await run_in_threadpool(head_editor, t) or {})

        def lazy() -> dict | None:
            if editor is not None:
                return editor
            with as_tenant(tenant):
                return self.app.editor_of(tid, self.app.get_version(tid, t["head"]), t.get("kind")) or {}
        room.load(t, row, lazy)

    async def serve(self, ws: WebSocket, tid: str, *, kind: str = "template") -> None:
        self.loop = asyncio.get_running_loop()
        if not self._origin_ok(ws):
            await ws.close(code=CLOSE_FORBIDDEN, reason="origin")
            return
        req, path = self._shim_request(ws)
        code, info = await self._gate(req, path)
        if code is not None:
            await ws.close(code=code, reason=info)
            return
        assert isinstance(info, dict)
        held = ws.query_params.get("epoch")

        async def open_room() -> tuple[Room | None, tuple[int, str] | None]:
            """(the room, None) or (None, (close code, reason))."""
            try:
                room = await (self._project_room(tid, info["tenant"]) if kind == "project" else self._room(tid, info["tenant"]))
            except Exception as e:                            # noqa: BLE001 — a 404 from get_template, or worse
                status = getattr(e, "status", 500)
                return None, (CLOSE_NOT_FOUND if status == 404 else CLOSE_ERROR, getattr(e, "code", "error"))
            if room is None:
                return None, (CLOSE_BAD_REQUEST, "not_builtin_doc")
            if held and room.epoch and held != room.epoch:
                # the tab's document belongs to a previous seeding (the room was restored or reseeded while
                # it was away): its sync would merge the replaced text back next to the new one (CI-115)
                return None, (CLOSE_STALE, "stale_document")
            return room, None
        room, refusal = await open_room()
        if refusal is not None:
            await ws.close(code=refusal[0], reason=refusal[1])
            return
        user = info["user"]
        conn = Conn(ws=ws, user_id=user["id"], name=display_name(user), color=peer_color(user["id"]),
                    readonly=info["role"] not in WRITE_ROLES, workspace_id=info["wid"], request=req, path=path,
                    loop=asyncio.get_running_loop())
        await ws.accept()
        hello = None
        for _ in range(5):
            with self._rooms_mu:
                if self.rooms.get(room.key) is room and not room.gone:
                    # `client`: the id a live table tab numbers its messages under, brought back to resume them
                    hello = room.admit(conn, ws.query_params.get("client"))
                    break
            # its last person left (or its template went) while this handshake ran: that room is closed
            # and written; joining it would leave this tab alone in a room nobody else can reach
            room, refusal = await open_room()
            if refusal is not None:
                await ws.close(code=refusal[0], reason=refusal[1])
                return
        if hello is None:
            await ws.close(code=CLOSE_ERROR, reason="room_unavailable")
            return
        await conn.send(hello)
        await conn.send(create_sync_message(room.doc))
        others = [cid for cid in room.awareness.states if cid != room.awareness.client_id]
        if others:
            await conn.send(create_awareness_message(room.awareness.encode_awareness_update(others)))
        room.broadcast_meta({"type": "joined", "user": {"id": conn.user_id, "name": conn.name, "color": conn.color, "readonly": conn.readonly}, "online": len(room.peers())}, exclude=conn)
        checker = asyncio.ensure_future(self._recheck(conn, room))
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if not data:
                    continue
                if len(data) > MAX_FRAME_BYTES:
                    log.warning("collab %s: %s sent a %d-byte frame (limit %d); closing", room.key, conn.user_id, len(data), MAX_FRAME_BYTES)
                    await ws.close(code=CLOSE_BAD_REQUEST, reason="frame_too_large")
                    break
                await self._handle(room, conn, data)
        except WebSocketDisconnect:
            pass
        except Exception:                                     # noqa: BLE001
            log.exception("collab %s: connection of %s failed", room.key, conn.user_id)
        finally:
            checker.cancel()
            await self._leave(room, conn)

    def _close_in_background(self, room: Room, done: threading.Event, *, threaded: bool = True) -> None:
        """The closing write in a thread of its own — off the event loop and outside every room lock;
        a plain thread, since the handler may be cancelled (shutdown, the test client) and the write
        may not. When no thread can be started, it is written here (review 3, item 6): the next opener
        must not wait for a write that will never come."""
        def final(key=room.key):
            try:
                room.close_final()
            except Exception:                                # noqa: BLE001
                log.exception("collab %s: the closing write failed", key)
            finally:
                with self._rooms_mu:
                    if self._closing.get(key, (None,))[0] is done:
                        del self._closing[key]
                done.set()
        if not threaded:
            final()
            return
        try:
            threading.Thread(target=final, name=f"collab-close-{room.tid}", daemon=True).start()
        except Exception:                                    # noqa: BLE001 — no thread to be had: write now
            log.exception("collab %s: could not start the closing write; writing it here", room.key)
            final()

    def background(self, fn: Callable[[], None], name: str) -> None:
        """Run ``fn`` (a database write) off the event loop, in a thread of its own; here when no
        thread can be started."""
        try:
            threading.Thread(target=fn, name=name, daemon=True).start()
        except Exception:                                    # noqa: BLE001
            log.exception("collab: could not start %s; running it here", name)
            fn()

    async def _handle(self, room: Room, conn: Conn, data: bytes) -> None:
        kind = data[0]
        if kind == YMessageType.SYNC:
            if len(data) < 2:
                return
            sub = data[1]
            if sub == YSyncMessageType.SYNC_STEP1:
                reply = handle_sync_message(data[1:], room.doc)
                if reply is not None:
                    await conn.send(reply)
                return
            if sub in (YSyncMessageType.SYNC_STEP2, YSyncMessageType.SYNC_UPDATE):
                if not room.writable:
                    return                                    # presence-only room: the document is not a thing here
                if conn.readonly:
                    # A viewer's step-2 answer to our step 1 is normally empty (they lack nothing
                    # we need); anything with content is a write and is not applied — told once.
                    if read_message(data[2:]) == b"\x00\x00":
                        return
                    conn.dropped += 1
                    if not conn.refused_told:
                        conn.refused_told = True
                        await conn.meta({"type": "refused", "reason": "read_only"})
                    return
                room.sender = conn
                try:
                    handle_sync_message(data[1:], room.doc)
                finally:
                    room.sender = None
                await room.flush(conn)
            return
        if kind == YMessageType.AWARENESS:
            update = read_message(data[1:])
            try:
                entries = decode_awareness(update)
            except Exception:                                 # noqa: BLE001 — not the protocol; ignore the frame
                return
            for cid, _clock, state in entries:
                if state is None:
                    continue
                who = state.get("user") if isinstance(state, dict) else None
                if not isinstance(who, dict) or not who.get("id"):
                    return                                    # not identified yet (the editor's first frame): nobody's caret
                if who.get("id") != conn.user_id:
                    log.warning("collab %s: awareness for another identity dropped (from %s)", room.key, conn.user_id)
                    return
                conn.client_ids.add(cid)
            room.awareness.apply_awareness_update(update, conn)
            room.broadcast(data)                              # everyone, including the sender: its liveness check counts this
            return
        if kind == MESSAGE_QUERY_AWARENESS:
            ids = [cid for cid in room.awareness.states if cid != room.awareness.client_id]
            if ids:
                await conn.send(create_awareness_message(room.awareness.encode_awareness_update(ids)))
            return
        if kind == MESSAGE_SHEET:
            room.on_sheet_frame(conn, data)
            return
        # MESSAGE_META from a client and unknown kinds are ignored: the server decides state.

    async def _recheck(self, conn: Conn, room: Room) -> None:
        """Periodically prove the connection is still allowed, without extending the session.

        The session is read with ``touch=False``: staying connected is not activity. Membership and
        the account are read live, like on every request."""
        from synwevo_core.sessions import COOKIE_NAME
        interval = recheck_seconds()
        while True:
            await asyncio.sleep(interval)
            try:
                verdict = await run_in_threadpool(self._still_allowed, conn, COOKIE_NAME)
            except Exception:                                 # noqa: BLE001
                log.exception("collab %s: re-check failed", room.key)
                continue
            code, role = verdict
            if code is not None:
                await conn.meta({"type": "closed", "reason": role})
                with contextlib.suppress(Exception):
                    await conn.ws.close(code=code, reason=role)
                return
            readonly = role not in WRITE_ROLES
            if readonly != conn.readonly:
                conn.readonly = readonly
                conn.refused_told = False
                await conn.meta({"type": "role", "readonly": readonly, "role": role})
                room.broadcast_meta({"type": "joined", "user": {"id": conn.user_id, "name": conn.name, "color": conn.color, "readonly": readonly},
                                     "online": len(room.peers())}, exclude=conn)

    def _still_allowed(self, conn: Conn, cookie_name: str) -> tuple[int | None, str]:
        session = self.sessions.load(conn.request.cookies.get(cookie_name), touch=False)
        if not session or session.get("user_id") != conn.user_id:
            return CLOSE_UNAUTHORIZED, "session_ended"
        user = self.identity.get_user(conn.user_id)
        if not user or user.get("status") != "active":
            return CLOSE_UNAUTHORIZED, "account_unavailable"
        member = self.identity.membership(conn.workspace_id, conn.user_id)
        if not member:
            return CLOSE_FORBIDDEN, "membership_ended"
        from synwevo_core.http import app_access_refusal
        refusal = app_access_refusal(self.store, self.identity, conn.user_id, conn.workspace_id, None)
        if refusal is not None:
            return CLOSE_FORBIDDEN, getattr(refusal, "code", "workspace_unavailable")
        return None, member["role"]

    async def _leave(self, room: Room, conn: Conn) -> None:
        closing = None
        with self._rooms_mu:
            # the last one out closes the room: unregistered here, in one step with the check a joining
            # tab makes (it either got in before, or opens the room again after the closing write)
            room.clients.discard(conn)
            if not room.clients:
                if room.persist_handle is not None:
                    room.persist_handle.cancel()
                    room.persist_handle = None
                if room.activity_handle is not None:
                    room.activity_handle.cancel()
                    room.activity_handle = None
                if self.rooms.get(room.key) is room:
                    del self.rooms[room.key]
                    if room.persists():
                        closing = threading.Event()
                        self._closing[room.key] = (closing, room)
        if closing is not None:
            # the closing write, after the lock. A live table (up to 16 MiB) is serialised, compressed
            # and written in a thread of its own; a document's state is small and is taken and written
            # here, as before (it waits for the database like any write on the loop, never under a lock)
            if room.sheet:
                self._close_in_background(room, closing)
            else:
                room.closing_snapshot()
                self._close_in_background(room, closing, threaded=False)
        ids = [cid for cid in conn.client_ids if cid in room.awareness.states]
        if ids:
            room.awareness.remove_awareness_states(ids, "server")
            room.broadcast(create_awareness_message(room.awareness.encode_awareness_update(ids)))
        room.broadcast_meta({"type": "left", "user": {"id": conn.user_id, "name": conn.name}, "online": len(room.peers())})
