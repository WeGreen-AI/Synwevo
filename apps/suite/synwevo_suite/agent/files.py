"""User-owned, bounded files for explicit Agent context. Parsing never invokes a model."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synwevo_core.capabilities import canonical, fingerprint
from synwevo_core.errors import ServiceError
from synwevo_core.member_import import accept_upload
from synwevo_core.store import new_id, now
from ..common.files import available_blob
from ..common.process import TIMEOUT, run_bounded

MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 50
MAX_STORAGE = 50 * 1024 * 1024
RETENTION = 86400
MAX_CHARS = 60_000
FILE_ID = r'^af_[a-f0-9]{12}$'
# Metadata reads never load up to 8 MiB per file while checking history or listing files.
METADATA = 'id,user_id,filename,size,content_hash,idempotency_key,status,scan_json,parsed_json,snapshot_hash,error_code,attempts,lease_token,lease_until,created_at,updated_at,expires_at,content IS NOT NULL AS has_content'


class FileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    file_id: str = Field(pattern=FILE_ID)


class FileReference(FileInput):
    snapshot: str = Field(pattern=r'^[a-f0-9]{64}$')


class FileList(BaseModel):
    model_config = ConfigDict(extra='forbid')
    offset: int = Field(default=0, strict=True, ge=0, le=10000)


class FileView(BaseModel):
    id: str
    filename: str
    size: int
    created_at: float
    updated_at: float
    attempts: int
    status: Literal['receiving','queued','parsing','ready','failed','cancelled','deleted']
    error_code: str | None
    scan_status: str
    reference: FileReference | None
    download_path: str | None


class FileDetail(FileView):
    extraction: dict | None


class FilePage(BaseModel):
    files: list[FileView]
    next_offset: int | None


class FileDeleted(BaseModel):
    id: str
    status: Literal['deleted']


class Files:
    def __init__(self, agent, reader=None):
        self.agent, self.store, self.reader = agent, agent.store, reader
        self.lock = threading.Lock()
        self.stopped = False

    def auth(self, uid):
        op = self.agent.caps.registry.get('agent.conversation.get')
        self.agent.caps.authorize(op, uid, None)

    def capabilities(self):
        supported = callable(getattr(self.reader, 'extract_context', None))
        description = self.reader.describe() if supported else {}
        return {'configured': supported, 'formats': description.get('context_formats', []),
                'max_bytes': MAX_BYTES, 'max_files': MAX_FILES, 'max_storage_bytes': MAX_STORAGE,
                'max_context_chars': MAX_CHARS, 'unattached_retention_seconds': RETENTION,
                'scanner': description.get('scanner'), 'ocr': False}

    def row(self, uid, fid):
        self.auth(uid)
        row = self.store.one(f'SELECT {METADATA} FROM agent_files WHERE id=? AND user_id=?', (fid, uid))
        if not row:
            raise ServiceError(404, 'agent_file_not_found', '个人文件不存在')
        return row

    def _expired(self, row):
        return row['expires_at'] <= now() and not self.store.one(
            'SELECT 1 FROM agent_messages WHERE user_id=? AND sources_json LIKE ? LIMIT 1',
            (row['user_id'], '%'+row['id']+'%'))

    def _available(self, row):
        if self._expired(row):
            raise ServiceError(409, 'agent_file_expired', '临时文件已过期，请重新上传')
        if row['status'] != 'ready' or not row['has_content'] or not row['parsed_json']:
            raise ServiceError(409, 'agent_file_not_ready', '文件尚未就绪或已被删除')
        available_blob(self.store, row['content_hash'])

    def describe(self, row, *, preview=False):
        status, error = row['status'], row['error_code']
        if status == 'ready':
            try:
                self._available(row)
            except ServiceError as exc:
                status, error = 'failed', exc.code
        out = {k: row[k] for k in ('id', 'filename', 'size', 'created_at', 'updated_at', 'attempts')}
        out.update(status=status, error_code=error,
                   scan_status=json.loads(row['scan_json'] or '{}').get('status', 'pending'),
                   reference={'file_id': row['id'], 'snapshot': row['snapshot_hash']} if status == 'ready' else None,
                   download_path='/api/suite/agent/files/' + row['id'] + '/download' if status == 'ready' else None)
        if preview:
            out['extraction'] = json.loads(row['parsed_json']) if status == 'ready' else None
        return out

    def get(self, uid, fid):
        return self.describe(self.row(uid, fid), preview=True)

    def list(self, uid, offset):
        self.auth(uid)
        rows = self.store.all(f"SELECT {METADATA} FROM agent_files WHERE user_id=? AND status!='deleted' ORDER BY created_at DESC,id DESC LIMIT 21 OFFSET ?", (uid, offset))
        return {'files': [self.describe(r) for r in rows[:20]], 'next_offset': offset + 20 if len(rows) > 20 else None}

    def upload(self, uid, filename, data, key):
        self.auth(uid)
        self.agent._check_person(uid, None)
        filename = PurePosixPath(filename.replace('\\', '/')).name
        if not filename or len(filename) > 180 or any(ord(c) < 32 or ord(c) == 127 for c in filename):
            raise ServiceError(422, 'invalid_filename', '文件名无效')
        if not key or len(key) > 200 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ServiceError(422, 'idempotency_required', '上传需要有效幂等键')
        if not data or len(data) > MAX_BYTES:
            raise ServiceError(413, 'agent_file_limit', '请选择不超过8 MB的非空文件')
        caps = self.capabilities()
        if not caps['configured']:
            raise ServiceError(503, 'agent_file_not_configured', '个人文件读取服务尚未配置')
        if filename.rsplit('.', 1)[-1].lower() not in caps['formats']:
            raise ServiceError(422, 'context_format_unsupported', '此文件格式暂不支持完整读取')
        digest = hashlib.sha256(data).hexdigest()
        available_blob(self.store, digest)
        ts, fid = now(), new_id('af')
        with self.store.tx():
            self.auth(uid)
            self.agent._check_person(uid, None)
            prior = self.store.one(f'SELECT {METADATA} FROM agent_files WHERE user_id=? AND idempotency_key=?', (uid, key))
            if prior:
                if prior['content_hash'] != digest or prior['filename'] != filename:
                    raise ServiceError(409, 'idempotency_conflict', '同一上传标识对应不同文件')
                return self.describe(prior)
            usage = self.store.one("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes FROM agent_files WHERE user_id=? AND status!='deleted'", (uid,))
            daily = self.store.one('SELECT COUNT(*) AS n FROM agent_files WHERE user_id=? AND created_at>?', (uid, ts-86400))['n']
            pending = self.store.one("SELECT COUNT(*) AS n FROM agent_files WHERE status='receiving'")['n']
            if usage['n'] >= MAX_FILES or usage['bytes']+len(data) > MAX_STORAGE or daily >= 100:
                raise ServiceError(413, 'agent_file_quota', '个人文件配额已用完，请删除不用的文件后重试')
            if pending >= 4:
                raise ServiceError(429, 'agent_file_busy', '文件接纳繁忙，请稍后重试')
            self.store.insert('agent_files', {'id': fid, 'user_id': uid, 'filename': filename, 'size': len(data),
                'content_hash': digest, 'idempotency_key': key, 'status': 'receiving', 'created_at': ts,
                'updated_at': ts, 'expires_at': ts+RETENTION, 'lease_until': ts+300})
        try:
            scan = accept_upload(filename, data)
            with self.store.tx():
                current = self.row(uid, fid)
                self.agent._check_person(uid, None)
                if current['status'] != 'receiving':
                    return self.describe(current)
                available_blob(self.store, digest)
                self.store.update('agent_files', {'id': fid}, {'content': data, 'scan_json': canonical(scan),
                    'status': 'queued', 'updated_at': now(), 'lease_until': None})
        except ServiceError as exc:
            self.store.execute("UPDATE agent_files SET status='failed',error_code=?,lease_until=NULL,updated_at=? WHERE id=? AND status='receiving'", (exc.code, now(), fid))
            raise
        return self.describe(self.row(uid, fid))

    def resolve(self, uid, ref):
        row = self.row(uid, ref.file_id)
        self._available(row)
        if row['snapshot_hash'] != ref.snapshot:
            raise ServiceError(409, 'agent_file_changed', '所选文件版本已变化，请重新选择')
        return row

    def dependencies_lost(self, uid, sources):
        lost = set()
        seen = set()
        for s in sources:
            if not isinstance(s, dict) or 'file_id' not in s:
                continue
            key = canonical(s)
            if key in seen:
                continue
            seen.add(key)
            try:
                self.resolve(uid, FileReference(file_id=s['file_id'], snapshot=s['snapshot_hash']))
            except (ServiceError, ValueError, KeyError):
                lost.add('file:' + s['file_id'])
        return lost

    def delete(self, uid, fid):
        with self.store.tx():
            self.row(uid, fid)
            self.store.execute("UPDATE agent_files SET status='deleted',content=NULL,parsed_json=NULL,snapshot_hash=NULL,lease_token=NULL,lease_until=NULL,error_code=NULL,updated_at=? WHERE id=?", (now(), fid))
            # Saved context is also a copy of the input. Erase that copy; provenance remains so
            # all derived answers are hidden and queued steps stop. Other business artifacts stay.
            for m in self.store.all("SELECT id,api_json FROM agent_messages WHERE user_id=? AND role='user' AND sources_json LIKE ?", (uid, '%'+fid+'%')):
                payload = json.loads(m['api_json'] or '{}')
                if isinstance(payload, dict) and payload.get('type') == 'business_context_v1':
                    for item in payload.get('items', []):
                        if item.get('file_id') == fid:
                            item['data'] = None
                    self.store.execute('UPDATE agent_messages SET api_json=? WHERE id=?', (canonical(payload), m['id']))
        return {'id': fid, 'status': 'deleted'}

    def retry(self, uid, fid):
        with self.store.tx():
            self.agent._check_person(uid, None)
            row = self.row(uid, fid)
            if self._expired(row):
                raise ServiceError(409, 'agent_file_expired', '临时文件已过期，请重新上传')
            if row['status'] not in ('failed','cancelled') or not row['has_content'] or row['attempts'] >= 3:
                raise ServiceError(409, 'agent_file_retry_unavailable', '无法重试此文件，请检查文件后重新上传')
            available_blob(self.store, row['content_hash'])
            self.store.execute("UPDATE agent_files SET status='queued',error_code=NULL,lease_token=NULL,lease_until=NULL,updated_at=?,expires_at=? WHERE id=?", (now(), now()+RETENTION, fid))
        return self.describe(self.row(uid, fid))

    def cancel(self, uid, fid):
        with self.store.tx():
            row = self.row(uid, fid)
            if row['status'] in ('receiving','queued','parsing'):
                self.store.execute("UPDATE agent_files SET status='cancelled',lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (now(), fid))
        return self.describe(self.row(uid, fid))

    def download(self, uid, fid):
        row = self.row(uid, fid)
        self._available(row)
        return row, self._bytes(row)

    def _bytes(self, row):
        available_blob(self.store, row['content_hash'])
        saved = self.store.one('SELECT content FROM agent_files WHERE id=? AND user_id=?', (row['id'],row['user_id']))
        data = saved['content'] if saved else None
        if data is None or hashlib.sha256(data).hexdigest() != row['content_hash']:
            raise ServiceError(409, 'file_unavailable', '原文件当前不可用')
        available_blob(self.store, row['content_hash'])
        return data

    def sweep(self):
        ts = now()
        self.store.execute("UPDATE agent_files SET status='failed',error_code='upload_interrupted',lease_until=NULL,updated_at=? WHERE status='receiving' AND lease_until<?", (ts,ts))
        # Expire only unattached files. Referenced files live until the owner deletes them;
        # removing a conversation makes its unused inputs eligible for normal reclamation.
        for row in self.store.all("SELECT id,user_id FROM agent_files WHERE status NOT IN ('deleted','receiving','parsing') AND expires_at<?", (ts,)):
            with self.store.tx():
                if not self.store.one('SELECT 1 FROM agent_messages WHERE user_id=? AND sources_json LIKE ? LIMIT 1', (row['user_id'], '%'+row['id']+'%')):
                    self.store.execute("UPDATE agent_files SET status='deleted',content=NULL,parsed_json=NULL,snapshot_hash=NULL,updated_at=? WHERE id=? AND status NOT IN ('receiving','parsing') AND expires_at<?", (ts,row['id'],ts))
        self.store.execute("DELETE FROM agent_files WHERE status='deleted' AND updated_at<? AND NOT EXISTS (SELECT 1 FROM agent_messages WHERE user_id=agent_files.user_id AND sources_json LIKE '%'||agent_files.id||'%')", (ts-30*86400,))

    def tick(self):
        if self.stopped or not self.lock.acquire(blocking=False):
            return
        try:
            self.sweep()
            if not callable(getattr(self.reader, 'extract_context', None)):
                return
            with self.store.tx():
                active = self.store.one("SELECT COUNT(*) AS n FROM agent_files WHERE status='parsing' AND lease_until>=?", (now(),))['n']
                if active >= 2:
                    return
                row = self.store.one(f"SELECT {METADATA} FROM agent_files WHERE status='queued' OR (status='parsing' AND lease_until<?) ORDER BY created_at LIMIT 1", (now(),))
                if not row:
                    return
                if row['attempts'] >= 3:
                    self.store.execute("UPDATE agent_files SET status='failed',error_code='parse_interrupted',lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (now(),row['id']))
                    return
                token = new_id('aflease')
                self.store.execute("UPDATE agent_files SET status='parsing',attempts=attempts+1,lease_token=?,lease_until=?,updated_at=? WHERE id=?", (token,now()+TIMEOUT+30,now(),row['id']))
            def check():
                current = self.row(row['user_id'],row['id'])
                self.agent._check_person(row['user_id'], None)
                if self._expired(current):
                    raise ServiceError(409, 'agent_file_expired', '临时文件已过期，请重新上传')
                if self.stopped or current['status']!='parsing' or current['lease_token']!=token:
                    raise ServiceError(409,'parse_cancelled','文件读取已停止')
                available_blob(self.store,row['content_hash'])
            try:
                check()
                data = self._bytes(row)
                parsed = run_bounded(self.reader.extract_context, (data,row['filename']), check)
                raw = canonical(parsed)
                if not isinstance(parsed,dict) or parsed.get('status')!='ready' or parsed.get('lines_truncated') or any(x.get('truncated') for x in parsed.get('tables',[])):
                    raise ServiceError(422,'context_incomplete','文件内容未完整读取')
                if len(raw)>MAX_CHARS:
                    # The same refusal as the reader's own output cap: the picker words it as
                    # contextLimit. (`context_too_large` is the business-context picker's code.)
                    raise ServiceError(413,'context_limit_exceeded','文件上下文超过完整解析上限，请拆分文件后重试')
                digest = fingerprint({'content_hash':row['content_hash'],'filename':row['filename'],'parsed':parsed})
                with self.store.tx():
                    check()
                    self.store.execute("UPDATE agent_files SET status='ready',parsed_json=?,snapshot_hash=?,error_code=NULL,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (raw,digest,now(),row['id']))
            except ServiceError as exc:
                self.store.execute("UPDATE agent_files SET status='failed',error_code=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=? AND status='parsing' AND lease_token=?", (exc.code,now(),row['id'],token))
        finally:
            self.lock.release()

    def stop(self):
        self.stopped = True
