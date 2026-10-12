"""Signed webhooks for job events: per-workspace subscriptions and deployment-level endpoints.

Two kinds of destination receive job events, and they differ in who owns them:

* **Subscriptions** (``webhook_subscriptions``) belong to one workspace. They are managed through
  ``/api/v1/webhooks/subscriptions`` (scope ``admin`` / ``docweave.manage``) and
  ``python -m synwevo_docs.cli webhooks`` — deliberately never through the product UI. Each has its
  own ``whsec_`` secret (sealed at rest, shown once), its own event filter, and receives only its
  own workspace's events. Deliveries follow Standard Webhooks::

      webhook-id: <event id>            (stable across retries and redeliveries: the dedupe key)
      webhook-timestamp: <unix seconds>
      webhook-signature: v1,<base64 HMAC-SHA256(secret, "<id>.<timestamp>.<body>")> [v1,<…previous…>]
      X-Synwevo-Delivery: <delivery id>
      X-Synwevo-Event: <event type>

* **Deployment endpoints**, configured by the operator (they receive every workspace's events)::

      SYNWEVO_DOCS_WEBHOOK_URLS=https://automation.example.com/hooks/synwevo
      SYNWEVO_DOCS_WEBHOOK_SECRET=<shared secret>
      SYNWEVO_DOCS_WEBHOOK_EVENTS=job.completed,job.completed_with_errors,job.failed,job.cancelled
      SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE=10.0.0.0/8,automation.internal     # only if you need it

  They keep ``X-DocWeave-Signature: v1=<hmac-sha256(secret, "<timestamp>.<body>")>`` and the other
  ``X-DocWeave-*`` headers byte for byte, and additionally carry the Standard Webhooks headers.

Retries: every non-2xx except 410 is retried on 5s, 5m, 30m, 2h, 5h, 10h, 14h, 20h, 24h (±20 %
jitter; ``Retry-After`` on 429/503 is honoured as a minimum). 410 disables a subscription. A
subscription that has failed for three days is disabled with a recorded reason. A delivery this
server could not try (no resolver free, ``resolver_busy``) is not an attempt and is not held against
the subscription. The receiver's response body is never read or stored.

Delivery never blocks a job: the artifacts stay downloadable regardless of what a receiver does.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import http.client
import json
import os
import random
import secrets as _secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from urllib.parse import urlsplit

from synwevo_core import auth as _auth
from synwevo_core import authority, env, journal, sealed
from synwevo_core.audit_change import change
from synwevo_core import webhook_delivery as _delivery
from synwevo_core.errors import ServiceError
# Where a callback may go, how it is signed and when it is retried are the shared delivery layer's;
# the names below are the ones this module has always had.
from synwevo_core.webhook_delivery import (  # noqa: F401 — part of this module's surface
    BACKOFF_S, JITTER, MAX_URL_LEN, RETRY_AFTER_CAP_S, SECRET_PREFIX, ResolverBusy, UrlRejected, UrlUnresolvable,
    new_secret, secret_bytes, sign_standard, signature_header, verify_standard)
from synwevo_core.webhook_delivery import (
    RequestDeadline as _RequestDeadline, connect_pinned as _connect_pinned, retry_after as _retry_after)
from synwevo_core.store import Store, new_id
from synwevo_core import workers
from synwevo_core.docs_file_events import FILE_EVENT_TYPES
from synwevo_core.member_events import MEMBER_EVENT_TYPES
from synwevo_core.project_copies import COPY_EVENT_TYPES
from synwevo_core.template_events import TEMPLATE_EVENT_TYPES

CLAIM_MARGIN_S = 30
POLL_S = 5.0
ROUND_S = 5.0
BATCH = 500

DEFAULT_EVENTS = ("job.completed", "job.completed_with_errors", "job.failed", "job.cancelled")
# Every type `jobs.py` emits. A subscription filter naming anything else is refused rather than
# stored, because a typo ("job.complete") would otherwise produce a subscription that never fires.
JOB_EVENT_TYPES = ("job.created", "job.started", "job.planned", "job.cancel_requested", "job.retry_requested", "job.recovered",
                   "job.completed", "job.completed_with_errors", "job.failed", "job.cancelled")
# Source-file verdicts, written by a trigger in the transaction that changes the file's status
# (synwevo_core.docs_file_events): job_id is '' and data carries file_id / project_id / status.
# Membership and invitation changes (synwevo_core.member_events): the same trigger pattern, with
# identifiers only; `member.*` / `invitation.*` are the prefixes a directory integration subscribes to.
# Template changes (synwevo_core.template_events): created / updated / deleted, and the organization
# library's published / released / deprecated, ids and version numbers only.
EVENT_TYPES = JOB_EVENT_TYPES + FILE_EVENT_TYPES + COPY_EVENT_TYPES + MEMBER_EVENT_TYPES + TEMPLATE_EVENT_TYPES
EVENT_PREFIXES = ("job.*", "file.*", "copy.*", "member.*", "invitation.*", "template.*")
PING_EVENT = "webhook.ping"
DELIVERY_STATUSES = ("pending", "delivered", "failed", "dropped")

LAST_SEQ_KEY = "webhook_last_seq"
RESTORE_FENCE_KEY = "restore_event_fence"
CURSOR_AT_KEY = "webhook_cursor_at"      # when a dispatcher last advanced (or confirmed) the cursor
DEPLOYMENT_KEY = "webhook_endpoint_fingerprint"
DEPLOYMENT_GAP_KEY = "webhook_endpoint_gap"
RESUME_SEQ_KEY = "webhook_resume_seq:"
FAILING_SINCE_KEY = "webhook_failing_since:"
GRACE_S = 120
HEARTBEAT_S = 30

MAX_SUBSCRIPTIONS = 16
LOCAL_RETRY_S = 15                       # after a fault of this server's own, which is not an attempt
CHECK_CONCURRENCY = 4                    # addresses being checked for management calls at once, one a workspace
CHECK_TIMEOUT_S = 5.0                    # an address looked up for a management call: the caller is waiting
# Whom a lookup is counted for in the resolver pool every application of the process shares: a
# subscription's workspace, or — for an endpoint the operator configured — that endpoint itself.
DEPLOYMENT_OWNER = "deployment:"
MAX_OVERLAP_S = 86400
DEFAULT_OVERLAP_S = 86400
MAX_DESCRIPTION_LEN = 500
SUB_ENDPOINT = "sub:"

# Keys that must never travel in a webhook body even if a future event starts carrying them:
# the payload is a notification, not a credential or a way to fetch documents without a key.
_FORBIDDEN_DATA_KEYS = ("token", "secret", "url", "link", "password")


# ----------------------------------------------------------------------------- configuration
def _env_float(suffix: str, default: float) -> float:
    try:
        return float(env.docs(suffix) or default)
    except ValueError:
        return float(default)


def timeout_s() -> float:
    return max(1.0, _env_float("WEBHOOK_TIMEOUT_S", 15))


def backoff_schedule() -> tuple[float, ...]:
    raw = (env.docs("WEBHOOK_BACKOFF_S") or "").strip()
    if raw:
        try:
            vals = tuple(float(x) for x in raw.split(",") if x.strip())
            if vals and all(v >= 0 for v in vals):
                return vals
        except ValueError:
            pass
    return BACKOFF_S


def disable_after_s() -> float:
    return _env_float("WEBHOOK_DISABLE_AFTER_S", 3 * 86400)


def redelivery_window_s() -> float:
    return _env_float("WEBHOOK_REDELIVERY_DAYS", 7) * 86400


def _concurrency() -> int:
    try:
        return max(1, min(16, int(env.docs("WEBHOOK_CONCURRENCY") or 4)))
    except ValueError:
        return 4


def endpoints() -> list[str]:
    return [u.strip() for u in (env.docs("WEBHOOK_URLS") or "").split(",") if u.strip()]


def wanted_events() -> tuple[str, ...]:
    raw = (env.docs("WEBHOOK_EVENTS") or "").strip()
    return tuple(e.strip() for e in raw.split(",") if e.strip()) or DEFAULT_EVENTS


def secret() -> str:
    return env.docs("WEBHOOK_SECRET") or ""


def previous_secret(at: float) -> str | None:
    """The deployment secret being rotated out, while it is still inside its overlap.

    Only honoured with an expiry (``SYNWEVO_DOCS_WEBHOOK_SECRET_PREVIOUS_EXPIRES_AT``, unix
    seconds): an old secret that signs forever is not a rotation. It adds a second signature to
    ``webhook-signature`` only — ``X-DocWeave-Signature`` stays a single value, because existing
    receivers compare that header as a whole.
    """
    raw = env.docs("WEBHOOK_SECRET_PREVIOUS") or ""
    try:
        exp = float(env.docs("WEBHOOK_SECRET_PREVIOUS_EXPIRES_AT") or 0)
    except ValueError:
        return None
    return raw if raw and at < exp else None


def _allow_private() -> list[str]:
    return [x.strip() for x in (env.docs("WEBHOOK_ALLOW_PRIVATE") or "").split(",") if x.strip()]


# ----------------------------------------------------------------------------- address policy
def check_url(url: str, *, owner: str) -> str:
    """Reject callbacks that would reach into private networks unless explicitly allowed.

    Returns the url. Use `resolve_checked` when you are about to connect: this function answers
    "is this address acceptable", and the answer stops being true the moment it returns.
    """
    resolve_checked(url, owner=owner)
    return url


def resolve_checked(url: str, *, owner: str, deadline: float | None = None) -> list:
    """Validate the callback host under this deployment's Docs policy **and return the exact
    addresses that were validated** — the caller connects to one of these, never to the hostname
    again (`synwevo_core.webhook_delivery.resolve_checked` says why). The lookup is counted for
    `owner` (a workspace, or ``DEPLOYMENT_OWNER`` + an operator's endpoint), as the suite's are."""
    return _delivery.resolve_checked(
        url, owner=owner, deadline=deadline, timeout=timeout_s(), allow_private=_allow_private(),
        allow_http=(env.docs("WEBHOOK_ALLOW_HTTP") or "").lower() in ("1", "true", "on", "yes"),
        http_refusal="回调默认要求 https；确需 http 时设置 SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP=1")


_CHECKS = threading.BoundedSemaphore(CHECK_CONCURRENCY)
_CHECKING: set[str] = set()
_CHECKING_LOCK = threading.Lock()


@contextlib.contextmanager
def _checking(workspace: str):
    """One of the process's places for a management call's address check, or 429. The check
    resolves a name on the request's own thread, and the resolvers are shared with every delivery
    of every workspace: a workspace has one place at a time, so one whose names resolve slowly
    waits for itself and keeps nobody else out (the suite's rule)."""
    with _CHECKING_LOCK:
        if workspace in _CHECKING or not _CHECKS.acquire(blocking=False):
            raise ServiceError(429, "destination_check_busy", "正在检查的回调地址过多，请稍后再试", {"retry_after": 2})
        _CHECKING.add(workspace)
    try:
        yield
    finally:
        with _CHECKING_LOCK:
            _CHECKING.discard(workspace)
        _CHECKS.release()


def _look_up(url: str, workspace: str) -> list:
    """Where a subscription's address leads, asked for a management call of `workspace`. No
    resolver free in time is not a verdict on the address: the caller is told to come back, not
    that its URL is invalid."""
    with _checking(workspace):
        try:
            # The check's own short deadline, not a send's whole one: one of the process's few
            # places is held only that long, so names that answer slowly cannot keep every other
            # tenant's management calls at 429 (review R4; the suite's rule). Never longer than a
            # send's: a name a delivery could not resolve in time is not accepted (review R5).
            return resolve_checked(url, owner=workspace, deadline=time.monotonic() + min(CHECK_TIMEOUT_S, timeout_s()))
        except ResolverBusy as e:
            raise ServiceError(429, "destination_check_busy", "正在检查的回调地址过多，请稍后再试",
                               {"retry_after": LOCAL_RETRY_S}) from e


def validate_url(url, owner: str) -> str:
    """A subscription URL, checked the way a delivery will check it. Raises 400 with field=url, or
    429 ``destination_check_busy`` when it cannot be checked now. `owner` is the workspace the
    check is made for: its lookups are counted against it."""
    return _delivery.validate_url(url, resolve=lambda u: _look_up(u, owner))


# ----------------------------------------------------------------------------- signatures
def sign(body: bytes, ts: int, key: str) -> str:
    """The legacy deployment signature (``X-DocWeave-Signature``)."""
    return "v1=" + hmac.new(key.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def verify(body: bytes, ts: str | int, signature: str, key: str, tolerance_s: int = 300) -> bool:
    """Reference check of the legacy ``X-DocWeave-Signature`` (also used by the tests)."""
    try:
        if abs(time.time() - int(ts)) > tolerance_s:
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(sign(body, int(ts), key), signature or "")


# ----------------------------------------------------------------------------- event filters
def normalize_events(value) -> list[str]:
    if value is None:
        return list(DEFAULT_EVENTS)
    if isinstance(value, str):
        value = [v for v in value.split(",")]
    if not isinstance(value, list) or not value:
        raise ServiceError(400, "invalid_events", "events 必须是事件类型的非空列表（或 [\"*\"] 表示全部）", {"field": "events"})
    out: list[str] = []
    for v in value:
        v = v.strip() if isinstance(v, str) else v
        if v == "*" or v in EVENT_PREFIXES or v in EVENT_TYPES:
            if v not in out:
                out.append(v)
            continue
        raise ServiceError(400, "invalid_events", f"未知的事件类型：{v!r}", {"field": "events", "allowed": ["*", *EVENT_PREFIXES, *EVENT_TYPES]})
    return out


def matches(filters, etype: str) -> bool:
    for f in filters or ():
        if f == "*" or f == etype or (f.endswith(".*") and etype.startswith(f[:-1])):
            return True
    return False


def _events_of(row: dict) -> list[str]:
    try:
        v = json.loads(row.get("events") or "[]")
    except (TypeError, ValueError):
        return []
    return v if isinstance(v, list) else []


def _clean_data(data):
    if isinstance(data, list):
        return [_clean_data(v) for v in data]
    if not isinstance(data, dict):
        return data
    out = {k: _clean_data(v) for k, v in data.items()
           if k != "error" and not any(bad in k.lower() for bad in _FORBIDDEN_DATA_KEYS)}
    if "error" in data:
        out.setdefault("error_code", "job_error")
    return out


# ----------------------------------------------------------------------------- dispatcher
class WebhookDispatcher:
    """Turns job events into deliveries and sends them, retrying with jittered backoff.

    Every running Synwevo Docs worker process has one loop (it is cheap when nothing is
    configured), which is what keeps the event cursor current: enabling webhooks later starts
    from the present instead of replaying the history of every workspace.
    """

    def __init__(self, store: Store, jobs=None, start: bool = True, *, clock=None, rng=None,
                 worker_id: str | None = None, concurrency: int | None = None):
        self.store = store
        self.jobs = jobs
        self.clock = clock or time.time
        self.rng = rng or random.Random()
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{_secrets.token_hex(3)}"
        self.concurrency = concurrency or _concurrency()
        self.started_at = self.clock()
        self._wake = threading.Event()
        self._stop = False
        self._inflight: set[str] = set()
        self._inflight_lock = threading.Lock()
        self.last_error: str | None = None
        self._thread = None
        # `enabled` describes the deployment-level endpoints only; subscriptions are always live.
        self.enabled = bool(endpoints())
        if self.enabled and not secret():
            self.last_error = "已配置 SYNWEVO_DOCS_WEBHOOK_URLS 但缺少 SYNWEVO_DOCS_WEBHOOK_SECRET，部署级回调未启用"
            self.enabled = False
        if start:
            self._thread = threading.Thread(target=self._loop, name="webhooks", daemon=True)
            self._thread.start()
            workers.register(self, self._thread)

    def notify(self) -> None:
        self._wake.set()

    def stop(self, wait: float | None = None) -> None:
        self._stop = True
        self._wake.set()
        if wait and self._thread and self._thread is not threading.current_thread():
            self._thread.join(wait)

    def _owner_stopped(self) -> bool:
        # The loop lives exactly as long as this process's job runner: that is what `install`
        # ties it to, and it keeps a stopped test application from polling a dead data dir.
        return bool(self.jobs is not None and getattr(self.jobs, "_stop", False))

    def _deployment_endpoints(self) -> list[str]:
        eps = endpoints()
        if eps and not secret():
            self.last_error = "已配置 SYNWEVO_DOCS_WEBHOOK_URLS 但缺少 SYNWEVO_DOCS_WEBHOOK_SECRET，部署级回调未启用"
            return []
        return eps

    # ---------------------------------------------------------- queue
    def enqueue_new_events(self) -> int:
        """Fan new job events out to the destinations that want them. Returns rows added.

        Reading the cursor, inserting the deliveries and advancing the cursor are one
        transaction: a failed insert leaves the cursor where it was, so the batch is retried
        instead of silently skipped.
        """
        t = self.clock()
        eps = self._deployment_endpoints()
        fingerprint = hashlib.sha256(json.dumps(sorted(eps)).encode()).hexdigest()
        want = set(wanted_events())
        added = 0
        # Fast path without the write lock: the loop runs every few seconds in every worker, and
        # an idle deployment should not take SQLite's write lock that often for nothing.
        cur = self.store.one("SELECT (SELECT value FROM meta WHERE key=?) AS seq, (SELECT value FROM meta WHERE key=?) AS at, "
                             "(SELECT value FROM meta WHERE key=?) AS fingerprint, "
                             "(SELECT COALESCE(MAX(seq), 0) FROM job_events) AS top",
                             (LAST_SEQ_KEY, CURSOR_AT_KEY, DEPLOYMENT_KEY))
        if cur and cur["seq"] is not None and cur["at"] is not None and int(cur["seq"]) == cur["top"] \
                and cur["fingerprint"] == fingerprint and t - float(cur["at"]) < HEARTBEAT_S:
            return 0
        with self.store.tx() as c:
            top = c.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM job_events").fetchone()["m"]
            row = c.execute("SELECT value FROM meta WHERE key=?", (LAST_SEQ_KEY,)).fetchone()
            at_row = c.execute("SELECT value FROM meta WHERE key=?", (CURSOR_AT_KEY,)).fetchone()
            ep_row = c.execute("SELECT value FROM meta WHERE key=?", (DEPLOYMENT_KEY,)).fetchone()
            same_endpoints = ep_row is not None and ep_row["value"] == fingerprint
            if row is None:
                # First run on this data directory: start at the end of the log. Enabling webhooks
                # must not replay the history of every workspace to a new receiver.
                self._save_cursor(c, top, t, fingerprint)
                return 0
            last = int(row["value"])
            if last > top:
                # The log is shorter than the cursor: an older backup was restored. New events
                # will reuse these sequence numbers, so the cursor has to come back with the log.
                self._save_cursor(c, top, t, fingerprint)
                return 0
            fence = c.execute("SELECT value FROM meta WHERE key=?", (RESTORE_FENCE_KEY,)).fetchone()
            last = max(last, int(fence["value"]) if fence else 0)
            prev_at = float(at_row["value"]) if at_row else self._legacy_cursor_time(c, last)
            gap_row = c.execute("SELECT value FROM meta WHERE key=?", (DEPLOYMENT_GAP_KEY,)).fetchone()
            gap = json.loads(gap_row["value"]) if gap_row else None
            if not same_endpoints:
                gap = {"through_seq": top, "before": self.started_at - GRACE_S,
                       "after": prev_at + GRACE_S if prev_at is not None else None}
                c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                          (DEPLOYMENT_GAP_KEY, json.dumps(gap)))
            evs = c.execute("SELECT seq, id, job_id, tenant, type, occurred_at FROM job_events "
                            "WHERE seq>? ORDER BY seq LIMIT ?", (last, BATCH)).fetchall()
            if not evs:
                if at_row is None or t - prev_at >= HEARTBEAT_S or not same_endpoints or last != int(row["value"]):
                    self._save_cursor(c, last, t, fingerprint)
                return 0
            subs: dict[str, list[dict]] = {}
            for s in c.execute("SELECT id, tenant, events, created_at FROM webhook_subscriptions "
                               "WHERE status='active' AND deleted_at IS NULL").fetchall():
                s = dict(s)
                s["filters"] = _events_of(s)
                resumed = c.execute("SELECT value FROM meta WHERE key=?", (RESUME_SEQ_KEY + s["id"],)).fetchone()
                s["after_seq"] = int(resumed["value"]) if resumed else 0
                subs.setdefault(s["tenant"], []).append(s)
            for e in evs:
                # A stopped worker is not evidence that webhooks were disabled: unchanged
                # endpoints still receive the backlog. For changed/previously unknown config,
                # retain the legacy no-replay gap rule.
                in_gap = gap is not None and e["seq"] <= gap["through_seq"] and e["occurred_at"] < gap["before"] and (
                    gap["after"] is None or e["occurred_at"] > gap["after"])
                if eps and e["type"] in want and not in_gap:
                    for url in eps:
                        added += self._insert(c, e, url, None, t)
                for s in subs.get(e["tenant"] or "default", ()):
                    # A subscription only ever sees events from after it existed — including after
                    # a restore that put the cursor back behind its creation.
                    if e["seq"] > s["after_seq"] and e["occurred_at"] >= s["created_at"] and matches(s["filters"], e["type"]):
                        added += self._insert(c, e, SUB_ENDPOINT + s["id"], s["id"], t)
            self._save_cursor(c, evs[-1]["seq"], t, fingerprint)
        return added

    @staticmethod
    def _insert(c, e, endpoint: str, sid: str | None, t: float) -> int:
        return c.execute(
            "INSERT OR IGNORE INTO webhook_deliveries (id, event_id, event_seq, endpoint, status, attempts, "
            "next_attempt_at, created_at, subscription_id, tenant, event_type) VALUES (?,?,?,?,'pending',0,?,?,?,?,?)",
            (new_id("wh"), e["id"], e["seq"], endpoint, t, t, sid, e["tenant"] or "default", e["type"])).rowcount

    @staticmethod
    def _save_cursor(c, seq: int, t: float, fingerprint: str) -> None:
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (LAST_SEQ_KEY, str(int(seq))))
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (CURSOR_AT_KEY, repr(float(t))))
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (DEPLOYMENT_KEY, fingerprint))

    @staticmethod
    def _legacy_cursor_time(c, last: int) -> float | None:
        # A cursor written before the heartbeat existed: the event it points at is the latest
        # moment we know a dispatcher was keeping up.
        r = c.execute("SELECT occurred_at FROM job_events WHERE seq=?", (last,)).fetchone()
        return float(r["occurred_at"]) if r else None

    def due(self) -> list[dict]:
        """Pending deliveries whose time has come and that nobody is currently sending."""
        t = self.clock()
        return self.store.all("SELECT * FROM webhook_deliveries WHERE status='pending' AND next_attempt_at<=? "
                              "AND (claimed_until IS NULL OR claimed_until<?) ORDER BY next_attempt_at, rowid LIMIT 50", (t, t))

    def deliveries(self, limit: int = 50) -> list[dict]:
        return self.store.all("SELECT * FROM webhook_deliveries ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))

    def claim_next(self, exclude: tuple = ()) -> dict | None:
        """Atomically take one due delivery for this worker.

        The select and the claim are one ``BEGIN IMMEDIATE`` transaction, which SQLite serialises
        across processes: two workers sharing a data directory can never both send the same
        delivery while the claim holds. The claim outlives the request timeout.
        """
        t = self.clock()
        sql = ("SELECT id FROM webhook_deliveries AS d WHERE status='pending' AND next_attempt_at<=? "
               "AND (claimed_until IS NULL OR claimed_until<?) "
               "AND NOT EXISTS (SELECT 1 FROM webhook_deliveries AS retry WHERE retry.status='pending' "
               "AND retry.attempts>0 AND retry.next_attempt_at>? "
               "AND COALESCE(retry.subscription_id, retry.endpoint)=COALESCE(d.subscription_id, d.endpoint))")
        args: list = [t, t, t]
        if exclude:
            sql += f" AND COALESCE(subscription_id, endpoint) NOT IN ({','.join('?' * len(exclude))})"
            args += list(exclude)
        sql += " ORDER BY next_attempt_at, rowid LIMIT 1"
        with self.store.tx() as c:
            row = c.execute(sql, args).fetchone()
            if not row:
                return None
            c.execute("UPDATE webhook_deliveries SET claimed_by=?, claimed_until=? WHERE id=?",
                      (self.worker_id, t + timeout_s() + CLAIM_MARGIN_S, row["id"]))
            return self.store.one("SELECT * FROM webhook_deliveries WHERE id=?", (row["id"],))

    # ---------------------------------------------------------- send
    def payload(self, event_id: str) -> tuple[bytes, dict] | None:
        """The deployment-endpoint body (unchanged shape)."""
        e = self.store.one("SELECT * FROM job_events WHERE id=?", (event_id,))
        if not e:
            return None
        job = self.store.one("SELECT id, project_id, status, counts, run_no, tenant, created_at, finished_at FROM jobs WHERE id=?", (e["job_id"],))
        body = {"id": e["id"], "type": e["type"], "version": e["version"], "occurred_at": e["occurred_at"],
                "tenant": e["tenant"], "job": job, "data": e["data"]}
        return json.dumps(body, ensure_ascii=False, default=str).encode(), body

    def subscription_payload(self, d: dict, sub: dict, event: dict | None) -> bytes:
        """The subscription body: identifiers, status and counts. No document content, no
        tokens, no download links — a receiver fetches anything else with its own credential."""
        if d.get("event_type") == PING_EVENT:
            body = {"id": d["event_id"], "type": PING_EVENT, "version": 1, "occurred_at": d["created_at"],
                    "tenant": sub["tenant"], "data": {"subscription_id": sub["id"]}, "job": None}
        else:
            job = self.store.one("SELECT id, project_id, status, counts, run_no FROM jobs WHERE id=?", (event["job_id"],))
            body = {"id": event["id"], "type": event["type"], "version": event["version"],
                    "occurred_at": event["occurred_at"], "tenant": event["tenant"],
                    "data": _clean_data(event["data"]), "job": _clean_data(job)}
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")

    def send_one(self, d: dict, *, single_attempt: bool = False) -> bool:
        """Send one claimed delivery. Returns True when the receiver answered 2xx."""
        if not self._owns_claim(d):
            return False
        fence = self.store.one("SELECT value FROM meta WHERE key=?", (RESTORE_FENCE_KEY,))
        if fence and d.get("event_type") != PING_EVENT:
            event = self.store.one("SELECT seq FROM job_events WHERE id=?", (d["event_id"],))
            if event and event["seq"] <= int(fence["value"]):
                self._drop(d, "restored_from_backup")
                return False
        t = self.clock()
        sub = None
        if d.get("subscription_id"):
            sub, event = self._sendable(d)
            if sub is None:
                return False
            try:
                keys = [sealed.unseal(self.store, "webhook-secret", sub["secret_enc"])]
                if sub.get("secret_prev_enc") and (sub.get("secret_prev_expires_at") or 0) > t:
                    keys.append(sealed.unseal(self.store, "webhook-secret", sub["secret_prev_enc"]))
            except Exception as e:  # noqa: BLE001 - a root-secret problem is the operator's to fix
                # Do not burn an attempt or lose the event over a configuration error: keep it
                # pending and try again later, with the reason visible.
                self.last_error = f"无法读取订阅签名密钥：{type(e).__name__}"
                self._finish(d, "pending", attempts=d.get("attempts") or 0, error=self.last_error,
                             next_attempt_at=t + 300)
                return False
            body = self.subscription_payload(d, sub, event)
            etype = d.get("event_type") or (event or {}).get("type") or PING_EVENT
            ts = int(t)
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Synwevo-Webhook/1",
                "webhook-id": d["event_id"],
                "webhook-timestamp": str(ts),
                "webhook-signature": signature_header(d["event_id"], ts, body, keys),
                "X-Synwevo-Delivery": d["id"],
                "X-Synwevo-Event": etype,
            }
            url = sub["url"]
        else:
            if d["endpoint"] not in endpoints():
                # Removing an address from the configuration has to stop signed deliveries to it,
                # not leave them retrying for days.
                self._finish(d, "dropped", attempts=d.get("attempts") or 0, error="endpoint_removed")
                return False
            key = secret()
            if not key:
                self._finish(d, "dropped", attempts=d.get("attempts") or 0, error="no_secret")
                return False
            made = self.payload(d["event_id"])
            if not made:
                self._finish(d, "dropped", attempts=d.get("attempts") or 0, error="event_missing")
                return False
            body, parsed = made
            etype = parsed["type"]
            ts = int(t)
            prev = previous_secret(t)
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "DocWeave-Webhook/1",
                # These header names are **not** rebranded. A receiver that was written
                # against them parses them by name; changing them is an interface break dressed
                # up as a brand change, and the people it breaks would find out from a missed
                # webhook rather than from a release note.
                "X-DocWeave-Event": etype,
                "X-DocWeave-Event-Id": d["event_id"],
                "X-DocWeave-Delivery": d["id"],
                "X-DocWeave-Timestamp": str(ts),
                "X-DocWeave-Signature": sign(body, ts, key),
                # Standard Webhooks alongside, so generic verifiers work too.
                "webhook-id": d["event_id"],
                "webhook-timestamp": str(ts),
                "webhook-signature": signature_header(d["event_id"], ts, body, [key] + ([prev] if prev else [])),
            }
            url = d["endpoint"]

        attempts = (d.get("attempts") or 0) + 1
        started = time.monotonic()
        try:
            # Validate and connect to *the same* address. Calling check_url and then letting
            # urllib resolve the name again is the hole this closes.
            with _RequestDeadline(timeout_s()) as deadline:
                validated = resolve_checked(url, deadline=deadline.end,
                                            owner=sub["tenant"] if sub is not None else DEPLOYMENT_OWNER + url)
                # DNS can take most of the attempt. A pause, restore or another claimant may
                # have intervened since the payload was made; never connect on stale authority.
                if not self._owns_claim(d):
                    return False
                if sub is not None:
                    current, _ = self._sendable(d)
                    if current is None:
                        return False
                    if current["version"] != sub["version"]:
                        self._finish(d, "pending", attempts=d.get("attempts") or 0,
                                     next_attempt_at=self.clock(), error="subscription_changed")
                        return False
                req = urllib.request.Request(url, data=body, method="POST", headers=headers)
                with _pinned_opener(validated, deadline).open(req, timeout=deadline.remaining()) as resp:
                    code = resp.status                  # the body is never read, let alone stored
        except urllib.error.HTTPError as e:
            code = e.code
            wait = _retry_after(e.headers, self.clock()) if code in (429, 503) else None
            e.close()
            err = f"HTTP {code}" + ("（回调地址发生跳转，出于安全不跟随）" if 300 <= code < 400 else "")
            if code == 410 and sub is not None:
                # 410 Gone is the receiver saying "stop": the subscription is disabled with that
                # reason (and its queue emptied) rather than retried for three days.
                if self._attempt_failed(d, None, attempts, code, err, retriable=False, duration_ms=_ms(started)):
                    self.disable_subscription(sub["id"], "gone")
                return False
            self._attempt_failed(d, sub, attempts, code, err, retriable=(code != 410 and not single_attempt),
                                 retry_after=wait, duration_ms=_ms(started))
            return False
        except ResolverBusy:
            # No resolver was free in time — this server's fault, not the receiver's, as in the
            # suite: not an attempt, and nothing on the subscription's record.
            return self._untried(d, "resolver_busy", single_attempt)
        except UrlUnresolvable as e:
            self._attempt_failed(d, sub, attempts, None, str(e)[:300], retriable=not single_attempt, duration_ms=_ms(started))
            return False
        except UrlRejected as e:
            self._attempt_failed(d, sub, attempts, None, str(e)[:300], retriable=False, duration_ms=_ms(started))
            return False
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            err = "回调请求超时" if time.monotonic() >= deadline.end else f"回调连接失败：{type(e).__name__}"
            self._attempt_failed(d, sub, attempts, None, err,
                                 retriable=not single_attempt, duration_ms=_ms(started))
            return False
        if not self._finish(d, "delivered", attempts=attempts, code=code, delivered_at=self.clock(), duration_ms=_ms(started)):
            return False
        if sub is not None and d.get("event_type") != PING_EVENT:
            self.store.execute("UPDATE webhook_subscriptions SET consecutive_failures=0, last_success_at=? WHERE id=?",
                               (self.clock(), sub["id"]))
        return True

    def _owns_claim(self, d: dict) -> bool:
        return bool(self.store.one("SELECT 1 FROM webhook_deliveries WHERE id=? AND status='pending' "
                                   "AND claimed_by=? AND claimed_until=? AND claimed_until>?",
                                   (d["id"], self.worker_id, d.get("claimed_until"), self.clock())))

    def _sendable(self, d: dict) -> tuple[dict | None, dict | None]:
        """The subscription and event for a delivery — or (None, None) after dropping it.

        Authority is asked again here, about the present: the credential or person that set the
        subscription up may have been revoked, or the workspace frozen, since the event was
        queued. Retries run for days; a decision made at enqueue time would outlive both.
        """
        sub = self.store.one("SELECT * FROM webhook_subscriptions WHERE id=?", (d["subscription_id"],))
        if not sub or sub["deleted_at"]:
            self._drop(d, "subscription_deleted")
            return None, None
        allowed = ("active", "paused") if d.get("event_type") == PING_EVENT else ("active",)
        if sub["status"] not in allowed:
            self._drop(d, f"subscription_{sub['status']}")
            return None, None
        if d.get("tenant") and d["tenant"] != sub["tenant"]:
            self._drop(d, "workspace_mismatch")
            return None, None
        ws = self.store.one("SELECT status FROM workspaces WHERE id=?", (sub["tenant"],))
        if ws and ws["status"] in authority.BLOCKED_WORKSPACE_STATES:
            reason = f"workspace_{ws['status']}"
            self.disable_subscription(sub["id"], reason)
            self._drop(d, reason)
            return None, None
        ok, why = authority.check(self.store, authority.loads(sub["created_by"]), need="read", tenant=sub["tenant"])
        if not ok:
            reason = f"authorization_revoked:{why}"
            self.disable_subscription(sub["id"], reason)
            self._drop(d, reason)
            return None, None
        event = None
        if d.get("event_type") != PING_EVENT:
            event = self.store.one("SELECT * FROM job_events WHERE id=?", (d["event_id"],))
            if not event:
                self._drop(d, "event_missing")
                return None, None
            if (event["tenant"] or "default") != sub["tenant"]:
                self._drop(d, "workspace_mismatch")
                return None, None
            if event["occurred_at"] < sub["created_at"]:
                # Checked again at send time: a restored database can carry queue rows that the
                # enqueue rule would never have produced.
                self._drop(d, "predates_subscription")
                return None, None
        return sub, event

    def _untried(self, d: dict, error: str, single_attempt: bool) -> bool:
        """This server could not try: the delivery waits `LOCAL_RETRY_S` with its attempts as they
        were; a single attempt (a ping), which nobody would come back to, ends there and says why."""
        attempts = d.get("attempts") or 0
        if single_attempt:
            self._finish(d, "failed", attempts=attempts, error=error)
        else:
            self._finish(d, "pending", attempts=attempts, error=error, next_attempt_at=self.clock() + LOCAL_RETRY_S)
        return False

    def next_delay(self, attempts: int, retry_after: float | None = None) -> float | None:
        """Seconds until the next attempt after `attempts` failed ones; None when exhausted."""
        return _delivery.next_delay(attempts, retry_after, schedule=backoff_schedule(), rng=self.rng)

    def _attempt_failed(self, d: dict, sub: dict | None, attempts: int, code: int | None, err: str,
                        retriable: bool, retry_after: float | None = None, duration_ms: int | None = None) -> bool:
        self.last_error = err
        t = self.clock()
        delay = self.next_delay(attempts, retry_after) if retriable else None
        if delay is None:
            finished = self._finish(d, "failed", attempts=attempts, code=code, error=err, duration_ms=duration_ms)
        else:
            finished = self._finish(d, "pending", attempts=attempts, code=code, error=err, duration_ms=duration_ms,
                         next_attempt_at=t + delay)
        if finished and sub is not None and d.get("event_type") != PING_EVENT:
            self._note_failure(sub, t)
        return finished

    def _note_failure(self, sub: dict, t: float) -> None:
        """Count the failure; disable the subscription once it has failed for three days.

        The run of failures starts at the attempt that took `consecutive_failures` from 0 to 1,
        and that moment is kept under ``meta['webhook_failing_since:<id>']``. A success resets
        the counter and re-enabling the subscription does too, so either starts a fresh clock —
        a re-enabled subscription is not disabled again by its own history.
        """
        key = FAILING_SINCE_KEY + sub["id"]
        with self.store.tx() as c:
            row = c.execute("SELECT consecutive_failures FROM webhook_subscriptions WHERE id=?", (sub["id"],)).fetchone()
            c.execute("UPDATE webhook_subscriptions SET consecutive_failures=consecutive_failures+1, last_failure_at=? "
                      "WHERE id=?", (t, sub["id"]))
            since = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            if not row or not row["consecutive_failures"] or since is None:
                c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, repr(float(t))))
                since = None
        if since is not None and t - float(since["value"]) >= disable_after_s():
            self.disable_subscription(sub["id"], "failing")

    def _drop(self, d: dict, reason: str) -> None:
        self._finish(d, "dropped", attempts=d.get("attempts") or 0, error=reason)

    def _finish(self, d: dict, status: str, *, attempts: int, code: int | None = None, error: str | None = None,
                delivered_at: float | None = None, next_attempt_at: float | None = None,
                duration_ms: int | None = None) -> bool:
        vals = {"status": status, "attempts": attempts, "last_error": error, "claimed_by": None, "claimed_until": None,
                "next_attempt_at": next_attempt_at}
        if code is not None or status in ("delivered", "failed", "pending"):
            vals["last_code"] = code
        if delivered_at is not None:
            vals["delivered_at"] = delivered_at
        if duration_ms is not None:
            vals["duration_ms"] = duration_ms
        # A stopped in-flight request still records its observed attempt, but cannot resurrect
        # the queue or replace the operator's reason. Match the lease as well as worker id:
        # the same worker may already have acquired a newer lease for this delivery.
        with self.store.tx() as c:
            row = c.execute("SELECT status FROM webhook_deliveries WHERE id=? AND claimed_by=? "
                            "AND claimed_until=? AND claimed_until>?",
                            (d["id"], self.worker_id, d.get("claimed_until"), self.clock())).fetchone()
            if not row or row["status"] not in ("pending", "dropped"):
                return False
            stopped = row["status"] == "dropped"
            if stopped and duration_ms is not None:
                for k in ("status", "last_error", "next_attempt_at", "delivered_at"):
                    vals.pop(k, None)
            sets = ",".join(f"{k}=?" for k in vals)
            c.execute(f"UPDATE webhook_deliveries SET {sets} WHERE id=?", tuple(vals.values()) + (d["id"],))
        return not stopped

    def disable_subscription(self, sid: str, reason: str) -> bool:
        """Disable a subscription and stop its pending deliveries; journalled, so a restore of an
        older database cannot bring it back to life."""
        t = self.clock()
        with self.store.tx() as c:
            sub = c.execute("SELECT tenant, url FROM webhook_subscriptions WHERE id=?", (sid,)).fetchone()
            n = c.execute("UPDATE webhook_subscriptions SET status='disabled', disabled_reason=?, updated_at=?, "
                          "version=version+1 WHERE id=? AND status!='disabled'", (reason, t, sid)).rowcount
            dropped = c.execute("UPDATE webhook_deliveries SET status='dropped', last_error=?, next_attempt_at=NULL "
                                "WHERE subscription_id=? AND status='pending'", (f"subscription_disabled:{reason}", sid)).rowcount
            if n and sub:
                # The platform's own decision (a destination given up on), on record like a person's.
                _auth.audit_event(self.store, "docs.webhook.disable", actor={"kind": "operator", "label": "webhook-dispatcher"},
                                  workspace_id=sub["tenant"], object_kind="webhook_subscription", object_id=sid,
                                  meta={"reason": reason, "host": urlsplit(sub["url"]).hostname, "deliveries_dropped": dropped})
        if n:
            try:
                journal.record(self.store, "webhook_disabled", subscription_id=sid)
            except Exception as e:  # noqa: BLE001 - the database already says disabled; say why the journal does not
                self.last_error = f"安全日志写入失败（webhook_disabled {sid}）：{e}"
        return bool(n)

    # ---------------------------------------------------------- loop
    def deliver_due(self, limit: int = 100) -> int:
        """Round-robin destinations within a bounded round, backing off a failing destination.

        Healthy destinations may drain multiple events, but every due destination gets a turn
        before one gets another. A round stops claiming at ROUND_S (an already-started attempt
        has its own total deadline), so the loop returns to enqueue and heartbeat regularly.
        """
        t = self.clock()
        if not self.store.one("SELECT 1 AS x FROM webhook_deliveries WHERE status='pending' AND next_attempt_at<=? "
                              "AND (claimed_until IS NULL OR claimed_until<?) LIMIT 1", (t, t)):
            return 0
        lock = threading.Lock()
        state = {"budget": limit, "sent": 0}
        served: set[str] = set()
        blocked: set[str] = set()
        end = time.monotonic() + ROUND_S

        def worker():
            while True:
                with lock:
                    if state["budget"] <= 0 or time.monotonic() >= end or self._stop or self._owner_stopped():
                        return
                    with self._inflight_lock:
                        d = self.claim_next(tuple(self._inflight | served | blocked))
                        if not d and served and not self._inflight:
                            served.clear()
                            d = self.claim_next(tuple(blocked))
                        if not d:
                            return
                        key = d.get("subscription_id") or d["endpoint"]
                        self._inflight.add(key)
                        served.add(key)
                        state["budget"] -= 1
                try:
                    sent = self.send_one(d)
                    with lock:
                        if sent:
                            state["sent"] += 1
                        else:
                            blocked.add(key)
                finally:
                    with self._inflight_lock:
                        self._inflight.discard(key)

        if self.concurrency <= 1:
            worker()
        else:
            with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="webhook-send") as ex:
                for f in [ex.submit(worker) for _ in range(self.concurrency)]:
                    f.result()
        return state["sent"]

    def run_once(self) -> int:
        self.enqueue_new_events()
        return self.deliver_due()

    def _loop(self) -> None:
        while not self._stop and not self._owner_stopped():
            try:
                self.run_once()
            except Exception as e:  # noqa: BLE001 - never kill the dispatcher
                self.last_error = f"{type(e).__name__}: {e}"
            self._wake.wait(POLL_S)
            self._wake.clear()


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _pinned_opener(validated: list, deadline: _RequestDeadline | None = None):
    """An opener that connects **only** to an address `resolve_checked` approved, without the
    ambient proxy and without following redirects (`synwevo_core.webhook_delivery.pinned_opener`).
    The socket is opened through this module's `_connect_pinned`, looked up at each connection."""
    return _delivery.pinned_opener(validated, deadline, connect=lambda *a, **k: _connect_pinned(*a, **k))


