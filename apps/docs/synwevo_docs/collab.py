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
from synwevo_core.http import WRITE_ROLES
from synwevo_core.store import Store, new_id, now

from .collab_pm import PARTS, block_texts, seed

log = logging.getLogger("synwevo.docs.collab")

MESSAGE_META = 100                 # this server's own frames beside sync (0), awareness (1), query awareness (3)
MESSAGE_QUERY_AWARENESS = 3
CLOSE_BAD_REQUEST, CLOSE_UNAUTHORIZED, CLOSE_FORBIDDEN, CLOSE_NOT_FOUND, CLOSE_GONE, CLOSE_ERROR = 4400, 4401, 4403, 4404, 4410, 1011
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

    async def send(self, data: bytes) -> None:
        try:
            await self.ws.send_bytes(data)
        except Exception:                                   # the receive loop notices the close
            pass

    async def meta(self, payload: dict) -> None:
        await self.send(meta_frame(payload))


class Room:
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

    # ---- state -------------------------------------------------------------------------------
    def load(self, template: dict, row: dict | None, editor_of_head: Callable[[], dict | None]) -> None:
        """Persisted live state if it belongs to the current head, else the head version."""
        if row and row.get("head") == template["head"] and row.get("state"):
            try:
                self.doc.apply_update(bytes(row["state"]))
                self.dirty = bool(row.get("dirty"))
            except Exception:                                # noqa: BLE001 — a corrupt draft must not keep the room from opening
                log.exception("collab %s: the persisted live state could not be read; reseeding from v%s (the draft is lost)", self.key, template["head"])
                self.doc = Doc()
                self.awareness = Awareness(self.doc)
                seed(self.doc, editor_of_head())
                self.dirty = False
                self.recovered = True
        else:
            if row:
                log.info("collab %s: persisted live state was for v%s, head is v%s — reseeding", self.key, row.get("head"), template["head"])
            seed(self.doc, editor_of_head())
            self.dirty = False
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

    # ---- sending -------------------------------------------------------------------------------
    def broadcast(self, data: bytes, exclude: Conn | None = None) -> None:
        for c in list(self.clients):
            if c is not exclude:
                asyncio.ensure_future(c.send(data))

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
        self.dirty = False
        if self.writable:
            self.schedule_persist()
        self.broadcast_meta({**self.state_payload(), "type": "head", "reason": reason, "by": actor, "by_id": actor_id})


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
                           previous: int | None = None, source: int | None = None) -> None:
        """Called by the template service after a version operation, from any thread. The history
        row says what the document became and what it was before: a restore names the version it
        copied (`source`) and the one it replaced (`previous`); undo/redo name where the head moved
        from; a save made outside the editor names the version it followed."""
        key = f"template:{tid}"
        actor = self.actor(tid)
        room = self.rooms.get(key)
        if reason != "delete":
            for prow in self.store.all("SELECT id FROM projects WHERE template_id=? AND deleted=0", (tid,)):
                self.on_project_change(prow["id"], f"template.{reason}", {"template_id": tid, "head": head})
        if reason == "delete":
            self.store.execute("DELETE FROM collab_docs WHERE room=?", (key,))
            self.store.execute("DELETE FROM collab_changes WHERE room=?", (key,))
            if room is not None and self.loop is not None:
                self.loop.call_soon_threadsafe(lambda: [asyncio.ensure_future(c.ws.close(code=CLOSE_GONE, reason="template_deleted")) for c in list(room.clients)])
            return
        if room is None or self.loop is None:
            # Nobody is in the room: the head is the truth now; the next opener seeds from it.
            self.store.execute("DELETE FROM collab_docs WHERE room=?", (key,))
            return
        snapshot = reason == "save" and actor is not None and any(c.user_id == actor for c in room.clients)
        if room.writable and (reason != "save" or not snapshot):
            became = f"v{source}" if reason == "restore" and source else f"v{head}"
            self.record_changes(room, actor, [("body", "restore" if reason in ("restore", "undo", "redo") else "replace", 0, "", became,
                                               f"v{previous}" if previous else None)], coalesce=False)
            room.changes_rev += 1
        actor_name = display_name(self.identity.get_user(actor)) if actor else None
        self.loop.call_soon_threadsafe(room.apply_head_change, head, editor, reason, actor_name, snapshot, actor)

    def live_state(self, tid: str) -> dict:
        """What a restore, undo or redo would throw away: whether the live document carries edits
        not yet saved as a version, who is in the room with write access, and who made the last
        recorded change. Read from the room when it is open, from the persisted draft otherwise."""
        key = f"template:{tid}"
        room = self.rooms.get(key)
        if room is not None:
            dirty, online = bool(room.dirty and room.writable), [p["name"] for p in room.peers() if not p.get("readonly")]
        else:
            live = self.store.one("SELECT dirty FROM collab_docs WHERE room=?", (key,))
            dirty, online = bool(live and live["dirty"]), []
        last_by = None
        if dirty:
            last = self.store.one("SELECT user_id FROM collab_changes WHERE room=? ORDER BY updated_at DESC LIMIT 1", (key,))
            if last and last.get("user_id"):
                last_by = display_name(self.identity.get_user(last["user_id"]))
        return {"dirty": dirty, "online": online, "last_by": last_by}

    def shutdown(self) -> None:
        """Process shutdown: write every live document now. The delayed persist runs on the event
        loop, which is about to stop; a save that waits for it would be lost with the process."""
        with self._lock:
            rooms = list(self.rooms.values())
        for room in rooms:
            if room.writable and room.clients:
                room.persist_now()

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
        live = self.store.one("SELECT head, dirty, updated_at FROM collab_docs WHERE room=?", (key,))
        return {"items": items, "dirty": bool(room.dirty if room else (live or {}).get("dirty")),
                "online": room.peers() if room else [], "changes_rev": room.changes_rev if room else 0}

    # ---- persistence (worker threads) -----------------------------------------------------------
    def write_state(self, room: Room, state: bytes) -> None:
        self.store.execute(
            "INSERT INTO collab_docs (room, tenant, template_id, state, head, dirty, updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(room) DO UPDATE SET state=excluded.state, head=excluded.head, dirty=excluded.dirty, updated_at=excluded.updated_at",
            (room.key, room.tenant, room.tid, state, room.head, 1 if room.dirty else 0, now()))

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
                c.execute("INSERT INTO collab_changes (id, room, tenant, user_id, part, kind, block_no, node_type, excerpt, before, count, started_at, updated_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (cid, room.key, room.tenant, user_id, part, kind, block_no, node_type, excerpt, before, 1, t, t))
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
                return room

            def read():
                with as_tenant(tenant):
                    return self.app.get_project(pid)
            await run_in_threadpool(read)                    # raises the service's 404 for a project not in this workspace
            room = Room(self, pid, tenant, kind="project")
            room.pending.clear()
            self.rooms[key] = room
            return room

    async def _room(self, tid: str, tenant: str) -> Room | None:
        key = f"template:{tid}"
        lock = self._room_locks.setdefault(key, asyncio.Lock())
        async with lock:
            room = self.rooms.get(key)
            if room is not None:
                return room

            def read_all():
                with as_tenant(tenant):
                    t = self.app.get_template(tid)
                    if t.get("kind") == "builtin_sheet":
                        return t, None, "presence"
                    if t.get("kind") != "builtin_doc":
                        return None, None, None
                    row = self.store.one("SELECT * FROM collab_docs WHERE room=?", (key,))
                    editor = None if (row and row.get("head") == t["head"] and row.get("state")) else (self.app.get_version(tid, t["head"]).get("editor") or {})
                    return t, row, editor
            t, row, editor = await run_in_threadpool(read_all)
            if t is None:
                return None
            if editor == "presence":
                # A table template: who is here and what they are on, plus version frames; the
                # cells themselves are merged on save (builtin_service.merge_sheets).
                room = Room(self, tid, tenant, kind="template", writable=False)
                room.head = t["head"]
                room.pending.clear()
                self.rooms[key] = room
                return room
            room = Room(self, tid, tenant)

            def head_editor() -> dict | None:
                # Read lazily: when a matching draft exists the head version is normally not needed,
                # but a draft that turns out to be unreadable falls back to it (Room.load).
                if editor is not None:
                    return editor
                with as_tenant(tenant):
                    return self.app.get_version(tid, t["head"]).get("editor") or {}
            room.load(t, row, head_editor)
            self.rooms[key] = room
            return room

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
        try:
            room = await (self._project_room(tid, info["tenant"]) if kind == "project" else self._room(tid, info["tenant"]))
        except Exception as e:                                # noqa: BLE001 — a 404 from get_template, or worse
            status = getattr(e, "status", 500)
            await ws.close(code=CLOSE_NOT_FOUND if status == 404 else CLOSE_ERROR, reason=getattr(e, "code", "error"))
            return
        if room is None:
            await ws.close(code=CLOSE_BAD_REQUEST, reason="not_builtin_doc")
            return
        user = info["user"]
        conn = Conn(ws=ws, user_id=user["id"], name=display_name(user), color=peer_color(user["id"]),
                    readonly=info["role"] not in WRITE_ROLES, workspace_id=info["wid"], request=req, path=path)
        await ws.accept()
        room.clients.add(conn)
        await conn.meta({"type": "hello", "kind": room.kind, "writable": room.writable, "recovered": room.recovered, "user": {"id": conn.user_id, "name": conn.name, "color": conn.color}, "readonly": conn.readonly,
                         "head": room.head, "dirty": room.dirty, "changes_rev": room.changes_rev, "peers": room.peers(exclude=conn),
                         "recheck_s": recheck_seconds()})
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
        room.clients.discard(conn)
        ids = [cid for cid in conn.client_ids if cid in room.awareness.states]
        if ids:
            room.awareness.remove_awareness_states(ids, "server")
            room.broadcast(create_awareness_message(room.awareness.encode_awareness_update(ids)))
        room.broadcast_meta({"type": "left", "user": {"id": conn.user_id, "name": conn.name}, "online": len(room.peers())})
        if not room.clients:
            if room.persist_handle is not None:
                room.persist_handle.cancel()
                room.persist_handle = None
            if room.activity_handle is not None:
                room.activity_handle.cancel()
                room.activity_handle = None
            if room.writable:
                room.persist_now()
            if self.rooms.get(room.key) is room:
                del self.rooms[room.key]
