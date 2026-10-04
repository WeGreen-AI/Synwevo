"""The business applications' machine API: the same operations, for a client a person authorised.

``/api/suite/v1`` is what an integration calls. The platform gate has already resolved the bearer
token (`MachineAuth.verify_suite`): a delegation — this client, for this person, in the workspace
they chose when they consented, with the scopes still standing. Nothing here reads a session.

There is no second implementation of anything. Every operation an application opted into
(`AppManifest.machine`) gets one route, and that route calls `Capabilities.invoke` with
``via="api"`` — the validation, the live role check, the run record, the idempotency and the events
are the page's own (acceptance A4). What this layer adds is what only a credential needs:

* the **scope** the operation requires (`capabilities.machine_scope`), checked before anything runs
  and again by the capability layer for whatever the handler reaches in another application;
* the **workspace is the credential's**, never the body's: naming another one is refused;
* the caller's idempotency key is kept **per client**, so two integrations acting for one person
  cannot replay each other's calls, and rotating a secret does not start a second attempt;
* a run can be read back only by the client that started it;
* the routes that move bytes (`acting`) are the applications' own, served a second time here;
* what changed since the client last asked is a feed it pulls (`machine_events`).

Operations that are a person's own (the Agent, spending, consent) have no route and no scope: the
capability layer refuses them for every ``via="api"`` caller, whatever asked.
"""
from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from synwevo_core import authority
from synwevo_core.apps import Apps
from synwevo_core.capabilities import (MACHINE_KEY_PREFIX, TERMINAL_STATES, Operation, caller_correlation,
                                       machine_credential, machine_scope, manifest_json, public_run)
from synwevo_core.errors import ServiceError
from synwevo_core.http import SUITE_V1
from synwevo_core.oidc import suite_resource

#: The caller's own key; what is stored also names the client (see `_key`).
MAX_CALLER_KEY = 128


class MachineInvoke(BaseModel):
    """One call. Unknown members are refused: a misspelt ``input`` must not run with defaults."""
    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] = Field(default_factory=dict)
    workspace_id: str | None = Field(default=None, max_length=100)
    idempotency_key: str | None = Field(default=None, max_length=MAX_CALLER_KEY)
    correlation_id: str | None = Field(default=None, max_length=100)


def caller_key(client_id: str, key: str) -> str:
    """A caller's idempotency key as it is stored. The client is part of the name; its secret and
    the token are not, so a rotation does not start a second attempt. A digest keeps the stored key
    inside the limit whatever the client was called."""
    if not isinstance(key, str) or not 1 <= len(key) <= MAX_CALLER_KEY or any(not 33 <= ord(ch) <= 126 for ch in key):
        raise ServiceError(400, "bad_idempotency_key",
                           f"幂等键须为 1–{MAX_CALLER_KEY} 个可见 ASCII 字符", {"maximum": MAX_CALLER_KEY})
    return MACHINE_KEY_PREFIX + hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:16] + ":" + key


@dataclass
class Acting:
    """Who a file route is serving: the signed-in person, or the person a credential acts for."""
    user: dict
    via: str = "ui"
    client_id: str | None = None
    #: What a nested `Capabilities.invoke` needs to record the call as this credential's.
    invoke: dict = field(default_factory=dict)

    def key(self, raw: str | None) -> str | None:
        """An upload's retry key as it is stored: kept apart per client for a credential, exactly as
        operations' keys are — and, for the same reason, a person may not name a client's."""
        if not raw:
            return raw
        if self.client_id is None:
            if raw.startswith(MACHINE_KEY_PREFIX):
                raise ServiceError(400, "bad_idempotency_key",
                                   f"以 {MACHINE_KEY_PREFIX} 开头的幂等键保留给接口凭据，请换一个")
            return raw
        return caller_key(self.client_id, raw)