def send_test(url: str, key: str | None = None) -> dict:
    """One signed ``webhook.ping`` to `url` with the deployment secret, through the same address
    check and signer as real deliveries. For proving a receiver works before any job runs."""
    key = key if key is not None else secret()
    if not key:
        raise ServiceError(400, "no_secret", "缺少 SYNWEVO_DOCS_WEBHOOK_SECRET，无法签名测试事件")
    ev_id, ts = new_id("ev"), int(time.time())
    body = json.dumps({"id": ev_id, "type": PING_EVENT, "version": 1, "occurred_at": time.time(),
                       "tenant": None, "job": None, "data": {}}, ensure_ascii=False, default=str).encode()
    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "DocWeave-Webhook/1",
               "X-DocWeave-Event": PING_EVENT, "X-DocWeave-Event-Id": ev_id, "X-DocWeave-Delivery": new_id("wh"),
               "X-DocWeave-Timestamp": str(ts), "X-DocWeave-Signature": sign(body, ts, key),
               "webhook-id": ev_id, "webhook-timestamp": str(ts),
               "webhook-signature": signature_header(ev_id, ts, body, [key])}
    started = time.monotonic()
    try:
        with _RequestDeadline(timeout_s()) as deadline:
            validated = resolve_checked(url, deadline=deadline.end, owner=DEPLOYMENT_OWNER + url)
            with _pinned_opener(validated, deadline).open(
                    urllib.request.Request(url, data=body, method="POST", headers=headers),
                    timeout=deadline.remaining()) as resp:
                code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
    return {"status": code, "event_id": ev_id, "duration_ms": _ms(started)}


