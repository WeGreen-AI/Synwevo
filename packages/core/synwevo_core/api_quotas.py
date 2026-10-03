"""Traffic and capacity policies for machine callers, decided in one place and shared by every process.

Three limits, kept apart because they answer different questions and need different fixes:

* ``credential_rpm`` — requests per minute from one credential. A rotated key shares its
  predecessor's budget (the bucket is keyed by the key's lineage), so rotation is not a way to a
  fresh bucket.
* ``workspace_rpm`` — requests per minute from every machine credential of one workspace
  together, so a second key or a second service account is not a way around the workspace's
  budget.
* ``active_jobs`` — generation jobs queued or running in one workspace at once, from every channel
  (the browser and the API alike). Docs checks it inside the transaction that inserts the job.

A request-rate refusal and a capacity refusal are different answers: the first says "wait this
many seconds" and nothing ran; the second says "too much is already in flight" and waiting only
helps once some of it finishes, so it carries no ``Retry-After``.

**Layers.** A value is looked up credential → workspace → platform → deployment default
(environment). Any layer may leave a value unset, which means *inherit*; the effective value says
which layer it came from. Only the fields that make sense at a layer can be set there: a
credential override sets ``credential_rpm`` only.

**Shared enforcement.** Request rate uses GCRA (the generic cell rate algorithm): one row per bucket
holding its *theoretical arrival time*, advanced inside ``BEGIN IMMEDIATE``. SQLite serialises
writers across processes, so N processes do not mean N× the limit, and a policy change is read by
the next request in every process — there is no cache to wait out. If the shared state cannot be
written, the gate falls back to a per-process bucket at the effective credential rate: degraded to
per-process limiting, never opened.

**Versions.** Each policy row carries a version; a write names the version it read and a stale one is
refused with the current values (409 ``stale_policy``). Clearing an override keeps the row (all
fields NULL) so the version keeps counting and an old editor cannot silently undo a newer change.
Every change appends to ``api_quota_history`` with before/after values, the actor and the reason.

Store alone executes ``SCHEMA_SQL``.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from . import env
from .errors import ServiceError
from .store import Store, new_id, now

log = logging.getLogger("synwevo.quotas")

WINDOW_S = 60
WINDOW_NS = WINDOW_S * 1_000_000_000
FIELDS = ("credential_rpm", "workspace_rpm", "active_jobs")
#: Which fields each layer may set.
LAYER_FIELDS = {"platform": FIELDS, "workspace": FIELDS, "credential": ("credential_rpm",)}
CREDENTIAL_KINDS = ("api_key", "service_account")
RANGES = {"credential_rpm": (1, 100_000), "workspace_rpm": (1, 1_000_000), "active_jobs": (1, 1_000)}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS api_quota_policies (
 scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL,
 credential_rpm INTEGER, workspace_rpm INTEGER, active_jobs INTEGER,
 version INTEGER NOT NULL, updated_at REAL NOT NULL, updated_by TEXT NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY (scope_kind, scope_id),
 CHECK (scope_kind IN ('platform','workspace','credential'))
);
CREATE TABLE IF NOT EXISTS api_quota_history (
 id TEXT PRIMARY KEY, scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL, version INTEGER NOT NULL,
 before TEXT NOT NULL, after TEXT NOT NULL, at REAL NOT NULL, actor_id TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS api_quota_history_scope ON api_quota_history(scope_kind, scope_id, at);
CREATE TABLE IF NOT EXISTS api_rate_state (bucket TEXT PRIMARY KEY, tat_ns INTEGER NOT NULL, interval_ns INTEGER);
"""
#: Additive: the emission interval a bucket was last charged at (NULL on rows written before it).
RATE_STATE_COLUMNS = [("api_rate_state", "interval_ns", "INTEGER")]