@contextlib.contextmanager
def acting(ctx, request: Request, workspace_id: str, *, app: str, scope: str):
    """For the routes that move bytes (uploads, downloads, exports), which are served twice: under
    the page's prefix for a session, and under ``/api/suite/v1`` for a credential. The handler is
    one function and everything behind it — the upload check, quarantine, the person's export
    right — is the same; this decides who is asking. On the machine path that is the credential's
    person, only with the scope the route names, only in the credential's workspace, and with the
    credential's scopes in effect for whatever the handler reads in another application."""
    if not request.url.path.startswith(SUITE_V1 + "/"):
        from .web import signed_in
        yield Acting(user=signed_in(request))
        return
    p = getattr(request.state, "principal", None)
    if not p or p.get("kind") != "delegated" or not p.get("user_id") or not p.get("tenant"):
        raise ServiceError(401, "unauthorized", "缺少凭据（Authorization: Bearer <平台访问令牌>）")
    if scope not in (p.get("scopes") or ()):
        raise ServiceError(403, "insufficient_scope", f"该凭据没有 {scope} 权限",
                           {"required_scope": scope, "scopes": sorted(p.get("scopes") or ())})
    if workspace_id != p["tenant"]:
        raise ServiceError(403, "workspace_mismatch", "该凭据只能在授权时选定的工作空间内使用",
                           {"workspace_id": p["tenant"]})
    user = ctx.identity.get_user(p["user_id"])
    if not user or user["status"] != "active":
        raise ServiceError(401, "unauthorized", "该凭据背后的账户不可用")
    with machine_credential(p["scopes"], app):
        yield Acting(user=user, via="api", client_id=p["client_id"], invoke={
            "principal": authority.describe(p),
            "delegation": {"kind": "oauth_client", "client_id": p["client_id"], "grant": p["grant_family"]}})


def machine_operations(registry) -> list[tuple[Operation, str]]:
    """Every operation a machine caller may ask for, with the scope it needs."""
    out = []
    for op in registry.operations():
        scope = machine_scope(op, registry.app(op.app))
        if scope is not None:
            out.append((op, scope))
    return sorted(out, key=lambda pair: pair[0].id)


def idempotency_rule(op: Operation) -> str:
    """``required``: the call reaches someone outside, and a blind retry would reach them twice —
    or the operation's own handler will not run without a key. ``supported``: a change inside
    Synwevo. ``none``: a read."""
    if op.side_effect == "external" or op.requires_key:
        return "required"
    return "supported" if op.side_effect != "none" else "none"


def contract(op: Operation, scope: str) -> dict:
    """An operation as an integration sees it. ``scope`` is the operation's own; ``also_requires``
    lists the scopes of other applications its handler reads in on the caller's behalf."""
    return {"id": op.id, "app": op.app, "version": op.version, "title": op.title, "summary": op.summary,
            "path": f"{SUITE_V1}/ops/{op.id}", "scope": scope, "also_requires": list(op.reaches), "need": op.need,
            "side_effect": op.side_effect, "mode": op.mode, "cancellable": op.cancellable,
            "idempotency": idempotency_rule(op), "events": list(op.events),
            "object_kind": op.object_kind, "input_schema": op.input_schema(),
            "output_schema": op.output_schema()}


