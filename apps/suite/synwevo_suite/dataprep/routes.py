"""Binary upload/download only. Business actions are registered operations."""
from typing import Literal
from fastapi import APIRouter, Request, File, UploadFile, Form, Header
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from synwevo_core.errors import ServiceError
from synwevo_core.capabilities import canonical
from ..common.web import signed_in
from .models import MAX_FILE
from .history import check_sources


def router(ctx, service):
    api = APIRouter(prefix='/api/suite/dataprep', tags=['dataprep'])

    def authorize(request, workspace, need='read', export=False):
        user = signed_in(request)
        ctx.capabilities.authorize(ctx.registry.get('dataprep.run.start' if need == 'write' else 'dataprep.dataset.get'), user['id'], workspace)
        if export:
            member = ctx.identity.membership(workspace, user['id'])
            if not member or not (member['role'] in ('owner', 'admin', 'editor') or member.get('can_export')):
                raise ServiceError(403, 'export_not_allowed', '你在该工作空间没有导出权限')
        return user

    @api.post('/files')
    async def upload(request: Request, workspace_id: str, file: UploadFile = File(...),
                     idempotency_key: str | None = Header(None, min_length=1, max_length=200),
                     upload_key: str | None = Form(None, min_length=1, max_length=200)):
        user = authorize(request, workspace_id, 'write')
        key = idempotency_key or upload_key
        if not key:
            raise ServiceError(422, 'invalid_input', '上传需要重试标识')
        try:
            data = await file.read(MAX_FILE + 1)
            if not data or len(data) > MAX_FILE:
                raise ServiceError(413, 'file_limit', '文件为空或超过 8 MB')
            return await run_in_threadpool(service.upload, workspace_id, user['id'], file.filename or '', data, key)
        finally:
            await file.close()

    @api.get('/datasets/{dataset_id}/export')
    def export(request: Request, dataset_id: str, workspace_id: str, format: Literal['csv', 'xlsx', 'json'] = 'csv'):
        user = authorize(request, workspace_id, export=True)
        check_sources(service, workspace_id, user['id'], dataset_id)
        data, mime = service.export(workspace_id, dataset_id, format)
        return Response(data, media_type=mime, headers={'Content-Disposition': f'attachment; filename="{dataset_id}.{format}"',
                         'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @api.get('/runs/{run_id}/issues')
    def issues(request: Request, run_id: str, workspace_id: str):
        user = authorize(request, workspace_id, export=True)
        row = service.get('runs', workspace_id, run_id)
        import json
        for ref in json.loads(row['inputs_json']).values():
            check_sources(service, workspace_id, user['id'], ref['dataset_id'])
        from .service import decode
        rows = ctx.store.all('SELECT * FROM dataprep_run_issues WHERE run_id=? AND workspace_id=? ORDER BY seq', (run_id, workspace_id))
        return Response(canonical([decode(r) for r in rows]), media_type='application/json',
                        headers={'Content-Disposition': f'attachment; filename="{run_id}-issues.json"', 'Cache-Control': 'no-store'})
    return api
