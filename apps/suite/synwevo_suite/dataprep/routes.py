"""Binary upload/download only. Business actions are registered operations.

Served twice by the same handlers: under the page's prefix for a session, and under the machine
API's for a credential holding `dataprep.write` (upload) or `dataprep.read` (exports). Who is asking
is `acting`'s answer; the upload check, the source re-checks and the person's export right are one
path for both.
"""
import contextlib
from typing import Literal
from fastapi import APIRouter, Request, File, UploadFile, Form, Header
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from synwevo_core.errors import ServiceError
from synwevo_core.capabilities import canonical
from ..common.machine import acting
from .models import MAX_FILE
from .history import check_sources


def router(ctx, service, prefix='/api/suite/dataprep'):
    api = APIRouter(prefix=prefix, tags=['dataprep'])

    @contextlib.contextmanager
    def allowed(request, workspace, need='read', export=False):
        with acting(ctx, request, workspace, app='dataprep', scope='dataprep.write' if need == 'write' else 'dataprep.read') as who:
            ctx.capabilities.authorize(ctx.registry.get('dataprep.run.start' if need == 'write' else 'dataprep.dataset.get'), who.user['id'], workspace)
            if export:
                member = ctx.identity.membership(workspace, who.user['id'])
                if not member or not (member['role'] in ('owner', 'admin', 'editor') or member.get('can_export')):
                    raise ServiceError(403, 'export_not_allowed', '你在该工作空间没有导出权限')
            yield who

    @api.post('/files')
    async def upload(request: Request, workspace_id: str, file: UploadFile = File(...),
                     idempotency_key: str | None = Header(None, min_length=1, max_length=200),
                     upload_key: str | None = Form(None, min_length=1, max_length=200)):
        try:
            with allowed(request, workspace_id, 'write') as who:
                key = who.key(idempotency_key or upload_key)
                if not key:
                    raise ServiceError(422, 'invalid_input', '上传需要重试标识')
                data = await file.read(MAX_FILE + 1)
                if not data or len(data) > MAX_FILE:
                    raise ServiceError(413, 'file_limit', '文件为空或超过 8 MB')
                out = await run_in_threadpool(service.upload, workspace_id, who.user['id'], file.filename or '', data, key)
                # The caller is told the key it sent, not how it is stored for its client.
                return {**out, 'upload_key': idempotency_key or upload_key}
        finally:
            await file.close()

    @api.get('/datasets/{dataset_id}/export')
    def export(request: Request, dataset_id: str, workspace_id: str, format: Literal['csv', 'xlsx', 'json'] = 'csv'):
        with allowed(request, workspace_id, export=True) as who:
            check_sources(service, workspace_id, who.user['id'], dataset_id)
            data, mime = service.export(workspace_id, dataset_id, format)
        return Response(data, media_type=mime, headers={'Content-Disposition': f'attachment; filename="{dataset_id}.{format}"',
                         'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @api.get('/runs/{run_id}/issues')
    def issues(request: Request, run_id: str, workspace_id: str):
        with allowed(request, workspace_id, export=True) as who:
            row = service.get('runs', workspace_id, run_id)
            import json
            for ref in json.loads(row['inputs_json']).values():
                check_sources(service, workspace_id, who.user['id'], ref['dataset_id'])
            from .service import decode
            rows = ctx.store.all('SELECT * FROM dataprep_run_issues WHERE run_id=? AND workspace_id=? ORDER BY seq', (run_id, workspace_id))
        return Response(canonical([decode(r) for r in rows]), media_type='application/json',
                        headers={'Content-Disposition': f'attachment; filename="{run_id}-issues.json"', 'Cache-Control': 'no-store'})
    return api
