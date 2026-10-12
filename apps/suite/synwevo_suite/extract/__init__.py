"""Independent file extraction and review. Composition injects a DocumentIngest-compatible reader."""
from __future__ import annotations

import inspect
from urllib.parse import quote

from fastapi import Request, UploadFile, File, Header
from fastapi.responses import Response
from synwevo_core import auth as _auth
from synwevo_core.capabilities import AppManifest, EventFeed, EventType, canonical
from .ops import Revision

_paths = {"object_paths": (("extract_template", "/extract/w/{workspace}/templates/{id}"),
                           ("extract_task", "/extract/w/{workspace}/tasks/{id}"))} if "object_paths" in inspect.signature(AppManifest).parameters else {}
MANIFEST = AppManifest(id="extract", name="Synwevo Extract", path="/extract", icon="scan",
    summary={"zh-Hans": "从真实文件提取字段，核对来源，保留人工修正和确认版本。",
             "zh-Hant": "從真實檔案提取欄位，核對來源，保留人工修正和確認版本。",
             "en": "Extract fields from real files, check sources and preserve correction and confirmation versions."},
    object_kinds=("extract_template", "extract_task", "extract_file"), machine=True, **_paths)

#: What a credential may read about Extract's events. Templates, tasks and files are readable by
#: everyone who can read Extract in the workspace.
EVENTS = EventFeed(types=(
    *(EventType(f"extract.template.{what}", "extract_template")
      for what in ("created", "saved", "published", "retired", "archived")),
    *(EventType(f"extract.task.{what}", "extract_task")
      for what in ("queued", "started", "ready", "failed", "cancelled", "correction", "confirmation")),
    *(EventType(f"extract.file.{what}", "extract_file") for what in ("uploaded", "imported"))))


def install(ctx):
    from synwevo_core.http import SUITE_V1
    from ..common.machine import acting
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
    ctx.registry.register_events(MANIFEST.id, EVENTS)

    # Each file route is served under the page's prefix (a session) and under the machine API's (a
    # credential holding the scope named here); `acting` decides which, the rest is one path.
    @ctx.api.post("/api/suite/extract/files", tags=["extract"])
    @ctx.api.post(SUITE_V1 + "/extract/files", tags=["extract"])
    async def upload(request: Request, workspace_id: str, file: UploadFile = File(...),
                     idempotency_key: str = Header(default="", alias="Idempotency-Key")):
        try:
            with acting(ctx, request, workspace_id, app="extract", scope="extract.write") as who:
                service.auth(who.user["id"], workspace_id, "write")
                data = await file.read(MAX_FILE_BYTES + 1)
                # Scanner is blocking; leave the event loop free for cancellation/status calls.
                from starlette.concurrency import run_in_threadpool
                return await run_in_threadpool(service.upload, who.user["id"], workspace_id, file.filename or "",
                                              data, who.key(idempotency_key))
        finally:
            await file.close()

    @ctx.api.get("/api/suite/extract/files/{file_id}", tags=["extract"])
    @ctx.api.get(SUITE_V1 + "/extract/files/{file_id}", tags=["extract"])
    def download(request: Request, workspace_id: str, file_id: str):
        with acting(ctx, request, workspace_id, app="extract", scope="extract.read") as who:
            row, data = service.file_bytes(who.user["id"], workspace_id, file_id)
        return Response(data, media_type=row["content_type"], headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["filename"], safe=""),
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @ctx.api.get("/api/suite/extract/tasks/{task_id}/export", tags=["extract"])
    @ctx.api.get(SUITE_V1 + "/extract/tasks/{task_id}/export", tags=["extract"])
    def export(request: Request, workspace_id: str, task_id: str, revision: int):
        with acting(ctx, request, workspace_id, app="extract", scope="extract.read") as who:
            service.export_right(who.user["id"], workspace_id)
            # Query parameters are validated by the exact operation schema, not a second business path.
            from pydantic import ValidationError
            try:
                args = Revision(task_id=task_id, revision=revision)
            except ValidationError:
                raise ServiceError(422, "invalid_input", "导出参数无效") from None
            result = ctx.capabilities.invoke("extract.task.result", args.model_dump(), user_id=who.user["id"],
                                            workspace_id=workspace_id, via=who.via, **who.invoke)
            if who.via == "ui":
                # A credential's export is audited by the operation it invokes (via != ui); a session's
                # read operation is not, so the export itself is recorded here (G26 / D7).
                _auth.audit_event(ctx.store, "suite.extract.export", workspace_id=workspace_id, object_kind="extract_task",
                                  object_id=task_id, meta={"revision": revision, "via": who.via})
        return Response(canonical(result["output"]).encode(), media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="{task_id}-r{revision}.json"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    if ctx.start_jobs:
        hooks = getattr(ctx.api.state, "shutdown_hooks", None)
        if hooks is not None:
            hooks.append(service.stop)
        Ticker("extract-worker", service.tick, 1).start(ctx.api)
