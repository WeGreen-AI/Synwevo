"""The business applications' events, for a client a person authorised: a feed to pull.

``GET /api/suite/v1/events`` answers "what changed since I last asked" for one credential: the
events of its workspace, of the applications whose ``<app>.read`` it holds and its person can
still read there, in the order they were written.

An item says where to look, never what is there: the event's type, the object it is about and the
identifiers needed to open that object (`EventType.refs`). The event's data — notes, assignees,
results — is not served; the client reads the record through the operation that authorizes it.

An event is a fact about a record, so a person who may not read the record is not told the event
happened. Each application decides that for its own events (`EventFeed.visible`), with the
credential in effect. An application that declared no feed, and any event type it left out, is not
served at all: the Agent's, spending, consent.

The cursor is a position its holder can neither read nor make. Positions in the log count every
workspace's events — a readable one would tell a client how busy everyone else is, and whether
something it may not see just happened — so it is sealed (`synwevo_core.sealed_token`) and bound
to the workspace, the client and the person. It carries no authority:
every read is filtered again by what the credential holds and the person may read *now*, so a
grant that was narrowed sees less from the same cursor, and one that was widened sees the new
application from where the cursor stands, not from the beginning.

A read without a cursor starts `RETENTION_S` back. A cursor is good until what follows it is older
than that by `CURSOR_GRACE_S` — the margin lets a client that started at the far edge keep paging —
and one that has waited longer is refused (410 ``cursor_expired``) rather than continued across a
gap: the client lists what it tracks again and starts a new cursor. So is a cursor that no longer
agrees with the log (a database restored from an older backup), whether the log is still behind
it or has grown past it again.

Every request is bounded in what it reads, not only in what it returns: `SCAN_ROWS` rows of the
workspace's log whoever they are for, and `CHECKED_ROWS` handed to an application's per-record
decision, which reads that application's records. A request that stops for either says
``has_more`` and the next one continues.
"""
from __future__ import annotations

import json
import math
import re
import struct

from synwevo_core import sealed_token
from synwevo_core.capabilities import EventFeed, machine_credential
from synwevo_core.errors import ServiceError
from synwevo_core.store import now

#: How far back a read without a cursor starts.
RETENTION_S = 30 * 24 * 3600
#: How much older than that the events after a cursor may be before the cursor is refused. Without
#: it a cursor issued for a page at the far edge of the window would expire before its next use.
CURSOR_GRACE_S = 24 * 3600
#: A cursor's time may trail the row it stands on by this much (a clock step) and still agree with it.
CLOCK_SLACK_S = 60
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
#: Rows of the workspace's log read for one request, served or not, of any application: a page of
#: events the caller may not see costs the same as one it may, and is bounded the same way.
SCAN_ROWS = 1000
BATCH_ROWS = 200
#: Rows handed to an application's per-record decision in one request (it reads their records).
CHECKED_ROWS = 300
MAX_TYPES = 50
MAX_CURSOR = 256
STARTS = ("oldest", "now")

_KEY_LABEL = b"synwevo/suite-event-cursor/v1"
_VERSION = "s1."
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,100}")


def _clock() -> float:
    return now()


# --------------------------------------------------------------------------------- the cursor
def _holder(p: dict) -> bytes:
    """Whose cursor it is. Part of the seal, so another client's, another person's or another
    workspace's cursor does not open at all."""
    return json.dumps([p["tenant"], p["client_id"], p["user_id"]], separators=(",", ":")).encode("utf-8")


def seal_cursor(store, p: dict, position: int, floor: float) -> str:
    """`position`: the last place read. `floor`: no event after it is older than this — the time
    of the last event read, or of the request when it reached the end of the log. Fixed width: the
    length of a cursor says nothing about where it stands."""
    return _VERSION + sealed_token.seal(store, _KEY_LABEL, struct.pack(">Qd", int(position), float(floor)), _holder(p))


def open_cursor(store, p: dict, raw: str) -> tuple[int, float]:
    bad = ServiceError(400, "invalid_cursor", "游标无效；请不带 cursor 重新开始读取", {"field": "cursor"})
    if not isinstance(raw, str) or len(raw) > MAX_CURSOR or not raw.startswith(_VERSION):
        raise bad
    opened = sealed_token.unseal(store, _KEY_LABEL, raw[len(_VERSION):], _holder(p), max_length=MAX_CURSOR)
    if opened is None or len(opened) != struct.calcsize(">Qd"):
        raise bad
    position, floor = struct.unpack(">Qd", opened)
    if not math.isfinite(floor):
        raise bad
    return position, floor


def _expired() -> ServiceError:
    return ServiceError(410, "cursor_expired",
                        "该游标之后的事件已超出事件流的保留期（或事件日志已恢复到更早的状态），不能再从这里继续；"
                        "请重新读取你关注的记录，然后不带 cursor 重新开始",
                        {"field": "cursor", "retention_seconds": RETENTION_S, "grace_seconds": CURSOR_GRACE_S})


