"""The live table room (docs/platform/docs-sheet-live.md §3–§4 and §9, batch B1 step ②).

Behind the switch ``SYNWEVO_SHEET_LIVE`` (off by default; ``on``, or a comma-separated list of the
workspace ids a trial covers: ``collab.sheet_live_scope``) the room of a table template in a workspace
it covers holds the shared live state of :mod:`collab_sheet` instead of presence only; everywhere else
the room is CI-47's presence room. Everything else about the room —
the handshake, the 30-second authorization re-check, awareness bound to the proven identity, the
2 MiB frame bound, version frames — is the document room's (``collab.py``); this module adds:

* **opening**: the persisted draft (``collab_docs``, ``model='sheet'``) when it belongs to the head
  version; a draft with unsaved edits left at an older version is loaded and the head version folded
  into it; otherwise the head version seeded under a new epoch. A model the live state cannot hold
  (``SeedError``, more than ``SYNWEVO_SHEET_LIVE_MAX_CELLS`` cells or ``SYNWEVO_SHEET_LIVE_MAX_BYTES``
  of state) keeps the room in CI-47's presence-only mode and ``hello`` says so (``live: false``,
  ``live_reason``); an unreadable draft is logged and replaced by the head version (``recovered``).
* **hello** carries ``live``, ``epoch``, ``seq``, ``client``, ``next``, ``resumed``, ``persisted``,
  ``limits`` and the whole ``content`` a browser mirrors. ``client`` is the tab's client id: the one
  it asked for with ``?client=`` when valid — a reconnecting tab takes its id over from its own
  previous connection, which is closed (4409 ``superseded``) — else a new one.
* **frames** of type ``MESSAGE_SHEET`` (101), JSON after the varuint length like the meta frames:

  - client → server ``{"type": "op", "epoch", "n", "expect", "op": {...}}`` (``"tx": [...]`` is
    shorthand for ``"op": {"op": "tx", "ops": [...]}``; a ``"client"`` in the frame must be the
    connection's) and ``{"type": "sync"}`` (send me the whole state again);
  - server → sender ``{"type": "ack", "n", "ok", "seq", "patch", "before"}`` or ``{"type": "ack",
    "n", "ok": false, "code", "detail", "people"}``. The model's codes (``conflict`` names whose value
    it was; ``resync`` asks for ``sync``) and the room's own: ``stale_epoch`` (the message belongs
    to an epoch the room has left: never applied, numbers untouched), ``read_only``,
    ``rate_limited`` / ``busy`` with ``retry_after`` seconds (not recorded: resend the same
    message), ``too_large`` (``what: "table"``), ``superseded``, ``not_live``, ``invalid``,
    ``error``. A message delivered twice answers what it answered the first time (``duplicate``);
  - server → everyone else ``{"type": "patch", "seq", "patch", "by"}`` — ``by`` is stamped by the
    server from the connection, never read from the frame; a patch from outside the room carries
    ``outside: {reason, head}``;
  - ``{"type": "full", ...}`` (the answer to ``sync``) and ``{"type": "reset", "epoch", ...}`` (the
    room started afresh from a version: everything a tab holds from the previous epoch is void).

  Meta frames (100) add ``persisted {seq}``, ``overwritten`` (an outside version replaced these
  people's values) and ``live`` (the room left live mode, or ``stale``: a version could not be folded
  in and the unsaved edits are kept instead of dropped).
* **persistence**: zlib-compressed JSON, a second after the last change, synchronously when the last
  person leaves and at shutdown.
* **saving** (``save``): the state at a sequence number is rendered and written as a version in a
  worker thread; edits made meanwhile stay unsaved (``collab_sheet.mark_saved``).
* **outside versions** (``outside_change``): folded in with ``collab_sheet.reconcile`` in a worker
  thread (the room answers ``busy`` meanwhile) and attributed to whoever saved them; with
  ``discard_live`` confirmed while the room held unsaved edits the room starts afresh from the
  version under a new epoch. Unsaved edits are never dropped without that confirmation.

Ordering: work arriving from other threads (outside versions, the end of a save) goes through the
room's queue (``Room.enqueue``), which runs on the room's event loop in arrival order and waits
while the room is held — being opened, or folding a version in a worker thread. While held nothing
changes the state, so the worker can read it without the lock.

Concurrency: the state is only changed under ``self.mu``. In production every connection runs on
one event loop and the lock is uncontended; the test client runs each socket on its own loop.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
import threading
import time
import zlib
from typing import Any, Callable

from pycrdt import read_message, write_message

from synwevo_core import env
from synwevo_core.context import as_tenant, reset_actor, set_actor
from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id

from . import collab as _collab
from . import collab_sheet as CS
from .collab import CLOSE_STALE, MESSAGE_SHEET, OWN_LIVE_SAVE, Conn, Room, RoomFull, display_name, meta_frame, sheet_live_enabled, sheet_live_scope

log = logging.getLogger("synwevo.docs.collab")

__all__ = ["SheetRoom", "last_writer", "sheet_live_enabled", "sheet_live_scope"]

MAX_CELLS_DEFAULT = 50_000        # docs-sheet-live.md §3: larger templates stay in CI-47's mode; the live table stays below it
MAX_BYTES_DEFAULT = 16 * 1024 * 1024   # the live state's JSON; bounds memory, every draft write and every hello
OPS_PER_MIN_DEFAULT = 600         # §4: per person per room
TX_COST_UNIT = 100                # a group costs one message more per full hundred operations
PARSE_UNIT = 64 * 1024            # ... and at least one message per started 64 KiB of frame
SYNC_COST = 10                    # a whole state sent again costs like ten messages
BUSY_RETRY_S = 0.25               # while a version is folded in: try again after this
HELD_RETRY_S = 0.05               # a message sent after one of the same tab refused for now: try again after this
MAX_LOADS = 32                    # the loads of an epoch a state remembers (a tab that missed more is conservative)
GROWTH = 3                        # a patch grows the state by its content, its basis copy and the bookkeeping
SAVE_WAIT_S = 30.0                # a save waits this long for the room's loop to mark it saved
SYSTEM = "system"                 # `by` of changes no person made (an outside save without a person, the server's bookkeeping)
SHRINKING = frozenset({"clear", "unmerge", "field_del", "sheet_delete"})
_CLIENT = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _env_int(name: str, default: int, least: int) -> int:
    raw = env.get(name)
    try:
        return max(least, int(raw)) if raw else default
    except ValueError:
        return default


def max_cells() -> int:
    return _env_int("SHEET_LIVE_MAX_CELLS", MAX_CELLS_DEFAULT, 0)


def max_bytes() -> int:
    return _env_int("SHEET_LIVE_MAX_BYTES", MAX_BYTES_DEFAULT, 1024)


def ops_per_minute() -> int:
    return _env_int("SHEET_LIVE_OPS_PER_MIN", OPS_PER_MIN_DEFAULT, 1)


def _dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _size(x) -> int:
    """The state's size as it is written and sent: UTF-8 bytes of its JSON."""
    return len(_dumps(x).encode())