def deployment_defaults() -> dict[str, int]:
    """The values in force where no policy row says otherwise (one definition, read per call so a
    process or test that sets the environment before building an app gets those values)."""
    def num(name: str, default: int, lo: int = 1) -> int:
        try:
            return max(lo, int(env.get(name, str(default))))
        except (TypeError, ValueError):
            return default

    try:
        jobs = max(1, int(env.docs("MAX_ACTIVE_JOBS", "10")))
    except (TypeError, ValueError):
        jobs = 10
    return {"credential_rpm": num("API_RATE_PER_MIN", 300),
            "workspace_rpm": num("API_WORKSPACE_RATE_PER_MIN", 1200),
            "active_jobs": jobs}


# ------------------------------------------------------------------------------ identities
def credential_scope(principal: dict) -> str | None:
    """The policy target a machine principal answers to, or None (a user delegation has no
    credential-level policy: it is governed by its workspace and the platform)."""
    kind = principal.get("kind")
    if kind == "api_key":
        ident = principal.get("lineage") or principal.get("key_id")
        return f"api_key:{ident}" if ident else None
    if kind == "service_account" and principal.get("client_id"):
        return f"service_account:{principal['client_id']}"
    return None


def credential_bucket(principal: dict) -> str:
    scope = credential_scope(principal)
    if scope:
        return "c:" + scope
    return f"c:{principal.get('kind') or 'unknown'}:{principal.get('key_id') or ''}"


# ------------------------------------------------------------------------------ policies
def _row_values(row: dict | None) -> dict:
    return {f: (row or {}).get(f) for f in FIELDS}


def resolve(layers: dict[str, dict | None]) -> dict:
    """First value set, credential → workspace → platform, else the deployment default."""
    defaults = deployment_defaults()
    out = {}
    for f in FIELDS:
        chosen = {"value": defaults[f], "source": "deployment"}
        for layer in ("credential", "workspace", "platform"):
            row = layers.get(layer)
            if row is not None and f in LAYER_FIELDS[layer] and row.get(f) is not None:
                chosen = {"value": int(row[f]), "source": layer}
                break
        out[f] = chosen
    return out