# --------------------------------------------------------------------------------- what is served
def feeds(registry) -> dict[str, EventFeed]:
    """The applications that serve events to credentials, with what each declared."""
    out = {}
    for m in registry.apps():
        feed = registry.events(m.id)
        if m.machine and feed is not None:
            out[m.id] = feed
    return out


def event_types(registry) -> list[dict]:
    """Every event type a credential can be served, for the contract."""
    return sorted(({"type": t.type, "app": app, "object_kind": t.object_kind, "refs": list(t.refs),
                    "scope": f"{app}.read"} for app, feed in feeds(registry).items() for t in feed.types),
                  key=lambda e: e["type"])


def _before(store, cutoff: float, top: int) -> int:
    """The last position older than `cutoff`. Events are written in time order, so this is a search
    over the primary key rather than a pass over the log."""
    lo, hi = 0, top
    while lo < hi:
        mid = (lo + hi + 1) // 2
        row = store.one("SELECT created_at FROM app_events WHERE seq<=? ORDER BY seq DESC LIMIT 1", (mid,))
        if row is None or row["created_at"] < cutoff:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _shown(caps, p: dict, usable: dict[str, EventFeed], names: set[str], rows: list[dict]) -> tuple[set[str], int]:
    """The events of this batch the person may know about now, and how many of its rows an
    application had to decide one by one."""
    shown: set[str] = set()
    by_app: dict[str, list[dict]] = {}
    for r in rows:
        feed = usable.get(r["app"])
        declared = feed.type(r["type"]) if feed and r["type"] in names else None
        # Fail closed on anything that is not exactly what the application declared.
        if (declared is None or r["object_kind"] != declared.object_kind
                or not isinstance(r["object_id"], str) or not _ID.fullmatch(r["object_id"])):
            continue
        by_app.setdefault(r["app"], []).append(r)
    checked = 0
    for app, batch in by_app.items():
        visible = usable[app].visible
        if visible is None:
            shown.update(r["id"] for r in batch)
            continue
        checked += len(batch)
        # What it reads elsewhere is the credential's. An error that is not a refusal is not caught:
        # the request fails and the cursor stays where it was, rather than passing over events.
        with machine_credential(p["scopes"], app):
            shown.update(visible(p["user_id"], p["tenant"], batch) & {r["id"] for r in batch})
    return shown, checked


def _own(store, p: dict, run_ids) -> set[str]:
    """Which of these runs this client started for this person here."""
    values = sorted({v for v in run_ids if isinstance(v, str) and v})
    out: set[str] = set()
    for i in range(0, len(values), 200):
        chunk = values[i:i + 200]
        rows = store.all(
            f"SELECT id FROM op_runs WHERE id IN ({','.join('?' * len(chunk))}) "
            "AND user_id=? AND workspace_id=? AND via='api' AND CASE WHEN json_valid(delegation) THEN "
            "json_extract(delegation,'$.kind') || ':' || json_extract(delegation,'$.client_id') END = ?",
            (*chunk, p["user_id"], p["tenant"], "oauth_client:" + p["client_id"]))
        out.update(r["id"] for r in rows)
    return out


def _data(row: dict) -> dict:
    data = row.get("data")
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def _item(row: dict, feed: EventFeed, runs: set[str]) -> dict:
    data, refs, own = _data(row), {}, row["run_id"] in runs
    for name in feed.type(row["type"]).refs:
        value = data.get(name)
        if isinstance(value, str) and _ID.fullmatch(value):
            refs[name] = value
    return {"id": row["id"], "type": row["type"], "version": row["version"], "app": row["app"],
            "object": {"kind": row["object_kind"], "id": row["object_id"]}, "refs": refs,
            "occurred_at": row["created_at"],
            # The run that caused the event, and the correlation id it carried, are the caller's to
            # see only when that run is the caller's own. Another caller's correlation id is free
            # text of its choosing — and matching it against ids this client has used would let a
            # client claim another integration's by using the same text once.
            "run_id": row["run_id"] if own else None,
            "correlation_id": row["correlation_id"] if own else None}


def _usable(caps, p: dict, held: dict[str, EventFeed]) -> dict[str, EventFeed]:
    """Of the applications whose read scope the caller holds, the ones its person can still read in
    the workspace. One they lost is simply absent."""
    usable = {}
    for app, declared in sorted(held.items()):
        try:
            caps.check_workspace(p["user_id"], app, p["tenant"], "read")
        except ServiceError:
            continue
        usable[app] = declared
    return usable


def visible_items(caps, p: dict, rows: list[dict], *, match=None) -> list[tuple[dict, dict]]:
    """Of these rows of the event log, the ones this caller may be told about now, each with the
    item the feed would serve for it — the feed's whole decision, for whoever pushes the same events
    instead of waiting to be asked (signed webhooks). `p` needs what a credential says of itself:
    ``tenant``, ``client_id``, ``user_id`` and the ``scopes`` it holds now; `match` narrows by type."""
    served = feeds(caps.registry)
    held = {app: f for app, f in served.items() if f"{app}.read" in (p.get("scopes") or ())}
    usable = _usable(caps, p, held)
    names = {t.type for f in usable.values() for t in f.types if match is None or match(t.type)}
    rows = [r for r in rows if r.get("workspace_id") == p["tenant"]]
    for r in rows:
        r["data"] = _data(r)
    shown, _ = _shown(caps, p, usable, names, rows)
    picked = [r for r in rows if r["id"] in shown]
    runs = _own(caps.store, p, (r["run_id"] for r in picked))
    return [(r, _item(r, usable[r["app"]], runs)) for r in picked]