# ----------------------------------------------------------------------------- subscriptions
def _public(row: dict, at: float) -> dict:
    prev_exp = row.get("secret_prev_expires_at")
    return {"id": row["id"], "url": row["url"], "description": row.get("description") or "",
            "events": _events_of(row), "status": row["status"], "disabled_reason": row.get("disabled_reason"),
            "created_at": row["created_at"], "updated_at": row["updated_at"], "version": row["version"],
            "authorized_by": authority.label(authority.loads(row.get("created_by"))),
            "consecutive_failures": row.get("consecutive_failures") or 0,
            "last_success_at": row.get("last_success_at"), "last_failure_at": row.get("last_failure_at"),
            "previous_secret_expires_at": prev_exp if (row.get("secret_prev_enc") and prev_exp and prev_exp > at) else None}


def delivery_public(row: dict) -> dict:
    return {"id": row["id"], "webhook_id": row["event_id"], "event_type": row.get("event_type"),
            "status": row["status"], "attempts": row.get("attempts") or 0, "last_code": row.get("last_code"),
            "last_error": row.get("last_error"), "duration_ms": row.get("duration_ms"),
            "created_at": row.get("created_at"), "delivered_at": row.get("delivered_at"),
            "next_attempt_at": row.get("next_attempt_at") if row["status"] == "pending" else None,
            "redelivery": bool(row.get("redelivery_of")), "redelivery_of": row.get("redelivery_of")}


