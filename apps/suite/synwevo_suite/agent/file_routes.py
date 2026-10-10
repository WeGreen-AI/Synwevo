"""Personal file HTTP boundary. No workspace query parameter or machine/admin credential."""
from urllib.parse import quote

from fastapi import File, Header, Request, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from ..common.web import signed_in
from ..common.worker import Ticker
from .files import MAX_BYTES


def install(ctx, agent):
    files = agent.files
    files.reader = getattr(ctx.docs, 'ingest', ctx.docs)

    @ctx.api.get('/api/suite/agent/files/capabilities', tags=['agent'])
    def capabilities(request: Request):
        files.auth(signed_in(request)['id'])
        return files.capabilities()

    @ctx.api.post('/api/suite/agent/files', tags=['agent'])
    async def upload(request: Request, file: UploadFile = File(...),
                     idempotency_key: str = Header(default='', alias='Idempotency-Key')):
        try:
            uid = signed_in(request)['id']
            files.auth(uid)
            data = await file.read(MAX_BYTES+1)
            return await run_in_threadpool(files.upload, uid, file.filename or '', data, idempotency_key)
        finally:
            await file.close()

    @ctx.api.get('/api/suite/agent/files/{file_id}/download', tags=['agent'])
    def download(request: Request, file_id: str):
        row, data = files.download(signed_in(request)['id'],file_id)
        # Always download: user HTML/Markdown must not run on the authenticated origin.
        return Response(data, media_type='application/octet-stream', headers={
            'Content-Disposition': "attachment; filename*=UTF-8''"+quote(row['filename'],safe=''),
            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})

    if ctx.start_jobs:
        Ticker('agent-files',files.tick,1).start(ctx.api)
