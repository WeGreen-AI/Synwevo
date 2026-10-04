"""Signed webhooks for a client a person authorised: the event feed, pushed.

A subscription belongs to one grant — this client, acting for this person, in the workspace they
chose — and only that grant sees or manages it. What is sent is exactly what the same credential
could pull from ``GET /api/suite/v1/events``: where to look (type, object, the identifiers needed
to open it), never the event's data. ``webhook-id`` is the event's id, the same value the feed
gives, so one table of seen ids covers both channels.

Pushing outlives the request that asked for it, so nothing is decided once:

* **when an event is queued** and again **immediately before every send** — retries run for days —
  the grant must be one its client could still renew at that moment (`Capabilities.grant_scopes`
  with ``renewable``: not revoked, its refresh token not run out, the sign-in it came from not
  ended, the person still entitled), the person must still be a member of a live workspace, the
  application's read scope must still be held and the application still readable by the person,
  and the application must say the person may read that record now
  (`machine_events.visible_items`, the feed's own decision). An event that fails any of these is
  dropped unsent. A grant that has ended (`ENDED`) disables the subscription and empties its
  queue; a scope or a record that is no longer the person's only filters, as in the feed;
* **which grant answers** for a subscription is the one that created it, resumed it, or last
  changed its address, its filter or its secret. Those calls bind it to the caller's grant and
  check the filter against what that grant holds; changing only the description does not;
* **where it goes** is validated when a subscription is created, changed or resumed and again at
  every send, and the connection is pinned to the address that was validated
  (`synwevo_core.webhook_delivery`): https to a globally routable address, no proxy, no redirects,
  one total deadline. A receiver's response body is never read.

Deliveries follow Standard Webhooks and the retry schedule Synwevo Docs uses: every non-2xx except
410 is retried on 5s, 5m, 30m, 2h, 5h, 10h, 14h, 20h, 24h (±20 %; ``Retry-After`` on 429/503 is a
minimum); 410 disables the subscription; one with no success for three days and a whole schedule
of attempts is disabled with the reason recorded. At least once, and in the order queued: a
subscription's oldest pending delivery is the only one that may be sent (decided in the claim
query, so it holds across processes), and while it waits for its retry nothing later is tried.

The cursor is the queue: a subscription with `MAX_PENDING` deliveries waiting gets no more rows
until some settle, so a slow receiver costs a bounded number of rows, and one that has fallen
further behind than the feed keeps events is disabled (``backlog_expired``) rather than left to
skip them silently. Whenever pushing stops, where it stopped is kept and returned as a feed
cursor (``missed_events_cursor``): what was not pushed is pulled, under the feed's own checks.

What is this server's fault is not charged to a receiver: no free resolver, a destination's
per-minute budget used up, a signing key that cannot be read — the delivery waits, its attempts
and the subscription's health untouched.

The per-record decision reads the applications' own records and is made outside any write
transaction; what it decided is then written only if the subscription has not changed meanwhile.
"""
from __future__ import annotations

import collections
import contextlib
import json
import logging
import os
import random
import secrets as _secrets
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request
import http.client
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Request, Response

from synwevo_core import authority, env, journal, sealed, sealed_token
from synwevo_core import webhook_delivery as delivery
from synwevo_core.errors import ServiceError
from synwevo_core.http import SUITE_V1
from synwevo_core.store import new_id, now
from synwevo_core.suite_schema.webhooks import stopping
from synwevo_core.webhook_delivery import (BACKOFF_S, RequestDeadline, ResolverBusy, UrlRejected, UrlUnresolvable,
                                           connect_pinned, new_secret, signature_header)

from . import machine_events

log = logging.getLogger("synwevo.suite")

MAX_PER_GRANT = 4                           # one client, one person, one workspace — disabled ones included
MAX_PER_CLIENT = 32                         # live (active or paused) ones of one client in a workspace
MAX_PER_WORKSPACE = 64                      # live ones in a workspace; the operator may set another ceiling
MAX_DESCRIPTION_LEN = 500
MAX_EVENTS = 50
MAX_OVERLAP_S = DEFAULT_OVERLAP_S = 86400
MAX_REDELIVERIES = 5                        # of one event to one subscription
TIMEOUT_S = 15.0
CLAIM_MARGIN_S = 30
LEASE_S = TIMEOUT_S + CLAIM_MARGIN_S         # how long a claim holds: the request's whole deadline and a margin
MAX_ATTEMPTS = len(BACKOFF_S) + 1
DISABLE_AFTER_S = 3 * 86400                 # …with at least a whole schedule of attempts and no success
MAX_PENDING = 100                           # deliveries waiting for one subscription; the cursor waits beyond it
HOST_PER_MINUTE = 300                       # requests this process sends to one host, every subscription together
LOCAL_RETRY_S = 15                          # after a fault of this server's own, which is not an attempt
VALIDATION_CONCURRENCY = 4                  # addresses being checked for management calls at once
SWEEP_CHUNK = 2000
REDELIVERY_WINDOW_S = machine_events.RETENTION_S
LOG_RETENTION_S = 30 * 86400
FAN_SUBSCRIPTIONS = 20                      # subscriptions looked at per tick
FAN_ROWS = 200                              # rows of the event log read for one of them per tick
ROUND_S = 5.0
CONCURRENCY = 4
PING_EVENT = "webhook.ping"
PING_MIN_INTERVAL_S = 10                    # per subscription
PING_PER_HOUR = 20
PING_CONCURRENCY = 4                        # pings are sent inside the request that asks for them
ORPHAN_AFTER_S = LOG_RETENTION_S            # a subscription whose grant ended is removed after this
DELIVERY_STATUSES = ("pending", "delivered", "failed", "dropped")
#: What `last_error` may say about an attempt. A closed list: never an exception's name or text,
#: which would describe the server's view of the network to whoever chose the address.
ATTEMPT_ERRORS = ("http_status", "redirect_not_followed", "timeout", "tls_failed", "connect_failed",
                  "destination_unavailable", "internal_error", "attempts_exhausted")
#: …and about a delivery that is waiting through no fault of the receiver.
LOCAL_ERRORS = ("resolver_busy", "destination_busy", "signing_key_unavailable", "subscription_changed")
WRITABLE = ("url", "description", "events")
_CURSOR_LABEL = b"synwevo/suite-webhook-deliveries-cursor/v1"
#: Why a grant no longer covers a subscription. Each disables it with the reason
#: ``authorization_revoked:<code>``; nothing else does — a scope or a record that is no longer the
#: person's to read only filters, as it does in the feed.
ENDED = ("unknown_principal", "account_unavailable", "workspace_unavailable", "not_a_member",
         "credential_revoked", "grant_revoked", "grant_not_renewable", "grant_expired", "session_ended",
         "entitlement_revoked")


# --------------------------------------------------------------------------------- destination policy
def _on(suffix: str) -> bool:
    return (env.get(suffix) or "").strip().lower() in ("1", "true", "on", "yes")


def _allow_private() -> list[str]:
    return [x.strip() for x in (env.get("SUITE_WEBHOOK_ALLOW_PRIVATE") or "").split(",") if x.strip()]


def workspace_limit() -> int:
    """How many live subscriptions one workspace may have, every client and person together."""
    raw = (env.get("SUITE_WEBHOOK_MAX_PER_WORKSPACE") or "").strip()
    try:
        return max(1, min(10_000, int(raw))) if raw else MAX_PER_WORKSPACE
    except ValueError:
        return MAX_PER_WORKSPACE


def resolve_checked(url: str, *, deadline: float | None = None) -> list:
    """This deployment's policy for the business applications' callbacks: https to a globally
    routable address. The two switches are the operator's, for local development and tests.

    One refusal for "does not resolve" and "resolves somewhere it may not go": told apart, they
    would let a caller ask this server which internal names exist."""
    try:
        return delivery.resolve_checked(url, deadline=deadline, timeout=TIMEOUT_S, allow_private=_allow_private(),
                                        allow_http=_on("SUITE_WEBHOOK_ALLOW_HTTP"), http_refusal="回调地址必须使用 https")
    except ResolverBusy:
        raise                                               # this server's fault: says nothing about the address
    except UrlUnresolvable as e:
        raise UrlUnresolvable("回调地址不可用：无法解析，或不被允许") from e
    except UrlRejected as e:
        if "https" in str(e) or "http(s)" in str(e) or "端口" in str(e) or "主机名" in str(e):
            raise                                           # about the text of the URL, not about where it leads
        raise UrlRejected("回调地址不可用：无法解析，或不被允许") from e


def _resolve_now(url: str) -> list:
    """The check a management call makes. No resolver free in time is not a verdict on the
    address: the caller is told to come back, not that its URL is invalid."""
    try:
        return resolve_checked(url)
    except ResolverBusy as e:
        raise ServiceError(429, "destination_check_busy", "正在检查的回调地址过多，请稍后再试",
                           {"retry_after": LOCAL_RETRY_S}) from e


def validate_url(url) -> str:
    return delivery.validate_url(url, resolve=_resolve_now)


def _connect_pinned(address, timeout, deadline=None):
    return connect_pinned(address, timeout, deadline)


def _pinned_opener(validated: list, deadline: RequestDeadline | None = None):
    return delivery.pinned_opener(validated, deadline, connect=lambda *a, **k: _connect_pinned(*a, **k))


