"""The operations surface: one HTTP entry for every registered operation.

`POST /api/suite/ops/{operation}` is what the applications' own pages call, what a script calls,
and what the Agent's tool calls resolve to — one path through `Capabilities.invoke`, so the three
cannot disagree about validation, workspace or permission (acceptance A4). The workspace is a field
of the request body, never the browser tab's binding.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from synwevo_core.capabilities import manifest_json
from synwevo_core.errors import ServiceError

from .web import page_args, signed_in


class InvokeBody(BaseModel):
    workspace_id: str | None = Field(default=None, max_length=100)
    input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=200)
    correlation_id: str | None = Field(default=None, max_length=100)


def build_router(ctx) -> APIRouter:
    r = APIRouter(prefix="/api/suite", tags=["suite"])
    caps = ctx.capabilities

    @r.get("/catalog")
    def catalog(request: Request):
        """The applications this person can open, and the workspaces they can open each in."""
        user = signed_in(request)
        return {"apps": caps.catalogue(user["id"])}

    @r.get("/operations")
    def operations(request: Request, app: str | None = None):
        """The contract of every operation of the applications this person can open."""
        user = signed_in(request)
        usable = {a["id"] for a in caps.catalogue(user["id"])}
        if app is not None and app not in usable:
            raise ServiceError(404, "app_unavailable", "该应用未启用或你不能使用它")
        ops = [op.describe() for op in caps.registry.operations(app) if op.app in usable]
        return {"operations": ops,
                "apps": [manifest_json(m) for m in caps.registry.apps() if m.id in usable]}

    @r.post("/ops/{operation}")
    def invoke(operation: str, body: InvokeBody, request: Request,
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        user = signed_in(request)
        key = body.idempotency_key or idempotency_key
        if body.idempotency_key and idempotency_key and body.idempotency_key != idempotency_key:
            raise ServiceError(400, "bad_idempotency_key", "请求头与请求体里的幂等键不一致")
        return caps.invoke(operation, body.input, user_id=user["id"], workspace_id=body.workspace_id,
                           via="ui", idempotency_key=key, correlation_id=body.correlation_id,
                           request=request)

    @r.get("/runs")
    def runs(request: Request, limit: int | None = None, before: float | None = None,
             app: str | None = None):
        """This person's own runs, newest first."""
        user = signed_in(request)
        n = page_args(limit)
        sql = "SELECT * FROM op_runs WHERE user_id=?"
        args: list = [user["id"]]
        if app:
            sql += " AND app=?"
            args.append(app)
        if before is not None:
            sql += " AND created_at<?"
            args.append(before)
        rows = caps.store.all(sql + " ORDER BY created_at DESC LIMIT ?", (*args, n + 1))
        more = len(rows) > n
        rows = rows[:n]
        return {"runs": [caps.present_run(x, user_id=user["id"]) for x in rows],
                "next_before": rows[-1]["created_at"] if more and rows else None}

    @r.get("/runs/{run_id}")
    def run(run_id: str, request: Request):
        user = signed_in(request)
        return caps.run(run_id, user_id=user["id"])

    return r
