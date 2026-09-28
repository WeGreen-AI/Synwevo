"""The live table room (docs/platform/docs-sheet-live.md §3–§4, batch B1 step ②).

Behind the switch ``SYNWEVO_SHEET_LIVE`` (off by default) the room of a table template holds the
shared live state of :mod:`collab_sheet` instead of presence only. Everything else about the room —
the handshake, the 30-second authorization re-check, awareness bound to the proven identity, the
2 MiB frame bound, version frames — is the document room's (``collab.py``); this module adds:

* **opening**: the persisted draft (``collab_docs``, ``model='sheet'``) when it belongs to the head
  version, else a fresh seeding of the head version's model under a new epoch. A model the live state
  cannot hold (``SeedError``, or more than ``SYNWEVO_SHEET_LIVE_MAX_CELLS`` cells) keeps the room in
  CI-47's presence-only mode and ``hello`` says so (``live: false``, ``live_reason``); an unreadable
  draft is logged and replaced by the head version (``recovered``).
* **hello** carries ``live``, ``epoch``, ``seq``, ``client`` (the tab's client id — the one it asked
  for with ``?client=`` when it is valid and not in use by another open tab of the same person),
  ``next`` (the number its next message must carry: after the last one the room remembers for that
  client), ``resumed``, ``persisted`` and the whole ``content`` a browser mirrors.
* **frames** of type ``MESSAGE_SHEET`` (101), JSON after the varuint length like the meta frames:

  - client → server ``{"type": "op", "n", "expect", "op": {...}}`` (``"tx": [...]`` is shorthand for
    ``"op": {"op": "tx", "ops": [...]}``; a ``"client"`` in the frame must be the connection's) and
    ``{"type": "sync"}`` (send me the whole state again, after a ``resync``);
  - server → sender ``{"type": "ack", "n", "ok", "seq", "patch", "before"}`` or ``{"type": "ack", "n",
    "ok": false, "code", "detail", "people"}`` (``conflict`` names whose value it was; ``resync``
    asks for ``sync``; ``read_only``, ``rate_limited`` with ``retry_after`` seconds, ``not_live``,
    ``invalid`` are the room's own); a message delivered twice answers what it answered the first
    time with ``duplicate: true``;
  - server → everyone else ``{"type": "patch", "seq", "patch", "by"}`` — ``by`` is stamped by the
    server from the connection, never read from the frame; a patch from outside the room carries
    ``outside: {reason, head}``;
  - ``{"type": "full", ...}`` (the answer to ``sync``) and ``{"type": "reset", "epoch", ...}`` (the
    room started afresh from a version: everything a tab holds from the previous epoch is void).

  Meta frames (100) add ``persisted {seq}`` (the draft up to ``seq`` is on disk), ``overwritten``
  (an outside version replaced these people's values: ``people``, ``conflicts``) and ``live`` (the
  room left live mode).
* **persistence**: the state as zlib-compressed JSON in ``collab_docs``, a second after the last
  change, synchronously when the last person leaves and at shutdown.
* **saving** (``save``): the state at a sequence number is rendered and written as a version in a
  worker thread; edits made meanwhile stay unsaved (``collab_sheet.mark_saved``).
* **outside versions** (``outside_change``): folded in with ``collab_sheet.reconcile`` and attributed
  to whoever saved them; with ``discard_live`` confirmed while the room held unsaved edits the room
  starts afresh from the version under a new epoch instead (see docs-sheet-live.md §9).

Concurrency: the state is only touched under ``self.mu``. In production every connection runs on
one event loop and the lock is uncontended; the test client runs each socket on its own loop, and
the lock keeps a persist on one thread from serialising the state while another applies an edit.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
import threading
import zlib
from typing import Any, Callable

from pycrdt import read_message, write_message

from synwevo_core import env
from synwevo_core.context import as_tenant, reset_actor, set_actor
from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id

from . import collab_sheet as CS
from . import collab as _collab
from .collab import MESSAGE_SHEET, OWN_LIVE_SAVE, Conn, Room, display_name, meta_frame, sheet_live_enabled

log = logging.getLogger("synwevo.docs.collab")

__all__ = ["SheetRoom", "last_writer", "sheet_live_enabled"]

MAX_CELLS_DEFAULT = 50_000        # docs-sheet-live.md §3: larger templates stay in CI-47's mode
OPS_PER_MIN_DEFAULT = 600         # §4: per person per room
TX_COST_UNIT = 100                # a group costs one message per started hundred operations beyond the first
SYNC_COST = 10                    # a whole state sent again costs like ten messages
SYSTEM = "system"                 # `by` of changes no person made (an outside save without a person, the server's bookkeeping)
_CLIENT = re.compile(r"[A-Za-z0-9_-]{8,64}")


def max_cells() -> int:
    raw = env.get("SHEET_LIVE_MAX_CELLS")
    try:
        return max(0, int(raw)) if raw else MAX_CELLS_DEFAULT
    except ValueError:
        return MAX_CELLS_DEFAULT


def ops_per_minute() -> int:
    raw = env.get("SHEET_LIVE_OPS_PER_MIN")
    try:
        return max(1, int(raw)) if raw else OPS_PER_MIN_DEFAULT
    except ValueError:
        return OPS_PER_MIN_DEFAULT


def sheet_frame(payload: dict) -> bytes:
    return bytes([MESSAGE_SHEET]) + write_message(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())


def last_writer(state: dict) -> str | None:
    """The person who last changed the live state (the newest object write that a person made)."""
    best = None
    for seq_by in state.get("kseq", {}).values():
        if seq_by[1] != SYSTEM and (best is None or seq_by[0] > best[0]):
            best = seq_by
    return best[1] if best else None


def _cells_in(editor: dict) -> int:
    return sum(len(sh["cells"]) for sh in (editor.get("sheets") or []) if isinstance(sh, dict) and isinstance(sh.get("cells"), dict))


def _named_by(detail) -> set[str]:
    """The people a refusal's detail names (every ``by``), so the client can show their names."""
    out: set[str] = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "by":
                    out.update(i for i in (v if isinstance(v, list) else [v]) if isinstance(i, str))
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(detail)
    return out