# --------------------------------------------------------------------------------- what may be subscribed
def subscribable(registry) -> list[str]:
    """Everything a filter may name: ``*``, ``<app>.*`` and each declared event type."""
    served = machine_events.feeds(registry)
    return ["*", *(f"{app}.*" for app in sorted(served)),
            *sorted(t.type for f in served.values() for t in f.types)]


def normalize_events(registry, value, scopes) -> list[str]:
    """The filter as stored. Each entry must be something the credential may read now: naming an
    application whose read scope it does not hold is refused by name, as the feed refuses it."""
    allowed = subscribable(registry)
    if isinstance(value, str):
        value = [x for x in value.split(",")]
    if not isinstance(value, list) or not value or len(value) > MAX_EVENTS:
        raise ServiceError(400, "invalid_events", f"events 须为 1–{MAX_EVENTS} 个事件类型、\"<应用>.*\" 或 \"*\"",
                           {"field": "events", "allowed": allowed})
    out = []
    for item in value:
        item = item.strip() if isinstance(item, str) else item
        if item not in allowed:
            raise ServiceError(400, "invalid_events", "events 里有未知的事件类型", {"field": "events", "allowed": allowed})
        if item not in out:
            out.append(item)
    held = {s.split(".")[0] for s in scopes if s.endswith(".read")} & set(machine_events.feeds(registry))
    if not held:
        raise ServiceError(403, "insufficient_scope", "订阅事件需要至少一个应用的 read 权限",
                           {"any_of": sorted(f"{app}.read" for app in machine_events.feeds(registry))})
    for item in out:
        app = item.split(".")[0]
        if item != "*" and app not in held:
            raise ServiceError(403, "insufficient_scope", f"该凭据没有 {app}.read 权限", {"required_scope": f"{app}.read"})
    return out


def matches(filters, etype: str) -> bool:
    return any(f == "*" or f == etype or (f.endswith(".*") and etype.startswith(f[:-1])) for f in filters)


def _events_of(row: dict) -> list[str]:
    try:
        value = json.loads(row["events"])
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


# --------------------------------------------------------------------------------- representations
def _family(row: dict) -> str | None:
    desc = authority.loads(row["created_by"])
    return desc.get("family_id") if isinstance(desc, dict) else None


def _public(store, row: dict, at: float, p: dict) -> dict:
    prev = row.get("secret_prev_expires_at")
    # Where pushing last stopped, as the feed's own cursor for this caller: what was not pushed
    # can be pulled from there. Gone once it is older than the feed would accept.
    gap = None
    if row["gap_cursor"] is not None and row["gap_at"] >= at - machine_events.RETENTION_S:
        gap = machine_events.seal_cursor(store, p, row["gap_cursor"], row["gap_at"])
    return {"id": row["id"], "workspace_id": row["workspace_id"], "url": row["url"], "description": row["description"],
            "events": _events_of(row), "status": row["status"], "disabled_reason": row["disabled_reason"],
            "status_changed_at": row["status_changed_at"], "missed_events_cursor": gap,
            "stalled_since": row["stalled_since"],
            # Whether the grant making this call is the one deliveries are decided for (see `resume`).
            "authorized_at": row["authorized_at"], "authorized_by_caller": _family(row) == p.get("grant_family"),
            "created_at": row["created_at"], "updated_at": row["updated_at"], "version": row["version"],
            "consecutive_failures": row["consecutive_failures"], "last_success_at": row["last_success_at"],
            "last_failure_at": row["last_failure_at"],
            "previous_secret_expires_at": prev if row.get("secret_prev_enc") and prev and prev > at else None}