# --------------------------------------------------------------------------------- the feed
def feed(caps, p: dict, *, cursor: str | None = None, types: str | None = None, limit: int | None = None,
         start: str | None = None) -> dict:
    store, registry = caps.store, caps.registry
    lim = DEFAULT_LIMIT if limit is None else limit
    if type(lim) is not int or not 1 <= lim <= MAX_LIMIT:
        raise ServiceError(400, "invalid_limit", f"limit 须在 1–{MAX_LIMIT} 之间",
                           {"field": "limit", "minimum": 1, "maximum": MAX_LIMIT})
    if start is not None and (start not in STARTS or cursor):
        raise ServiceError(400, "invalid_start", "start 只能是 oldest 或 now，且不能与 cursor 同时使用",
                           {"field": "start", "allowed": list(STARTS)})
    served = feeds(registry)
    held = {app: f for app, f in served.items() if f"{app}.read" in (p.get("scopes") or ())}
    if not held:
        raise ServiceError(403, "insufficient_scope", "读取事件需要至少一个应用的 read 权限",
                           {"any_of": sorted(f"{app}.read" for app in served)})
    wanted = None
    if types is not None:
        wanted = {x.strip() for x in types.split(",") if x.strip()}
        known = {t.type: app for app, f in served.items() for t in f.types}
        unknown = sorted(wanted - known.keys())
        if not wanted or len(wanted) > MAX_TYPES or unknown:
            raise ServiceError(400, "invalid_events", f"types 须为 1–{MAX_TYPES} 个以逗号分隔的事件类型，且不能有未知的类型",
                               {"field": "types", "unknown": unknown[:MAX_TYPES], "maximum": MAX_TYPES})
        for name in sorted(wanted):
            if known[name] not in held:
                raise ServiceError(403, "insufficient_scope", f"该凭据没有 {known[name]}.read 权限",
                                   {"required_scope": f"{known[name]}.read"})

    # The end of the log first, the time second: everything up to `top` was written before `started`.
    top = store.one("SELECT COALESCE(MAX(seq), 0) AS s FROM app_events")["s"]
    started = _clock()
    if cursor:
        position, floor = open_cursor(store, p, cursor)
        # Behind the window: something after it may be gone. Not agreeing with the log: the database
        # was restored from an older backup, and positions this cursor has passed are being used
        # again — the log is still behind the cursor, or it has grown past it, in which case the row
        # the cursor stands on was written after the cursor was.
        under = store.one("SELECT created_at FROM app_events WHERE seq<=? ORDER BY seq DESC LIMIT 1", (position,))
        if (position > top or floor < started - RETENTION_S - CURSOR_GRACE_S
                or (under is not None and under["created_at"] > floor + CLOCK_SLACK_S)):
            raise _expired()
    elif start == "now":
        return {"workspace_id": p["tenant"], "items": [], "next_cursor": seal_cursor(store, p, top, started),
                "has_more": False}
    else:
        position = _before(store, started - RETENTION_S, top)

    usable = _usable(caps, p, held)
    names = {t.type for f in usable.values() for t in f.types if wanted is None or t.type in wanted}

    picked, floor, drained, scanned, checked = [], started, not names, 0, 0
    if names:
        # Every row of the workspace's log in order, by its own index, whatever it is about: what is
        # not this caller's is dropped here rather than in the query, so that it counts as read.
        sql = ("SELECT seq, id, type, version, app, object_kind, object_id, run_id, correlation_id, data, created_at "
               "FROM app_events INDEXED BY app_events_workspace WHERE workspace_id=? AND seq>? AND seq<=? "
               "ORDER BY seq LIMIT ?")
        while True:
            batch = min(SCAN_ROWS - scanned, BATCH_ROWS)
            rows = store.all(sql, (p["tenant"], position, top, batch))
            for r in rows:
                r["data"] = _data(r)
            scanned += len(rows)
            shown, asked = _shown(caps, p, usable, names, rows)
            checked += asked
            full = False
            for r in rows:
                if r["id"] in shown:
                    if len(picked) == lim:
                        full = True                  # stop in front of the first one that does not fit
                        break
                    picked.append(r)
                position, floor = r["seq"], r["created_at"]
            if full:
                break
            if len(rows) < batch:
                drained = True
                break
            if scanned >= SCAN_ROWS or checked >= CHECKED_ROWS:
                break
    if drained:
        # Everything up to the end of the log, as it stood when this request began, has been read.
        position, floor = top, started
    runs = _own(store, p, (r["run_id"] for r in picked))
    return {"workspace_id": p["tenant"],
            "items": [_item(r, usable[r["app"]], runs) for r in picked],
            "next_cursor": seal_cursor(store, p, position, floor), "has_more": not drained}