def _enc_cursor(prefix: str, n: int) -> str:
    return base64.urlsafe_b64encode(f"{prefix}:{int(n)}".encode()).decode().rstrip("=")


def _dec_cursor(prefix: str, raw: str | None, field: str = "cursor") -> int | None:
    if not raw:
        return None
    try:
        txt = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        p, _, n = txt.partition(":")
        if p != prefix:
            raise ValueError(p)
        v = int(n)
        if v < 0:
            raise ValueError(n)
        return v
    except (ValueError, UnicodeDecodeError) as e:
        raise ServiceError(400, "invalid_cursor", "游标无效；请使用上一页返回的 next_cursor", {"field": field}) from e


def _limit(value, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = -1
    if v < 1 or v > maximum:
        raise ServiceError(400, "invalid_limit", f"limit 必须在 1–{maximum} 之间", {"field": "limit"})
    return v


class Subscriptions:
    """A workspace's webhook subscriptions.

    Every method takes the workspace (`tenant`) the caller acts in and treats another
    workspace's subscription as nonexistent (404). ``tenant=None`` is the operator scope and is
    reachable only from the command line.

    `created_by` holds the authority a subscription delivers under. It is re-set by the actions
    that decide *where* events go or *who can verify them* — creating it, changing its URL,
    rotating its secret, re-enabling it — so revoking whoever did the last of those stops it.
    """

    WRITABLE = ("url", "description", "events")

    def __init__(self, store: Store, dispatcher: WebhookDispatcher | None = None, *, clock=None):
        self.store = store
        self.dispatcher = dispatcher
        self.clock = clock or time.time

    def _dispatcher(self) -> WebhookDispatcher:
        if self.dispatcher is None:
            self.dispatcher = WebhookDispatcher(self.store, start=False, clock=self.clock)
        return self.dispatcher

    def _row(self, tenant: str | None, sid: str) -> dict:
        row = self.store.one("SELECT * FROM webhook_subscriptions WHERE id=? AND deleted_at IS NULL", (sid,))
        if not row or (tenant is not None and row["tenant"] != tenant):
            raise ServiceError(404, "not_found", "Webhook 订阅不存在或已删除")
        return row

    @staticmethod
    def _check_fields(body: dict, allowed: tuple) -> None:
        if not isinstance(body, dict):
            raise ServiceError(400, "invalid_body", "请求体必须是 JSON 对象")
        for k in body:
            if k in allowed or k == "version":
                continue
            hint = {"status": "；暂停与恢复请用 /pause、/resume", "secret": "；更换密钥请用 /rotate-secret"}.get(k, "")
            raise ServiceError(400, "unknown_field", f"不支持的字段：{k}{hint}", {"field": k})

    @staticmethod
    def _description(v) -> str:
        if v is None:
            return ""
        if not isinstance(v, str) or len(v) > MAX_DESCRIPTION_LEN:
            raise ServiceError(400, "invalid_description", f"description 必须是不超过 {MAX_DESCRIPTION_LEN} 字的文本", {"field": "description"})
        return v.strip()

    # ---------------------------------------------------------- CRUD
    def create(self, tenant: str, body: dict, by: dict) -> dict:
        if not tenant:
            raise ServiceError(400, "tenant_required", "请指定工作空间")
        self._check_fields(body, self.WRITABLE)
        url = validate_url(body.get("url"), tenant)
        events = normalize_events(body.get("events"))
        desc = self._description(body.get("description"))
        plain = new_secret()
        enc = sealed.seal(self.store, "webhook-secret", plain)
        t, sid = self.clock(), new_id("whs")
        with self.store.tx() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM webhook_subscriptions WHERE tenant=? AND deleted_at IS NULL",
                          (tenant,)).fetchone()["n"]
            if n >= MAX_SUBSCRIPTIONS:
                raise ServiceError(409, "subscription_limit_reached",
                                   f"每个工作空间最多 {MAX_SUBSCRIPTIONS} 个 Webhook 订阅；请先删除不用的订阅",
                                   {"limit": MAX_SUBSCRIPTIONS})
            c.execute("INSERT INTO webhook_subscriptions (id, tenant, url, description, events, status, secret_enc, "
                      "created_by, created_at, updated_at, version) VALUES (?,?,?,?,?,'active',?,?,?,?,1)",
                      (sid, tenant, url, desc, json.dumps(events), enc, authority.dumps(by), t, t))
            self._audit("create", by, tenant, sid, host=urlsplit(url).hostname, events=events, version=1)
        out = self.get(tenant, sid)
        out["secret"] = plain
        return out

    def _audit(self, action: str, by: dict | None, tenant: str, object_id: str, *, object_kind: str = "webhook_subscription",
               **meta) -> None:
        """One row per change to where a workspace's events go (admin coverage G26, decision D7),
        in the transaction of the change: who (the authority descriptor; the request's principal
        when a caller passes none), the destination's host — never the address's path, which may
        carry a token — and identifiers. Never the secret."""
        _auth.audit_event(self.store, f"docs.webhook.{action}", actor=by, workspace_id=tenant,
                          object_kind=object_kind, object_id=object_id,
                          meta={k: v for k, v in meta.items() if v is not None})

    def list(self, tenant: str | None) -> list[dict]:
        t = self.clock()
        if tenant is None:
            rows = self.store.all("SELECT * FROM webhook_subscriptions WHERE deleted_at IS NULL ORDER BY tenant, created_at")
        else:
            rows = self.store.all("SELECT * FROM webhook_subscriptions WHERE tenant=? AND deleted_at IS NULL ORDER BY created_at",
                                  (tenant,))
        out = []
        for r in rows:
            p = _public(r, t)
            if tenant is None:
                p["tenant"] = r["tenant"]
            out.append(p)
        return out

    def get(self, tenant: str | None, sid: str) -> dict:
        row = self._row(tenant, sid)
        out = _public(row, self.clock())
        if tenant is None:
            out["tenant"] = row["tenant"]
        return out

    def update(self, tenant: str | None, sid: str, body: dict, expected_version, by: dict) -> dict:
        self._check_fields(body, self.WRITABLE)
        row = self._row(tenant, sid)
        if expected_version is not None:
            try:
                ev = int(expected_version)
            except (TypeError, ValueError) as e:
                raise ServiceError(400, "invalid_version", "version / If-Match 必须是整数版本号", {"field": "version"}) from e
            if ev != row["version"]:
                raise ServiceError(409, "version_conflict", "订阅已被修改；请重新读取后再提交",
                                   {"current_version": row["version"]})
        values: dict = {}
        if "url" in body:
            values["url"] = validate_url(body["url"], row["tenant"])
            values["created_by"] = authority.dumps(by)
        if "events" in body:
            values["events"] = json.dumps(normalize_events(body["events"]))
        if "description" in body:
            values["description"] = self._description(body["description"])
        if values:
            values["updated_at"] = self.clock()
            sets = ",".join(f"{k}=?" for k in values)
            with self.store.tx() as c:
                n = c.execute(f"UPDATE webhook_subscriptions SET {sets}, version=version+1 WHERE id=? AND version=? "
                              f"AND deleted_at IS NULL", tuple(values.values()) + (sid, row["version"])).rowcount
                if n:
                    self._audit("update", by, row["tenant"], sid, version=row["version"] + 1,
                                description_changed="description" in values or None,
                                **change({"host": urlsplit(row["url"]).hostname, "events": json.loads(row["events"])},
                                         {"host": urlsplit(values.get("url", row["url"])).hostname,
                                          "events": json.loads(values.get("events", row["events"]))}))
            if not n:
                cur = self.store.one("SELECT version FROM webhook_subscriptions WHERE id=?", (sid,))
                raise ServiceError(409, "version_conflict", "订阅已被同时修改；请重新读取后再提交",
                                   {"current_version": (cur or {}).get("version")})
        return self.get(tenant, sid)

    def delete(self, tenant: str | None, sid: str, by: dict | None = None) -> dict:
        row = self._row(tenant, sid)
        # Journal first: a delete that cannot be recorded outside the database is not durable
        # against a restore, and the caller must hear that instead of a success.
        try:
            journal.record(self.store, "webhook_disabled", subscription_id=sid)
        except Exception as e:  # noqa: BLE001
            raise ServiceError(503, "journal_unavailable", f"无法写入安全日志，删除未执行：{e}") from e
        t = self.clock()
        with self.store.tx() as c:
            c.execute("UPDATE webhook_subscriptions SET status='disabled', disabled_reason='deleted', deleted_at=?, "
                      "updated_at=?, version=version+1 WHERE id=?", (t, t, sid))
            dropped = c.execute("UPDATE webhook_deliveries SET status='dropped', last_error='subscription_deleted', next_attempt_at=NULL "
                                "WHERE subscription_id=? AND status='pending'", (sid,)).rowcount
            c.execute("DELETE FROM meta WHERE key=?", (FAILING_SINCE_KEY + sid,))
            self._audit("delete", by, row["tenant"], sid, host=urlsplit(row["url"]).hostname, version=row["version"] + 1,
                        deliveries_dropped=dropped)
        return {"deleted": sid}

    def pause(self, tenant: str | None, sid: str, by: dict | None = None) -> dict:
        row = self._row(tenant, sid)
        if row["status"] == "disabled":
            raise ServiceError(409, "subscription_disabled", "订阅已停用；如需重新启用请调用 /resume")
        if row["status"] == "active":
            t = self.clock()
            with self.store.tx() as c:
                c.execute("UPDATE webhook_subscriptions SET status='paused', updated_at=?, version=version+1 WHERE id=?", (t, sid))
                # Pausing stops what is already queued too: a paused destination must not keep
                # receiving retries for days.
                dropped = c.execute("UPDATE webhook_deliveries SET status='dropped', last_error='subscription_paused', next_attempt_at=NULL "
                                    "WHERE subscription_id=? AND status='pending'", (sid,)).rowcount
                self._audit("pause", by, row["tenant"], sid, host=urlsplit(row["url"]).hostname, version=row["version"] + 1,
                            deliveries_dropped=dropped)
        return self.get(tenant, sid)

    def resume(self, tenant: str | None, sid: str, by: dict) -> dict:
        row = self._row(tenant, sid)
        if row["status"] != "active":
            try:
                _look_up(row["url"], row["tenant"])
            except UrlRejected as e:
                raise ServiceError(400, "invalid_url", f"当前回调地址不可用，无法恢复：{e}", {"field": "url"}) from e
            t = self.clock()
            with self.store.tx() as c:
                c.execute("UPDATE webhook_subscriptions SET status='active', disabled_reason=NULL, consecutive_failures=0, "
                          "created_by=?, updated_at=?, version=version+1 WHERE id=?", (authority.dumps(by), t, sid))
                top = c.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM job_events").fetchone()["seq"]
                c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (RESUME_SEQ_KEY + sid, str(top)))
                self._audit("resume", by, row["tenant"], sid, host=urlsplit(row["url"]).hostname, version=row["version"] + 1,
                            from_status=row["status"], disabled_reason=row.get("disabled_reason"))
        return self.get(tenant, sid)

    def rotate_secret(self, tenant: str | None, sid: str, overlap_s, by: dict) -> dict:
        if overlap_s is None:
            overlap = DEFAULT_OVERLAP_S
        else:
            try:
                overlap = int(overlap_s)
            except (TypeError, ValueError):
                overlap = -1
            if overlap < 0 or overlap > MAX_OVERLAP_S:
                raise ServiceError(400, "invalid_overlap", f"overlap_s 必须在 0–{MAX_OVERLAP_S} 秒之间", {"field": "overlap_s"})
        row = self._row(tenant, sid)
        plain = new_secret()
        enc = sealed.seal(self.store, "webhook-secret", plain)
        t = self.clock()
        prev_enc, prev_exp = (row["secret_enc"], t + overlap) if overlap > 0 else (None, None)
        with self.store.tx():
            self.store.execute("UPDATE webhook_subscriptions SET secret_enc=?, secret_prev_enc=?, secret_prev_expires_at=?, "
                               "created_by=?, updated_at=?, version=version+1 WHERE id=?",
                               (enc, prev_enc, prev_exp, authority.dumps(by), t, sid))
            self._audit("rotate_secret", by, row["tenant"], sid, host=urlsplit(row["url"]).hostname,
                        version=row["version"] + 1, overlap_s=overlap)
        out = self.get(tenant, sid)
        out["secret"] = plain
        return out

    # ---------------------------------------------------------- deliveries
    def ping(self, tenant: str | None, sid: str, by: dict | None = None) -> dict:
        """Send one signed ``webhook.ping`` now (a single attempt) and return its record."""
        row = self._row(tenant, sid)
        if row["status"] == "disabled":
            raise ServiceError(409, "subscription_disabled", "订阅已停用；请先调用 /resume")
        disp = self._dispatcher()
        t, did = self.clock(), new_id("wh")
        # The delivery row and its audit commit together, before the network send (never under
        # the write lock).
        with self.store.tx():
            self.store.execute(
                "INSERT INTO webhook_deliveries (id, event_id, event_seq, endpoint, status, attempts, next_attempt_at, created_at, "
                "subscription_id, tenant, event_type, claimed_by, claimed_until) VALUES (?,?,NULL,?,'pending',0,?,?,?,?,?,?,?)",
                (did, new_id("ev"), SUB_ENDPOINT + sid, t, t, sid, row["tenant"], PING_EVENT, disp.worker_id,
                 t + timeout_s() + CLAIM_MARGIN_S))
            self._audit("ping", by, row["tenant"], did, object_kind="webhook_delivery", subscription_id=sid,
                        host=urlsplit(row["url"]).hostname)
        disp.send_one(self.store.one("SELECT * FROM webhook_deliveries WHERE id=?", (did,)), single_attempt=True)
        return self.delivery(tenant, sid, did)

    def deliveries(self, tenant: str | None, sid: str, status: str | None = None, cursor: str | None = None,
                   limit=None) -> dict:
        self._row(tenant, sid)
        lim = _limit(limit, 50, 100)
        if status and status not in DELIVERY_STATUSES:
            raise ServiceError(400, "invalid_status", f"status 只能是 {'/'.join(DELIVERY_STATUSES)}", {"field": "status"})
        before = _dec_cursor("d1", cursor)
        sql = "SELECT rowid AS _rid, * FROM webhook_deliveries WHERE subscription_id=?"
        args: list = [sid]
        if status:
            sql += " AND status=?"
            args.append(status)
        if before is not None:
            sql += " AND rowid<?"
            args.append(before)
        rows = self.store.all(sql + " ORDER BY rowid DESC LIMIT ?", tuple(args) + (lim + 1,))
        more = len(rows) > lim
        rows = rows[:lim]
        return {"items": [delivery_public(r) for r in rows],
                "next_cursor": _enc_cursor("d1", rows[-1]["_rid"]) if (more and rows) else None, "has_more": more}

    def delivery(self, tenant: str | None, sid: str, did: str) -> dict:
        self._row(tenant, sid)
        row = self.store.one("SELECT * FROM webhook_deliveries WHERE id=? AND subscription_id=?", (did, sid))
        if not row:
            raise ServiceError(404, "not_found", "投递记录不存在")
        return delivery_public(row)

    def replay_delivery(self, tenant: str, did: str) -> dict:
        """Current history access to a cached receipt, resolved from its actual delivery id.

        A pause or the redelivery window restricts new work, not reading an accepted receipt.
        Never authorize this result through a new request's sid (including on key conflicts).
        """
        row = self.store.one("SELECT subscription_id FROM webhook_deliveries WHERE id=?", (did,))
        if not row:
            raise ServiceError(404, "not_found", "投递记录不存在")
        return self.delivery(tenant, row["subscription_id"], did)

    def redeliver(self, tenant: str | None, sid: str, did: str, by: dict | None = None) -> dict:
        """Queue the same event again as a new delivery (same webhook-id, new delivery id)."""
        with self.store.tx() as c:
            sub = self._row(tenant, sid)
            orig = self.store.one("SELECT * FROM webhook_deliveries WHERE id=? AND subscription_id=?", (did, sid))
            if not orig:
                raise ServiceError(404, "not_found", "投递记录不存在")
            if sub["status"] != "active":
                raise ServiceError(409, "subscription_not_active", f"订阅当前为 {sub['status']}，恢复后才能重新投递")
            ev = None
            if orig.get("event_type") != PING_EVENT:
                ev = self.store.one("SELECT occurred_at FROM job_events WHERE id=?", (orig["event_id"],))
                if not ev:
                    raise ServiceError(409, "event_missing", "该事件已不存在，无法重新投递")
            ref = ev["occurred_at"] if ev else orig["created_at"]
            t = self.clock()
            window = redelivery_window_s()
            if t - ref > window:
                raise ServiceError(409, "redelivery_window_expired", f"只能重新投递 {int(window // 86400)} 天内的事件",
                                   {"window_days": window / 86400})
            new = new_id("wh")
            n = c.execute("SELECT COUNT(*) AS n FROM webhook_deliveries WHERE event_id=? AND subscription_id=? "
                          "AND redelivery_of IS NOT NULL", (orig["event_id"], sid)).fetchone()["n"]
            c.execute("INSERT INTO webhook_deliveries (id, event_id, event_seq, endpoint, status, attempts, next_attempt_at, "
                      "created_at, subscription_id, tenant, event_type, redelivery_of) VALUES (?,?,?,?,'pending',0,?,?,?,?,?,?)",
                      (new, orig["event_id"], orig.get("event_seq"), f"{SUB_ENDPOINT}{sid}:re:{n + 1}", t, t, sid,
                       sub["tenant"], orig.get("event_type"), did))
            self._audit("redeliver", by, sub["tenant"], new, object_kind="webhook_delivery", subscription_id=sid,
                        redelivery_of=did, event_id=orig["event_id"], event_type=orig.get("event_type"),
                        host=urlsplit(sub["url"]).hostname)
            out = self.delivery(tenant, sid, new)
            if self.dispatcher is not None:
                self.store.after_commit(self.dispatcher.notify)
        return out