def delivery_public(row: dict) -> dict:
    return {"id": row["id"], "webhook_id": row["event_id"], "event_type": row["event_type"], "status": row["status"],
            "attempts": row["attempts"], "last_code": row["last_code"], "last_error": row["last_error"],
            "duration_ms": row["duration_ms"], "created_at": row["created_at"], "delivered_at": row["delivered_at"],
            "next_attempt_at": row["next_attempt_at"] if row["status"] == "pending" else None,
            "redelivery": bool(row["redelivery_of"]), "redelivery_of": row["redelivery_of"]}


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class Webhooks:
    """Subscriptions, their queue and the sender. One per process; `tick` is the worker's step."""

    def __init__(self, caps, *, clock=None, rng=None, worker_id: str | None = None, concurrency: int = CONCURRENCY):
        self.caps, self.store, self.identity = caps, caps.store, caps.identity
        self.clock = clock or now
        self.rng = rng or random
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{_secrets.token_hex(3)}"
        self.concurrency = max(1, min(16, concurrency))
        self.last_error: str | None = None
        self._stop = False
        self._inflight: set[str] = set()
        self._inflight_lock = threading.Lock()
        self._pings = threading.BoundedSemaphore(PING_CONCURRENCY)
        self._validations = threading.BoundedSemaphore(VALIDATION_CONCURRENCY)
        self._hosts: dict[str, collections.deque] = {}
        self._hosts_lock = threading.Lock()

    def stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------ whose it is
    @staticmethod
    def _holder(p: dict) -> tuple[str, str, str]:
        return p["tenant"], p["client_id"], p["user_id"]

    def _row(self, p: dict, sid: str) -> dict:
        """A subscription of this grant. Another client's, another person's or another workspace's
        is the same 404 as one that does not exist."""
        row = self.store.one("SELECT * FROM suite_webhook_subscriptions WHERE id=? AND deleted_at IS NULL", (sid,))
        if not row or (row["workspace_id"], row["client_id"], row["user_id"]) != self._holder(p):
            raise ServiceError(404, "subscription_not_found", "订阅不存在")
        return row

    @staticmethod
    def _descriptor(p: dict) -> str:
        return authority.dumps(authority.describe(p))

    def _principal(self, sub: dict) -> dict:
        """The grant behind a subscription as it stands now, as the feed needs a caller described —
        or a refusal naming why it no longer covers the subscription (one of `ENDED`)."""
        gone = lambda code: ServiceError(403, code, "该订阅背后的授权已不可用")  # noqa: E731
        desc = authority.loads(sub["created_by"])
        if (not isinstance(desc, dict) or desc.get("kind") != "delegated" or desc.get("client_id") != sub["client_id"]
                or desc.get("user_id") != sub["user_id"] or desc.get("tenant") != sub["workspace_id"]):
            raise gone("unknown_principal")
        user = self.identity.get_user(sub["user_id"])
        if not user or user["status"] != "active":
            raise gone("account_unavailable")
        space = self.identity.get_workspace(sub["workspace_id"])
        if not space or space.get("deleted_at") or space["status"] != "active":
            raise gone("workspace_unavailable")
        from synwevo_core.organizations import workspace_access_failure
        if workspace_access_failure(self.store, sub["workspace_id"], sub["user_id"]) \
                or not self.identity.membership(sub["workspace_id"], sub["user_id"]):
            raise gone("not_a_member")
        # The client, its tokens, and whether it could still renew them now: a push never meets
        # the refresh at which an ended sign-in or a lost entitlement would stop a pulling client.
        scopes = self.caps.grant_scopes(desc, renewable=True)
        return {"tenant": sub["workspace_id"], "client_id": sub["client_id"], "user_id": sub["user_id"], "scopes": scopes}

    def _binding(self, p: dict, events: list[str]) -> str:
        """The descriptor that makes the caller's grant the one answering for a subscription: it
        must be renewable (a subscription outlives an access token), and hold what the filter
        names — as on creation, whichever call binds."""
        self.caps.grant_scopes(authority.describe(p), renewable=True)
        normalize_events(self.caps.registry, events, p["scopes"])
        return self._descriptor(p)

    @contextlib.contextmanager
    def _checking(self):
        """An address checked for a management call resolves a name on the request's thread. Only
        so many at once: the resolvers are shared with every delivery of every workspace."""
        if not self._validations.acquire(blocking=False):
            raise ServiceError(429, "destination_check_busy", "正在检查的回调地址过多，请稍后再试", {"retry_after": 2})
        try:
            yield
        finally:
            self._validations.release()

    def _address(self, url) -> str:
        with self._checking():
            return validate_url(url)

    def _journal(self, sid: str) -> None:
        """Written before a change that takes something back — a pause, another address, another
        secret, a removal: restored from a backup taken before it, the subscription comes back
        disabled rather than as it was. A change that cannot be made durable is not made."""
        try:
            journal.record(self.store, "webhook_disabled", subscription_id=sid)
        except Exception as e:  # noqa: BLE001
            raise ServiceError(503, "journal_unavailable", "暂时无法安全地保存这项更改，请稍后再试") from e

    @staticmethod
    def _note(note, p: dict, action: str, sid: str, url: str, **meta) -> None:
        """The audit record of a change to where a person's events are sent: who, for which client,
        to which host. Never the secret, and not the address's path — it may carry a token."""
        if note is not None:
            note(action, sid, {"host": urlsplit(url).hostname, **{k: v for k, v in meta.items() if v is not None},
                               "delegation": {"kind": "oauth_client", "client_id": p["client_id"], "grant": p.get("grant_family")}})

    #: Where a subscription stands as it stops. The rule is the tables' own (the journal's replay
    #: of a stop over a restored database follows it too), so it lives beside them.
    _stopping = staticmethod(stopping)

    # ------------------------------------------------------------------ management
    @staticmethod
    def _check_fields(body, allowed: tuple) -> None:
        if not isinstance(body, dict):
            raise ServiceError(400, "invalid_body", "请求体须为 JSON 对象")
        for k in body:
            if k not in allowed and k != "version":
                raise ServiceError(400, "unknown_field", f"不支持的字段：{k}", {"field": k})

    @staticmethod
    def _description(v) -> str:
        if v is None:
            return ""
        if not isinstance(v, str) or len(v) > MAX_DESCRIPTION_LEN:
            raise ServiceError(400, "invalid_description", f"description 须为不超过 {MAX_DESCRIPTION_LEN} 个字符的文本",
                               {"field": "description"})
        return v.strip()

    def _room(self, c, p: dict, *, besides: str = "") -> None:
        """Refuse when the client or the workspace already has as many live subscriptions as it
        may. Disabled ones are not counted: nobody may be left to remove them."""
        live = ("SELECT COUNT(*) AS n FROM suite_webhook_subscriptions WHERE workspace_id=? AND deleted_at IS NULL "
                "AND status IN ('active','paused') AND id<>?")
        if c.execute(live + " AND client_id=?", (p["tenant"], besides, p["client_id"])).fetchone()["n"] >= MAX_PER_CLIENT:
            raise ServiceError(409, "subscription_limit_reached", f"每个应用在一个工作空间最多 {MAX_PER_CLIENT} 个启用的订阅",
                               {"limit": MAX_PER_CLIENT, "scope": "client"})
        ceiling = workspace_limit()
        if c.execute(live, (p["tenant"], besides)).fetchone()["n"] >= ceiling:
            # Says nothing of whose they are: the number alone.
            raise ServiceError(409, "workspace_subscription_limit_reached", f"该工作空间的订阅数已达上限（{ceiling}）",
                               {"limit": ceiling})

    def _address_free(self, c, p: dict, url: str, *, besides: str = "") -> None:
        taken = c.execute("SELECT id FROM suite_webhook_subscriptions WHERE workspace_id=? AND client_id=? AND user_id=? "
                          "AND url=? AND deleted_at IS NULL AND id<>?", (*self._holder(p), url, besides)).fetchone()
        if taken:
            raise ServiceError(409, "subscription_exists", "这个回调地址已有订阅；修改它，或轮换它的密钥",
                               {"id": taken["id"]})

    def create(self, p: dict, body: dict, *, note=None) -> dict:
        self._check_fields(body, WRITABLE)
        url = self._address(body.get("url"))
        events = normalize_events(self.caps.registry, body.get("events"), p["scopes"])
        description = self._description(body.get("description"))
        descriptor = self._binding(p, events)
        plain = new_secret()
        sid, t = new_id("whsub"), self.clock()
        with self.store.tx() as c:
            # A lost answer and a second try do not leave two: the first is named instead.
            self._address_free(c, p, url)
            mine = c.execute("SELECT COUNT(*) AS n FROM suite_webhook_subscriptions WHERE workspace_id=? AND client_id=? "
                             "AND user_id=? AND deleted_at IS NULL", self._holder(p)).fetchone()["n"]
            if mine >= MAX_PER_GRANT:
                raise ServiceError(409, "subscription_limit_reached", f"每个授权最多 {MAX_PER_GRANT} 个订阅",
                                   {"limit": MAX_PER_GRANT, "scope": "grant"})
            self._room(c, p)
            top = c.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM app_events").fetchone()["s"]
            c.execute("INSERT INTO suite_webhook_subscriptions (id, workspace_id, client_id, user_id, created_by, authorized_at, "
                      "url, description, events, status, status_changed_at, secret_enc, version, cursor, cursor_at, "
                      "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,1,?,?,?,?)",
                      (sid, *self._holder(p), descriptor, t, url, description, json.dumps(events), t,
                       sealed.seal(self.store, "webhook-secret", plain), top, t, t, t))
            self._note(note, p, "suite.webhook.create", sid, url, events=events)
        return {**self.get(p, sid), "secret": plain}

    def list(self, p: dict) -> list[dict]:
        rows = self.store.all("SELECT * FROM suite_webhook_subscriptions WHERE workspace_id=? AND client_id=? AND user_id=? "
                              "AND deleted_at IS NULL ORDER BY created_at, id", self._holder(p))
        t = self.clock()
        return [_public(self.store, r, t, p) for r in rows]

    def get(self, p: dict, sid: str) -> dict:
        return _public(self.store, self._row(p, sid), self.clock(), p)

    def update(self, p: dict, sid: str, body: dict, expected_version, *, note=None) -> dict:
        self._check_fields(body, WRITABLE)
        row = self._row(p, sid)
        if expected_version is not None:
            try:
                expected_version = int(expected_version)
            except (TypeError, ValueError):
                raise ServiceError(400, "invalid_version", "version 须为整数", {"field": "version"}) from None
            if expected_version != row["version"]:
                raise ServiceError(409, "version_conflict", "订阅已被修改，请重新读取后再改",
                                   {"current_version": row["version"]})
        values = {}
        if "url" in body:
            values["url"] = self._address(body["url"])
        if "events" in body:
            values["events"] = json.dumps(normalize_events(self.caps.registry, body["events"], p["scopes"]))
        if values:
            # Where it goes or what it asks for changed: whoever changed it answers for it from now
            # on, for the whole filter. A new description alone changes nothing about who answers.
            values["created_by"] = self._binding(p, json.loads(values["events"]) if "events" in values else _events_of(row))
            values["authorized_at"] = self.clock()
        if "description" in body:
            values["description"] = self._description(body["description"])
        if values:
            moved = "url" in values and values["url"] != row["url"]
            if moved:
                self._journal(sid)                          # a restore must not send to the address it left
            sets = ", ".join(f"{k}=?" for k in values)       # keys of the closed set above
            with self.store.tx() as c:
                if moved:
                    self._address_free(c, p, values["url"], besides=sid)
                n = c.execute(f"UPDATE suite_webhook_subscriptions SET {sets}, updated_at=?, version=version+1 "
                              "WHERE id=? AND version=? AND deleted_at IS NULL",
                              (*values.values(), self.clock(), sid, row["version"])).rowcount
                if n and "created_by" in values:
                    self._note(note, p, "suite.webhook.update", sid, values.get("url", row["url"]),
                               events=json.loads(values["events"]) if "events" in values else None,
                               previous_host=urlsplit(row["url"]).hostname if moved else None)
            if not n:
                raise ServiceError(409, "version_conflict", "订阅已被修改，请重新读取后再改")
        return self.get(p, sid)

    def delete(self, p: dict, sid: str, *, note=None) -> dict:
        row = self._row(p, sid)
        self._journal(sid)
        self._remove(sid, "deleted", lambda: self._note(note, p, "suite.webhook.delete", sid, row["url"]))
        return {"deleted": sid}

    def _remove(self, sid: str, reason: str, noted=None, *, orphaned_before: float | None = None) -> None:
        """Remove a subscription, its secret and its queue. `orphaned_before`, the housekeeping's:
        only if it still is what the sweep listed — disabled because its grant ended, since before
        that time. One its holder has resumed in between is theirs again and stays whole."""
        t = self.clock()
        still, args = "", ()
        if orphaned_before is not None:
            still = " AND status='disabled' AND disabled_reason LIKE 'authorization_revoked:%' AND status_changed_at<?"
            args = (orphaned_before,)
        with self.store.tx() as c:
            if noted is not None:
                noted()
            # The secret goes with it: nothing signs for a removed subscription again.
            if c.execute("UPDATE suite_webhook_subscriptions SET status='disabled', disabled_reason=?, deleted_at=?, "
                         "status_changed_at=?, updated_at=?, version=version+1, secret_enc=?, secret_prev_enc=NULL, "
                         "secret_prev_expires_at=NULL WHERE id=? AND deleted_at IS NULL" + still,
                         (reason, t, t, t, sealed.seal(self.store, "webhook-secret", ""), sid, *args)).rowcount:
                c.execute("UPDATE suite_webhook_deliveries SET status='dropped', last_error='subscription_deleted', "
                          "next_attempt_at=NULL WHERE subscription_id=? AND status='pending'", (sid,))

    def pause(self, p: dict, sid: str, *, note=None) -> dict:
        row = self._row(p, sid)
        if row["status"] == "disabled":
            raise ServiceError(409, "subscription_disabled", "订阅已停用；用 resume 重新启用", {"reason": row["disabled_reason"]})
        if row["status"] == "active":
            self._journal(sid)
            t = self.clock()
            with self.store.tx() as c:
                current = c.execute("SELECT * FROM suite_webhook_subscriptions WHERE id=? AND status='active' "
                                    "AND deleted_at IS NULL", (sid,)).fetchone()
                if current:
                    c.execute("UPDATE suite_webhook_subscriptions SET status='paused', status_changed_at=?, updated_at=?, "
                              "gap_cursor=?, gap_at=?, version=version+1 WHERE id=?", (t, t, *self._stopping(c, current), sid))
                    # Pausing stops what is already queued too: no retries for days to a paused endpoint.
                    c.execute("UPDATE suite_webhook_deliveries SET status='dropped', last_error='subscription_paused', "
                              "next_attempt_at=NULL WHERE subscription_id=? AND status='pending'", (sid,))
                    self._note(note, p, "suite.webhook.pause", sid, row["url"])
        return self.get(p, sid)

    def resume(self, p: dict, sid: str, *, note=None) -> dict:
        """Make it active under the caller's grant. A paused or disabled subscription starts again
        from the present — what happened while it was stopped is in the feed, not delivered
        afterwards. One that is already active keeps its place and its queue: the call changes
        only which grant answers for it, and nothing at all when that is already the caller's."""
        row = self._row(p, sid)
        if row["status"] == "active" and _family(row) == p.get("grant_family"):
            return self.get(p, sid)
        descriptor = self._binding(p, _events_of(row))
        t = self.clock()
        if row["status"] == "active":
            with self.store.tx() as c:
                if c.execute("UPDATE suite_webhook_subscriptions SET created_by=?, authorized_at=?, updated_at=?, "
                             "version=version+1 WHERE id=? AND status='active' AND deleted_at IS NULL",
                             (descriptor, t, t, sid)).rowcount:
                    self._note(note, p, "suite.webhook.resume", sid, row["url"], rebound=True)
            return self.get(p, sid)
        try:
            with self._checking():
                _resolve_now(row["url"])
        except UrlRejected as e:
            raise ServiceError(400, "invalid_url", str(e), {"field": "url"}) from e
        with self.store.tx() as c:
            if row["status"] == "disabled":
                self._room(c, p, besides=sid)
            top = c.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM app_events").fetchone()["s"]
            if c.execute("UPDATE suite_webhook_subscriptions SET status='active', disabled_reason=NULL, status_changed_at=?, "
                         "consecutive_failures=0, failing_since=NULL, stalled_since=NULL, created_by=?, authorized_at=?, "
                         "cursor=?, cursor_at=?, updated_at=?, version=version+1 WHERE id=? AND status<>'active' "
                         "AND deleted_at IS NULL", (t, descriptor, t, top, t, t, sid)).rowcount:
                self._note(note, p, "suite.webhook.resume", sid, row["url"], was=row["status"], reason=row["disabled_reason"])
        return self.get(p, sid)

    def rotate_secret(self, p: dict, sid: str, overlap_s, *, note=None) -> dict:
        """A new signing secret, shown once. The one it replaces keeps signing alongside it for
        `overlap_s`. Rotating again inside that time replaces only the newest secret: the one the
        receiver is known to hold, and its end, stay — so repeating a rotation whose answer was
        lost does not cut the receiver off. ``overlap_s: 0`` ends every earlier secret at once."""
        if overlap_s is None:
            overlap_s = DEFAULT_OVERLAP_S
        try:
            overlap = int(overlap_s)
        except (TypeError, ValueError):
            overlap = -1
        if isinstance(overlap_s, bool) or not 0 <= overlap <= MAX_OVERLAP_S:
            raise ServiceError(400, "invalid_overlap", f"overlap_s 须为 0–{MAX_OVERLAP_S} 的整数", {"field": "overlap_s"})
        row = self._row(p, sid)
        descriptor = self._binding(p, _events_of(row))
        plain, t = new_secret(), self.clock()
        if not overlap:
            prev, prev_until = None, None
        elif row["secret_prev_enc"] and (row["secret_prev_expires_at"] or 0) > t:
            prev, prev_until = row["secret_prev_enc"], row["secret_prev_expires_at"]
        else:
            prev, prev_until = row["secret_enc"], t + overlap
        self._journal(sid)                                  # a restore must not sign with the secret this one replaces
        with self.store.tx() as c:
            n = c.execute("UPDATE suite_webhook_subscriptions SET secret_enc=?, secret_prev_enc=?, secret_prev_expires_at=?, "
                          "created_by=?, authorized_at=?, updated_at=?, version=version+1 "
                          "WHERE id=? AND version=? AND deleted_at IS NULL",
                          (sealed.seal(self.store, "webhook-secret", plain), prev, prev_until, descriptor, t, t, sid,
                           row["version"])).rowcount
            if n:
                self._note(note, p, "suite.webhook.rotate_secret", sid, row["url"], overlap_s=overlap)
        if not n:
            raise ServiceError(409, "version_conflict", "订阅已被修改，请重新读取后再轮换")
        return {**self.get(p, sid), "secret": plain}

    def ping(self, p: dict, sid: str) -> dict:
        """One signed ``webhook.ping`` now, through the same checks and the same connection as a
        real delivery. It carries no event, is not retried and does not count for or against the
        subscription's health — and it is rationed: the destination never proved it wants these."""
        row = self._row(p, sid)
        if row["status"] == "disabled":
            raise ServiceError(409, "subscription_disabled", "订阅已停用；用 resume 重新启用", {"reason": row["disabled_reason"]})
        limited = lambda wait, why: ServiceError(  # noqa: E731
            429, "ping_rate_limited", "测试事件发送过于频繁，请稍后再试",
            {"retry_after": wait, "reason": why, "min_interval_s": PING_MIN_INTERVAL_S, "per_hour": PING_PER_HOUR})
        if not self._pings.acquire(blocking=False):
            raise limited(5, "busy")
        try:
            t, did = self.clock(), new_id("whdel")
            with self.store.tx() as c:
                recent = c.execute("SELECT created_at FROM suite_webhook_deliveries WHERE subscription_id=? AND event_type=? "
                                   "AND created_at>? ORDER BY created_at DESC", (sid, PING_EVENT, t - 3600)).fetchall()
                if recent and t - recent[0]["created_at"] < PING_MIN_INTERVAL_S:
                    raise limited(PING_MIN_INTERVAL_S - (t - recent[0]["created_at"]), "interval")
                if len(recent) >= PING_PER_HOUR:
                    raise limited(recent[-1]["created_at"] + 3600 - t, "hourly")
                # Queued already claimed and counted, as `claim_next` would hand it over.
                c.execute("INSERT INTO suite_webhook_deliveries (id, subscription_id, workspace_id, event_id, event_type, "
                          "status, attempts, next_attempt_at, created_at, claimed_by, claimed_until) "
                          "VALUES (?,?,?,?,?,'pending',1,?,?,?,?)",
                          (did, sid, row["workspace_id"], new_id("evt"), PING_EVENT, t + LEASE_S, t, self.worker_id,
                           t + LEASE_S))
            self.send_one(self.store.one("SELECT * FROM suite_webhook_deliveries WHERE id=?", (did,)))
        finally:
            self._pings.release()
        return delivery_public(self.store.one("SELECT * FROM suite_webhook_deliveries WHERE id=?", (did,)))

    def _log_holder(self, p: dict, sid: str) -> bytes:
        return json.dumps([*self._holder(p), sid], separators=(",", ":")).encode("utf-8")

    def deliveries(self, p: dict, sid: str, status: str | None = None, cursor: str | None = None, limit=None) -> dict:
        self._row(p, sid)
        lim = 50 if limit is None else limit
        if type(lim) is not int or not 1 <= lim <= 100:
            raise ServiceError(400, "invalid_limit", "limit 须在 1–100 之间", {"field": "limit", "minimum": 1, "maximum": 100})
        if status is not None and status not in DELIVERY_STATUSES:
            raise ServiceError(400, "invalid_status", "未知的投递状态", {"field": "status", "allowed": list(DELIVERY_STATUSES)})
        before = None
        if cursor:
            opened = sealed_token.unseal(self.store, _CURSOR_LABEL, cursor, self._log_holder(p, sid), max_length=128)
            if opened is None or len(opened) != 8:
                raise ServiceError(400, "invalid_cursor", "游标无效；请不带 cursor 重新读取", {"field": "cursor"})
            before = struct.unpack(">Q", opened)[0]
        sql, args = "SELECT rowid AS _n, * FROM suite_webhook_deliveries WHERE subscription_id=?", [sid]
        if status:
            sql, args = sql + " AND status=?", args + [status]
        if before is not None:
            sql, args = sql + " AND rowid<?", args + [before]
        rows = self.store.all(sql + " ORDER BY rowid DESC LIMIT ?", (*args, lim + 1))
        more, rows = len(rows) > lim, rows[:lim]
        # Row numbers count every subscription's deliveries; the cursor does not show them.
        nxt = sealed_token.seal(self.store, _CURSOR_LABEL, struct.pack(">Q", rows[-1]["_n"]),
                                self._log_holder(p, sid)) if more else None
        return {"items": [delivery_public(r) for r in rows], "next_cursor": nxt, "has_more": more}

    def delivery(self, p: dict, sid: str, did: str) -> dict:
        self._row(p, sid)
        row = self.store.one("SELECT * FROM suite_webhook_deliveries WHERE id=? AND subscription_id=?", (did, sid))
        if not row:
            raise ServiceError(404, "delivery_not_found", "投递记录不存在")
        return delivery_public(row)

    def redeliver(self, p: dict, sid: str, did: str) -> dict:
        """Queue one more delivery of an event this subscription was sent before. It is not a way
        around anything: the new delivery goes through the same checks before it is sent, and an
        event older than the feed serves is not sent again."""
        sub = self._row(p, sid)
        orig = self.store.one("SELECT * FROM suite_webhook_deliveries WHERE id=? AND subscription_id=?", (did, sid))
        if not orig:
            raise ServiceError(404, "delivery_not_found", "投递记录不存在")
        if sub["status"] != "active":
            raise ServiceError(409, "subscription_not_active", "订阅未启用，不能重新投递", {"status": sub["status"]})
        if orig["event_type"] == PING_EVENT:
            raise ServiceError(409, "not_redeliverable", "测试事件不能重新投递；请再发一次 ping")
        t = self.clock()
        event = self.store.one("SELECT created_at FROM app_events WHERE id=? AND workspace_id=?",
                               (orig["event_id"], sub["workspace_id"]))
        if not event:
            raise ServiceError(409, "event_missing", "该事件已不存在，不能重新投递")
        if t - event["created_at"] > REDELIVERY_WINDOW_S:
            raise ServiceError(409, "redelivery_window_expired", "该事件已超出可重新投递的期限",
                               {"window_days": int(REDELIVERY_WINDOW_S // 86400)})
        new = new_id("whdel")
        with self.store.tx() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM suite_webhook_deliveries WHERE subscription_id=? AND event_id=? "
                          "AND redelivery_of IS NOT NULL", (sid, orig["event_id"])).fetchone()["n"]
            if n >= MAX_REDELIVERIES:
                raise ServiceError(409, "redelivery_limit_reached", f"同一事件最多重新投递 {MAX_REDELIVERIES} 次",
                                   {"limit": MAX_REDELIVERIES})
            c.execute("INSERT INTO suite_webhook_deliveries (id, subscription_id, workspace_id, event_id, event_seq, event_type, "
                      "status, attempts, next_attempt_at, created_at, redelivery_of) VALUES (?,?,?,?,?,?,'pending',0,?,?,?)",
                      (new, sid, sub["workspace_id"], orig["event_id"], orig["event_seq"], orig["event_type"], t, t, did))
        return delivery_public(self.store.one("SELECT * FROM suite_webhook_deliveries WHERE id=?", (new,)))

    # ------------------------------------------------------------------ disabling
    def disable(self, sid: str, reason: str, *, unsent: int | None = None, version: int | None = None) -> bool:
        """Disable a subscription and stop what is queued for it; journalled, so restoring an older
        database cannot bring it back to life. `unsent`: the position of the event the receiver
        would not take, when that is why.

        `version`: that of the row the decision was made on. The dispatcher judges rows it read a
        moment — or a request of fifteen seconds — earlier; a subscription that has changed since
        (bound to another grant, moved to another address, stopped and resumed) is no longer the
        one that was judged. Nothing is done to it or to its queue, and that is not an error:
        whoever looks next decides again, on the row as it stands."""
        t = self.clock()
        judged, args = (" AND version=?", (version,)) if version is not None else ("", ())
        with self.store.tx() as c:
            current = c.execute("SELECT * FROM suite_webhook_subscriptions WHERE id=? AND status!='disabled'" + judged,
                                (sid, *args)).fetchone()
            if not current and version is not None:
                return False
            n = 0
            if current:
                n = c.execute("UPDATE suite_webhook_subscriptions SET status='disabled', disabled_reason=?, status_changed_at=?, "
                              "updated_at=?, gap_cursor=?, gap_at=?, version=version+1 WHERE id=?" + judged,
                              (reason, t, t, *self._stopping(c, current, unsent), sid, *args)).rowcount
            c.execute("UPDATE suite_webhook_deliveries SET status='dropped', last_error=?, next_attempt_at=NULL "
                      "WHERE subscription_id=? AND status='pending'", (f"subscription_disabled:{reason}", sid))
        if n:
            try:
                journal.record(self.store, "webhook_disabled", subscription_id=sid)
            except Exception as e:  # noqa: BLE001 — the database is already right; say that the journal is not
                self.last_error = f"安全日志写入失败（webhook_disabled {sid}）：{e}"
                log.error("%s", self.last_error)
        return bool(n)

    # ------------------------------------------------------------------ fan-out
    def fan_out(self, limit: int = FAN_SUBSCRIPTIONS) -> int:
        """Queue deliveries for what happened since each subscription last looked. Returns how many
        were queued. A subscription with nothing new in its workspace costs an index probe and no
        write, whatever other workspaces do; one whose queue is full is not looked at until some of
        it has settled (the cursor waits: `MAX_PENDING`)."""
        top = self.store.one("SELECT COALESCE(MAX(seq), 0) AS s FROM app_events")["s"]
        # Something new in its workspace — or a log that no longer agrees with its cursor (a
        # restore): shorter than it, or regrown under it.
        subs = self.store.all(
            "SELECT * FROM suite_webhook_subscriptions AS s WHERE status='active' AND deleted_at IS NULL AND (cursor>? "
            " OR EXISTS (SELECT 1 FROM app_events AS e INDEXED BY app_events_workspace "
            "            WHERE e.workspace_id=s.workspace_id AND e.seq>s.cursor) "
            " OR (SELECT e.created_at FROM app_events AS e INDEXED BY app_events_workspace WHERE e.workspace_id=s.workspace_id "
            "     AND e.seq<=s.cursor ORDER BY e.seq DESC LIMIT 1)>s.cursor_at+?) "
            "AND (SELECT COUNT(*) FROM suite_webhook_deliveries AS d WHERE d.subscription_id=s.id AND d.status='pending')<? "
            "ORDER BY scanned_at, id LIMIT ?", (top, machine_events.CLOCK_SLACK_S, MAX_PENDING, limit))
        queued = 0
        for sub in subs:
            if self._stop:
                break
            try:
                queued += self._fan_one(sub, top)
            except Exception:  # noqa: BLE001 — one subscription's fault must not stop the others
                # It keeps its place and is tried again; nothing is passed over and nothing is sent undecided.
                log.exception("webhook fan-out failed for %s", sub["id"])
                self.store.execute("UPDATE suite_webhook_subscriptions SET scanned_at=? WHERE id=?", (self.clock(), sub["id"]))
        return queued

    def _decide(self, sub: dict, p: dict, rows: list[dict]) -> tuple[list, list[dict], bool]:
        """Which of these rows the grant may be told about now: (what to queue, the rows that were
        decided, whether one could not be).

        An application's per-record decision that fails is not a refusal, and passing the event
        over would lose it: the batch is then decided row by row and stops before the first row
        that cannot be decided. The subscription waits there, visibly (`stalled_since`)."""
        filters = _events_of(sub)
        match = lambda etype: matches(filters, etype)       # noqa: E731
        # A subscription only ever sees events from after it existed, whatever a restore did to its cursor.
        fresh = [r for r in rows if r["created_at"] >= sub["created_at"]]
        if not fresh:
            return [], rows, False
        try:
            return machine_events.visible_items(self.caps, p, fresh, match=match), rows, False
        except Exception:  # noqa: BLE001 — decided again below, one at a time
            pass
        visible, decided = [], []
        for r in rows:
            if r["created_at"] >= sub["created_at"]:
                try:
                    visible += machine_events.visible_items(self.caps, p, [r], match=match)
                except Exception:  # noqa: BLE001
                    if sub["stalled_since"] is None:
                        log.exception("webhook subscription %s cannot decide event %s (%s); it waits there",
                                      sub["id"], r["id"], r["type"])
                    return visible, decided, True
            decided.append(r)
        return visible, decided, False

    def _fan_one(self, sub: dict, top: int) -> int:
        t = self.clock()
        try:
            p = self._principal(sub)
        except ServiceError as gone:
            self.disable(sub["id"], f"authorization_revoked:{gone.code}", version=sub["version"])
            return 0
        position, at = sub["cursor"], sub["cursor_at"]
        if position > top:
            # The log is shorter than the cursor: an older backup was restored, and these positions
            # will be used again. The cursor comes back with the log.
            position, at = top, t
        else:
            under = self.store.one("SELECT created_at FROM app_events WHERE seq<=? ORDER BY seq DESC LIMIT 1", (position,))
            if under and under["created_at"] > sub["cursor_at"] + machine_events.CLOCK_SLACK_S:
                # …or it has already grown past the cursor again: the rows it stands on were written
                # after it was. Go back to before the first of them rather than pass them over.
                first = self.store.one("SELECT MIN(seq) AS s FROM app_events WHERE seq<=? AND created_at>?",
                                       (position, sub["cursor_at"] + machine_events.CLOCK_SLACK_S))["s"]
                position = max(0, (first or 1) - 1)
        rows = self.store.all(
            "SELECT seq, id, type, version, app, workspace_id, object_kind, object_id, run_id, correlation_id, data, created_at "
            "FROM app_events INDEXED BY app_events_workspace WHERE workspace_id=? AND seq>? AND seq<=? ORDER BY seq LIMIT ?",
            (sub["workspace_id"], position, top, FAN_ROWS))
        if not rows and position == sub["cursor"]:
            return 0                                        # nothing of its workspace: nothing to write
        if rows and t - rows[0]["created_at"] > machine_events.RETENTION_S + machine_events.CURSOR_GRACE_S:
            # Further behind than the feed keeps events (a receiver too slow for what it asked for, a
            # long outage, an event that could never be decided): what follows the cursor can no
            # longer be served by either channel. Stopped with the reason, not skipped in silence.
            self.disable(sub["id"], "backlog_expired", version=sub["version"])
            return 0
        drained = len(rows) < FAN_ROWS
        visible, decided, stalled = self._decide(sub, p, rows)
        waiting = self.store.one("SELECT COUNT(*) AS n FROM suite_webhook_deliveries WHERE subscription_id=? AND status='pending'",
                                 (sub["id"],))["n"]
        if len(visible) > max(0, MAX_PENDING - waiting):
            # The queue is full: the rest stay in the log, behind the cursor, until some of it settles.
            visible = visible[:max(0, MAX_PENDING - waiting)]
            last = visible[-1][0]["seq"] if visible else position
            decided = [r for r in decided if r["seq"] <= last]
        if len(decided) < len(rows):
            drained = False
        if drained:
            position, at = top, t
        elif decided:
            position, at = decided[-1]["seq"], decided[-1]["created_at"]
        with self.store.tx() as c:
            current = c.execute("SELECT status, version, cursor, deleted_at FROM suite_webhook_subscriptions WHERE id=?",
                                (sub["id"],)).fetchone()
            if (not current or current["deleted_at"] or current["status"] != "active"
                    or current["version"] != sub["version"] or current["cursor"] != sub["cursor"]):
                return 0                                    # it changed while this was decided: decide again
            queued = 0
            for row, _item in visible:
                queued += c.execute(
                    "INSERT OR IGNORE INTO suite_webhook_deliveries (id, subscription_id, workspace_id, event_id, event_seq, "
                    "event_type, status, attempts, next_attempt_at, created_at) VALUES (?,?,?,?,?,?,'pending',0,?,?)",
                    (new_id("whdel"), sub["id"], sub["workspace_id"], row["id"], row["seq"], row["type"], t, t)).rowcount
            c.execute("UPDATE suite_webhook_subscriptions SET cursor=?, cursor_at=?, scanned_at=?, stalled_since=? WHERE id=?",
                      (position, at, t, (sub["stalled_since"] or t) if stalled else None, sub["id"]))
        return queued

    # ------------------------------------------------------------------ sending
    def claim_next(self, exclude: tuple = ()) -> dict | None:
        """Claim one due delivery. The select and the claim are one ``BEGIN IMMEDIATE`` transaction,
        so two workers sharing a data directory never both send it while the claim holds.

        Only a subscription's oldest pending delivery can be claimed: while it is being sent, or
        waits for its retry, nothing queued after it goes out. That is the order, and it is decided
        here, in the database, so it holds for every process.

        The claim counts the attempt and schedules the next look for when the claim ends, before
        anything is sent: an attempt that never reports back (the process died) is still an attempt,
        and a delivery cannot be claimed for ever without reaching the end of its schedule."""
        t = self.clock()
        sql = ("SELECT id, attempts FROM suite_webhook_deliveries AS d WHERE status='pending' AND next_attempt_at<=? "
               "AND (claimed_until IS NULL OR claimed_until<?) AND NOT EXISTS (SELECT 1 FROM suite_webhook_deliveries AS head "
               "WHERE head.subscription_id=d.subscription_id AND head.status='pending' AND head.rowid<d.rowid)")
        args: list = [t, t]
        if exclude:
            sql += f" AND subscription_id NOT IN ({','.join('?' * len(exclude))})"
            args += list(exclude)
        with self.store.tx() as c:
            while True:
                row = c.execute(sql + " ORDER BY next_attempt_at, rowid LIMIT 1", args).fetchone()
                if not row:
                    return None
                if row["attempts"] >= MAX_ATTEMPTS:
                    c.execute("UPDATE suite_webhook_deliveries SET status='failed', last_error='attempts_exhausted', "
                              "next_attempt_at=NULL, claimed_by=NULL, claimed_until=NULL WHERE id=?", (row["id"],))
                    continue
                c.execute("UPDATE suite_webhook_deliveries SET attempts=attempts+1, next_attempt_at=?, claimed_by=?, "
                          "claimed_until=? WHERE id=?", (t + LEASE_S, self.worker_id, t + LEASE_S, row["id"]))
                return dict(c.execute("SELECT * FROM suite_webhook_deliveries WHERE id=?", (row["id"],)).fetchone())

    def _owns_claim(self, d: dict) -> bool:
        return bool(self.store.one("SELECT 1 FROM suite_webhook_deliveries WHERE id=? AND status='pending' AND claimed_by=? "
                                   "AND claimed_until=? AND claimed_until>?",
                                   (d["id"], self.worker_id, d.get("claimed_until"), self.clock())))

    def _not_sent(self, d: dict) -> bool:
        """The claim is no longer this worker's, and nothing was sent. If that is because the
        delivery was stopped meanwhile (its subscription paused, disabled or deleted), the attempt
        the claim counted did not happen: it is taken back and the claim let go. A claim that ran
        out or was taken over is another worker's row now, and is left as it is."""
        self.store.execute("UPDATE suite_webhook_deliveries SET attempts=MAX(0, attempts-1), claimed_by=NULL, "
                           "claimed_until=NULL WHERE id=? AND status='dropped' AND claimed_by=? AND claimed_until=?",
                           (d["id"], self.worker_id, d.get("claimed_until")))
        return False

    def _sendable(self, d: dict) -> tuple[dict | None, bytes | None]:
        """The subscription and the body to send — or (None, None) once the delivery has been
        dropped because, asked about the present, it may not be sent."""
        sub = self.store.one("SELECT * FROM suite_webhook_subscriptions WHERE id=?", (d["subscription_id"],))
        ping = d["event_type"] == PING_EVENT
        if not sub or sub["deleted_at"]:
            self._drop(d, "subscription_deleted")
            return None, None
        if sub["status"] not in (("active", "paused") if ping else ("active",)):
            # In the words the stop itself uses for what it drops: a disable names its reason.
            self._drop(d, f"subscription_disabled:{sub['disabled_reason']}" if sub["status"] == "disabled"
                       else f"subscription_{sub['status']}")
            return None, None
        try:
            p = self._principal(sub)
        except ServiceError as gone:
            if self.disable(sub["id"], f"authorization_revoked:{gone.code}", version=sub["version"]):
                self._drop(d, f"subscription_disabled:authorization_revoked:{gone.code}")
            else:
                # It changed while its grant was being asked about (another grant bound it, or
                # somebody stopped it): the delivery is decided again on the subscription as it
                # stands. Nothing was tried.
                self._untried(d, "subscription_changed", 0)
            return None, None
        if ping:
            item = {"id": d["event_id"], "type": PING_EVENT, "version": 1, "app": None, "object": None, "refs": {},
                    "occurred_at": d["created_at"], "run_id": None, "correlation_id": None}
        else:
            event = self.store.one(
                "SELECT seq, id, type, version, app, workspace_id, object_kind, object_id, run_id, correlation_id, data, created_at "
                "FROM app_events WHERE id=?", (d["event_id"],))
            if not event or event["workspace_id"] != sub["workspace_id"]:
                self._drop(d, "event_missing")
                return None, None
            # What the feed would no longer serve is not pushed either, however it came to wait this long.
            if event["created_at"] < sub["created_at"] or self.clock() - event["created_at"] > REDELIVERY_WINDOW_S:
                self._drop(d, "event_out_of_window")
                return None, None
            filters = _events_of(sub)
            seen = machine_events.visible_items(self.caps, p, [event], match=lambda etype: matches(filters, etype))
            if not seen:
                # The scope went, the application or the record is no longer the person's to read,
                # or the filter no longer asks for it. Which, the log does not say.
                self._drop(d, "no_longer_readable")
                return None, None
            item = seen[0][1]
        # Signed with the rest: for which subscription — whose grant — this copy was decided.
        body = json.dumps({**item, "workspace_id": sub["workspace_id"], "subscription_id": sub["id"]},
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return sub, body

    def _host_wait(self, url: str) -> float:
        """Count one request to the address's host, or say how long until there is room for one.
        Whoever it is and however many subscriptions name it, a host gets `HOST_PER_MINUTE` from
        this process: an address never proved it wants the traffic."""
        host, at = (urlsplit(url).hostname or "").lower(), time.monotonic()
        with self._hosts_lock:
            sent = self._hosts.setdefault(host, collections.deque())
            while sent and at - sent[0] >= 60:
                sent.popleft()
            if len(sent) >= HOST_PER_MINUTE:
                return sent[0] + 60 - at
            sent.append(at)
            if len(self._hosts) > 4096:
                for name in [h for h, q in self._hosts.items() if not q or at - q[-1] >= 60]:
                    del self._hosts[name]
            return 0.0

    def send_one(self, d: dict) -> bool:
        """Send a claimed delivery; True when the receiver accepted it. Whatever goes wrong inside,
        the attempt ends recorded: an error of this server's own making is counted against the
        delivery's schedule (so it ends) and not against the receiver."""
        try:
            return self._send(d)
        except Exception:  # noqa: BLE001
            log.exception("webhook delivery %s failed inside the sender", d["id"])
            self._attempt_failed(d, None, d.get("attempts") or 1, None, "internal_error", retriable=d["event_type"] != PING_EVENT)
            return False

    def _send(self, d: dict) -> bool:
        if not self._owns_claim(d):
            return self._not_sent(d)
        sub, body = self._sendable(d)
        if sub is None:
            return False
        attempts = d.get("attempts") or 1                    # counted when it was claimed
        single = d["event_type"] == PING_EVENT               # a ping is one attempt, whatever happens to it
        local = lambda error, wait=LOCAL_RETRY_S: self._untried(d, error, wait)  # noqa: E731
        t = self.clock()
        try:
            keys = [sealed.unseal(self.store, "webhook-secret", sub["secret_enc"])]
            if sub["secret_prev_enc"] and (sub["secret_prev_expires_at"] or 0) > t:
                keys.append(sealed.unseal(self.store, "webhook-secret", sub["secret_prev_enc"]))
        except Exception as e:  # noqa: BLE001
            # A configuration fault, not the receiver's: keep the event and say why.
            self.last_error = f"无法读取签名密钥：{type(e).__name__}"
            return local("signing_key_unavailable", 300)
        ts = int(t)
        headers = {"Content-Type": "application/json", "User-Agent": "Synwevo-Webhook/1",
                   "webhook-id": d["event_id"], "webhook-timestamp": str(ts),
                   "webhook-signature": signature_header(d["event_id"], ts, body, keys),
                   # Which subscription this is for — hence which secret verifies it. The body says
                   # it too, under the signature: one receiver may serve several people's grants.
                   "X-Synwevo-Subscription": sub["id"],
                   "X-Synwevo-Delivery": d["id"], "X-Synwevo-Event": d["event_type"]}
        url = sub["url"]
        started = time.monotonic()
        deadline = None
        try:
            # Validate and connect to *the same* address; the hostname is never resolved twice.
            with RequestDeadline(TIMEOUT_S) as deadline:
                validated = resolve_checked(url, deadline=deadline.end)
                # DNS can take most of the attempt. A pause, a revocation or another claimant may
                # have intervened since the body was made: never connect on stale authority.
                if not self._owns_claim(d):
                    return self._not_sent(d)
                current, fresh = self._sendable(d)
                if current is None:
                    return False
                if current["version"] != sub["version"] or fresh != body:
                    return local("subscription_changed", 0)
                wait = self._host_wait(url)
                if wait:
                    return local("destination_busy", wait)
                req = urllib.request.Request(url, data=body, method="POST", headers=headers)
                with _pinned_opener(validated, deadline).open(req, timeout=deadline.remaining()) as resp:
                    code = resp.status                      # the body is never read, let alone stored
        except urllib.error.HTTPError as e:
            code = e.code
            wait = delivery.retry_after(e.headers, self.clock()) if code in (429, 503) else None
            e.close()
            err = "redirect_not_followed" if 300 <= code < 400 else "http_status"
            if code == 410 and not single and self._unchanged(sub):
                # 410 Gone is the receiver saying "stop": disabled with that reason, queue emptied.
                # It is the address that was asked that says so: a subscription moved or bound
                # anew while the request was out is not stopped by it, and the event goes on
                # through its schedule like any other refusal.
                if self._attempt_failed(d, None, attempts, code, err, retriable=False, duration_ms=_ms(started)):
                    self.disable(sub["id"], "gone", unsent=None if d["redelivery_of"] else d["event_seq"],
                                 version=sub["version"])
                return False
            return self._after_failure(d, sub, attempts, code, err, not single, wait, _ms(started))
        except ResolverBusy:
            return local("resolver_busy")
        except UrlRejected:
            # Does not resolve, or resolves somewhere it may not go: one answer and one treatment, so
            # neither the log nor the timing of retries tells them apart.
            return self._after_failure(d, sub, attempts, None, "destination_unavailable", not single, None, _ms(started))
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            reason = getattr(e, "reason", e)
            if (deadline is not None and time.monotonic() >= deadline.end) or isinstance(reason, TimeoutError):
                err = "timeout"
            else:
                err = "tls_failed" if isinstance(reason, ssl.SSLError) or isinstance(e, ssl.SSLError) else "connect_failed"
            return self._after_failure(d, sub, attempts, None, err, not single, None, _ms(started))
        if not 200 <= code < 300:
            return self._after_failure(d, sub, attempts, code, "http_status", not single, None, _ms(started))
        if not self._finish(d, "delivered", attempts=attempts, code=code, delivered_at=self.clock(), duration_ms=_ms(started)):
            return False
        if not single:
            self.store.execute("UPDATE suite_webhook_subscriptions SET consecutive_failures=0, failing_since=NULL, "
                               "last_success_at=? WHERE id=?", (self.clock(), sub["id"]))
        return True

    def _untried(self, d: dict, error: str, wait: float = LOCAL_RETRY_S) -> bool:
        """This server could not try: not an attempt, and nothing about the receiver. The delivery
        waits `wait` seconds with the attempt its claim counted taken back; a ping, which nobody
        would come back to, ends there and says why."""
        attempts = max(0, (d.get("attempts") or 1) - 1)
        if d["event_type"] == PING_EVENT:
            self._finish(d, "failed", attempts=attempts, error=error)
        else:
            self._finish(d, "pending", attempts=attempts, error=error, next_attempt_at=self.clock() + wait)
        return False

    def _unchanged(self, sub: dict) -> bool:
        return bool(self.store.one("SELECT 1 AS x FROM suite_webhook_subscriptions WHERE id=? AND version=?",
                                   (sub["id"], sub["version"])))

    def _after_failure(self, d, sub, attempts, code, err, retriable, retry_after, duration_ms) -> bool:
        self._attempt_failed(d, sub, attempts, code, err, retriable=retriable, retry_after=retry_after, duration_ms=duration_ms)
        return False

    def _attempt_failed(self, d: dict, sub: dict | None, attempts: int, code: int | None, err: str, *, retriable: bool,
                        retry_after: float | None = None, duration_ms: int | None = None) -> bool:
        self.last_error = err
        t = self.clock()
        wait = delivery.next_delay(attempts, retry_after, rng=self.rng) if retriable else None
        if wait is None:
            finished = self._finish(d, "failed", attempts=attempts, code=code, error=err, duration_ms=duration_ms)
        else:
            finished = self._finish(d, "pending", attempts=attempts, code=code, error=err, duration_ms=duration_ms,
                                    next_attempt_at=t + wait)
        if finished and sub is not None and d["event_type"] != PING_EVENT:
            self._note_failure(sub, t, None if d["redelivery_of"] else d["event_seq"])
        return finished

    def _note_failure(self, sub: dict, t: float, unsent: int | None = None) -> None:
        """A subscription is given up on after `DISABLE_AFTER_S` without a success *and* a whole
        schedule of attempts in that time — so not by one failure before and one after an outage,
        a restore or a step of the clock, and not by where the jitter put an attempt. The run starts
        at the attempt that took the counter from 0 to 1; a success or a resume starts a fresh one."""
        with self.store.tx() as c:
            row = c.execute("SELECT consecutive_failures, failing_since FROM suite_webhook_subscriptions WHERE id=?",
                            (sub["id"],)).fetchone()
            since = row["failing_since"] if row and row["consecutive_failures"] else None
            count = (row["consecutive_failures"] if row else 0) + 1
            c.execute("UPDATE suite_webhook_subscriptions SET consecutive_failures=consecutive_failures+1, last_failure_at=?, "
                      "failing_since=? WHERE id=?", (t, since if since is not None else t, sub["id"]))
        if since is not None and t - since >= DISABLE_AFTER_S and count >= MAX_ATTEMPTS:
            self.disable(sub["id"], "failing", unsent=unsent, version=sub["version"])

    def _dropped(self, d: dict) -> bool:
        row = self.store.one("SELECT status FROM suite_webhook_deliveries WHERE id=?", (d["id"],))
        return not row or row["status"] == "dropped"

    def _drop(self, d: dict, reason: str) -> None:
        # Dropped unsent: the attempt the claim counted did not happen.
        self._finish(d, "dropped", attempts=max(0, (d.get("attempts") or 1) - 1), error=reason)

    def _finish(self, d: dict, status: str, *, attempts: int, code: int | None = None, error: str | None = None,
                delivered_at: float | None = None, next_attempt_at: float | None = None,
                duration_ms: int | None = None) -> bool:
        """Record an outcome under this worker's lease. A delivery that was stopped meanwhile (the
        subscription paused, disabled or deleted) keeps its status and reason: an in-flight request
        records what it observed but cannot put the queue back."""
        vals = {"status": status, "attempts": attempts, "last_error": error, "claimed_by": None, "claimed_until": None,
                "next_attempt_at": next_attempt_at}
        if code is not None or status in ("delivered", "failed", "pending"):
            vals["last_code"] = code
        if delivered_at is not None:
            vals["delivered_at"] = delivered_at
        if duration_ms is not None:
            vals["duration_ms"] = duration_ms
        with self.store.tx() as c:
            row = c.execute("SELECT status FROM suite_webhook_deliveries WHERE id=? AND claimed_by=? AND claimed_until=? "
                            "AND claimed_until>?", (d["id"], self.worker_id, d.get("claimed_until"), self.clock())).fetchone()
            if not row or row["status"] not in ("pending", "dropped"):
                return False
            stopped = row["status"] == "dropped"
            if stopped:
                for k in ("status", "last_error", "next_attempt_at", "delivered_at"):
                    vals.pop(k, None)
            sets = ", ".join(f"{k}=?" for k in vals)        # keys of the closed set above
            c.execute(f"UPDATE suite_webhook_deliveries SET {sets} WHERE id=?", (*vals.values(), d["id"]))
        return not stopped

    def deliver_due(self, limit: int = 100) -> int:
        """Send what is due. Every subscription with something due gets a turn before one gets a
        second; a round stops claiming after `ROUND_S` (a request already under way has its own
        total deadline), so fan-out and the other subscriptions are returned to regularly. A
        receiver that is slow to answer keeps one worker waiting and nobody else: the next turns
        start for everyone who is not being sent to at that moment."""
        t = self.clock()
        if not self.store.one("SELECT 1 AS x FROM suite_webhook_deliveries WHERE status='pending' AND next_attempt_at<=? "
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
                    if state["budget"] <= 0 or time.monotonic() >= end or self._stop:
                        return
                    with self._inflight_lock:
                        d = self.claim_next(tuple(self._inflight | served | blocked))
                        if not d and served - self._inflight:
                            # Everyone has had a turn. Those still being sent to stay out (the
                            # claim keeps their order anyway); the rest start the next one.
                            served.intersection_update(self._inflight)
                            d = self.claim_next(tuple(self._inflight | blocked))
                        if not d:
                            return
                        key = d["subscription_id"]
                        self._inflight.add(key)
                    served.add(key)
                    state["budget"] -= 1
                try:
                    if self.send_one(d):
                        with lock:
                            state["sent"] += 1
                    elif not self._dropped(d):
                        # The receiver was tried and did not take it: it waits for its retry. A
                        # delivery dropped unsent says nothing about the receiver, which keeps its turn.
                        with lock:
                            blocked.add(key)
                finally:
                    with self._inflight_lock:
                        self._inflight.discard(key)

        if self.concurrency <= 1:
            worker()
        else:
            with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="suite-webhook-send") as pool:
                for f in [pool.submit(worker) for _ in range(self.concurrency)]:
                    f.result()
        return state["sent"]

    def sweep(self) -> int:
        """Housekeeping, hourly. The delivery log keeps `LOG_RETENTION_S`: settled deliveries older
        than that go (the count returned). A subscription whose grant has ended is disabled even
        if nothing happened for it to notice by, and removed — its secret with it — once it has
        stayed that way for `ORPHAN_AFTER_S`: nobody may be left who could remove it."""
        t = self.clock()
        for sub in self.store.all("SELECT * FROM suite_webhook_subscriptions WHERE status IN ('active','paused') "
                                  "AND deleted_at IS NULL ORDER BY id"):
            if self._stop:
                break
            try:
                self._principal(sub)
            except ServiceError as gone:
                self.disable(sub["id"], f"authorization_revoked:{gone.code}", version=sub["version"])
            except Exception:  # noqa: BLE001 — one subscription's fault must not stop the housekeeping
                log.exception("webhook sweep could not check %s", sub["id"])
        for sub in self.store.all("SELECT id FROM suite_webhook_subscriptions WHERE status='disabled' AND deleted_at IS NULL "
                                  "AND disabled_reason LIKE 'authorization_revoked:%' AND status_changed_at<?",
                                  (t - ORPHAN_AFTER_S,)):
            self._remove(sub["id"], "authorization_revoked", orphaned_before=t - ORPHAN_AFTER_S)
        removed = 0
        while True:                                         # in pieces: each is one short hold of the write lock
            n = self.store.execute("DELETE FROM suite_webhook_deliveries WHERE rowid IN (SELECT rowid FROM suite_webhook_deliveries "
                                   "WHERE status IN ('delivered','failed','dropped') AND created_at<? LIMIT ?)",
                                   (t - LOG_RETENTION_S, SWEEP_CHUNK))
            removed += n
            if n < SWEEP_CHUNK or self._stop:
                return removed

    def tick(self) -> int:
        """One step of the worker: queue what is new, send what is due."""
        self.fan_out()
        return self.deliver_due()


# --------------------------------------------------------------------------------- HTTP
def build_router(service: Webhooks, audit=None):
    """``/api/suite/v1/webhooks/subscriptions``: a grant's own subscriptions. The platform gate has
    resolved the bearer token; nothing here reads a session, and a subscription that is not this
    client's, for this person, in this workspace does not exist as far as any route can tell.

    Every change to where a person's events go is in the audit trail (`audit`, the platform's),
    written in the transaction that makes the change."""
    r = APIRouter(prefix=SUITE_V1 + "/webhooks/subscriptions", tags=["suite-machine"])

    def noting(request: Request, p: dict):
        if audit is None:
            return None
        return lambda action, sid, meta: audit(request, action, actor_id=p["user_id"], workspace_id=p["tenant"],
                                               workspace_given=True, object_kind="webhook_subscription", object_id=sid,
                                               meta=meta)

    def caller(request: Request) -> dict:
        p = getattr(request.state, "principal", None)
        if not p or p.get("kind") != "delegated" or not p.get("user_id") or not p.get("tenant"):
            raise ServiceError(401, "unauthorized", "缺少凭据（Authorization: Bearer <平台访问令牌>）")
        return p

    def no_key(request: Request) -> None:
        # These answers carry a secret or start a send: neither may be stored and replayed.
        if request.headers.get("Idempotency-Key") is not None:
            raise ServiceError(400, "idempotency_not_supported", "这个请求不支持 Idempotency-Key",
                               {"field": "Idempotency-Key"})

    def etag(response: Response, sub: dict) -> dict:
        response.headers["ETag"] = f'"{sub["version"]}"'
        return sub

    def expected(request: Request, body) -> object:
        raw = request.headers.get("If-Match")
        if raw is None:
            return body.get("version") if isinstance(body, dict) else None
        raw = raw.strip()
        if raw == "*":
            return None
        return raw.removeprefix("W/").strip('"')

    @r.post("", status_code=201)
    def create(request: Request, response: Response, body: dict = Body(...)):
        p = caller(request)
        no_key(request)
        response.headers["Cache-Control"] = "no-store"      # the signing secret is in this answer, once
        return etag(response, service.create(p, body, note=noting(request, p)))

    @r.get("")
    def listing(request: Request):
        return {"items": service.list(caller(request)), "limit": MAX_PER_GRANT, "workspace_limit": workspace_limit()}

    @r.get("/{sid}")
    def get(sid: str, request: Request, response: Response):
        return etag(response, service.get(caller(request), sid))

    @r.patch("/{sid}")
    def update(sid: str, request: Request, response: Response, body: dict = Body(...)):
        p = caller(request)
        return etag(response, service.update(p, sid, body, expected(request, body), note=noting(request, p)))

    @r.delete("/{sid}")
    def delete(sid: str, request: Request):
        p = caller(request)
        return service.delete(p, sid, note=noting(request, p))

    @r.post("/{sid}/pause")
    def pause(sid: str, request: Request, response: Response):
        p = caller(request)
        return etag(response, service.pause(p, sid, note=noting(request, p)))

    @r.post("/{sid}/resume")
    def resume(sid: str, request: Request, response: Response):
        p = caller(request)
        return etag(response, service.resume(p, sid, note=noting(request, p)))

    @r.post("/{sid}/rotate-secret")
    def rotate(sid: str, request: Request, response: Response, body: dict | None = Body(default=None)):
        p = caller(request)
        no_key(request)
        service._check_fields(body or {}, ("overlap_s",))
        response.headers["Cache-Control"] = "no-store"
        return etag(response, service.rotate_secret(p, sid, (body or {}).get("overlap_s"), note=noting(request, p)))

    @r.post("/{sid}/ping")
    def ping(sid: str, request: Request):
        p = caller(request)
        no_key(request)
        return service.ping(p, sid)

    @r.get("/{sid}/deliveries")
    def deliveries(sid: str, request: Request, status: str | None = None, cursor: str | None = None,
                   limit: int | None = None):
        return service.deliveries(caller(request), sid, status, cursor, limit)

    @r.get("/{sid}/deliveries/{did}")
    def one_delivery(sid: str, did: str, request: Request):
        return service.delivery(caller(request), sid, did)

    @r.post("/{sid}/deliveries/{did}/redeliver", status_code=202)
    def redeliver(sid: str, did: str, request: Request):
        p = caller(request)
        no_key(request)
        return service.redeliver(p, sid, did)

    return r


def install(ctx) -> Webhooks:
    """Mount the routes and, where this process runs background work, start the dispatcher."""
    service = Webhooks(ctx.capabilities)
    ctx.api.state.suite_webhooks = service
    ctx.api.include_router(build_router(service, getattr(ctx.plat, "audit", None)))
    hooks = getattr(ctx.api.state, "shutdown_hooks", None)
    if hooks is not None:
        hooks.append(service.stop)
    if ctx.start_jobs:
        from .worker import Ticker
        ticker = Ticker("suite-webhooks", service.tick, 5)

        def wake(events):                                   # a committed event: do not wait for the next tick
            ctx.store.after_commit(ticker.wake)
        ctx.capabilities.listeners.append(wake)
        ticker.start(ctx.api)
        Ticker("suite-webhooks-sweep", service.sweep, 3600).start(ctx.api)
        if hooks is not None:
            hooks.append(lambda: ctx.capabilities.listeners.remove(wake) if wake in ctx.capabilities.listeners else None)
    return service