def build_machine_router(ctx) -> APIRouter:
    r = APIRouter(prefix=SUITE_V1, tags=["suite-machine"])
    caps = ctx.capabilities
    callable_ops = machine_operations(caps.registry)

    def caller(request: Request) -> dict:
        p = getattr(request.state, "principal", None)
        if not p or p.get("kind") != "delegated" or not p.get("user_id") or not p.get("tenant"):
            # The gate sets this for every path under the prefix; reaching here without it means
            # the route was mounted somewhere the gate does not cover. Refuse rather than guess.
            raise ServiceError(401, "unauthorized", "缺少凭据（Authorization: Bearer <平台访问令牌>）")
        return p

    def require_scope(p: dict, scope: str) -> None:
        if scope not in (p.get("scopes") or ()):
            raise ServiceError(403, "insufficient_scope", f"该凭据没有 {scope} 权限",
                               {"required_scope": scope, "scopes": sorted(p.get("scopes") or ())})

    def usable_apps(p: dict) -> list[str]:
        """The machine applications this person can use in the credential's workspace, now."""
        apps = Apps(caps.store, caps.identity)
        out = []
        for m in caps.registry.apps():
            if not m.machine:
                continue
            ok, wid, _ = apps.check_access(p["user_id"], m.id, p["tenant"])
            if ok and wid == p["tenant"]:
                out.append(m.id)
        return sorted(out)

    def _key(p: dict, op: Operation, in_body: str | None, in_header: str | None) -> str | None:
        if in_body and in_header and in_body != in_header:
            raise ServiceError(400, "bad_idempotency_key", "请求头与请求体里的幂等键不一致")
        key = in_body or in_header
        if key is None:
            if idempotency_rule(op) == "required":
                raise ServiceError(400, "idempotency_key_required",
                                   "这个操作必须带 Idempotency-Key：它会向他人发出请求、通知或链接，或以该键识别重试，"
                                   "没有键的重试会重复执行", {"operation": op.id})
            return None
        stored = caller_key(p["client_id"], key)
        # A read has nothing to make repeatable: a client may send its key on every call and
        # retry freely, and a retried read answers with what is true now, not a stored copy.
        return None if op.side_effect == "none" else stored

    @r.get("/whoami")
    def whoami(request: Request):
        """Who this credential is, for whom, where, and what it may still ask for."""
        p = caller(request)
        return {"kind": p["kind"], "client_id": p["client_id"], "user_id": p["user_id"],
                "workspace_id": p["tenant"], "role": p["role"], "scopes": sorted(p["scopes"]),
                "resource": suite_resource(), "apps": usable_apps(p), "expires_at": p.get("expires_at")}

    @r.get("/operations")
    def operations(request: Request, app: str | None = None):
        """The contract of every operation this credential's person can use here, and whether the
        credential holds the scope each one needs."""
        p = caller(request)
        usable = set(usable_apps(p))
        if app is not None and app not in usable:
            raise ServiceError(404, "app_unavailable", "该应用未启用或你不能使用它")
        held = set(p["scopes"])
        ops = [{**contract(op, scope), "granted": {scope, *op.reaches} <= held} for op, scope in callable_ops
               if op.app in usable and (app is None or op.app == app)]
        return {"workspace_id": p["tenant"], "operations": ops,
                "apps": [manifest_json(m) for m in caps.registry.apps() if m.id in usable]}

    def invoker(op: Operation, scope: str):
        def invoke(body: MachineInvoke, request: Request, response: Response,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
            p = caller(request)
            for needed in (scope, *op.reaches):
                require_scope(p, needed)
            if body.workspace_id is not None and body.workspace_id != p["tenant"]:
                raise ServiceError(403, "workspace_mismatch", "该凭据只能在授权时选定的工作空间内使用",
                                   {"workspace_id": p["tenant"]})
            key = _key(p, op, body.idempotency_key, idempotency_key)
            result = caps.invoke(
                op.id, body.input, user_id=p["user_id"], workspace_id=p["tenant"], via="api",
                idempotency_key=key, correlation_id=caller_correlation(body.correlation_id), request=request,
                principal=authority.describe(p), scopes=frozenset(p["scopes"]),
                delegation={"kind": "oauth_client", "client_id": p["client_id"], "grant": p["grant_family"]})
            if result["status"] not in TERMINAL_STATES:
                response.status_code = 202          # accepted, not done: poll the run or the object
            return result
        invoke.__name__ = "invoke_" + op.id.replace(".", "_")
        return invoke

    for op, scope in callable_ops:
        r.add_api_route(f"/ops/{op.id}", invoker(op, scope), methods=["POST"], summary=op.summary,
                        name=op.id)

    @r.post("/ops/{operation}")
    def not_callable(operation: str, request: Request):
        """Everything that has no route of its own: unknown, or not a credential's to ask for."""
        caller(request)
        if caps.registry.get(operation) is None:
            raise ServiceError(404, "unknown_operation", "没有这个操作", {"operation": operation})
        raise ServiceError(403, "person_required",
                           "这个操作必须由本人在应用里亲自完成，不能通过接口凭据代为执行。",
                           {"operation": operation})

    @r.get("/events")
    def events(request: Request, cursor: str | None = None, types: str | None = None,
               limit: int | None = None, start: str | None = None):
        """What changed in this credential's workspace since `cursor`, for the applications it may
        read: where to look, never what is there (see `machine_events`)."""
        from . import machine_events
        return machine_events.feed(caps, caller(request), cursor=cursor, types=types, limit=limit, start=start)

    @r.get("/runs/{run_id}")
    def run(run_id: str, request: Request):
        """A run this client started for this person here — a receipt, re-authorized as it is read.
        Anyone else's, another client's, or one made in the page is the same 404 as a missing run."""
        p = caller(request)
        row = caps.store.one("SELECT * FROM op_runs WHERE id=?", (run_id,))
        missing = ServiceError(404, "run_not_found", "运行记录不存在")
        if not row or row["user_id"] != p["user_id"] or row["via"] != "api" or row["workspace_id"] != p["tenant"]:
            raise missing
        started_by = public_run(row)["delegation"] or {}
        if started_by.get("kind") != "oauth_client" or started_by.get("client_id") != p["client_id"]:
            raise missing
        op = caps.registry.get(row["op"])
        scope = machine_scope(op, caps.registry.app(op.app)) if op is not None else None
        if scope is None:
            raise missing
        require_scope(p, scope)
        with machine_credential(p["scopes"], op.app):      # its sources are re-read as this credential
            return caps.present_run(row, user_id=p["user_id"])

    return r