def sheet_frame(payload: dict) -> bytes:
    return bytes([MESSAGE_SHEET]) + write_message(_dumps(payload).encode())


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


def _shrinks(op) -> bool:
    """Operations that can only make the table smaller: allowed when it is at its size bound."""
    if isinstance(op, dict) and op.get("op") == "tx" and isinstance(op.get("ops"), list):
        return bool(op["ops"]) and all(isinstance(o, dict) and o.get("op") in SHRINKING for o in op["ops"])
    return isinstance(op, dict) and op.get("op") in SHRINKING


def _over_caps(st: dict, size: int) -> dict | None:
    """Why a live state is beyond the room's own caps (cells on live sheets, bytes of its JSON)."""
    cells = sum(len(sh["cells"]) for sh in st["content"]["sheets"].values() if not sh["gone"])
    if cells > max_cells():
        return {"code": "too_large", "what": "cells", "max": max_cells(), "size": cells}
    if size > max_bytes():
        return {"code": "too_large", "what": "bytes", "max": max_bytes(), "size": size}
    return None


def fits(editor) -> list[dict]:
    """Why an open live table room could not hold ``editor`` — the model's own limits and the room's
    caps (the bytes estimated from the model: the state, with its ids and bookkeeping, is larger, and
    folding it in checks that exactly). ``[]`` when it can."""
    issues = CS.holdable(editor, max_cells=max_cells())
    if issues:
        return issues
    size, cap = len(_dumps(CS.normalize(editor)).encode()), max_bytes()
    return [{"code": "too_large", "what": "bytes", "max": cap, "size": size}] if size > cap else []


def _seeded(editor) -> tuple[dict | None, dict | None, int]:
    """(a new live state of ``editor`` under a new epoch, None, its size) or (None, why it cannot be
    live, 0). Pure: runs in a worker thread."""
    if not isinstance(editor, dict):
        return None, {"code": "no_model"}, 0
    cells, cap = _cells_in(editor), max_cells()
    if cells > cap:
        return None, {"code": "too_large", "what": "cells", "max": cap, "size": cells}, 0
    try:
        st = CS.seed_state(editor, epoch=new_id("ep"))
    except CS.SeedError as e:
        return None, {"code": (e.issues[0].get("code") if e.issues else None) or "invalid", "issues": e.issues[:10]}, 0
    except ServiceError as e:
        return None, {"code": e.code}, 0
    size, cap = _size(st), max_bytes()
    if size > cap:
        return None, {"code": "too_large", "what": "bytes", "max": cap, "size": size}, 0
    return st, None, size