def _cost(op) -> int:
    if isinstance(op, dict) and op.get("op") == "tx" and isinstance(op.get("ops"), list):
        return 1 + min(len(op["ops"]), CS.MAX_TX) // TX_COST_UNIT
    return 1


class SheetRoom(Room):
    """A table template's room with the switch on: live when its model can be held, presence-only
    otherwise (``state is None``)."""

    sheet = True

    def __init__(self, hub, tid: str, tenant: str):
        super().__init__(hub, tid, tenant, kind="template", writable=False)
        self.mu = threading.RLock()
        self.save_mu = threading.Lock()                 # one save at a time: the second snapshots after the first
        self.write_mu = threading.Lock()
        self.state: dict | None = None
        self.live_reason: dict | None = None
        self.persisted_seq = 0
        self._gen = 0                                   # serialisations handed out ...
        self._written = 0                               # ... and the newest one on disk
        self._dropped = False                           # left live mode: nothing of this room is written again
        self._names: dict[str, dict] = {}

    # ---- mode --------------------------------------------------------------------------------------
    def persists(self) -> bool:
        return self.state is not None

    def live(self) -> bool:
        return self.state is not None

    def _not_live(self, reason: dict) -> dict:
        self.state, self.live_reason, self.epoch, self.dirty = None, reason, None, False
        return reason

    def _seed(self, editor) -> dict | None:
        """Start from a version's model under a new epoch; the reason the room cannot be live, or None."""
        if not isinstance(editor, dict):
            return self._not_live({"code": "no_model"})
        cells, cap = _cells_in(editor), max_cells()
        if cells > cap:
            return self._not_live({"code": "too_large", "what": "cells", "max": cap, "size": cells})
        try:
            st = CS.seed_state(editor, epoch=new_id("ep"))
        except CS.SeedError as e:
            return self._not_live({"code": (e.issues[0].get("code") if e.issues else None) or "invalid", "issues": e.issues[:10]})
        except ServiceError as e:
            return self._not_live({"code": e.code})
        self.state, self.live_reason, self.epoch, self.dirty, self.persisted_seq = st, None, st["epoch"], False, 0
        return None

    def load_sheet(self, template: dict, row: dict | None, editor_of_head: Callable[[], dict | None]) -> None:
        """Worker thread, before anyone can reach the room: the persisted draft when it belongs to the
        head version, else the head version seeded afresh."""
        self.head = template["head"]
        if row and row.get("model") == "sheet" and row.get("head") == template["head"] and row.get("state"):
            try:
                st = json.loads(zlib.decompress(bytes(row["state"])))
                if not (isinstance(st, dict) and st.get("format") == CS.FORMAT and isinstance(st.get("content"), dict)
                        and isinstance(st.get("epoch"), str) and type(st.get("seq")) is int):
                    raise ValueError("not a live table state")
                dirty = CS.dirty(st)                      # renders it: a draft that cannot be rendered is not used
            except Exception:                            # noqa: BLE001 — a corrupt draft must not keep the room from opening
                log.exception("collab %s: the persisted live table could not be read; reseeding from v%s (the draft is lost)", self.key, template["head"])
                self.recovered = True
            else:
                self.state, self.epoch, self.dirty, self.persisted_seq = st, st["epoch"], dirty, st["seq"]
                return
        elif row:
            log.info("collab %s: persisted live state was for v%s, head is v%s — reseeding", self.key, row.get("head"), template["head"])
        self._seed(editor_of_head())

    # ---- joining -----------------------------------------------------------------------------------
    def _client_for(self, conn: Conn, requested) -> str:
        """The tab's client id: the one it brings back (to resume) when valid and not held by another
        open tab of this person, else a new one. Keys are per person, so nobody can answer for, or
        spend the numbers of, someone else's tab."""
        taken = {c.client for c in self.clients if c.user_id == conn.user_id and c.client}
        if isinstance(requested, str) and _CLIENT.fullmatch(requested) and requested not in taken:
            return requested
        return new_id("sc")

    def admit(self, conn: Conn, requested_client: str | None = None) -> bytes:
        with self.mu:
            st = self.state
            if st is not None:
                conn.client = self._client_for(conn, requested_client)
                conn.client_key = f"{conn.user_id}/{conn.client}"
                conn.seen = st["seq"]
            self.clients.add(conn)
            hello = self.hello(conn)
            if st is None:
                hello.update({"live": False, "live_reason": self.live_reason})
            else:
                hello.update({"live": True, "seq": st["seq"], "client": conn.client, "next": CS.next_number(st, conn.client_key),
                              "resumed": conn.client_key in st["clients"], "persisted": self.persisted_seq, "content": st["content"],
                              "limits": {"max_tx": CS.MAX_TX, "max_frame_bytes": _collab.MAX_FRAME_BYTES, "ops_per_min": self.hub.sheet_rate.cap}})
            return meta_frame(hello)

    def state_payload(self) -> dict:
        out = super().state_payload()
        if self.state is not None:
            out["seq"] = self.state["seq"]
        return out

    def last_writer(self) -> str | None:
        with self.mu:
            return last_writer(self.state) if self.state is not None else None

    # ---- names ------------------------------------------------------------------------------------
    def _who(self, uid: str | None) -> dict:
        if not uid or uid == SYSTEM:
            return {"id": None, "name": None, "color": None, "system": True}
        w = self._names.get(uid)
        if w is None:
            c = next((c for c in list(self.clients) if c.user_id == uid), None)
            w = {"id": uid, "name": c.name, "color": c.color} if c else (self.hub._who(uid) or {"id": uid, "name": "—", "color": None})
            self._names[uid] = w
        return w

    # ---- frames from a connection ------------------------------------------------------------------
    def _send(self, conn: Conn, payload: dict) -> None:
        asyncio.ensure_future(conn.send(sheet_frame(payload)))

    def _refuse(self, conn: Conn, n, code: str, **detail) -> None:
        self._send(conn, {"type": "ack", "n": n if type(n) is int else None, "ok": False, "code": code, "detail": detail})

    def _rate(self, conn: Conn, cost: int) -> float | None:
        """Seconds to wait when this person is over their share of the room, else None."""
        bucket = self.hub.sheet_rate
        key = f"{self.key}|{conn.user_id}"
        for i in range(cost):
            ok, _left, wait = bucket.take(key)
            if not ok:
                return round(wait + (cost - i - 1) * bucket.WINDOW_S / bucket.cap, 2)
        return None

    def on_sheet_frame(self, conn: Conn, data: bytes) -> None:
        try:
            msg = json.loads(read_message(data[1:]))
        except Exception:                                # noqa: BLE001 — not the protocol: said, not applied
            msg = None
        if not isinstance(msg, dict):
            self._refuse(conn, None, "invalid", reason="frame")
            return
        kind = msg.get("type", "op")
        n = msg.get("n")
        op = msg.get("op")
        if kind == "op" and msg.get("tx") is not None:
            op = {"op": "tx", "ops": msg["tx"]} if op is None else None
        wait = self._rate(conn, SYNC_COST if kind == "sync" else _cost(op))
        if wait is not None:
            self._refuse(conn, n, "rate_limited", retry_after=wait)
            return
        if kind == "sync":
            self._send_full(conn)
            return
        if kind != "op":
            self._refuse(conn, n, "invalid", reason="type")
            return
        if "client" in msg and msg["client"] != conn.client:
            self._refuse(conn, n, "invalid", reason="client")
            return
        expect = msg.get("expect")
        if type(n) is not int or n < 1 or type(expect) is not int or op is None:
            self._refuse(conn, n, "invalid", reason="n" if type(n) is not int or n < 1 else "expect" if type(expect) is not int else "op")
            return
        if conn.readonly:
            self._refuse(conn, n, "read_only")
            return
        with self.mu:
            st = self.state
            if st is None or conn.client_key is None:
                self._refuse(conn, n, "not_live", reason=self.live_reason)
                return
            try:
                res = CS.apply(st, op, by=conn.user_id, client=conn.client_key, n=n, expect=expect)
            except ValueError:
                self._refuse(conn, n, "invalid", reason="n")
                return
            except Exception:                            # noqa: BLE001 — a fault in the model: said, and the socket stays
                log.exception("collab %s: applying %s's message %s failed", self.key, conn.user_id, n)
                self._refuse(conn, n, "error")
                return
            conn.seen = max(conn.seen, min(expect, st["seq"])) if expect >= 0 else conn.seen
            ack = {"type": "ack", "n": n, **res}
            if not res["ok"] and res.get("detail"):
                people = _named_by(res["detail"])
                if people:
                    ack["people"] = {uid: self._who(uid) for uid in sorted(people)}
            if res["ok"] and not res.get("duplicate") and res["patch"]:
                self.broadcast(sheet_frame({"type": "patch", "seq": res["seq"], "patch": res["patch"], "by": self._who(conn.user_id)}), exclude=conn)
                self._send(conn, ack)
                self._changed()
            else:
                self._send(conn, ack)

    def _send_full(self, conn: Conn) -> None:
        with self.mu:
            st = self.state
            if st is None or conn.client_key is None:
                payload = {"type": "full", "live": False, "reason": self.live_reason, "head": self.head}
            else:
                conn.seen = st["seq"]
                payload = {"type": "full", "live": True, "epoch": st["epoch"], "seq": st["seq"], "next": CS.next_number(st, conn.client_key),
                           "head": self.head, "dirty": self.dirty, "persisted": self.persisted_seq, "content": st["content"]}
            self._send(conn, payload)

    def _changed(self) -> None:
        """After an accepted edit (event loop, under the lock). Whether the room is dirty is read from
        the state's record of changed paths (O(1)); the exact answer — a rendering — is taken when
        the draft is written (``persist``) and corrects it if needed."""
        was = self.dirty
        self.dirty = bool(self.state["base"])
        self.schedule_persist()
        if self.dirty != was:
            self.broadcast_meta(self.state_payload())

    # ---- persistence -------------------------------------------------------------------------------
    def _serialize(self) -> dict | None:
        with self.mu:
            st = self.state
            if st is None or self._dropped:
                return None
            self._gen += 1
            return {"gen": self._gen, "text": json.dumps(st, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                    "seq": st["seq"], "fast": self.dirty, "head": self.head, "epoch": self.epoch}

    def _write(self, snap: dict) -> bool:
        """Compress and write one serialisation (a worker thread, or the last one out); a newer one
        already written wins. Returns whether that state is really unsaved."""
        exact = bool(snap["fast"]) and CS.dirty(json.loads(snap["text"]))
        blob = zlib.compress(snap["text"].encode(), 6)
        with self.write_mu:
            if self._dropped or snap["gen"] < self._written:
                return exact
            self.hub.write_state(self, blob, model="sheet", head=snap["head"], dirty=exact, epoch=snap["epoch"])
            self._written = snap["gen"]
        return exact

    async def persist(self) -> None:
        from starlette.concurrency import run_in_threadpool
        self.persist_handle = None
        snap = self._serialize()
        if snap is None:
            return
        try:
            exact = await run_in_threadpool(self._write, snap)
        except Exception:                                # noqa: BLE001 — the next change retries
            log.exception("collab %s: persisting the live table failed", self.key)
            return
        with self.mu:
            same = self.state is not None and self.epoch == snap["epoch"]
            fix = same and self.state["seq"] == snap["seq"] and self.dirty != exact
            if fix:
                self.dirty = exact
            if same:
                self.persisted_seq = max(self.persisted_seq, snap["seq"])
        if fix:
            self.broadcast_meta(self.state_payload())
        if same:
            self.broadcast_meta({"type": "persisted", "seq": snap["seq"], "epoch": snap["epoch"]})

    def persist_now(self) -> None:
        try:
            snap = self._serialize()
            if snap is not None:
                self._write(snap)
        except Exception:                                # noqa: BLE001
            log.exception("collab %s: persisting the live table on close failed", self.key)

    def forget_draft(self) -> None:
        """The template is deleted: its draft is removed and nothing of this room is written again."""
        self._drop_draft()

    def _drop_draft(self) -> None:
        with self.write_mu:
            self._dropped = True
            try:
                self.hub.store.execute("DELETE FROM collab_docs WHERE room=?", (self.key,))
            except Exception:                            # noqa: BLE001
                log.exception("collab %s: removing the live table draft failed", self.key)

    # ---- versions ----------------------------------------------------------------------------------
    def save(self, user_id: str | None, note) -> dict:
        """Worker thread (the save route): render the state at this moment, write it as a version,
        then mark the room saved on its event loop. Saves of one room run one after the other."""
        from .builtin_service import save_live_sheet
        with self.save_mu:
            with self.mu:
                if self.state is None:
                    raise ServiceError(409, "not_live", "这张表格没有正在进行的实时协同", {"reason": self.live_reason})
                snap = CS.snapshot(self.state)
                base_head = self.head
            editor = CS.project(snap)
            OWN_LIVE_SAVE.room = self
            token = set_actor(user_id)
            try:
                with as_tenant(self.tenant), self.hub.acting(self.tid, user_id):
                    detail = save_live_sheet(self.hub.app, self.tid, editor, base_head=base_head, note=note)
            finally:
                reset_actor(token)
                OWN_LIVE_SAVE.room = None
            unchanged = bool(detail.get("unchanged"))
            detail["live"] = self._on_loop(lambda inline: self._saved(snap, base_head, detail["head"], user_id, unchanged, inline))
            return detail

    def _on_loop(self, fn: Callable[[bool], Any]) -> Any:
        """Run ``fn`` on the room's event loop (patches must be numbered and sent in one order) and
        wait for it. When nobody is connected any more, run it here: there is nobody to tell."""
        loop = self.work_loop()
        if loop is not None and self.hub.rooms.get(self.key) is self:
            fut: concurrent.futures.Future = concurrent.futures.Future()

            def run():
                if fut.set_running_or_notify_cancel():
                    try:
                        fut.set_result(fn(False))
                    except BaseException as e:            # noqa: BLE001 — handed to the waiting thread
                        fut.set_exception(e)
            loop.call_soon_threadsafe(run)
            try:
                return fut.result(timeout=30)
            except concurrent.futures.TimeoutError:
                if not fut.cancel():
                    return fut.result()
                log.warning("collab %s: the room's event loop did not answer; marking the save here", self.key)
        return fn(True)

    def _saved(self, snap: dict, base_head: int, saved_head: int, user_id: str | None, unchanged: bool, inline: bool) -> dict:
        with self.mu:
            st = self.state
            if st is None or st["epoch"] != snap["epoch"]:
                return {"seq": snap["seq"], "stale": True}
            res = CS.mark_saved(st, snap)
            if res.get("stale"):
                # an outside version was folded in while this one was built: the room follows that one
                return {"seq": snap["seq"], "dirty": self.dirty, "stale": True}
            if self.head == base_head:
                self.head = saved_head
            self.dirty = res["dirty"]
            # everyone connected has seen up to here: who wrote what before it (and before this basis)
            # is no longer needed to decide conflicts
            CS.compact(st, upto=min([c.seen for c in list(self.clients) if c.client_key] or [st["seq"]]))
            if res["patch"]:
                self.broadcast(sheet_frame({"type": "patch", "seq": res["seq"], "patch": res["patch"], "by": self._who(SYSTEM)}))
            if unchanged:
                self.broadcast_meta(self.state_payload())
            else:
                name = display_name(self.hub.identity.get_user(user_id)) if user_id else None
                self.broadcast_meta({**self.state_payload(), "type": "head", "reason": "save", "by": name, "by_id": user_id, "saved_seq": snap["seq"]})
            out = {"seq": snap["seq"], "dirty": self.dirty}
        if not inline:
            self.schedule_persist()
        elif self.hub.rooms.get(self.key) in (None, self):
            self.persist_now()                           # everyone left during the build: keep the draft current
        return out

    def outside_change(self, head: int | None, editor: dict | None, reason: str, actor_id: str | None, actor_name: str | None,
                       discard_live: bool) -> None:
        """Event loop: a version was written outside the room (the machine API, a single-person save,
        a field changed on the project page, restore / undo / redo)."""
        with self.mu:
            if self.state is None:
                super().apply_head_change(head, editor, reason, actor_name, False, actor_id)
                return
            by = actor_id or SYSTEM
            if discard_live and self.dirty:
                # the caller confirmed throwing the unsaved edits away (CI-113's discard_live): the room
                # starts afresh from the version; tabs holding the previous epoch are told to reload
                self._restart(head, editor, reason, actor_id, actor_name)
                return
            try:
                if not isinstance(editor, dict):
                    raise CS.SeedError([{"code": "no_model"}])
                res = CS.reconcile(self.state, editor, by=by)
            except CS.SeedError as e:
                self._leave_live(head, reason, actor_id, actor_name, {"code": (e.issues[0].get("code") if e.issues else None) or "invalid",
                                                                      "issues": e.issues[:10]})
                return
            except Exception:                            # noqa: BLE001 — a bug must not wedge the room on an old version
                log.exception("collab %s: folding v%s into the live table failed; starting afresh from it", self.key, head)
                self._restart(head, editor, reason, actor_id, actor_name)
                return
            self.head = head
            if res["patch"]:
                self.broadcast(sheet_frame({"type": "patch", "seq": res["seq"], "patch": res["patch"], "by": self._who(by),
                                            "outside": {"reason": reason, "head": head}}))
            if res["overwritten"]:
                self.broadcast_meta({"type": "overwritten", "people": [self._who(p) for p in res["overwritten"]], "conflicts": res["conflicts"],
                                     "by": self._who(by), "head": head, "seq": res["seq"], "reset": res["reset"]})
            self.dirty = CS.dirty(self.state)
            self.schedule_persist()
            self.broadcast_meta({**self.state_payload(), "type": "head", "reason": reason, "by": actor_name, "by_id": actor_id})

    def _restart(self, head, editor, reason, actor_id, actor_name) -> None:
        previous = self.epoch
        why = self._seed(editor)
        self.head, self.recovered = head, False
        if why is not None:
            self._leave_live(head, reason, actor_id, actor_name, why)
            return
        st = self.state
        for c in list(self.clients):
            c.seen = 0
            self._send(c, {"type": "reset", "epoch": st["epoch"], "previous_epoch": previous, "seq": st["seq"],
                           "next": CS.next_number(st, c.client_key or ""), "head": head, "dirty": False, "reason": reason,
                           "by": self._who(actor_id), "persisted": 0, "content": st["content"]})
        self.schedule_persist()
        self.broadcast_meta({**self.state_payload(), "type": "head", "reason": reason, "by": actor_name, "by_id": actor_id})

    def _leave_live(self, head, reason, actor_id, actor_name, why: dict) -> None:
        """The version cannot be held live: the room carries presence only from now on (CI-47) and
        its draft — which the version replaced — is removed."""
        log.info("collab %s: v%s cannot be held live (%s); the room carries presence only", self.key, head, why.get("code"))
        self._not_live(why)
        self.head = head
        if self.persist_handle is not None:
            self.persist_handle.cancel()
            self.persist_handle = None
        self._drop_draft()
        self.broadcast_meta({"type": "live", "live": False, "reason": why, "head": head})
        self.broadcast_meta({**self.state_payload(), "type": "head", "reason": reason, "by": actor_name, "by_id": actor_id})
