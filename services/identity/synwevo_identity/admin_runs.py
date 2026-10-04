"""Platform view of what the business applications ran: operation runs and flow runs.

Platform administration could not see whether the applications on the workplace origin were
working at all: a failed operation, a run left `unknown` by a restart, or a flow that stopped
moving was visible only to the person who started it. These routes are the read side of that
governance — which operation ran, for whom, through which entry, how it ended and when.

They return **metadata, never content**, by construction rather than by filtering:

* every query names its columns. `op_runs.input`, `output`, `error_message` and `error_detail`,
  `flow_runs.params_json` and `event_json`, and `flow_steps.value_json` / `receipt_json` /
  `reader_json` are never selected, so no later change to a presenter can leak them. The two
  stored descriptors (`principal`, `delegation`) are read, reduced to four fixed fields by
  `_caller_of`, and dropped. An error *message* may quote a field label a person typed, so it
  stays with the person who may read the run; the error *code* is returned (the one code that
  is not a fixed identifier is replaced, below).
* names a person chose are not shown: a flow's name and its step ids are authored text (the same
  rule as project names on the workspace list — admin_routes `_workspace_row`). Steps are shown
  by position and by the operation they called.
* text a caller chose is not shown either. A correlation id is returned as stored only when the
  platform made it (a run's or a flow run's own id). A caller may name any hundred characters
  instead — the workplace origin's operation route and the machine API both take one, and a flow
  run inherits it — and that is returned as a digest (`correlation_token`). The runs that share
  it are still grouped, on the server, by the stored value; a search by the digest, or by the
  stored text for someone who was given it, still finds them.
* an error code is the engine's or an operation's own identifier, with one exception: the code a
  flow's author writes into a `fail` step, which has the grammar of the step ids above. The flow
  routes read which steps of that version are `fail` steps and the code each ends with
  (`_fail_steps`), keep nothing else of the definition, and answer `AUTHORED_FAILURE` in its
  place on the run, on the step and in the history.
* the account's address is added only for a caller who may list accounts
  (`platform.users.view`), and the workspace's name only for one who may list workspaces
  (`platform.workspaces.view`); everyone else gets the identifiers.

Reading a run here gives no way to open the object it touched: the application's own routes
decide that, for the caller's own membership, on every request.

The run states and entry names are the capability layer's (`RUN_STATES` and `VIA` in the shared
package's capabilities module; `ACTIVE`, `PARKED` and `TERMINAL` — together `STATES` — in its flow
engine). They are repeated here rather than imported, and the modules are not named by their
import path: the account service does not load the capability registry, and the refusal coverage
test of the account shell follows every module path this file mentions.
services/identity/tests/test_admin_runs.py holds the copies to the originals.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from fastapi import APIRouter, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.platform_access import require_platform
from synwevo_core.store import Store

PERMISSION = "platform.apps.view"

RUN_STATES = ("queued", "running", "waiting_input", "waiting_approval", "succeeded", "partial",
              "failed", "cancelled", "unknown")
RUN_ACTIVE = ("queued", "running")
VIA = ("ui", "api", "flow", "agent", "automation", "system")
FLOW_ACTIVE = ("queued", "running", "waiting_input", "waiting_approval", "retrying")
#: Where the engine leaves a run it will not pick up again by itself: paused by its owner,
#: blocked by a refusal, or `unknown` after a call whose outcome was never confirmed. Not
#: finished and not moving, until the owner resumes or cancels it.
FLOW_PARKED = ("paused", "blocked", "unknown")
FLOW_TERMINAL = ("succeeded", "partial", "failed", "cancelled")
FLOW_STATES = FLOW_ACTIVE + FLOW_PARKED + FLOW_TERMINAL
#: What `status=attention` lists besides an unfinished run past its deadline: ended badly, or
#: parked by something other than its owner's own pause.
FLOW_ATTENTION = ("failed", "partial", "blocked", "unknown")

#: A `queued` or `running` operation that has not been touched for this long is reported as
#: stalled. The capability layer's own sweep turns a `running` one into `unknown` after the same
#: interval (`Capabilities.reconcile_stale`), so a stalled row here means that sweep has not run.
STALE_AFTER_S = 600
#: The summary counts runs updated inside a window; a day by default, a month at most, so the
#: counting stays inside the (status, updated_at) index however large the history grows.
WINDOWS_S = (3600, 86400, 7 * 86400, 30 * 86400)
DEFAULT_WINDOW_S = 86400
RELATED_LIMIT = 50
HISTORY_LIMIT = 200
MAX_PAGE = 200
#: A list counts its matches up to here and then says "more than": counting every match read
#: the whole table, or a whole index, on each request.
COUNT_CAP = 10000
INT64 = 2 ** 63 - 1

#: The correlation ids the platform makes itself: a run's own id, or the id of the flow run it
#: belongs to (`new_id("run")` in the capability layer, `new_id("flowrun")` in the flow engine).
_GENERATED = re.compile(r"(?:run|flowrun)_[0-9a-f]{12}")
_GENERATED_GLOB = tuple(prefix + "[0-9a-f]" * 12 for prefix in ("run_", "flowrun_"))
_TOKEN = re.compile(r"ext-[0-9a-f]{24}")
_TOKEN_BATCH = 2000
#: Answered in place of the code a flow's author gave a `fail` step.
AUTHORED_FAILURE = "flow_failed_by_definition"

_RUN_COLUMNS = ("r.id, r.op, r.app, r.op_version, r.workspace_id, r.user_id, r.principal, r.delegation, r.via, "
                "r.correlation_id, r.parent_run_id, r.depth, r.status, r.error_code, r.object_kind, "
                "r.object_id, r.created_at, r.started_at, r.finished_at, r.updated_at")
_FLOW_COLUMNS = ("f.id, f.flow_id, f.version, f.user_id, f.status, f.step_id, f.error_code, f.correlation_id, "
                 "f.parent_run_id, f.depth, f.trigger_id, f.calls_used, f.deadline, f.due_at, "
                 "f.lease_until, f.created_at, f.updated_at, f.finished_at")


def _descriptor(raw) -> dict:
    try:
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _caller_of(principal, delegation) -> dict:
    """Who the run was made as and on whose behalf: the kind of caller, a client's id, the kind
    of delegation, and — for a flow step — the flow run it belongs to. Nothing else of the two
    stored descriptors leaves this module (a delegation also names a conversation or a step,
    which are the person's own)."""
    who, behalf = _descriptor(principal), _descriptor(delegation)
    kind = _text(behalf.get("kind"))
    return {"principal_kind": _text(who.get("kind")) or "unknown",
            "client_id": _text(who.get("client_id")) or _text(behalf.get("client_id")),
            "delegation_kind": kind,
            "flow_run_id": _text(behalf.get("run_id")) if kind == "flow" else None}


def correlation_token(stored: str) -> str:
    """What the routes return for a stored correlation id: the id itself when the platform made
    it, otherwise a digest of it — the same digest for the same text, so the runs of one chain
    still read as one, and nothing of what the caller wrote."""
    if _GENERATED.fullmatch(stored):
        return stored
    return "ext-" + hashlib.sha256(b"synwevo/admin-runs/correlation/v1\0" + stored.encode("utf-8")).hexdigest()[:24]


def _fail_steps(spec) -> dict[str, str]:
    """`{step id: code}` for the `fail` steps of one stored flow version. This is all that is
    kept of a definition, and neither half is returned: it only decides which stored error codes
    are the author's."""
    steps = _descriptor(spec).get("steps")
    if not isinstance(steps, list):
        return {}
    return {s["id"]: s["code"] for s in steps if isinstance(s, dict) and s.get("kind") == "fail"
            and isinstance(s.get("id"), str) and isinstance(s.get("code"), str)}


def _shown_code(code, step_id, fails: dict[str, str]):
    """The stored error code, or `AUTHORED_FAILURE` when it is the code that step's author chose."""
    return AUTHORED_FAILURE if code and fails.get(step_id) == code else code


def build_router(store: Store, audit) -> APIRouter:
    r = APIRouter(prefix="/api/admin", tags=["admin"])

    def _caller(request: Request) -> frozenset[str]:
        return require_platform(request, PERMISSION, store=store, audit=audit)["platform_permissions"]

    def _people(held: frozenset[str], rows: list[dict]) -> None:
        """Add the address and the workspace name the caller's other permissions let them read."""
        users = {row["user_id"] for row in rows if row.get("user_id")}
        spaces = {row["workspace_id"] for row in rows if row.get("workspace_id")}
        emails, names = {}, {}
        if users and "platform.users.view" in held:
            marks = ",".join("?" * len(users))
            emails = {u["id"]: u["email"] for u in store.all(
                f"SELECT id, email FROM users WHERE id IN ({marks})", tuple(users))}
        if spaces and "platform.workspaces.view" in held:
            marks = ",".join("?" * len(spaces))
            names = {w["id"]: w["name"] for w in store.all(
                f"SELECT id, name FROM workspaces WHERE id IN ({marks})", tuple(spaces))}
        for row in rows:
            row["user_email"] = emails.get(row.get("user_id"))
            if "workspace_id" in row:
                row["workspace_name"] = names.get(row["workspace_id"])

    def _run(row: dict, ts: float) -> dict:
        who = _caller_of(row.pop("principal", None), row.pop("delegation", None))
        return {**row, **who, "correlation_id": correlation_token(row["correlation_id"]),
                "stalled": row["status"] in RUN_ACTIVE and row["updated_at"] < ts - STALE_AFTER_S}

    def _flow(row: dict, ts: float, fails: dict[str, str]) -> dict:
        active = row["status"] in FLOW_ACTIVE
        lease, step = row.pop("lease_until"), row.pop("step_id")
        return {**row, "error_code": _shown_code(row["error_code"], step, fails),
                "correlation_id": correlation_token(row["correlation_id"]), "leased": active and lease > ts,
                # past the run's own deadline and still not finished: the runner has not reached it
                "overdue": active and row["deadline"] < ts}

    def _fails(flow_id: str, version: int) -> dict[str, str]:
        row = store.one("SELECT spec_json FROM flow_versions WHERE flow_id=? AND version=?", (flow_id, version))
        return _fail_steps(row["spec_json"]) if row else {}

    def _stored_correlations(table: str, q: str) -> list[str]:
        """The stored correlation ids `q` stands for: `q` itself when a run carries it, and —
        when `q` has the form of the digest this module returns — every caller-chosen id with
        that digest.

        A digest cannot be found through an index. The second half reads the correlation index
        alone, in batches, keeps the ids the platform did not make and compares their digests:
        linear in that index, and only when someone searches by a digest."""
        found = [q] if store.one(f"SELECT 1 AS n FROM {table} WHERE correlation_id=? LIMIT 1", (q,)) else []
        after = ""
        while _TOKEN.fullmatch(q):
            batch = store.all(
                f"SELECT DISTINCT correlation_id AS c FROM {table} WHERE correlation_id>? AND correlation_id NOT GLOB ? "
                "AND correlation_id NOT GLOB ? ORDER BY correlation_id LIMIT ?", (after, *_GENERATED_GLOB, _TOKEN_BATCH))
            found += [row["c"] for row in batch if row["c"] != q and correlation_token(row["c"]) == q]
            if len(batch) < _TOKEN_BATCH:
                break
            after = batch[-1]["c"]
        return found

    def _named(table: str, alias: str, q: str) -> tuple[str, list] | None:
        """The runs `q` names exactly — the one with that id, and the ones with that correlation
        id — as a WHERE term that the primary key and the correlation index answer, or None when
        it names none."""
        own = store.one(f"SELECT correlation_id FROM {table} WHERE id=?", (q,))
        stored = _stored_correlations(table, q)
        terms, args = [], []
        if own and own["correlation_id"] not in stored:      # otherwise its chain already includes it
            terms.append(f"{alias}.id=?")
            args.append(q)
        if stored:
            terms.append(f"{alias}.correlation_id=?" if len(stored) == 1
                         else f"{alias}.correlation_id IN ({','.join('?' * len(stored))})")
            args += stored
        if not terms:
            return None
        return (terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"), args

    def _page(table: str, alias: str, columns: str, where: list[str], params: list,
              limit: int, offset: int, snapshot: int | None, *, by_created: bool = False,
              exact: bool = False) -> dict:
        """One bounded page, newest first, under a high-water rowid: rows inserted while someone
        pages do not shift the pages (admin_routes `_page`).

        A run is inserted when it is created, so rowid order is creation order and the newest
        page is read from the end of the table without sorting it. With one person or one
        workspace chosen (`by_created`) the order is creation time instead: that is the order of
        the (user, created_at) and (workspace, created_at) indexes, so the page is read from the
        end of that person's entries rather than sorted out of all of them. A run's creation
        time never changes, so the pages still hold still.

        `total` counts at most `COUNT_CAP` matches; past that `total_capped` says there are more
        and `more` says whether a next page exists. When there are that many and no index gives
        the order (`exact` is a term the correlation index or the primary key orders), the table
        is read backwards and filtered — the matches are dense — instead of every match being
        collected and sorted; with fewer, the sort the planner may choose is over at most
        `COUNT_CAP` rows.

        Plain reads, outside the writer lock: an administrator looking at the list never makes a
        business write wait. States still change under the reader — this is a moving view of
        live work, not a frozen copy, and the count is the count at the moment it was asked."""
        ceiling = store.one(f"SELECT COALESCE(MAX(rowid), 0) AS n FROM {table}")["n"]
        snapshot = ceiling if snapshot is None else min(snapshot, ceiling)
        terms = f"WHERE {alias}.rowid<=?" + "".join(f" AND {w}" for w in where)
        args = (snapshot, *params)
        counted = store.one(f"SELECT COUNT(*) AS n FROM (SELECT 1 FROM {table} {alias} {terms} LIMIT ?)",
                            (*args, COUNT_CAP + 1))["n"]
        capped = counted > COUNT_CAP
        order = f"{alias}.created_at DESC, {alias}.rowid DESC" if by_created else f"{alias}.rowid DESC"
        scan = " NOT INDEXED" if capped and not by_created and not exact else ""
        rows = store.all(f"SELECT {columns} FROM {table} {alias}{scan} {terms} ORDER BY {order} LIMIT ? OFFSET ?",
                         (*args, limit + 1, offset))
        return {"items": rows[:limit], "total": min(counted, COUNT_CAP), "total_capped": capped,
                "more": len(rows) > limit, "limit": limit, "offset": offset, "snapshot": snapshot}

    # ------------------------------------------------------------------ operation runs
    @r.get("/app-runs")
    def app_runs(request: Request, status: str = Query("", max_length=32), app: str = Query("", max_length=40),
                 via: str = Query("", max_length=16), workspace_id: str = Query("", max_length=80),
                 user_id: str = Query("", max_length=80), q: str = Query("", max_length=100),
                 limit: int = Query(50, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0, le=INT64),
                 snapshot: int | None = Query(None, ge=0, le=INT64)):
        """Operation runs, newest first. `status` is a run state, or `attention` for the ones
        that need a look: failed, unknown, or stalled. `q` is a run id or a correlation id (as
        stored, or the digest shown for one a caller chose); when it is neither, the start of an
        operation id."""
        held = _caller(request)
        ts = time.time()
        where, params = [], []
        if status == "attention":
            where.append("(r.status IN ('failed','unknown') OR (r.status IN ('queued','running') AND r.updated_at<?))")
            params.append(ts - STALE_AFTER_S)
        elif status:
            if status not in RUN_STATES:
                raise ServiceError(400, "bad_run_filter", "筛选条件无效，请重新选择", {"field": "status"})
            where.append("r.status=?")
            params.append(status)
        if via:
            if via not in VIA:
                raise ServiceError(400, "bad_run_filter", "筛选条件无效，请重新选择", {"field": "via"})
            where.append("r.via=?")
            params.append(via)
        for column, value in (("r.app", app), ("r.workspace_id", workspace_id), ("r.user_id", user_id)):
            if value:
                where.append(f"{column}=?")
                params.append(value)
        named = _named("op_runs", "r", q) if q else None
        if named:
            where.append(named[0])
            params += named[1]
        elif q:
            where.append("r.op LIKE ? ESCAPE '\\'")
            params.append(_prefix(q))
        page = _page("op_runs", "r", _RUN_COLUMNS, where, params, limit, offset, snapshot,
                     by_created=bool(workspace_id or user_id) and not named, exact=bool(named))
        page["items"] = [_run(row, ts) for row in page["items"]]
        _people(held, page["items"])
        return {**page, "stale_after_s": STALE_AFTER_S, "generated_at": ts}

    @r.get("/app-runs/summary")
    def app_runs_summary(request: Request, window_s: int = Query(DEFAULT_WINDOW_S, ge=1, le=INT64)):
        """Counts of runs updated inside the window, by state and by application, plus what is
        stalled now and which flow runs are parked now — a run blocked last month is still
        waiting for its owner, whatever the window. Every count is read through a
        (status, updated_at) index and none takes the writer lock; the numbers are each true at
        the moment they were read, a few milliseconds apart, which is what `generated_at` says."""
        _caller(request)
        if window_s not in WINDOWS_S:
            raise ServiceError(400, "bad_run_filter", "筛选条件无效，请重新选择", {"field": "window_s"})
        ts = time.time()
        since = ts - window_s
        by_status = {state: store.one("SELECT COUNT(*) AS n FROM op_runs WHERE status=? AND updated_at>=?",
                                      (state, since))["n"] for state in RUN_STATES}
        apps: dict[str, dict] = {}
        for state in RUN_STATES:
            for row in store.all("SELECT app, COUNT(*) AS n FROM op_runs WHERE status=? AND updated_at>=? "
                                 "GROUP BY app", (state, since)):
                entry = apps.setdefault(row["app"], {"app": row["app"], "total": 0, "failed": 0, "unknown": 0})
                entry["total"] += row["n"]
                if state in ("failed", "unknown"):
                    entry[state] += row["n"]
        stalled = sum(store.one("SELECT COUNT(*) AS n FROM op_runs WHERE status=? AND updated_at<?",
                                (state, ts - STALE_AFTER_S))["n"] for state in RUN_ACTIVE)
        flows = {state: store.one("SELECT COUNT(*) AS n FROM flow_runs WHERE status=? AND updated_at>=?",
                                  (state, since))["n"] for state in FLOW_TERMINAL}
        flows_active = store.one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(deadline<?), 0) AS overdue FROM flow_runs "
            f"WHERE status IN ({','.join('?' * len(FLOW_ACTIVE))})", (ts, *FLOW_ACTIVE))
        parked = {state: store.one("SELECT COUNT(*) AS n FROM flow_runs WHERE status=?", (state,))["n"]
                  for state in FLOW_PARKED}
        return {"window_s": window_s, "since": since, "generated_at": ts, "stale_after_s": STALE_AFTER_S,
                "runs": {"total": sum(by_status.values()), "by_status": by_status, "stalled": stalled,
                         "by_app": sorted(apps.values(), key=lambda a: (-a["total"], a["app"]))},
                "flows": {"finished": flows, "active": flows_active["n"], "overdue": flows_active["overdue"],
                          "parked": parked}}

    @r.get("/app-runs/{run_id}")
    def app_run(request: Request, run_id: str):
        """One run and the runs that share its correlation id — the chain a flow, an Agent turn
        or a client's request made — oldest first."""
        held = _caller(request)
        ts = time.time()
        row = store.one(f"SELECT {_RUN_COLUMNS} FROM op_runs r WHERE r.id=?", (run_id,))
        if not row:
            raise ServiceError(404, "run_not_found", "找不到这次运行")
        # grouped by the stored value (in insertion order, which the correlation index gives);
        # what is returned for it is `correlation_token`'s answer, the same on every row
        related = [_run(x, ts) for x in store.all(
            f"SELECT {_RUN_COLUMNS} FROM op_runs r WHERE r.correlation_id=? AND r.id<>? "
            "ORDER BY r.rowid LIMIT ?", (row["correlation_id"], run_id, RELATED_LIMIT + 1))]
        run = _run(row, ts)
        _people(held, [run, *related])
        return {"run": run, "related": related[:RELATED_LIMIT], "related_truncated": len(related) > RELATED_LIMIT,
                "stale_after_s": STALE_AFTER_S, "generated_at": ts}

    # ------------------------------------------------------------------ flow runs
    @r.get("/flow-runs")
    def flow_runs(request: Request, status: str = Query("", max_length=32), user_id: str = Query("", max_length=80),
                  flow_id: str = Query("", max_length=80), q: str = Query("", max_length=100),
                  limit: int = Query(50, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0, le=INT64),
                  snapshot: int | None = Query(None, ge=0, le=INT64)):
        """Flow runs, newest first. `status` is a flow state, `active` for the ones the runner
        will pick up again, or `attention`: failed, partly done, blocked, left with an unknown
        outcome, or past its deadline and still unfinished. `q` is a flow run id or a correlation
        id (as stored, or the digest shown for one a caller chose)."""
        held = _caller(request)
        ts = time.time()
        where, params = [], []
        active = ",".join("?" * len(FLOW_ACTIVE))
        if status == "attention":
            where.append(f"(f.status IN ({','.join('?' * len(FLOW_ATTENTION))}) "
                         f"OR (f.status IN ({active}) AND f.deadline<?))")
            params += [*FLOW_ATTENTION, *FLOW_ACTIVE, ts]
        elif status == "active":
            where.append(f"f.status IN ({active})")
            params += FLOW_ACTIVE
        elif status:
            if status not in FLOW_STATES:
                raise ServiceError(400, "bad_run_filter", "筛选条件无效，请重新选择", {"field": "status"})
            where.append("f.status=?")
            params.append(status)
        for column, value in (("f.user_id", user_id), ("f.flow_id", flow_id)):
            if value:
                where.append(f"{column}=?")
                params.append(value)
        if q:
            # names no run: the id term says so through the primary key, without reading the table
            term, args = _named("flow_runs", "f", q) or ("f.id=?", [q])
            where.append(term)
            params += args
        page = _page("flow_runs", "f", _FLOW_COLUMNS, where, params, limit, offset, snapshot,
                     by_created=bool(user_id) and not q, exact=bool(q))
        fails = {key: _fails(*key) for key in {(row["flow_id"], row["version"]) for row in page["items"]
                                               if row["error_code"]}}
        page["items"] = [_flow(row, ts, fails.get((row["flow_id"], row["version"]), {})) for row in page["items"]]
        _people(held, page["items"])
        return {**page, "generated_at": ts}

    @r.get("/flow-runs/{flow_run_id}")
    def flow_run(request: Request, flow_run_id: str):
        """One flow run with its steps in the order they started — each by position and by the
        operation it called, not by the name its author gave it — and the run's history."""
        held = _caller(request)
        ts = time.time()
        row = store.one(f"SELECT {_FLOW_COLUMNS} FROM flow_runs f WHERE f.id=?", (flow_run_id,))
        if not row:
            raise ServiceError(404, "run_not_found", "找不到这次运行")
        steps = store.all(
            "SELECT s.step_id, s.status, s.attempt, s.checks, s.error_code, s.op_run_id, s.started_at, s.updated_at, "
            "r.op, r.app, r.status AS op_status FROM flow_steps s LEFT JOIN op_runs r ON r.id=s.op_run_id "
            "WHERE s.run_id=? ORDER BY s.started_at, s.rowid", (flow_run_id,))
        history = store.all("SELECT step_id, seq, type, status, attempt, op_run_id, error_code, fence, created_at "
                            "FROM flow_history WHERE run_id=? ORDER BY seq DESC LIMIT ?", (flow_run_id, HISTORY_LIMIT + 1))
        # The step ids are read to tell an author's `fail` code from the engine's and the
        # operations' own, and dropped: neither they nor the author's code leave this function.
        coded = any(x["error_code"] for x in (row, *steps, *history))
        fails = _fails(row["flow_id"], row["version"]) if coded else {}
        for x in (*steps, *history):
            x["error_code"] = _shown_code(x["error_code"], x.pop("step_id"), fails)
        run = _flow(row, ts, fails)
        _people(held, [run])
        return {"run": run, "steps": [{"position": i + 1, **s} for i, s in enumerate(steps)],
                "history": history[:HISTORY_LIMIT], "history_truncated": len(history) > HISTORY_LIMIT,
                "generated_at": ts}

    return r


def _prefix(text: str) -> str:
    """`text` as the literal start of a LIKE pattern (the caller escapes with a backslash)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
