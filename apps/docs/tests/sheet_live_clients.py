"""TEST FIXTURE (not product code): real browser sync engines against the real live table room.

Each ``Tab`` is a ``SheetSync`` (``apps/docs/web/src/lib/sheetsync.ts``) living in one Node process
(``apps/docs/web/tests/support/sheet_tabs.ts``); its connection is a ``FakeConn`` on the real
``SheetRoom``, whose frames are queued instead of written to a socket, so the driver delivers every
frame — both ways — in whatever interleaving it chooses, drops connections with frames still on the
wire, holds the room (a version being folded in: ``busy``), and restores the room from an earlier
draft (a server that died without its closing write). The approach is the reviewer's harness of the
live-table review of steps ③–⑤, kept in CI (``test_sheet_live_clients.py``).

Needs ``node`` (22+) and the workspace's ``node_modules`` (``npm ci``)."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from pycrdt import read_message, write_message

from synwevo_docs.collab import MESSAGE_META, MESSAGE_SHEET, Conn

WEB = Path(__file__).resolve().parents[1] / "web"
SIDECAR = WEB / "tests" / "support" / "sheet_tabs.ts"
ROOT = Path(__file__).resolve().parents[3]


def node_missing() -> str | None:
    """Why the Node side cannot run here, or None."""
    if shutil.which("node") is None:
        return "node is not installed"
    if not (ROOT / "node_modules" / "@synwevo" / "ui").exists():
        return "the workspace's node_modules are not installed (npm ci)"
    return None


class Node:
    """The sidecar: one JSON line in, one JSON line out."""

    def __init__(self):
        self.p = subprocess.Popen(["node", "--experimental-strip-types", "--disable-warning=ExperimentalWarning", str(SIDECAR)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=str(WEB), bufsize=1)

    def __call__(self, **m):
        self.p.stdin.write(json.dumps(m, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("the Node side of the fixture exited")
        r = json.loads(line)
        if "error" in r:
            raise RuntimeError(r["error"])
        return r

    def close(self):
        self.p.stdin.close()
        self.p.wait(5)


class FakeConn(Conn):
    """TEST FIXTURE: a connection whose frames are queued for the driver instead of a socket."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.inbox: list[bytes] = []
        self.closed_with = None

    def post(self, data: bytes) -> None:
        self.inbox.append(data)

    def close_later(self, code: int, reason: str) -> None:
        self.closed_with = (code, reason)


def decode(data: bytes):
    if data[0] == MESSAGE_META:
        return "meta", json.loads(read_message(data[1:]))
    if data[0] == MESSAGE_SHEET:
        return "sheet", json.loads(read_message(data[1:]))
    return "other", None


def encode_sheet(payload: dict) -> bytes:
    return bytes([MESSAGE_SHEET]) + write_message(json.dumps(payload, ensure_ascii=False).encode())


class Tab:
    """One browser tab: its engine in Node, its connection (when online) on the room."""

    def __init__(self, h: "Harness", name: str, user: dict, readonly=False):
        self.h, self.name, self.user, self.readonly = h, name, user, readonly
        self.conn: FakeConn | None = None
        self.out: list[dict] = []          # frames the tab sent that have not reached the room yet
        self.client_id: str | None = None
        h.node(cmd="new", tab=name)

    @property
    def online(self):
        return self.conn is not None

    def _take(self, r):
        self.out.extend(r.get("out") or [])
        return r.get("result")

    def connect(self):
        assert self.conn is None
        c = FakeConn(ws=None, user_id=self.user["id"], name=self.user.get("display_name") or self.user["email"], color=1,
                     readonly=self.readonly, workspace_id=self.h.wid, request=None, path="")
        hello = self.h.room.admit(c, self.client_id)
        self.conn = c
        self.out = []
        self._take(self.h.node(cmd="open", tab=self.name))
        c.inbox.insert(0, hello)
        for old in list(self.h.room.clients):          # superseded connections: the real server closes them
            if isinstance(old, FakeConn) and old is not c and old.closed_with:
                self.h.room.clients.discard(old)

    def disconnect(self, *, server_notices=True, deliver_prefix=0):
        """The socket drops: some frames the tab sent may still reach the room first; what the room
        sent and the tab did not read is lost. ``server_notices``: the room sees the close now (else
        the connection lingers until the tab is back)."""
        assert self.conn is not None
        for _ in range(min(deliver_prefix, len(self.out))):
            self.to_server()
        c = self.conn
        self.conn = None
        self.out = []
        self._take(self.h.node(cmd="disconnect", tab=self.name))
        if server_notices:
            self.h.room.clients.discard(c)
        else:
            self.h.lingering.append(c)
        c.inbox.clear()

    def to_server(self):
        f = self.out.pop(0)
        self.h.room.on_sheet_frame(self.conn, encode_sheet(f))

    def to_client(self):
        kind, payload = decode(self.conn.inbox.pop(0))
        if kind == "meta":
            if payload.get("type") == "hello":
                self.client_id = payload.get("client") or self.client_id
            self._take(self.h.node(cmd="meta", tab=self.name, frame=payload))
        elif kind == "sheet":
            self._take(self.h.node(cmd="sheet", tab=self.name, frame=payload))

    def act(self, kinds=None):
        return self._take(self.h.node(cmd="act", tab=self.name, kinds=kinds))

    def submit(self, op, **kw):
        return self._take(self.h.node(cmd="submit", tab=self.name, op=op, **kw))

    def undo(self):
        return self._take(self.h.node(cmd="undo", tab=self.name))

    def flush(self):
        return self._take(self.h.node(cmd="flush", tab=self.name))

    def state(self):
        return self._take(self.h.node(cmd="state", tab=self.name))

    def fates(self):
        return self._take(self.h.node(cmd="fates", tab=self.name))

    def undos(self):
        return self._take(self.h.node(cmd="undos", tab=self.name))

    def applied_numbers(self):
        """{queued id: {n, client, epoch}} of this tab's changes the room answered as applied."""
        return self._take(self.h.node(cmd="appliedN", tab=self.name))

    def texts(self):
        return self._take(self.h.node(cmd="texts", tab=self.name))

    def use_mine(self, aid):
        return self._take(self.h.node(cmd="useMine", tab=self.name, id=aid))

    def pump(self):
        """Deliver everything both ways for this tab until nothing moves."""
        moved = True
        while moved and self.conn is not None:
            moved = False
            while self.out:
                self.to_server()
                moved = True
            while self.conn is not None and self.conn.inbox:
                self.to_client()
                moved = True