class Policies:
    def __init__(self, store: Store):
        self.store = store

    def get(self, scope_kind: str, scope_id: str) -> dict | None:
        return self.store.one("SELECT * FROM api_quota_policies WHERE scope_kind=? AND scope_id=?",
                              (scope_kind, scope_id))

    def layers(self, workspace_id: str | None, credential: str | None) -> dict[str, dict | None]:
        wanted = [("platform", "")]
        if workspace_id:
            wanted.append(("workspace", workspace_id))
        if credential:
            wanted.append(("credential", credential))
        where = " OR ".join("(scope_kind=? AND scope_id=?)" for _ in wanted)
        rows = self.store.all(f"SELECT * FROM api_quota_policies WHERE {where}",
                              tuple(x for pair in wanted for x in pair))
        by = {r["scope_kind"]: r for r in rows}
        return {"credential": by.get("credential"), "workspace": by.get("workspace"),
                "platform": by.get("platform")}

    def effective(self, workspace_id: str | None, credential: str | None = None) -> dict:
        """``{field: {"value", "source"}}`` — source is the layer that decided it."""
        return resolve(self.layers(workspace_id, credential))

    # ---------------------------------------------------------------- administration
    @staticmethod
    def validate_target(scope_kind: str, scope_id: str) -> None:
        if scope_kind not in LAYER_FIELDS:
            raise ServiceError(400, "invalid_scope", "不支持的策略范围", {"field": "scope_kind"})
        if scope_kind == "platform" and scope_id != "":
            raise ServiceError(400, "invalid_scope", "平台策略没有目标 ID", {"field": "scope_id"})
        if scope_kind != "platform" and (not scope_id or len(scope_id) > 200):
            raise ServiceError(400, "invalid_scope", "请指定策略目标", {"field": "scope_id"})
        if scope_kind == "credential" and scope_id.split(":", 1)[0] not in CREDENTIAL_KINDS:
            raise ServiceError(400, "invalid_scope", "凭据策略的目标必须是访问密钥或服务身份", {"field": "scope_id"})

    def target_exists(self, scope_kind: str, scope_id: str) -> dict | None:
        """The target's workspace (for audit and display), or None when it does not exist."""
        if scope_kind == "platform":
            return {"workspace_id": None}
        if scope_kind == "workspace":
            row = self.store.one("SELECT id FROM workspaces WHERE id=?", (scope_id,))
            return {"workspace_id": row["id"]} if row else None
        kind, ident = scope_id.split(":", 1)
        if kind == "api_key":
            row = self.store.one("SELECT tenant FROM api_keys WHERE COALESCE(lineage,id)=? "
                                 "ORDER BY created_at DESC LIMIT 1", (ident,))
            return {"workspace_id": row["tenant"]} if row else None
        row = self.store.one("SELECT workspace_id FROM oauth_clients WHERE client_id=? AND kind='service'", (ident,))
        return {"workspace_id": row["workspace_id"]} if row else None

    def normalise(self, scope_kind: str, body: dict) -> dict:
        allowed = LAYER_FIELDS[scope_kind]
        unknown = set(body) - set(FIELDS) - {"base_version", "reason", "dry_run"}
        if unknown:
            raise ServiceError(400, "unknown_field", "包含不支持的字段", {"field": sorted(unknown)[0]})
        values = {}
        for f in FIELDS:
            v = body.get(f)
            if f not in allowed:
                if v is not None:
                    raise ServiceError(400, "field_not_at_scope", "该范围不能设置此项限制", {"field": f})
                values[f] = None
                continue
            if v is None:
                values[f] = None
                continue
            lo, hi = RANGES[f]
            if type(v) is not int or not lo <= v <= hi:
                raise ServiceError(400, "invalid_limit", f"限制值必须是 {lo} 至 {hi} 之间的整数，留空表示继承",
                                   {"field": f, "min": lo, "max": hi})
            values[f] = v
        return values

    def change(self, scope_kind: str, scope_id: str, body: dict, *, actor_id: str, reason: str,
               dry_run: bool = False) -> dict:
        """Write (or preview) an override. Returns the row, the effective values before and after,
        and what the change does to work already in flight."""
        self.validate_target(scope_kind, scope_id)
        values = self.normalise(scope_kind, body)
        base = body.get("base_version")
        if type(base) is not int or base < 0:
            raise ServiceError(400, "base_version_required", "请提供读取时的策略版本（首次设置为 0）",
                               {"field": "base_version"})
        with self.store.tx():
            target = self.target_exists(scope_kind, scope_id)
            if target is None:
                raise ServiceError(404, "not_found", "策略目标不存在")
            current = self.get(scope_kind, scope_id)
            version = current["version"] if current else 0
            if base != version:
                raise ServiceError(409, "stale_policy", "策略已被他人修改：请查看当前值后重新提交",
                                   {"version": version, "current": _row_values(current)})
            ws = target["workspace_id"] if scope_kind != "workspace" else scope_id
            cred = scope_id if scope_kind == "credential" else None
            before = self.effective(ws, cred)
            impact = self._impact(scope_kind, scope_id, ws, values)
            if dry_run:
                after = self._preview(ws, cred, scope_kind, values)
                return {"dry_run": True, "version": version, "before": before, "after": after,
                        "impact": impact, "workspace_id": ws}
            ts, new_version = now(), version + 1
            self.store.execute(
                "INSERT INTO api_quota_policies (scope_kind, scope_id, credential_rpm, workspace_rpm, active_jobs, "
                "version, updated_at, updated_by, reason) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(scope_kind, scope_id) DO UPDATE SET credential_rpm=excluded.credential_rpm, "
                "workspace_rpm=excluded.workspace_rpm, active_jobs=excluded.active_jobs, version=excluded.version, "
                "updated_at=excluded.updated_at, updated_by=excluded.updated_by, reason=excluded.reason",
                (scope_kind, scope_id, values["credential_rpm"], values["workspace_rpm"], values["active_jobs"],
                 new_version, ts, actor_id, reason))
            self.store.insert("api_quota_history", {
                "id": new_id("qph"), "scope_kind": scope_kind, "scope_id": scope_id, "version": new_version,
                "before": json.dumps(_row_values(current)), "after": json.dumps(values), "at": ts,
                "actor_id": actor_id, "reason": reason})
            after = self.effective(ws, cred)
        return {"dry_run": False, "version": new_version, "policy": self.get(scope_kind, scope_id),
                "before": before, "after": after, "impact": impact, "workspace_id": ws,
                "applies": "next_request"}

    def _preview(self, ws, cred, scope_kind, values) -> dict:
        """What ``effective`` will say once ``values`` are written at ``scope_kind`` (no write)."""
        layers = self.layers(ws, cred)
        layers[scope_kind] = {**(layers[scope_kind] or {}), **values}
        return resolve(layers)

    def _impact(self, scope_kind, scope_id, ws, values) -> dict:
        """What a lower capacity does to work already running: nothing is stopped; new submissions
        wait until the workspace is below the new limit."""
        out: dict = {"stops_running_work": False}
        if scope_kind == "workspace" and values.get("active_jobs") is not None:
            n = self.store.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running','cancelling') "
                               "AND tenant=?", (scope_id,))["n"]
            out["active_jobs_now"] = n
            out["over_new_limit"] = max(0, n - values["active_jobs"])
        if scope_kind == "platform":
            out["workspaces_with_own_override"] = self.store.one(
                "SELECT COUNT(*) AS n FROM api_quota_policies WHERE scope_kind='workspace' AND "
                "(credential_rpm IS NOT NULL OR workspace_rpm IS NOT NULL OR active_jobs IS NOT NULL)")["n"]
        return out

    def overrides(self, *, limit: int, offset: int) -> dict:
        where = ("FROM api_quota_policies WHERE credential_rpm IS NOT NULL OR workspace_rpm IS NOT NULL "
                 "OR active_jobs IS NOT NULL")
        with self.store.tx():
            total = self.store.one(f"SELECT COUNT(*) AS n {where}")["n"]
            items = self.store.all(f"SELECT * {where} ORDER BY updated_at DESC, scope_kind, scope_id LIMIT ? OFFSET ?",
                                   (limit, offset))
        return {"items": items, "total": total, "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < total else None}

    def history(self, scope_kind: str, scope_id: str, *, limit: int, offset: int) -> dict:
        with self.store.tx():
            total = self.store.one("SELECT COUNT(*) AS n FROM api_quota_history WHERE scope_kind=? AND scope_id=?",
                                   (scope_kind, scope_id))["n"]
            items = self.store.all("SELECT * FROM api_quota_history WHERE scope_kind=? AND scope_id=? "
                                   "ORDER BY at DESC, version DESC LIMIT ? OFFSET ?", (scope_kind, scope_id, limit, offset))
        for r in items:
            r["before"], r["after"] = json.loads(r["before"]), json.loads(r["after"])
        return {"items": items, "total": total, "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < total else None}


# ------------------------------------------------------------------------------ enforcement
@dataclass
class Limit:
    name: str               # "key" (the credential; the name existing clients already parse) or "workspace"
    cap: int
    remaining: int
    reset: int              # delay-seconds until one more request would be allowed
    source: str


@dataclass
class Decision:
    allowed: bool
    limits: list[Limit] = field(default_factory=list)
    retry_after: float = 0.0
    refused_by: Limit | None = None
    degraded: bool = False

    def headers(self) -> dict[str, str]:
        """`RateLimit-Policy` / `RateLimit` (draft-ietf-httpapi-ratelimit-headers): one item per
        policy, the credential's first, named ``key`` as before so existing parsers keep working."""
        return {"RateLimit-Policy": ", ".join(f'"{l.name}";q={l.cap};w={WINDOW_S}' for l in self.limits),
                "RateLimit": ", ".join(f'"{l.name}";r={l.remaining};t={l.reset}' for l in self.limits)}


def _ceil_s(ns: int) -> int:
    return max(1, -(-ns // 1_000_000_000))


def _rescaled(tat_ns: int | None, t_ns: int, interval: int, charged: int | None) -> int | None:
    """Carry the requests in use across a limit change, not the time they were charged at.

    A bucket's debt is (requests in use) x (interval charged). Kept as raw time, three requests
    under 3/min (60 s) would read as the whole 1200/min budget after the override is cleared, and
    a tightened limit would admit a new full burst on top of what was just used. Raising a limit
    therefore applies on the next request; lowering one leaves at most a full bucket, so it bites
    from now on without charging past usage retroactively for longer than one window.
    """
    if tat_ns is None or not charged or charged == interval or tat_ns <= t_ns:
        return tat_ns
    return t_ns + min(WINDOW_NS, (tat_ns - t_ns) * interval // charged)


def _gcra(tat_ns: int | None, t_ns: int, cap: int) -> tuple[bool, int, int, int, float]:
    """One GCRA step: (allowed, new tat, remaining, reset, retry_after seconds).

    Emission interval T = window / cap; a request is allowed while the theoretical arrival time
    stays within one window of now, so a full bucket admits ``cap`` requests at once — the same
    burst as the per-process token bucket this replaces. ``reset`` keeps that bucket's meaning:
    delay-seconds until one more request becomes available (0 when the budget is full).

    Integer nanoseconds, not float seconds: adding an interval to an epoch-sized float thousands of
    times drifts by more than one interval at high rates. T is rounded down, so the only error is
    admitting at most cap² ns / window extra requests per window (none below ~250 000/min).
    """
    interval = max(1, WINDOW_NS // cap)
    base = max(tat_ns or 0, t_ns)
    new = base + interval
    allowed = new - t_ns <= WINDOW_NS
    debt = (new if allowed else base) - t_ns          # budget in use after this request
    remaining = max(0, min(cap, (WINDOW_NS - debt) // interval))
    reset = 0 if debt <= 0 else _ceil_s(debt - WINDOW_NS + (remaining + 1) * interval)
    wait = 0.0 if allowed else (new - t_ns - WINDOW_NS) / 1e9
    return allowed, new if allowed else base, remaining, reset, wait


class RateGate:
    """Request-rate enforcement for one application process (state shared through the store)."""

    def __init__(self, store: Store):
        self.store = store
        self.policies = Policies(store)
        self._local: dict[str, list[int]] = {}
        self._lock = threading.Lock()
        self._warned = 0.0

    def take(self, principal: dict) -> Decision:
        tenant = principal.get("tenant")
        cred_scope = credential_scope(principal)
        try:
            eff = self.policies.effective(tenant, cred_scope)
        except sqlite3.Error:
            d = deployment_defaults()
            eff = {f: {"value": d[f], "source": "deployment"} for f in FIELDS}
        wanted = [("key", credential_bucket(principal), eff["credential_rpm"])]
        if tenant:
            wanted.append(("workspace", "w:" + tenant, eff["workspace_rpm"]))
        try:
            return self._take_shared(wanted)
        except sqlite3.Error as e:
            if time.monotonic() - self._warned > 60:
                self._warned = time.monotonic()
                log.warning("shared rate state unavailable (%s); limiting per process", type(e).__name__)
            return self._take_local(wanted)

    def _take_shared(self, wanted) -> Decision:
        t = time.time_ns()
        with self.store.tx() as c:
            steps, writes = [], []
            for name, bucket, spec in wanted:
                row = c.execute("SELECT tat_ns, interval_ns FROM api_rate_state WHERE bucket=?", (bucket,)).fetchone()
                interval = max(1, WINDOW_NS // int(spec["value"]))
                tat = _rescaled(row["tat_ns"], t, interval, row["interval_ns"]) if row else None
                steps.append((name, bucket, spec, _gcra(tat, t, int(spec["value"]))))
                writes.append((bucket, tat, interval, bool(row) and row["interval_ns"] != interval))
            allowed = all(s[3][0] for s in steps)
            # Charge every bucket when admitted. When refused, still record a converted bucket so the
            # requests in use drain at the limit now in force rather than at the one they were made under.
            for (_, _, _, (_, charged, _, _, _)), (bucket, tat, interval, converted) in zip(steps, writes):
                if allowed or converted:
                    c.execute("INSERT INTO api_rate_state (bucket, tat_ns, interval_ns) VALUES (?,?,?) "
                              "ON CONFLICT(bucket) DO UPDATE SET tat_ns=excluded.tat_ns, interval_ns=excluded.interval_ns",
                              (bucket, charged if allowed else max(tat or 0, t), interval))
        return self._decision(steps, allowed)

    def _take_local(self, wanted) -> Decision:
        t = time.time_ns()
        with self._lock:
            steps, writes = [], []
            for name, bucket, spec in wanted:
                previous, charged = (self._local.get(bucket) or [None, None])[:2]
                interval = max(1, WINDOW_NS // int(spec["value"]))
                tat = _rescaled(previous, t, interval, charged)
                steps.append((name, bucket, spec, _gcra(tat, t, int(spec["value"]))))
                writes.append((bucket, tat, interval, previous is not None and charged != interval))
            allowed = all(s[3][0] for s in steps)
            for (_, _, _, (_, admitted, _, _, _)), (bucket, tat, interval, converted) in zip(steps, writes):
                if allowed or converted:
                    self._local[bucket] = [admitted if allowed else max(tat or 0, t), interval]
            if len(self._local) > 4096:
                for k in [k for k, v in self._local.items() if v[0] < t]:
                    del self._local[k]
        d = self._decision(steps, allowed)
        d.degraded = True
        return d

    @staticmethod
    def _decision(steps, allowed: bool) -> Decision:
        limits, refused, wait = [], None, 0.0
        for name, _, spec, (ok, _, remaining, reset, retry) in steps:
            lim = Limit(name, int(spec["value"]), remaining, reset, spec["source"])
            limits.append(lim)
            if not ok and retry >= wait:
                refused, wait = lim, retry
        return Decision(allowed, limits, wait, refused)

    def usage(self, workspace_id: str | None, credential: str | None = None) -> dict:
        """How full the shared buckets are right now, for the console (read only, no write)."""
        t, at = time.time_ns(), now()
        eff = self.policies.effective(workspace_id, credential)
        out = {}
        for name, bucket, f in (("key", "c:" + credential if credential else None, "credential_rpm"),
                                ("workspace", "w:" + workspace_id if workspace_id else None, "workspace_rpm")):
            if not bucket:
                continue
            row = self.store.one("SELECT tat_ns, interval_ns FROM api_rate_state WHERE bucket=?", (bucket,))
            cap = int(eff[f]["value"])
            tat = _rescaled(row["tat_ns"], t, max(1, WINDOW_NS // cap), row["interval_ns"]) if row else None
            debt = max(0, (tat or 0) - t)
            out[name] = {"limit": cap, "window_s": WINDOW_S, "source": eff[f]["source"], "measured_at": at,
                         "remaining": max(0, min(cap, (WINDOW_NS - debt) // max(1, WINDOW_NS // cap)))}
        return out


def active_jobs_limit(store: Store, workspace_id: str) -> dict:
    """The effective active-jobs capacity for a workspace: ``{"value", "source"}``."""
    try:
        return Policies(store).effective(workspace_id)["active_jobs"]
    except sqlite3.Error:
        return {"value": deployment_defaults()["active_jobs"], "source": "deployment"}
