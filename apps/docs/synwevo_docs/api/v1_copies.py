"""/api/v1 copy requests (API-5, scenario C): an integration the source owner authorized asks for a
copy of a personal project into an organization workspace, follows it, and withdraws it.

The same state machine as the workbench (synwevo_core.project_copies) and the same announcement to
the people who may decide (copies.announce_request). What stays with people:

* **Only a delegation can ask.** A copy request is the source owner's; an access key or a service
  account has no owner behind it, so it gets 403 `delegation_required`. The delegated token acts in
  the workspace the person chose when they consented — the personal workspace the project is in.
* **Deciding is not offered here.** Approval needs the decider's recent re-authentication in the
  organization's approval page; the workbench's "request and self-approve" shortcut is not
  available to machines either (400 `decision_in_browser`). The API reports the decision's outcome
  (status, `copy.*` events) instead.
* **Execution re-checks everything**, as for any copy: a changed source, a lost role or an expired
  approval ends in `stale`, never in a copy made on old authority.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from synwevo_core import authority
from synwevo_core.errors import ServiceError

from ..copies import announce_request, build_manifest
from .v1_copy_models import (CopyManifestResponse, CopyRequestCreate, CopyRequestList,
                             CopyRequestView, CopyTargetsResponse)

COPY_STATUSES = ("pending", "approved", "rejected", "withdrawn", "expired", "running", "done", "failed", "stale")


def register(r: APIRouter, app_, scope, idem) -> None:
    from .v1 import _ox
    from synwevo_core.context import tenant as current_tenant

    store = app_.store

    def person(request: Request, needed: str) -> dict:
        p = scope(request, needed)
        if p.get("kind") != "delegated" or not p.get("user_id"):
            raise ServiceError(403, "delegation_required",
                               "复制申请只能由项目所有者本人授权的应用发起；访问密钥与服务账户不能代替本人申请")
        return p

    def service(request: Request):
        return request.app.state.copies

    def mine(request: Request, p: dict, cid: str) -> dict:
        row = service(request).get(cid)
        if not row or row["requester_id"] != p["user_id"] or row["source_workspace_id"] != current_tenant():
            raise ServiceError(404, "no_copy_request", "找不到该复制申请")
        # A cached manifest is still source content. Deleting the project must also revoke
        # reads and idempotent replays of the historical request that contained it.
        app_.get_project(row["source_project_id"])
        return row

    @r.get("/copy-targets", response_model=CopyTargetsResponse, summary="可作为复制目标的组织工作空间（本人可编辑的）", openapi_extra=_ox("read", identity="delegated"))
    def copy_targets(request: Request):
        p = person(request, "read")
        ident = request.app.state.identity
        items = []
        for w in ident.workspaces_for(p["user_id"]):
            if w.get("kind") == "org" and w.get("status") == "active" and w.get("organization_id") and w.get("role") in ("editor", "admin", "owner"):
                org = store.one("SELECT name, status FROM organizations WHERE id=?", (w["organization_id"],)) or {}
                if org.get("status") == "active":
                    items.append({"id": w["id"], "name": w["name"], "role": w["role"], "organization_id": w["organization_id"],
                                  "organization_name": org.get("name")})
        src = store.one("SELECT kind FROM workspaces WHERE id=?", (current_tenant(),)) or {}
        return {"items": items, "source_is_personal": src.get("kind") == "personal"}

    @r.get("/projects/{pid}/copy-manifest", response_model=CopyManifestResponse, summary="预览复制清单与摘要（不创建申请）", openapi_extra=_ox("read", identity="delegated"))
    def copy_manifest(request: Request, pid: str, sources: bool = False, corrections: bool = False):
        person(request, "read")
        manifest, digest = build_manifest(app_, pid, {"sources": sources, "corrections": corrections})
        return {"manifest": manifest, "digest": digest}

    @r.get("/projects/{pid}/copy-requests", response_model=CopyRequestList, summary="本项目的复制申请（本人发起的，最新在前）", openapi_extra=_ox("read", identity="delegated"))
    def list_copy_requests(request: Request, pid: str, status: str | None = Query(None, pattern="^(" + "|".join(COPY_STATUSES) + ")$"),
                           limit: int = Query(50, ge=1, le=100)):
        p = person(request, "read")
        app_.get_project(pid)
        copies = service(request)
        copies.sweep()
        rows = copies.list_for_requester(p["user_id"], source_project_id=pid, source_workspace_id=current_tenant(),
                                         status=status, limit=limit)
        return {"items": [copies.public(x, for_requester=True) for x in rows]}

    @r.post("/projects/{pid}/copy-requests", status_code=201, response_model=CopyRequestView,
            summary="申请把个人项目复制到组织工作空间（由目标审批人在网页中决定；可幂等重试）",
            openapi_extra=_ox("write", idempotent=True, identity="delegated"))
    def request_copy(request: Request, pid: str, body: CopyRequestCreate):
        p = person(request, "write")
        body = body.model_dump(exclude_unset=True)
        scope_ = body.get("scope", {})
        scope_ = {k: scope_.get(k, False) for k in ("sources", "corrections")}
        normalized = {"target_workspace_id": body["target_workspace_id"].strip(),
                      "name": body["name"].strip() if "name" in body else None,
                      "purpose": body.get("purpose", "").strip() or None, "scope": scope_}
        uid, copies = p["user_id"], service(request)

        def work() -> dict:
            manifest, digest = build_manifest(app_, pid, scope_)
            # The request row and its record commit together with or without an Idempotency-Key (G26).
            with store.tx():
                row = copies.create(requester_id=uid, source_workspace_id=current_tenant(), source_project_id=pid,
                                    target_workspace_id=normalized["target_workspace_id"],
                                    name=normalized["name"] or manifest["project"]["name"], purpose=normalized["purpose"],
                                    scope=scope_, manifest=manifest, digest=digest,
                                    requested_authority=authority.describe_request(request))
                request.app.state.platform_audit(
                    request, "docs.copy.request", actor_id=uid, workspace_id=current_tenant(), object_kind="project_copy",
                    object_id=row["id"], result="ok", meta={"project_id": pid, "target_workspace_id": row["target_workspace_id"],
                                                            "scope": row["scope"], "digest": digest, "via": "api", "client_id": p.get("client_id")})
            announce_request(store, request.app.state.identity, copies, request.app.state.notifier, row, uid, manifest["project"]["name"])
            return CopyRequestView.model_validate(copies.public(row, for_requester=True)).model_dump(mode="json")

        def replay_check(out: dict) -> None:
            mine(request, p, out["id"])

        return idem.run(request, "/api/v1/projects/{pid}/copy-requests", {"pid": pid, "body": normalized}, work, replay_check=replay_check)

    @r.get("/copy-requests/{cid}", response_model=CopyRequestView, summary="一个复制申请的状态、清单与结果", openapi_extra=_ox("read", identity="delegated"))
    def get_copy_request(request: Request, cid: str):
        p = person(request, "read")
        service(request).sweep()
        return service(request).public(mine(request, p, cid), for_requester=True)

    @r.post("/copy-requests/{cid}/withdraw", response_model=CopyRequestView,
            responses={409: {"$ref": "#/components/responses/Conflict"}}, summary="撤回待审批或已批准未执行的复制申请", openapi_extra=_ox("write", identity="delegated"))
    def withdraw_copy_request(request: Request, cid: str):
        p = person(request, "write")
        mine(request, p, cid)
        with store.tx():
            out = service(request).withdraw(cid, p["user_id"])
            request.app.state.platform_audit(request, "docs.copy.withdraw", actor_id=p["user_id"], object_kind="project_copy", object_id=cid,
                                             result="ok", meta={"via": "api", "client_id": p.get("client_id")})
        return service(request).public(out, for_requester=True)
