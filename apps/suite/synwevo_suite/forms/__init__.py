"""Synwevo Forms: forms and information collection.

Build a form, publish immutable versions, send personal fill-in requests (with prefill, deadline
and an optional sign-in requirement) or open a public link, collect submissions with attachments,
ask for changes and accept. Every submission records the version it answered and its answers by
stable field id, readable by DataPrep, Tables, Docs and the personal Agent through the operations.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response

from synwevo_core.capabilities import AppManifest

MANIFEST = AppManifest(
    id="forms", name="Synwevo Forms", path="/forms", icon="form",
    summary={"zh-Hans": "设计表单，发出填写请求，收集资料与附件，要求补充并确认。",
             "zh-Hant": "設計表單，發出填寫請求，收集資料與附件，要求補充並確認。",
             "en": "Design forms, send fill-in requests, collect answers and files, ask for changes and accept."},
    object_kinds=("form", "form_request", "form_submission"),
    object_paths=(("form", "/forms/w/{workspace}/f/{id}"), ("form_request", "/forms/w/{workspace}/r/{id}"),
                  ("form_submission", "/forms/w/{workspace}/s/{id}")))


def install(ctx) -> None:
    from synwevo_core.errors import ServiceError
    from ..common.web import signed_in
    from ..common.worker import Ticker
    from .filling import Filling, _attachment, build_public_router
    from .ops import register
    from .service import Forms

    forms = Forms(ctx.store, ctx.identity, ctx.capabilities)
    ctx.api.state.forms = forms
    register(ctx.registry, forms)
    ctx.api.include_router(build_public_router(Filling(forms), ctx.capabilities))

    @ctx.api.get("/api/suite/forms/files/{file_id}", tags=["forms"])
    def download(file_id: str, workspace_id: str, request: Request) -> Response:
        """An attachment of a submission, for members of its workspace who may take files out
        (editors and above, or a member with the export right — the same rule as Docs)."""
        user = signed_in(request)
        ctx.capabilities.check_workspace(user["id"], "forms", workspace_id, "read")
        member = ctx.identity.membership(workspace_id, user["id"])
        if not (member and (member["role"] in ("owner", "admin", "editor") or member.get("can_export"))):
            raise ServiceError(403, "export_not_allowed", "你在该工作空间没有导出权限，不能下载附件")
        row = ctx.store.one("SELECT * FROM form_files WHERE id=? AND workspace_id=? AND state='attached'",
                            (file_id, workspace_id))
        if not row:
            raise ServiceError(404, "file_not_found", "附件不存在")
        return _attachment(row, ctx.store.get_blob(row["blob"]))

    if ctx.start_jobs:
        Ticker("forms-expiry", forms.sweep_expired, 60).start(ctx.api)