class Harness:
    def __init__(self, world, room, node: Node, clock=None):
        self.world, self.room, self.node, self.clock = world, room, node, clock
        self.wid = world["wid"]
        self.tabs: list[Tab] = []
        self.lingering: list[FakeConn] = []
        if clock is not None:
            node(cmd="clock", t=int(clock[0] * 1000))

    def tab(self, name, who, **kw):
        t = Tab(self, name, self.world[who], **kw)
        self.tabs.append(t)
        return t

    def advance(self, s: float):
        """Time passes (the rate limiter's and the tabs' clocks)."""
        if self.clock is not None:
            self.clock[0] += s
            self.node(cmd="clock", t=int(self.clock[0] * 1000))

    def quiesce(self, rounds=400):
        """Everyone back online, every frame delivered, every wait the room asked for waited."""
        for t in self.tabs:
            if not t.online:
                t.connect()
        st = []
        for _ in range(rounds):
            moved = False
            for t in self.tabs:
                if t.out or (t.conn and t.conn.inbox):
                    t.pump()
                    moved = True
            if moved:
                continue
            st = [t.state() for t in self.tabs]
            if not any(s["pending"] for s in st):
                return st
            self.advance(1.0)
            for t in self.tabs:
                t.flush()
            if not any(t.out for t in self.tabs):
                return st
        return [t.state() for t in self.tabs]


def write_draft(room) -> int:
    """The room writes its draft as it does on its own (``SheetRoom.persist``: serialise, write to the
    database, tell every tab `persisted`); the seq written."""
    seq = room.state["seq"]
    asyncio.run(room.persist())
    return seq


def restart_from_storage(room) -> int:
    """The process died without its closing write; the next one opens the room from storage exactly as
    ``CollabHub._load`` does (the draft row, the head version) — a new load of the same epoch. Every tab
    must have dropped before. Returns the seq it was loaded at."""
    from synwevo_core.context import as_tenant
    hub, tid = room.hub, room.tid
    with as_tenant(room.tenant):
        t = hub.app.get_template(tid)
        row = hub.store.one("SELECT * FROM collab_docs WHERE room=?", (room.key,))
        room.load_sheet(t, row, lambda: hub.app.editor_of(tid, hub.app.get_version(tid, t["head"]), t.get("kind")))
    assert room.catch_up is None, "the fixture writes the draft after every version (no catch-up to fold in)"
    return room.state["seq"]


def diff(a, b, path="", out=None, limit=8):
    """Where a tab's content differs from the room's (the first few places)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}/{k}: missing in tab (room {json.dumps(b[k], ensure_ascii=False)[:80]})")
            elif k not in b:
                out.append(f"{path}/{k}: only in tab ({json.dumps(a[k], ensure_ascii=False)[:80]})")
            else:
                diff(a[k], b[k], f"{path}/{k}", out, limit)
    elif a != b:
        out.append(f"{path}: tab {json.dumps(a, ensure_ascii=False)[:120]} != room {json.dumps(b, ensure_ascii=False)[:120]}")
    return out