# ----------------------------------------------------------------------------- event feed
def events_feed(store: Store, tenant: str, cursor: str | None = None, types: str | None = None, limit=None) -> dict:
    """A workspace's job and source-file events in log order, for consumers that missed webhooks or
    prefer to poll. The item ``id`` is the same value a webhook carries as ``webhook-id``, so one dedupe
    table covers both channels."""
    if not tenant:
        raise ServiceError(403, "forbidden", "该凭据没有绑定工作空间")
    lim = _limit(limit, 100, 500)
    after = _dec_cursor("e1", cursor)
    top = store.one("SELECT COALESCE(MAX(seq), 0) AS m FROM job_events")["m"]
    if after is not None and after > top:
        # The log is behind the cursor: the database was restored from an older backup. New
        # events will reuse positions the caller has already passed, so continuing would skip them.
        raise ServiceError(410, "cursor_expired", "事件日志已恢复到更早的状态，该游标不再有效；请不带 cursor 重新同步")
    wanted = None
    if types:
        wanted = [x.strip() for x in types.split(",") if x.strip()]
        bad = [x for x in wanted if x not in EVENT_TYPES]
        if bad:
            raise ServiceError(400, "invalid_events", f"未知的事件类型：{', '.join(bad)}", {"field": "types", "allowed": list(EVENT_TYPES)})
    sql = ("SELECT e.seq, e.id, e.job_id, e.type, e.version, e.occurred_at, e.data, j.project_id FROM job_events e "
           "LEFT JOIN jobs j ON j.id=e.job_id WHERE e.seq>? AND e.tenant=?")
    args: list = [after or 0, tenant]
    if wanted:
        sql += f" AND e.type IN ({','.join('?' * len(wanted))})"
        args += wanted
    rows = store.all(sql + " ORDER BY e.seq LIMIT ?", tuple(args) + (lim + 1,))
    more = len(rows) > lim
    rows = rows[:lim]
    items = []
    for r in rows:
        data = _clean_data(r["data"])
        item = {"id": r["id"], "type": r["type"], "version": r["version"], "occurred_at": r["occurred_at"],
                "job_id": r["job_id"] or None, "project_id": r["project_id"], "data": data}
        if r["type"].startswith("file.") and isinstance(data, dict):
            # A file verdict belongs to no job: its file and project come from the event itself.
            item["file_id"] = data.get("file_id")
            item["project_id"] = data.get("project_id")
        elif r["type"].startswith("copy.") and isinstance(data, dict):
            item["copy_id"] = data.get("copy_id")
            item["project_id"] = data.get("source_project_id")
        items.append(item)
    nxt = rows[-1]["seq"] if rows else (after or 0)
    return {"items": items, "next_cursor": _enc_cursor("e1", nxt), "has_more": more}
