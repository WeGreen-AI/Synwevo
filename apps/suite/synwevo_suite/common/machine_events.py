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

The feed serves `RETENTION_S` of history. A cursor is good for as long as nothing after it can have
left that window; one that has waited longer is refused (410 ``cursor_expired``) rather than
continued across a gap — the client lists what it tracks again and starts a new cursor.
"""
from __future__ import annotations

import json
import re

from synwevo_core import sealed_token
from synwevo_core.capabilities import EventFeed, machine_credential
from synwevo_core.errors import ServiceError
from synwevo_core.store import now

#: How far back the feed goes, and so how long a client may stay away and still continue.
RETENTION_S = 30 * 24 * 3600
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
#: Rows read for one request, shown or not: a page of events the caller may not see costs the same
#: as one it may, and is bounded the same way. The next request continues where this one stopped.
SCAN_ROWS = 1000
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
    of the last event read, or of the request when it reached the end of the log."""
    body = json.dumps({"q": int(position), "t": float(floor)}, separators=(",", ":")).encode("ascii")
    return _VERSION + sealed_token.seal(store, _KEY_LABEL, body, _holder(p))


def open_cursor(store, p: dict, raw: str) -> tuple[int, float]:
    bad = ServiceError(400, "invalid_cursor", "游标无效；请不带 cursor 重新开始读取", {"field": "cursor"})
    if not isinstance(raw, str) or len(raw) > MAX_CURSOR or not raw.startswith(_VERSION):
        raise bad
    opened = sealed_token.unseal(store, _KEY_LABEL, raw[len(_VERSION):], _holder(p), max_length=MAX_CURSOR)
    if opened is None:
        raise bad
    try:
        body = json.loads(opened)
    except ValueError:
        raise bad from None
    position, floor = (body.get("q"), body.get("t")) if isinstance(body, dict) else (None, None)
    if type(position) is not int or position < 0 or type(floor) not in (int, float):
        raise bad
    return position, float(floor)


def _expired() -> ServiceError:
    return ServiceError(410, "cursor_expired",
                        "该游标之后的事件已超出事件流的保留期（或事件日志已恢复到更早的状态），不能再从这里继续；"
                        "请重新读取你关注的记录，然后不带 cursor 重新开始",
                        {"field": "cursor", "retention_seconds": RETENTION_S})


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


def _shown(caps, p: dict, usable: dict[str, EventFeed], rows: list[dict]) -> set[str]:
    """The events of this batch the person may know about now."""
    shown: set[str] = set()
    by_app: dict[str, list[dict]] = {}
    for r in rows:
        feed = usable.get(r["app"])
        declared = feed.type(r["type"]) if feed else None
        # Fail closed on anything that is not exactly what the application declared.
        if declared is None or r["object_kind"] != declared.object_kind or not r["object_id"]:
            continue
        by_app.setdefault(r["app"], []).append(r)
    for app, batch in by_app.items():
        visible = usable[app].visible
        if visible is None:
            shown.update(r["id"] for r in batch)
            continue
        with machine_credential(p["scopes"], app):       # what it reads elsewhere is the credential's
            shown.update(visible(p["user_id"], p["tenant"], batch) & {r["id"] for r in batch})
    return shown


def _own(store, p: dict, column: str, values) -> set[str]:
    """Which of these run ids (or correlation ids) belong to runs this client started for this
    person here. `column` is a literal of this module."""
    values = sorted({v for v in values if isinstance(v, str) and v})
    out: set[str] = set()
    for i in range(0, len(values), 200):
        chunk = values[i:i + 200]
        rows = store.all(
            f"SELECT DISTINCT {column} AS v FROM op_runs WHERE {column} IN ({','.join('?' * len(chunk))}) "
            "AND user_id=? AND workspace_id=? AND via='api' AND CASE WHEN json_valid(delegation) THEN "
            "json_extract(delegation,'$.kind') || ':' || json_extract(delegation,'$.client_id') END = ?",
            (*chunk, p["user_id"], p["tenant"], "oauth_client:" + p["client_id"]))
        out.update(r["v"] for r in rows)
    return out


def _data(row: dict) -> dict:
    data = row.get("data")
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def _item(row: dict, feed: EventFeed, runs: set[str], correlations: set[str]) -> dict:
    data, refs = _data(row), {}
    for name in feed.type(row["type"]).refs:
        value = data.get(name)
        if isinstance(value, str) and _ID.fullmatch(value):
            refs[name] = value
    return {"id": row["id"], "type": row["type"], "version": row["version"], "app": row["app"],
            "object": {"kind": row["object_kind"], "id": row["object_id"]}, "refs": refs,
            "occurred_at": row["created_at"],
            # A run or a correlation id is the caller's to see only when it is the caller's own:
            # another caller's correlation id is free text that caller chose.
            "run_id": row["run_id"] if row["run_id"] in runs else None,
            "correlation_id": row["correlation_id"] if row["correlation_id"] in correlations else None}


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

    started = _clock()
    top = store.one("SELECT COALESCE(MAX(seq), 0) AS s FROM app_events")["s"]
    if cursor:
        position, floor = open_cursor(store, p, cursor)
        # Ahead of the log: the database was restored from an older backup, and new events will
        # reuse positions this cursor has passed. Behind the window: something after it may be gone.
        if position > top or floor < started - RETENTION_S:
            raise _expired()
    elif start == "now":
        return {"workspace_id": p["tenant"], "items": [], "next_cursor": seal_cursor(store, p, top, started),
                "has_more": False}
    else:
        position = _before(store, started - RETENTION_S, top)

    # The applications this person can still read here. One they lost is simply absent.
    usable = {}
    for app, declared in sorted(held.items()):
        try:
            caps.check_workspace(p["user_id"], app, p["tenant"], "read")
        except ServiceError:
            continue
        usable[app] = declared
    names = sorted(t.type for f in usable.values() for t in f.types if wanted is None or t.type in wanted)

    picked, floor, drained, scanned = [], started, not names, 0
    if names:
        sql = ("SELECT seq, id, type, version, app, object_kind, object_id, run_id, correlation_id, data, created_at "
               f"FROM app_events WHERE workspace_id=? AND seq>? AND seq<=? AND type IN ({','.join('?' * len(names))}) "
               "ORDER BY seq LIMIT ?")
        while True:
            batch = min(SCAN_ROWS - scanned, max(2 * (lim - len(picked)), 50))
            rows = store.all(sql, (p["tenant"], position, top, *names, batch))
            for r in rows:
                r["data"] = _data(r)
            scanned += len(rows)
            shown = _shown(caps, p, usable, rows)
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
            if scanned >= SCAN_ROWS:
                break
    if drained:
        # Everything up to the end of the log, as it stood when this request began, has been read.
        position, floor = top, started
    runs = _own(store, p, "id", (r["run_id"] for r in picked))
    correlations = _own(store, p, "correlation_id", (r["correlation_id"] for r in picked))
    return {"workspace_id": p["tenant"],
            "items": [_item(r, usable[r["app"]], runs, correlations) for r in picked],
            "next_cursor": seal_cursor(store, p, position, floor), "has_more": not drained}
