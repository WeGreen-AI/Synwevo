"""Independent file extraction and review. Composition injects a DocumentIngest-compatible reader."""
from __future__ import annotations

import inspect
from urllib.parse import quote

from fastapi import Request, UploadFile, File, Header
from fastapi.responses import Response
from synwevo_core.capabilities import AppManifest, canonical
from .ops import Revision

_paths = {"object_paths": (("extract_template", "/extract/w/{workspace_id}/templates/{id}"),
                           ("extract_task", "/extract/w/{workspace_id}/tasks/{id}"))} if "object_paths" in inspect.signature(AppManifest).parameters else {}
MANIFEST = AppManifest(id="extract", name="Synwevo Extract", path="/extract", icon="scan",
    summary={"zh-Hans": "从真实文件提取字段，核对来源，保留人工修正和确认版本。",
             "zh-Hant": "從真實檔案提取欄位，核對來源，保留人工修正和確認版本。",
             "en": "Extract fields from real files, check sources and preserve correction and confirmation versions."},
    object_kinds=("extract_template", "extract_task", "extract_file"), **_paths)


def install(ctx):
    from ..common.web import signed_in
    from ..common.worker import Ticker
    from .ops import register
    from .service import Extract, MAX_FILE_BYTES
    from synwevo_core.errors import ServiceError

    # Composition may inject a direct reader or a bundle with an `ingest` reader. Never import Docs.
    reader = getattr(ctx.docs, "ingest", ctx.docs)
    if reader is not None and not (callable(getattr(reader, "extract", None)) and callable(getattr(reader, "describe", None))):
        reader = None
    service = Extract(ctx.store, ctx.identity, ctx.capabilities, reader)
    ctx.api.state.extract = service
    register(ctx.registry, service)

    @ctx.api.post("/api/suite/extract/files", tags=["extract"])
    async def upload(request: Request, workspace_id: str, file: UploadFile = File(...),
                     idempotency_key: str = Header(default="", alias="Idempotency-Key")):
        user = signed_in(request)
        service.auth(user["id"], workspace_id, "write")
        try:
            data = await file.read(MAX_FILE_BYTES + 1)
            # Scanner is blocking; leave the event loop free for cancellation/status calls.
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(service.upload, user["id"], workspace_id, file.filename or "",
                                          data, idempotency_key)
        finally:
            await file.close()

    @ctx.api.get("/api/suite/extract/files/{file_id}", tags=["extract"])
    def download(request: Request, workspace_id: str, file_id: str):
        user = signed_in(request)
        service.export_right(user["id"], workspace_id)
        row = service.row(workspace_id, "file", file_id)
        return Response(ctx.store.get_blob(row["blob"]), media_type=row["content_type"], headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["filename"], safe=""),
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @ctx.api.get("/api/suite/extract/tasks/{task_id}/export", tags=["extract"])
    def export(request: Request, workspace_id: str, task_id: str, revision: int):
        user = signed_in(request)
        service.export_right(user["id"], workspace_id)
        # Query parameters are validated by the exact operation schema, not a second business path.
        from pydantic import ValidationError
        try:
            args = Revision(task_id=task_id, revision=revision)
        except ValidationError:
            raise ServiceError(422, "invalid_input", "导出参数无效") from None
        result = ctx.capabilities.invoke("extract.task.result", args.model_dump(),
                                        user_id=user["id"], workspace_id=workspace_id, via="ui")
        return Response(canonical(result["output"]).encode(), media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="{task_id}-r{revision}.json"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    if ctx.start_jobs:
        hooks = getattr(ctx.api.state, "shutdown_hooks", None)
        if hooks is not None:
            hooks.append(service.stop)
        Ticker("extract-worker", service.tick, 1).start(ctx.api)