class SheetRoom(Room):
    """A table template's room with the switch on for its workspace (opened only then: ``CollabHub.
    _open_room``): live when its model can be held, presence-only otherwise (``state is None``)."""

    sheet = True

    def __init__(self, hub, tid: str, tenant: str):
        super().__init__(hub, tid, tenant, kind="template", writable=False)
        self.mu = threading.RLock()
        self.save_mu = threading.Lock()                 # one save at a time: the second snapshots after the first
        self.state: dict | None = None
        self.live_reason: dict | None = None
        self.persisted_seq = 0
        # this load of the room (a new id each time it is opened from storage) and the seq it was loaded
        # at: a tab that sees a new id knows that what it had acknowledged above that seq may be gone (a
        # process that died without its closing write) — whatever the room's seq is by the time it is back
        self.boot = new_id("rb")
        self.loaded_seq = 0
        self.approx_bytes = 0                           # the state's JSON size: exact at each write, grown per patch between
        self.basis_gen = 0                              # changes whenever the basis does (a save, a folded version, a restart)
        self.stale_head: int | None = None              # a version that could not be folded in while edits were unsaved
        self.catch_up: tuple[int, dict | None] | None = None
        self._gen = 0                                   # serialisations handed out ...
        self._written = 0                               # ... and the newest one on disk (under the hub's room lock)
        self._names: dict[str, dict] = {}
        self._drop_pending = False

    # ---- mode --------------------------------------------------------------------------------------
    def persists(self) -> bool:
        return self.state is not None

    def live(self) -> bool:
        return self.state is not None

    def _not_live(self, reason: dict) -> dict:
        self.state, self.live_reason, self.epoch, self.dirty = None, reason, None, False
        return reason

    def _take(self, st: dict | None, why: dict | None, size: int) -> None:
        """Hold a freshly seeded state and its size (or say why the room is presence-only)."""
        if st is None:
            self._not_live(why or {"code": "invalid"})
            return
        self.state, self.live_reason, self.epoch, self.dirty, self.persisted_seq = st, None, st["epoch"], False, 0
        self.boot, self.loaded_seq = new_id("rb"), 0                  # a fresh epoch: its first load
        self.approx_bytes = size
        self.stale_head = None
        self.basis_gen += 1

    def load_sheet(self, template: dict, row: dict | None, editor_of_head: Callable[[], dict | None]) -> None:
        """Worker thread, while the room is held (nobody can reach its state): the persisted draft
        when it belongs to the head version; a draft with unsaved edits at an older version, with the
        head version to fold in once the room opens (``catch_up``); else the head version afresh."""
        self.head = template["head"]
        if row and row.get("model") == "sheet" and row.get("state"):
            try:
                raw = zlib.decompress(bytes(row["state"]))
                st = json.loads(raw)
                if not (isinstance(st, dict) and st.get("format") == CS.FORMAT and isinstance(st.get("content"), dict)
                        and isinstance(st.get("epoch"), str) and type(st.get("seq")) is int):
                    raise ValueError("not a live table state")
                dirty = CS.dirty(st)                      # renders it: a draft that cannot be rendered is not used
            except Exception:                            # noqa: BLE001 — a corrupt draft must not keep the room from opening
                log.exception("collab %s: the persisted live table could not be read; reseeding from v%s (the draft is lost)", self.key, template["head"])
                self.recovered = True
            else:
                over = _over_caps(st, len(raw))
                if over is not None and not dirty:
                    # beyond the room's caps (lowered since, or folded in before they were checked) and
                    # nothing unsaved: the head version decides, which the caps then keep out of live mode
                    log.info("collab %s: the persisted live table is beyond the room's caps (%s); reseeding", self.key, over)
                    row = None
                elif row.get("head") == template["head"] or dirty:
                    if over is not None:
                        # unsaved edits are never dropped: kept live, where only shrinking edits pass
                        log.warning("collab %s: the persisted live table with unsaved edits is beyond the room's caps (%s)", self.key, over)
                    self.state, self.epoch, self.dirty, self.persisted_seq, self.approx_bytes = st, st["epoch"], dirty, st["seq"], len(raw)
                    self.boot, self.loaded_seq = new_id("rb"), st["seq"]    # opened from storage: a new load
                    if row.get("head") != template["head"]:
                        # unsaved edits left at an older version (a version that could not be folded
                        # in while they were there): keep them, fold the head version in on opening
                        log.info("collab %s: draft with unsaved edits at v%s, head is v%s — folding the head in", self.key, row.get("head"), template["head"])
                        self.head = row.get("head")
                        self.catch_up = (template["head"], editor_of_head())
                    return
                if row is not None:
                    log.info("collab %s: persisted live state was for v%s, head is v%s — reseeding", self.key, row.get("head"), template["head"])
        self._take(*_seeded(editor_of_head()))

    # ---- joining -----------------------------------------------------------------------------------
    def _claim(self, conn: Conn, requested) -> None:
        """The tab's client id. A tab bringing back the id it had resumes it — also when its previous
        connection is still open here (the server has not noticed yet that it is gone): the new
        connection takes the id over, the old one is closed, so a message resent after the drop is
        recognised as the one already applied. Ids are per person: nobody can answer for, or spend
        the numbers of, someone else's tab."""
        if isinstance(requested, str) and _CLIENT.fullmatch(requested):
            for old in [c for c in self.clients if c.user_id == conn.user_id and c.client == requested]:
                old.client = old.client_key = None
                old.close_later(CLOSE_STALE, "superseded")
            conn.client = requested
        else:
            conn.client = new_id("sc")
        conn.client_key = f"{conn.user_id}/{conn.client}"

    def admit(self, conn: Conn, requested_client: str | None = None) -> bytes:
        with self.mu:
            st = self.state
            if st is not None:
                # the room never forgets the record of a client with an open connection (it may still
                # resend): so it admits no more open tabs than it keeps records for (review 4, item 4)
                taking_over = isinstance(requested_client, str) and _CLIENT.fullmatch(requested_client) is not None and any(
                    c.user_id == conn.user_id and c.client == requested_client for c in self.clients)
                if not taking_over and not conn.readonly:
                    # only tabs that can write hold records: viewers never lock writers out (review 5, item 4)
                    refusal = self._over_tab_caps(conn)
                    if refusal is not None:
                        raise RoomFull(refusal)
                self._claim(conn, requested_client)
                conn.seen = st["seq"]
                self._stamp_load(st)
            self.clients.add(conn)
            hello = self.hello(conn)
            if st is None:
                hello.update({"live": False, "live_reason": self.live_reason})
            else:
                hello.update({"live": True, "seq": st["seq"], "client": conn.client, "next": CS.next_number(st, conn.client_key),
                              "resumed": conn.client_key in st["clients"], "outcomes": CS.outcomes(st, conn.client_key),
                              "persisted": self.persisted_seq, "content": st["content"],
                              "loaded": {"id": self.boot, "seq": self.loaded_seq}, "loads": list(st["loads"]),
                              "stale_head": self.stale_head,
                              "limits": {"max_tx": CS.MAX_TX, "max_frame_bytes": _collab.MAX_FRAME_BYTES, "ops_per_min": self.hub.sheet_rate.cap,
                                         "max_cells": max_cells(), "max_bytes": max_bytes()}})
            return meta_frame(hello)

    def _stamp_load(self, st: dict) -> None:
        """Under ``mu``: the state records this load of the room (once) — in the drafts it writes and the
        hellos it sends — so a tab that was away while the room was loaded more than once can tell
        from the load it last saw what a later one dropped (every load's seq; the lowest after its own
        is where its history may have ended)."""
        loads = st.setdefault("loads", [])
        if not loads or loads[-1].get("id") != self.boot:
            loads.append({"id": self.boot, "seq": self.loaded_seq})
            del loads[:-MAX_LOADS]

    def _over_tab_caps(self, conn: Conn) -> str | None:
        """Under ``mu``: would one more writing tab of this person pass what the room keeps records for?"""
        writers = [c for c in self.clients if c.client_key and not c.readonly and c is not conn]
        if sum(1 for c in writers if c.user_id == conn.user_id) >= CS.MAX_CLIENTS_PER_SCOPE:
            return "too_many_tabs"
        if len(writers) >= CS.MAX_CLIENTS:
            return "room_full"
        return None

    def on_promoted(self, conn: Conn) -> str | None:
        if self.state is None:
            return None
        with self.mu:
            return self._over_tab_caps(conn)

    def closing_job(self) -> Callable[[], None]:
        return self.close_final                          # serialised in the writing thread

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
        conn.post(sheet_frame(payload))

    def _refuse(self, conn: Conn, n, code: str, **detail) -> None:
        self._send(conn, {"type": "ack", "n": n if type(n) is int else None, "ok": False, "code": code, "detail": detail})

    def _wait_refusal(self, conn: Conn, n, code: str, wait: float, *, epoch=None) -> None:
        """Refused for now (``rate_limited`` / ``busy``); frames sooner than that are refused unread. A
        message of the room's current ``epoch`` refused so is held: the connection's later messages wait
        for it (``on_sheet_frame``). One of an epoch the room has left is not: the tab has voided it and
        never sends it again."""
        wait = round(max(wait, 0.01), 2)
        conn.rate_until = time.monotonic() + wait
        st = self.state
        if epoch is not None and st is not None and epoch == st["epoch"] and type(n) is int and conn.held_n is None:
            conn.held_n, conn.held_code, conn.held_epoch = n, code, epoch
        self._refuse(conn, n, code, retry_after=wait)

    def on_sheet_frame(self, conn: Conn, data: bytes) -> None:
        now = time.monotonic()
        if now < conn.rate_until:
            # told to wait and did not: refused without reading it (parsing is work too)
            self._refuse(conn, None, "rate_limited", retry_after=round(conn.rate_until - now, 2))
            return
        bucket, key = self.hub.sheet_rate, f"{self.key}|{conn.user_id}"
        fee = 1 + len(data) // PARSE_UNIT
        try:
            msg = json.loads(read_message(data[1:]))
        except Exception:                                # noqa: BLE001 — not the protocol: said, not applied
            msg = None
        if not isinstance(msg, dict):
            ok, _left, wait = bucket.take(key, fee)       # reading it was work: it costs like any message
            if not ok:
                self._wait_refusal(conn, None, "rate_limited", wait)
                return
            self._refuse(conn, None, "invalid", reason="frame")
            return
        kind = msg.get("type", "op")
        n = msg.get("n")
        op = msg.get("op")
        if kind == "op" and msg.get("tx") is not None:
            op = {"op": "tx", "ops": msg["tx"]} if op is None else None
        if conn.held_n is not None and (self.state is None or conn.held_epoch != self.state["epoch"]):
            conn.held_n = None                           # held under an epoch the room has left: void
        if kind == "op" and conn.held_n is not None and n != conn.held_n:
            # an earlier message of this tab was refused for now and has not come back: nothing it sent
            # after that applies before it (the tab sends them all again, in order) — else its later
            # value would be overwritten by its earlier one, or an edit in rows it had just added refused
            ok, _left, wait = bucket.take(key, fee)
            if not ok:
                self._wait_refusal(conn, n, "rate_limited", wait)
                return
            self._refuse(conn, n, conn.held_code, retry_after=round(max(conn.rate_until - now, HELD_RETRY_S), 2), held=conn.held_n)
            return
        if self.holding():
            self._wait_refusal(conn, n, "busy", BUSY_RETRY_S, epoch=msg.get("epoch") if kind == "op" else None)
            return
        # the whole message is paid for at once, and a refused one pays nothing: waiting the time it
        # is told is always enough (RateBuckets.take with a cost)
        # a whole state sent again costs by its size (a viewer asking for it over and over is metered too)
        cost = max(SYNC_COST, 1 + self.approx_bytes // PARSE_UNIT) if kind == "sync" else _cost(op)
        ok, _left, wait = bucket.take(key, max(fee, cost))
        if not ok:
            self._wait_refusal(conn, n, "rate_limited", wait, epoch=msg.get("epoch") if kind == "op" else None)
            return
        if kind == "op" and conn.held_n == n:
            conn.held_n = None                           # the held message is back: it is answered now, in order
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
        with self.mu:
            st = self.state
            if st is None:
                self._refuse(conn, n, "not_live", reason=self.live_reason)
                return
            if msg.get("epoch") != st["epoch"]:
                # typed against an epoch the room has left (a restart crossed it on the wire): never
                # applied, and no number of the new epoch is spent
                self._refuse(conn, n, "stale_epoch", epoch=st["epoch"], seq=st["seq"])
                return
            if conn.readonly:
                self._refuse(conn, n, "read_only")
                return
            if conn.client_key is None:
                self._refuse(conn, n, "superseded")
                return
            cap = max_bytes()
            if self.approx_bytes > cap and not _shrinks(op):
                self._refuse(conn, n, "too_large", what="table", max=cap, size=self.approx_bytes)
                return
            try:
                res = CS.apply(st, op, by=conn.user_id, client=conn.client_key, n=n, expect=expect, max_cells=max_cells(),
                               keep=frozenset(c.client_key for c in list(self.clients) if c.client_key))
            except ValueError:
                self._refuse(conn, n, "invalid", reason="n")
                return
            except Exception:                            # noqa: BLE001 — a fault in the model: undone by apply, said, and the socket stays
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
                frame = sheet_frame({"type": "patch", "seq": res["seq"], "patch": res["patch"], "by": self._who(conn.user_id)})
                self.approx_bytes += GROWTH * len(frame)
                self.broadcast(frame, exclude=conn)
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
                           "outcomes": CS.outcomes(st, conn.client_key), "head": self.head, "dirty": self.dirty,
                           "persisted": self.persisted_seq, "content": st["content"]}
            self._send(conn, payload)

    def _persist_soon(self) -> None:
        """Schedule the draft write — for a room still registered, when a loop runs here; a room
        finishing a fold or marking a save after it closed leaves the writing to its closing write
        (which comes after them) or its correction."""
        if self.hub.rooms.get(self.key) is not self:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.schedule_persist()

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
            if st is None or self.dropped:
                return None
            self._stamp_load(st)
            self._gen += 1
            raw = _dumps(st).encode()
            self.approx_bytes = len(raw)
            return {"gen": self._gen, "raw": raw, "seq": st["seq"], "fast": self.dirty, "head": self.head, "epoch": self.epoch,
                    "basis_gen": self.basis_gen}

    def _write(self, snap: dict, *, final: bool = False) -> tuple[bool, bool]:
        """Compress and write one serialisation (a worker thread; the closing write; shutdown). The
        heavy part runs before any lock; the write itself is checked and done under the hub's room lock
        (``write_state``): a room that is no longer the registered one, or is gone, never writes, and a
        newer serialisation already written wins. Returns (was it written, is that state really
        unsaved); a write that was refused is never reported as persisted."""
        exact = bool(snap["fast"]) and CS.dirty(json.loads(snap["raw"]))
        blob = zlib.compress(snap["raw"], 6)
        with self.hub.store.tx():                        # the write lock first: this room's writes, one at a time
            if snap["gen"] < self._written:
                return False, exact
            written = self.hub.write_state(self, blob, model="sheet", head=snap["head"], dirty=exact, epoch=snap["epoch"], final=final)
            if written:
                self._written = snap["gen"]
        return written, exact

    def close_final(self) -> None:
        """The last one out: write the draft once more (a thread of its own, off the event loop and
        outside every room lock; the next opener of this template waits for it). Afterwards the room
        writes only to correct that draft (a save marked later), and only while no newer room has
        been registered for the template (CollabHub._may_write)."""
        self._write_now(final=True)

    def _write_now(self, *, final: bool = False) -> None:
        try:
            snap = self._serialize()
            if snap is not None:
                self._write(snap, final=final)
        except Exception:                                # noqa: BLE001
            log.exception("collab %s: writing the live table failed", self.key)

    async def persist(self) -> None:
        from starlette.concurrency import run_in_threadpool
        self.persist_starting()
        snap = self._serialize()
        if snap is None:
            return
        try:
            written, exact = await run_in_threadpool(self._write, snap)
        except Exception:                                # noqa: BLE001 — the next change retries
            log.exception("collab %s: persisting the live table failed", self.key)
            return
        if not written:
            # refused (the room closed, left live mode, its template went) or overtaken by a newer write:
            # nothing is reported as persisted and the dirty flag is not touched
            return
        with self.mu:
            same = self.state is not None and self.epoch == snap["epoch"]
            # the exact answer corrects the quick one only for the state it was computed from: not
            # after a save or a folded version moved the basis meanwhile (it would mark a saved room dirty)
            fix = same and self.state["seq"] == snap["seq"] and self.basis_gen == snap["basis_gen"] and self.dirty != exact
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

    def _write_and_tell(self, snap: dict) -> bool:
        """A worker thread (a save): write one serialisation now and, written, say so like ``persist``
        (the tabs keep what they typed until `persisted` covers it). Whether the draft now holds it —
        written, or a newer serialisation of this room was written already; False when it could not be
        written (a database error, or the room may not write any more): the save then writes no version
        (``save``). Never raises."""
        try:
            written, _exact = self._write(snap)
        except Exception:                                # noqa: BLE001 — the save is refused instead (``save``)
            log.exception("collab %s: writing the live table before a save failed; the save writes no version", self.key)
            return False
        if not written:
            return snap["gen"] < self._written           # overtaken by a newer write: that one holds it
        with self.mu:
            same = self.state is not None and self.epoch == snap["epoch"]
            if same:
                self.persisted_seq = max(self.persisted_seq, snap["seq"])
        if same:
            self.broadcast_meta({"type": "persisted", "seq": snap["seq"], "epoch": snap["epoch"]})
        return True

    def forget_draft(self) -> None:
        """The template is deleted: nothing of this room is written again (the hook removes the row)."""
        self.dropped = True

    def _drop_draft(self) -> None:
        """Left live mode: nothing of this room is written again, and its draft is removed — in a
        thread of its own (this runs on the loop, under the room's lock, which never waits for the
        database). A write already past its check commits first; the DELETE follows it."""
        self.dropped = True
        # registered draft work: an opener of the template waits for it (review 5, item 3); and it
        # removes only this room's draft — none of a room registered after it (item 5)
        self.hub.draft_work(self.key, lambda gen=self.gen: self.hub.delete_draft(self.key, gen),
                            room=None, name=f"collab-drop-{self.tid}")

    # ---- saving ------------------------------------------------------------------------------------
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
                draft = self._serialize()                  # this very state, as its draft
            # The draft of the snapshot goes first: a process that dies after the version is written and
            # before the next draft write comes back from a draft that holds all the version holds, so
            # folding the version in on opening changes nothing and no tab has anything below it to send
            # again. With an older draft the tabs sent again what the version already carried — rows
            # added twice (the crash model of review round 3). A draft that could not be written (a
            # database error) refuses the save: written anyway, the version reopened that window (the
            # third review's follow-up). Nothing is lost — the room still holds the edits, unsaved, and
            # its own draft writes carry on; saving again writes both.
            if draft is not None and not self._write_and_tell(draft):
                raise ServiceError(503, "live_unwritten", "表格的实时修改暂时无法写入，这次没有保存为版本；请稍后再保存",
                                   {"retry_after": 2})
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
            detail["live"] = self._on_loop(snap, lambda inline: self._saved(snap, base_head, detail["head"], user_id, unchanged, inline))
            return detail

    def _on_loop(self, snap: dict, fn: Callable[[bool], Any]) -> Any:
        """Run ``fn`` on the room's event loop, in order with the versions folded in (the room's
        queue), and wait for it. A loop that does not answer in time does it later (the answer says
        ``pending``); with nobody connected any more it runs here, telling nobody."""
        if self.work_loop() is not None and self.hub.rooms.get(self.key) is self:
            fut: concurrent.futures.Future = concurrent.futures.Future()

            def run():
                if not fut.set_running_or_notify_cancel():
                    return
                try:
                    fut.set_result(fn(False))
                except BaseException as e:            # noqa: BLE001 — handed to the waiting thread
                    log.exception("collab %s: marking a save failed", self.key)
                    fut.set_exception(e)
            self.enqueue(run)
            try:
                return fut.result(timeout=SAVE_WAIT_S)
            except concurrent.futures.TimeoutError:
                log.warning("collab %s: the room's loop did not mark the save within %ss; it will when it runs", self.key, SAVE_WAIT_S)
                return {"seq": snap["seq"], "pending": True}
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
            self.basis_gen += 1
            # everyone connected has seen up to here: who wrote what before it (and before this basis)
            # is no longer needed to decide conflicts
            CS.compact(st, upto=min([c.seen for c in list(self.clients) if c.client_key] or [st["seq"]]))
            out = {"seq": snap["seq"], "dirty": self.dirty}
            if not inline:
                if res["patch"]:
                    self.broadcast(sheet_frame({"type": "patch", "seq": res["seq"], "patch": res["patch"], "by": self._who(SYSTEM)}))
                if unchanged:
                    self.broadcast_meta(self.state_payload())
                else:
                    name = display_name(self.hub.identity.get_user(user_id)) if user_id else None
                    self.broadcast_meta({**self.state_payload(), "type": "head", "reason": "save", "by": name, "by_id": user_id, "saved_seq": snap["seq"]})
        if self.hub.rooms.get(self.key) is self:
            if inline:
                self.persist_now()                       # no loop to schedule on: write now
            else:
                self._persist_soon()
        elif self.closing_started:
            # marked after the room's closing write began: that write may carry the unsaved state —
            # correct it (off the loop, as registered draft work an opener waits for), unless a newer
            # room has been opened since; the newer serialisation wins over the closing write's
            # whichever commits last (review 3, item 3; review 5, item 3). Marked before (the last one
            # out runs the queue ahead of its closing write), the closing write carries it.
            self.hub.draft_work(self.key, lambda: self._write_now(final=True), room=self, name=f"collab-saved-{self.tid}")
        return out

    # ---- versions from outside the room -------------------------------------------------------------
    def outside_change(self, head: int | None, editor: dict | None, reason: str, actor_id: str | None, actor_name: str | None,
                       discard_live: bool) -> None:
        """Event loop, from the room's queue: a version was written outside the room (the machine API,
        a single-person save, a field changed on the project page, restore / undo / redo). The work
        happens in a worker thread; the room is held (and answers ``busy``) until it is folded in."""
        if self.state is None:
            super().apply_head_change(head, editor, reason, actor_name, False, actor_id)
            return
        kind = "restart" if (discard_live and self.dirty) else "reconcile"
        self._fold(kind, head, editor, reason, actor_id, actor_name)

    def _fold(self, kind: str, head, editor, reason, actor_id, actor_name) -> None:
        self.hold()
        by = actor_id or SYSTEM
        dirty = self.dirty

        def work():
            # the room is held: nothing changes its state, so it is read here without the lock
            if kind == "restart":
                return ("seeded",) + _seeded(editor)
            if not isinstance(editor, dict):
                return ("seed_error", {"code": "no_model"})
            mine = json.loads(_dumps(self.state))
            try:
                res = CS.reconcile(mine, editor, by=by)
            except CS.SeedError as e:
                return ("seed_error", {"code": (e.issues[0].get("code") if e.issues else None) or "invalid", "issues": e.issues[:10]})
            size = _size(mine)
            over = _over_caps(mine, size)
            if over is not None:
                return ("seed_error", over)              # beyond the room's caps: as if it could not be held at all
            return ("reconciled", mine, res, CS.dirty(mine), size)

        def finish(out):
            try:
                self._folded(kind, out, dirty, head, editor, reason, actor_id, actor_name)
                if self._drop_pending:                   # left live mode: its draft's DELETE, outside the room's lock
                    self._drop_pending = False
                    self._drop_draft()
            finally:
                self.release()                           # runs what waited: a closing write writes this result

        def thread():
            try:
                out = work()
            except Exception:                            # noqa: BLE001 — a bug must neither wedge the room nor drop edits
                log.exception("collab %s: folding v%s into the live table failed", self.key, head)
                out = ("error", {"code": "error"})
            # the result goes to a loop serving the room — not a task of the loop that started it, which
            # may be gone with its connection (the last one out while the fold ran: review 5, item 1);
            # with no loop serving the room any more, it is finished here
            loop = self.work_loop()
            if loop is not None:
                try:
                    loop.call_soon_threadsafe(finish, out)
                    return
                except RuntimeError:                     # that loop has just closed
                    pass
            finish(out)
        try:
            threading.Thread(target=thread, name=f"collab-fold-{self.tid}", daemon=True).start()
        except Exception:                                # noqa: BLE001 — no thread to be had: fold here
            log.exception("collab %s: could not start the fold; folding here", self.key)
            thread()

    def _folded(self, kind, out, dirty, head, editor, reason, actor_id, actor_name) -> None:
        by = actor_id or SYSTEM
        with self.mu:
            if out[0] == "reconciled":
                _tag, mine, res, now_dirty, size = out
                self.state, self.head, self.dirty, self.approx_bytes, self.stale_head = mine, head, now_dirty, size, None
                self.basis_gen += 1
                if res["patch"]:
                    self.broadcast(sheet_frame({"type": "patch", "seq": res["seq"], "patch": res["patch"], "by": self._who(by),
                                                "outside": {"reason": reason, "head": head}}))
                if res["overwritten"]:
                    self.broadcast_meta({"type": "overwritten", "people": [self._who(p) for p in res["overwritten"]], "conflicts": res["conflicts"],
                                         "by": self._who(by), "head": head, "seq": res["seq"], "reset": res["reset"]})
                self._persist_soon()
                self.broadcast_meta({**self.state_payload(), "type": "head", "reason": reason, "by": actor_name, "by_id": actor_id})
                return
            if out[0] == "seeded":
                _tag, st, why, size = out
                if st is None:
                    self._leave_live(head, reason, actor_id, actor_name, why)       # the caller confirmed the discard
                else:
                    self._restarted(st, size, head, reason, actor_id, actor_name)
                return
            why = out[1]
            if dirty or self.dirty:
                # never drop unsaved edits unasked: the room keeps them at its version and says so; an
                # outside save now needs discard_live (the guard), which starts afresh from a version
                log.warning("collab %s: v%s could not be folded into the live table (%s); its unsaved edits are kept", self.key, head, why.get("code"))
                self.stale_head = head
                self.broadcast_meta({"type": "live", "live": True, "stale": True, "head": head, "reason": why})
                return
            if out[0] == "seed_error":
                self._leave_live(head, reason, actor_id, actor_name, why)           # nothing unsaved: presence mode loses nothing
                return
        self._fold("restart", head, editor, reason, actor_id, actor_name)          # a fault, nothing unsaved: start afresh from it

    def _restarted(self, st: dict, size: int, head, reason, actor_id, actor_name) -> None:
        previous = self.epoch
        self._take(st, None, size)
        self.head, self.recovered = head, False
        # the whole content once, spliced into each frame (every connection's frame differs only in `next`)
        content = _dumps(st["content"])
        frames: dict[int, bytes] = {}
        for c in list(self.clients):
            c.seen = 0
            c.held_n = None                              # a message of the previous epoch will not come back
            nxt = CS.next_number(st, c.client_key or "")
            if nxt not in frames:
                head_json = _dumps({"type": "reset", "epoch": st["epoch"], "previous_epoch": previous, "seq": st["seq"], "next": nxt,
                                    "head": head, "dirty": False, "reason": reason, "by": self._who(actor_id), "persisted": 0,
                                    "loaded": {"id": self.boot, "seq": self.loaded_seq}})
                frames[nxt] = bytes([MESSAGE_SHEET]) + write_message((head_json[:-1] + ',"content":' + content + "}").encode())
            c.post(frames[nxt])
        self._persist_soon()
        self.broadcast_meta({**self.state_payload(), "type": "head", "reason": reason, "by": actor_name, "by_id": actor_id})

    def _leave_live(self, head, reason, actor_id, actor_name, why: dict) -> None:
        """The version cannot be held live and nothing unsaved is lost by that (the room was clean, or
        the discard was confirmed): the room carries presence only from now on (CI-47) and its draft —
        which the version replaced — is removed."""
        log.info("collab %s: v%s cannot be held live (%s); the room carries presence only", self.key, head, why.get("code"))
        self._not_live(why)
        self.head = head
        self.cancel_persist()
        # nothing of this room is written from now on; the draft's DELETE runs after the room's lock is
        # released (the lock order never nests the database inside a room's lock — even when no thread
        # can be started for it and it runs on the loop)
        self.dropped = True
        self._drop_pending = True                        # the fold's finish deletes the draft once the lock is released
        self.broadcast_meta({"type": "live", "live": False, "reason": why, "head": head})
        self.broadcast_meta({**self.state_payload(), "type": "head", "reason": reason, "by": actor_name, "by_id": actor_id})
