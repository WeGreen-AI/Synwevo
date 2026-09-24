"""Idempotency-Key for creating POSTs and supported PUTs of the machine API.

A network timeout does not tell a client whether its request ran. Without a key the only safe
retry is none at all; with one, the retry either replays the first answer or is refused. This
module is the one place that decides which, for every route that creates something (projects,
source files, templates, template fields, signed links, uploads). Job creation and retry keep
their own implementation in `JobService`, because there the dedupe has to happen inside the
same transaction that inserts the job; they only share the key validation below.

The model is Stripe's (docs.stripe.com/api/idempotent_requests), checked against the expired
IETF draft:

* **Scope** is the workspace + the caller + method + route template + key. Two routes never
  share a key's meaning, and another workspace's identical key is a different key. Neither is
  another *caller's*: keys are chosen by clients, and two integrations in one workspace can
  easily pick the same string. Sharing the namespace handed one caller the other's replay — for
  a link route, a signed link that carries the *other* caller's authority and dies with it. The
  caller is identified the way `JobService` identifies it (an API key by its lineage, so a
  rotated key still replays what its predecessor did).
* **Fingerprint** is a hash of what the route will act on: the normalised JSON body, or the
  uploaded file's SHA-256 plus its name, together with the path parameters. Same key and a
  different fingerprint is `409 idempotency_conflict` — never a silent replay of something the
  caller did not ask for this time. Current visibility is checked before exposing conflict
  metadata too, so a different body cannot disclose a now-inaccessible cached resource id.
* **Only successes are stored.** The claim, work's database writes, and response share one
  Store transaction. An exception or process death before commit rolls them all back, so the
  key stays usable and the corrected request runs. This is
  Stripe v2's rule; v1's "replay the first error" would pin a client to a mistake it has fixed.
  401 and 429 never reach this code (the platform gate answers them first).
* **Concurrent duplicates** wait on Store's write transaction, then replay the committed
  response. Old persisted claims without a response have an uncertain outcome: their work may
  already have committed under the former implementation. They are never taken over or purged
  automatically, even after the stale window or TTL.
* **Replays** first pass the route's required current-object visibility check, then carry
  `Idempotent-Replayed: true` and the stored status and body, byte for byte.
* Records live `SYNWEVO_DOCS_IDEMPOTENCY_TTL_HOURS` (default 24h), or less when the response
  itself expires sooner — replaying a signed link that has already expired would hand back a
  dead credential, so a link route passes the link lifetime as its TTL. The job supervisor
  invokes `purge_expired` for complete route responses; uncertain claims remain for explicit
  outcome reconciliation.
* **A response that carries a secret is never stored.** A replay must return the stored body
  byte for byte, so storing it would keep a plaintext credential in the database for a day.
  Routes that answer with a secret (`SECRET_ROUTES`) refuse to use this class at all.

The draft's 422 for "same key, different payload" is deliberately mapped to 409: `/api/v1`
already answered 409 for that on job creation, and the draft expired without becoming a
standard, so changing it would break clients for nothing.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from synwevo_core import authority, env
from synwevo_core.context import tenant as current_tenant
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store, now

HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replayed"
MAX_KEY_LEN = 255
TTL_S = float(env.docs("IDEMPOTENCY_TTL_HOURS", "24")) * 3600
# Diagnostic threshold for legacy incomplete claims, NOT a lease permitting takeover.
IN_FLIGHT_STALE_S = 600.0
# Routes whose success body contains a plaintext secret shown exactly once (a new API key, a
# webhook signing secret). `Idempotency.run` refuses them: see the module docstring.
SECRET_ROUTES = frozenset({
    "/api/v1/credentials/self/rotate",
    "/api/v1/webhooks/subscriptions",
    "/api/v1/webhooks/subscriptions/{sid}/rotate-secret",
})


def validate_key(raw) -> str:
    """The key as sent, or 400 naming the header.

    Printable ASCII only: the key is echoed into logs and compared byte-wise, and a control
    character or a non-ASCII look-alike is how two keys that print the same end up different.
    """
    if not isinstance(raw, str) or not raw or len(raw) > MAX_KEY_LEN \
            or any(not (0x20 <= ord(ch) <= 0x7e) for ch in raw):
        raise ServiceError(400, "invalid_idempotency_key",
                           f"Idempotency-Key 必须是 1–{MAX_KEY_LEN} 个可打印 ASCII 字符，建议使用 UUID",
                           {"field": HEADER})
    return raw


def key_of(request: Request) -> str | None:
    raw = request.headers.get(HEADER)
    return None if raw is None else validate_key(raw)


def owner_of(desc: dict | None) -> str:
    """Whose key namespace a request belongs to; ``""`` for the operator modes.

    Must agree with `JobService`'s `_idem_owner` (tested): the same client retrying a job and
    retrying a link must be recognised as the same client by both.
    """
    d = desc or {}
    k = d.get("kind")
    if k == "api_key":
        return f"api:{d.get('lineage') or d.get('key_id')}"
    if k == "service_account":
        return f"svc:{d.get('client_id')}"
    if k == "delegated":
        return f"dlg:{d.get('client_id')}:{d.get('user_id')}"
    if k == "user":
        return f"user:{d.get('user_id')}"
    return ""


def scope_of(method: str, route: str, owner: str) -> str:
    """The stored `scope` of a route record: ``POST /api/v1/projects|api:<lineage>``."""
    base = f"{method.upper()} {route}"
    return f"{base}|{owner}" if owner else base


def fingerprint(obj) -> str:
    """Stable hash of a normalised request: key order and whitespace do not matter."""
    return hashlib.sha256(json.dumps(jsonable_encoder(obj), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class Idempotency:
    def __init__(self, store: Store):
        self.store = store

    def run(self, request: Request, route: str, fingerprint_of, work: Callable[[], dict], *,
            replay_check: Callable[[dict], None], status: int = 201,
            ttl_s: float | None = None) -> JSONResponse:
        """Run `work` at most once per key; return its JSON response or the stored replay.

        `route` is the route template ("/api/v1/projects/{pid}/files"), not the concrete path:
        the concrete path parameters belong in `fingerprint_of`, so reusing one key for two
        different projects is a conflict rather than a replay of the first project's upload.

        `work` must use this Store on this thread for all database writes. Blob writes and
        background work are not transactional; callers must make those safe to retry.
        `replay_check(out)` must raise if the current caller cannot access the cached result
        or its parents. Its return value is ignored; a boolean predicate is not sufficient.
        It also runs before a fingerprint conflict: any identity used to authorize cached
        conflict metadata must come from the stored result, not the new request's path.
        The callback must only validate, without mutating the stored response.
        """
        if route in SECRET_ROUTES:
            raise RuntimeError(f"{route} answers with a secret; its response must never be stored for replay")
        key = key_of(request)
        if key is None:
            return JSONResponse(jsonable_encoder(work()), status_code=status)
        tenant = current_tenant()
        desc = authority.describe_request(request)
        scope = scope_of(request.method, route, owner_of(desc))
        h = fingerprint(fingerprint_of)
        life = min(TTL_S, ttl_s) if ttl_s is not None else TTL_S
        with self.store.tx():
            replay = self._claim(tenant, scope, key, h, life, authority.label(desc), replay_check)
            if replay is not None:
                return replay
            out = jsonable_encoder(work())
            # Serialize before commit too: an unrenderable response must not leave committed
            # work that the caller only sees as a failed request.
            response = JSONResponse(out, status_code=status)
            rid = out.get("id") if isinstance(out, dict) else None
            updated = self.store.execute(
                "UPDATE idempotency SET response_status=?, response_body=?, resource_id=? "
                "WHERE tenant=? AND scope=? AND key=? AND request_hash=? AND response_status IS NULL",
                (status, json.dumps(out, ensure_ascii=False), rid, tenant, scope, key, h))
            if updated != 1:
                raise RuntimeError("Idempotency claim disappeared before response persistence")
        return response

    # ------------------------------------------------------------------ internals
    def _claim(self, tenant: str, scope: str, key: str, h: str, life: float, who: str,
               replay_check: Callable[[dict], None]) -> JSONResponse | None:
        """Called within run's transaction; never commits a response-less claim by itself."""
        t = now()
        conflict: ServiceError | None = None
        with self.store.tx() as c:
            row = c.execute("SELECT request_hash, response_status, response_body, resource_id, created_at "
                            "FROM idempotency WHERE tenant=? AND scope=? AND key=?", (tenant, scope, key)).fetchone()
            if row is not None and (row["response_status"] is None or row["response_body"] is None):
                # Age cannot prove that a legacy worker did not commit its work. Keep both
                # the original fingerprint and claim intact until its outcome is reconciled.
                if row["created_at"] < t - IN_FLIGHT_STALE_S:
                    raise ServiceError(409, "idempotency_outcome_unknown",
                                       "该幂等键的先前请求结果尚未确认，不能自动重做；请先核对原请求结果",
                                       {"field": HEADER})
                raise ServiceError(409, "idempotency_in_progress",
                                   "使用同一个幂等键的请求尚无已确认的响应；请稍后用同一个键重试", {"field": HEADER})
            if row is not None and row["created_at"] < t - life:
                c.execute("DELETE FROM idempotency WHERE tenant=? AND scope=? AND key=?", (tenant, scope, key))
                row = None
            if row is None:
                c.execute("INSERT INTO idempotency (tenant, scope, key, request_hash, created_at, principal) "
                          "VALUES (?,?,?,?,?,?)", (tenant, scope, key, h, t, who))
                return None
            # A conflict can expose resource_id just as a replay can expose the body.
            # Validate the stored result before either branch, within this transaction.
            out = json.loads(row["response_body"])
            replay_check(out)
            if row["request_hash"] != h:
                conflict = ServiceError(409, "idempotency_conflict",
                                        "该幂等键已用于另一个不同内容的请求；请换一个新的幂等键，或发送与原请求完全相同的内容",
                                        {"field": HEADER, **({"resource_id": row["resource_id"]} if row["resource_id"] else {})})
            else:
                return JSONResponse(out, status_code=int(row["response_status"]),
                                    headers={REPLAY_HEADER: "true"})
        raise conflict


def purge_expired(store: Store, at: float | None = None) -> int:
    """Delete expired complete POST/PUT responses, never claims with uncertain outcomes.

    Job-scoped rows (``job:``/``retry:``) belong to JobService and are left alone.
    """
    t = at if at is not None else now()
    return store.execute("DELETE FROM idempotency WHERE created_at < ? "
                         "AND (scope LIKE 'POST %' OR scope LIKE 'PUT %') "
                         "AND response_status IS NOT NULL AND response_body IS NOT NULL", (t - TTL_S,))
