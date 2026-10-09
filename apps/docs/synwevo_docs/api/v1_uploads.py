"""Machine API: resumable uploads. Included by `v1.build_router` under the /api/v1 prefix.

A 100 MB source file sent as one multipart request is one network hiccup away from starting
over, and an automation tool (n8n, Make) that retries it re-sends everything. So a large file
can instead arrive in byte ranges:

    POST /uploads            {filename, size, sha256?, purpose?}  -> {id, upload_url, expires_at}
    PUT  /uploads/{id}       Content-Range: bytes <start>-<end>/<size>, raw bytes
    GET  /uploads/{id}       -> {received, ...}   (where to resume after an interruption)
    POST /uploads/{id}/complete  {sha256?}        -> verifies size and SHA-256, stores the blob
    POST /uploads/{id}/abort

and is then used exactly once, by `POST /projects/{pid}/files` or `POST /templates` with
`{"upload_id": ...}`.

Why it is built the way it is:

* **Bytes go to a private temp file under the data directory, never into memory whole.** The
  request body is streamed chunk by chunk to ``<data>/tmp/uploads/<id>.part`` (mode 0600 in a
  0700 directory). The database's ``received`` is the truth about how many bytes are good: each
  PUT first truncates the file back to it, so a write that crashed after the bytes but before
  the row update is simply overwritten by the resend.
* **Nothing can use an upload until `complete` has checked it.** The declared size must be
  reached and the SHA-256 (declared at create or complete) must match; a mismatch resets the
  upload to zero rather than storing bytes the client did not mean to send.
* **One upload, one use, one workspace.** Consumption flips ``complete → consumed`` with a
  conditional update, so two concurrent requests cannot both turn one upload into two files;
  an upload of another workspace is reported as not found, like every other object.
* **An upload is for what it was declared for.** ``purpose`` (``source`` or ``template``) is
  stored; an upload declared for one cannot be consumed as the other (409
  ``upload_wrong_purpose``). An upload created without a purpose may be used for either.
* **Uploads expire** 24 hours after creation. Expired or aborted uploads cannot be resumed or
  used, and `cleanup_expired()` removes their temp files; the job supervisor calls it
  periodically (`JobService._sweep_uploads`).
* **Pending uploads are bounded per workspace.** ``open`` and ``complete``-but-unused uploads
  both count against ``SYNWEVO_DOCS_MAX_OPEN_UPLOADS``: a completed upload's bytes already sit in
  the content store, and counting only open ones let a key create → send → complete in a loop.
* **Writers of one upload are serialised** by an exclusive lock on its temp file: ``PUT``,
  ``complete`` and ``abort`` take it without waiting and answer 409 ``upload_busy`` when another
  request holds it, and the periodic cleanup skips an upload that is being written.
* **The event loop is never blocked on the disk.** ``PUT`` streams the body asynchronously but
  writes, fsyncs and updates the database in the thread pool.

What is *not* claimed: using an upload reads the stored blob into memory once, because the
services that create a source file or a template take bytes (as a multipart upload does).
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Literal

try:                                    # POSIX: a lock that also holds across worker processes
    import fcntl
except ImportError:                     # pragma: no cover - Windows has no flock
    fcntl = None

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from synwevo_core import authority, env
from synwevo_core import quarantine
from synwevo_core.context import tenant as current_tenant
from synwevo_core.errors import ServiceError, not_found
from synwevo_core.store import Store, new_id, now

UPLOAD_TTL_S = 24 * 3600
MAX_OPEN_UPLOADS = int(env.docs("MAX_OPEN_UPLOADS", "20"))
PURPOSES = ("source", "template")
_ID = re.compile(r"^up_[0-9a-f]{12}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")
_CHUNK = 1024 * 1024


class UploadView(BaseModel):
    """The public session, not a blob/authority record. Null metadata stays explicit."""
    model_config = ConfigDict(strict=True)

    id: str
    filename: str
    size: int
    sha256: str | None
    purpose: Literal["source", "template"] | None
    received: int
    status: Literal["open", "complete", "consumed", "expired", "aborted"]
    created_at: float
    updated_at: float
    expires_at: float
    complete: bool
    upload_url: str


# --------------------------------------------------------------------- storage
def tmp_dir(store: Store) -> Path:
    d = Path(store.root) / "tmp" / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def part_path(store: Store, uid: str) -> Path:
    # The id comes from our own row, but it still becomes a file name: pin its shape so no
    # value can ever step outside the directory.
    if not _ID.match(uid or ""):
        raise ValueError("invalid upload id")
    return tmp_dir(store) / f"{uid}.part"


def _unlink_part(store: Store, uid: str) -> None:
    try:
        path = part_path(store, uid)
        path.unlink(missing_ok=True)
        path.with_suffix(".blob").unlink(missing_ok=True)
    except ValueError:
        pass


def cleanup_expired(store: Store, at: float | None = None) -> int:
    """Expire open/complete uploads past their deadline and delete their temp files.

    Returns how many uploads were expired. Temp files of uploads that already ended (aborted,
    consumed, expired) are removed too, so a crash between the status change and the unlink
    does not leave bytes behind. Blobs of completed-but-unused uploads stay in the content
    store: another file may share the same content, and only a reference-aware sweep may
    delete a blob.
    """
    t = at if at is not None else now()
    rows = store.all("SELECT id, status FROM uploads WHERE status IN ('open','complete') AND expires_at < ?", (t,))
    n = 0
    for r in rows:
        if r["status"] == "open":
            # A request may be writing or completing it right now; expiring it under that
            # request turned a finished `complete` into a 500. The next sweep takes it.
            lock = _try_lock(part_path(store, r["id"]))
            if lock is None:
                continue
            with lock:
                n += store.execute("UPDATE uploads SET status='expired', updated_at=? WHERE id=? AND status='open' "
                                   "AND expires_at < ?", (t, r["id"], t))
            continue
        n += store.execute("UPDATE uploads SET status='expired', updated_at=? WHERE id=? AND status='complete' "
                           "AND expires_at < ?", (t, r["id"], t))
    d = Path(store.root) / "tmp" / "uploads"
    if d.is_dir():
        # Walk the (few) temp files rather than every upload ever made; only an upload that is
        # still open owns its bytes.
        for f in (*d.glob("up_*.part"), *d.glob("up_*.blob")):
            row = store.one("SELECT status FROM uploads WHERE id=?", (f.stem,))
            if not row or row["status"] != "open":
                f.unlink(missing_ok=True)
    return n


def _view(row: dict, base: str | None = None) -> dict:
    out = {k: row.get(k) for k in ("id", "filename", "size", "sha256", "purpose", "received", "status",
                                   "created_at", "updated_at", "expires_at")}
    out["complete"] = row.get("status") in ("complete", "consumed")
    if base:
        out["upload_url"] = f"{base}/api/v1/uploads/{row['id']}"
    return out


def _load(store: Store, uid: str) -> dict:
    """The caller's upload, with an overdue one turned into `expired` on the spot."""
    row = store.one("SELECT * FROM uploads WHERE id=? AND tenant=?", (uid, current_tenant()))
    if not row:
        not_found("上传")
    if row["status"] in ("open", "complete") and row["expires_at"] < now():
        if row["status"] == "open":
            lock = _try_lock(part_path(store, uid))
            if lock is None:
                # A request is writing or completing it at this moment; report it as expired
                # but leave the row and the file to that request (the sweep finishes the job).
                return {**row, "status": "expired"}
            with lock:
                store.execute("UPDATE uploads SET status='expired', updated_at=? WHERE id=? AND status='open'",
                              (now(), uid))
                _unlink_part(store, uid)
        else:
            store.execute("UPDATE uploads SET status='expired', updated_at=? WHERE id=? AND status='complete'",
                          (now(), uid))
        row = {**row, "status": "expired"}
    return row


def _gone(row: dict) -> ServiceError | None:
    st = row["status"]
    if st == "expired":
        return ServiceError(410, "upload_expired", "该上传已过期（创建后 24 小时内有效）；请重新创建上传", {"field": "upload_id"})
    if st == "aborted":
        return ServiceError(410, "upload_aborted", "该上传已取消；请重新创建上传", {"field": "upload_id"})
    if st == "consumed":
        return ServiceError(409, "upload_consumed", "该上传已被使用过；每个上传只能使用一次", {"field": "upload_id"})
    return None


class _Locked:
    """Exclusive, non-blocking lock on an upload's temp file (across threads and processes).

    ``with _Locked(path) as fd`` answers 409 ``upload_busy`` when another request holds it;
    `_try_lock` is the variant that reports "busy" as None instead.
    """

    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def acquire(self) -> bool:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                return False
        self.fd = fd
        return True

    def release(self) -> None:
        fd, self.fd = self.fd, None
        if fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> int:
        if self.fd is None and not self.acquire():
            raise ServiceError(409, "upload_busy", "该上传正在被另一个请求写入；请等它结束后再试")
        return self.fd

    def __exit__(self, *exc):
        self.release()


def _try_lock(path: Path) -> _Locked | None:
    """The lock, already held (use it in a ``with``), or None when another request holds it."""
    lk = _Locked(path)
    return lk if lk.acquire() else None


def _rewind(fd: int, received: int) -> None:
    if os.fstat(fd).st_size < received:
        os.ftruncate(fd, 0)
        raise ServiceError(409, "upload_data_missing", "暂存内容不完整，上传已重置；请重新发送全部字节",
                           {"received": 0})
    os.ftruncate(fd, received)
    os.lseek(fd, received, os.SEEK_SET)


def _write_all(fd: int, data) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _copy_completed_blob(store: Store, uid: str, fd: int, digest: str) -> None:
    """Publish verified bytes without consuming the only recoverable upload file.

    A failed database commit must leave its original bytes available. A hard link is
    unsuitable: a subsequent range retry could then mutate an existing shared blob.
    The copy is streamed to a private staging file, fsynced and atomically renamed.
    Aborted/expired staging files are covered by the same upload cleanup.
    """
    dest = store.blob_path(digest)
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = part_path(store, uid).with_suffix(".blob")
    out_fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        while chunk := os.read(fd, _CHUNK):
            _write_all(out_fd, chunk)
        os.fsync(out_fd)
    finally:
        os.close(out_fd)
    os.replace(stage, dest)


def use_upload(store: Store, upload_id, make, kind: str, check_size=None) -> dict:
    """Turn a completed upload into a source file or template, exactly once.

    `make(filename, data)` does the actual work and returns the created object. The upload is
    claimed first with a conditional update, so a concurrent second use sees `consumed`; if
    `make` fails the claim is released and the upload stays usable.
    """
    if not isinstance(upload_id, str) or not _ID.match(upload_id):
        raise ServiceError(400, "invalid", "upload_id 格式不正确", {"field": "upload_id"})
    row = _load(store, upload_id)
    err = _gone(row)
    if err:
        raise err
    if row["status"] == "open":
        raise ServiceError(409, "upload_incomplete", "该上传尚未完成（需先 POST /uploads/{id}/complete 校验）",
                           {"field": "upload_id", "received": row["received"], "size": row["size"]})
    if row.get("purpose") and row["purpose"] != kind:
        use = {"source": "来源文件（POST /projects/{pid}/files）", "template": "模板（POST /templates）"}
        raise ServiceError(409, "upload_wrong_purpose",
                           f"该上传创建时声明用于{use.get(row['purpose'], row['purpose'])}，不能用作{use.get(kind, kind)}",
                           {"field": "upload_id", "purpose": row["purpose"]})
    if quarantine.verdict(store, row.get("blob")):
        # Completed before the verdict: the bytes are not read, and not used for anything (CI-90).
        from ..services import earlier_malicious
        raise earlier_malicious(row.get("filename") or "upload")
    if check_size is not None:
        check_size(int(row["size"]), row["filename"])
    claimed = store.execute("UPDATE uploads SET status='consumed', consumed_by=?, updated_at=? "
                            "WHERE id=? AND tenant=? AND status='complete'",
                            (f"{kind}:pending", now(), upload_id, current_tenant()))
    if claimed != 1:
        raise _gone(_load(store, upload_id)) or ServiceError(409, "upload_consumed", "该上传已被使用过", {"field": "upload_id"})
    try:
        out = make(row["filename"], store.get_blob(row["blob"]))
    except BaseException:
        store.execute("UPDATE uploads SET status='complete', consumed_by=NULL, updated_at=? "
                      "WHERE id=? AND status='consumed' AND consumed_by=?", (now(), upload_id, f"{kind}:pending"))
        raise
    store.execute("UPDATE uploads SET consumed_by=?, updated_at=? WHERE id=?",
                  (f"{kind}:{out.get('id')}", now(), upload_id))
    return out


def _sha_or_none(v, field: str = "sha256") -> str | None:
    if v is None or v == "":
        return None
    if not isinstance(v, str) or not _SHA.match(v.lower()):
        raise ServiceError(400, "invalid", "sha256 必须是 64 位十六进制字符串", {"field": field})
    return v.lower()


# --------------------------------------------------------------------- routes
def build_router(app_, auth, idem, scope, public_base) -> APIRouter:
    r = APIRouter(tags=["v1"])
    store: Store = app_.store

    def visible_upload(uid: str) -> dict:
        row = _load(store, uid)
        if row["status"] == "consumed":
            # The session remains useful as a retry handle, but cannot become an alternate
            # metadata endpoint for a source/template that has since been removed. Resolve
            # its committed consumer using the same live-object checks as the business API.
            kind, _, target = (row.get("consumed_by") or "").partition(":")
            if kind == "source":
                app_.get_source(target)
            elif kind == "template":
                app_.get_template(target)
            else:
                not_found("上传")
        return row

    @r.post("/uploads", status_code=201, response_model=UploadView, summary="创建可续传上传（大文件分段上传）",
            # Explicit RFC 9110 names: FastAPI otherwise takes them from the running Python,
            # whose 413/416 phrases changed in 3.13, so the published contract would depend on it.
            responses={410: {"$ref": "#/components/responses/Gone"},
                       413: {"description": "Content Too Large", "$ref": "#/components/responses/PayloadTooLarge"}},
            openapi_extra={"x-scope": "write", "x-idempotent": True, "requestBody": {"content": {"application/json": {
                "schema": {"type": "object", "required": ["filename", "size"], "properties": {
                    "filename": {"type": "string", "maxLength": 255},
                    "size": {"type": "integer", "minimum": 1, "description": "文件字节数，不超过 capabilities.limits.max_file_bytes 的当前接纳上限"},
                    "sha256": {"type": "string", "description": "可选：整个文件的 SHA-256（十六进制），complete 时校验"},
                    "purpose": {"type": "string", "enum": list(PURPOSES)}}}}}}})
    def create_upload(request: Request, body: dict = Body(...)):
        scope(request, "write")
        name = body.get("filename")
        if not isinstance(name, str) or not name.strip() or len(name) > 255:
            raise ServiceError(400, "invalid", "filename 必须是 1–255 个字符的文件名", {"field": "filename"})
        size = body.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ServiceError(400, "invalid", "size 必须是正整数（字节数）", {"field": "size"})
        sha = _sha_or_none(body.get("sha256"))
        purpose = body.get("purpose")
        if purpose is not None and purpose not in PURPOSES:
            raise ServiceError(400, "invalid", "purpose 只能是 source 或 template", {"field": "purpose"})
        unknown = set(body) - {"filename", "size", "sha256", "purpose"}
        if unknown:
            raise ServiceError(400, "invalid", f"不认识的字段：{', '.join(sorted(unknown))}", {"field": sorted(unknown)[0]})

        def make():
            app_.require_upload_size(size, name, field="size")
            t = now()
            live = store.one("SELECT COUNT(*) AS n FROM uploads WHERE tenant=? AND status IN ('open','complete') "
                             "AND expires_at >= ?", (current_tenant(), t))["n"]
            if live >= MAX_OPEN_UPLOADS:
                # Each pending upload may hold up to the file size limit on disk — an open one in
                # its temp file, a completed-but-unused one in the content store. An unbounded
                # number of either is a way for one key to fill the data volume.
                raise ServiceError(409, "too_many_open_uploads",
                                   f"未使用的上传已达上限（{MAX_OPEN_UPLOADS} 个，含已完成但尚未使用的）；"
                                   "请先使用、完成或取消已有上传",
                                   {"limit": MAX_OPEN_UPLOADS})
            uid = new_id("up")
            row = {"id": uid, "tenant": current_tenant(),
                   "principal": authority.dumps(authority.describe_request(request)),
                   "filename": name.strip(), "size": size, "sha256": sha, "purpose": purpose,
                   "received": 0, "status": "open",
                   "created_at": t, "updated_at": t, "expires_at": t + UPLOAD_TTL_S}
            store.insert("uploads", row)
            part_path(store, uid).touch(mode=0o600)
            return _view(row, public_base(request))

        def replay_check(out):
            row = visible_upload(out["id"])
            err = _gone(row)
            if err and row["status"] != "consumed":
                raise err
            # Replaying creation does not consume again: return the original response, so
            # a client can recover its upload_id and replay the separately keyed use.

        return idem.run(request, "/api/v1/uploads", {"body": body}, make,
                        replay_check=replay_check, status=201)

    @r.get("/uploads/{uid}", response_model=UploadView,
           summary="上传状态（已收到的字节数，用于断点续传）", openapi_extra={"x-scope": "read"})
    def get_upload(request: Request, uid: str):
        scope(request, "read")
        return _view(visible_upload(uid), public_base(request))

    @r.put("/uploads/{uid}", response_model=UploadView, summary="写入一段字节（Content-Range: bytes 起-止/总长）",
           responses={413: {"description": "Content Too Large", "$ref": "#/components/responses/PayloadTooLarge"},
                      409: {"$ref": "#/components/responses/Conflict"},
                      410: {"$ref": "#/components/responses/Gone"},
                      416: {"description": "Range Not Satisfiable", "$ref": "#/components/responses/RangeNotSatisfiable"}},
           openapi_extra={"x-scope": "write", "requestBody": {"required": True, "content": {
               "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
               "parameters": [{"name": "Content-Range", "in": "header", "required": False,
                               "description": "bytes <start>-<end>/<size>；省略时视为整个文件（从 0 开始）",
                               "schema": {"type": "string", "example": "bytes 0-1048575/5242880"}}]})
    async def put_bytes(request: Request, uid: str):
        scope(request, "write")
        row = await run_in_threadpool(visible_upload, uid)
        err = _gone(row)
        if err:
            raise err
        if row["status"] != "open":
            raise ServiceError(409, "upload_not_open", "该上传已完成校验，不能再写入", {"status": row["status"]})
        app_.require_upload_size(int(row["size"]), row["filename"])
        size, received = int(row["size"]), int(row["received"])
        cr = request.headers.get("content-range")
        if cr:
            m = _RANGE.match(cr.strip())
            if not m:
                raise ServiceError(400, "invalid", "Content-Range 格式应为 bytes <起>-<止>/<总长>", {"field": "Content-Range"})
            start, end = int(m.group(1)), int(m.group(2))
            if m.group(3) != "*" and int(m.group(3)) != size:
                raise ServiceError(400, "invalid", f"Content-Range 的总长与创建时声明的 size（{size}）不一致",
                                   {"field": "Content-Range"})
            if end < start or end >= size:
                raise ServiceError(400, "invalid", "Content-Range 的范围无效", {"field": "Content-Range"})
        else:
            start, end = 0, size - 1
        if start > received:
            # A gap would leave bytes nobody sent; the client resumes from `received`.
            raise ServiceError(416, "upload_range_gap", f"应从第 {received} 字节继续上传",
                               {"field": "Content-Range", "received": received})
        want = end - start + 1
        skip = received - start                  # a resend of bytes we already have: drop them
        lock = _Locked(part_path(store, uid))
        # The body is read asynchronously; everything that touches the disk or the database runs
        # in the thread pool, so a 100 MB chunk's writes and fsync never stall the event loop
        # (and with it every other request this worker is serving).
        if not await run_in_threadpool(lock.acquire):
            raise ServiceError(409, "upload_busy", "该上传正在被另一个请求写入；请等它结束后再试")
        with lock as fd:
            # Re-read under the lock: another request may have advanced it meanwhile.
            cur = await run_in_threadpool(store.one, "SELECT received, status FROM uploads WHERE id=?", (uid,))
            if cur["status"] != "open" or int(cur["received"]) != received:
                raise ServiceError(409, "upload_busy", "该上传刚被另一个请求改变；请 GET /uploads/{id} 后从 received 继续")
            try:
                await run_in_threadpool(_rewind, fd, received)
            except ServiceError:
                await run_in_threadpool(store.execute,
                                        "UPDATE uploads SET received=0, updated_at=? WHERE id=? AND status='open'",
                                        (now(), uid))
                raise
            seen = 0
            buf = bytearray()
            try:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    seen += len(chunk)
                    if seen > want:
                        raise ServiceError(400, "upload_length_mismatch",
                                           f"请求体比 Content-Range 声明的 {want} 字节更长", {"field": "Content-Range"})
                    lo = max(0, skip - (seen - len(chunk)))
                    if lo < len(chunk):
                        buf += chunk[lo:]
                    if len(buf) >= _CHUNK:
                        data, buf = bytes(buf), bytearray()
                        await run_in_threadpool(_write_all, fd, data)
                if seen != want:
                    raise ServiceError(400, "upload_length_mismatch",
                                       f"请求体长度 {seen} 与 Content-Range 声明的 {want} 字节不一致", {"field": "Content-Range"})
                if buf:
                    await run_in_threadpool(_write_all, fd, bytes(buf))
                await run_in_threadpool(os.fsync, fd)
            except BaseException:
                # Synchronous on purpose: this also runs when the request is cancelled, where an
                # await would not. Truncating is a metadata update, not a data write.
                os.ftruncate(fd, received)
                raise
            new_received = max(received, end + 1)
            await run_in_threadpool(store.execute, "UPDATE uploads SET received=?, updated_at=? "
                                    "WHERE id=? AND received=? AND status='open'", (new_received, now(), uid, received))
        row = await run_in_threadpool(visible_upload, uid)
        return _view(row, public_base(request))

    @r.post("/uploads/{uid}/complete", response_model=UploadView,
            responses={413: {"description": "Content Too Large", "$ref": "#/components/responses/PayloadTooLarge"},
                      409: {"$ref": "#/components/responses/Conflict"},
                       410: {"$ref": "#/components/responses/Gone"}},
            summary="完成上传：校验大小与 SHA-256 后保存", openapi_extra={"x-scope": "write"})
    def complete_upload(request: Request, uid: str, body: dict = Body(default={})):
        scope(request, "write")
        row = visible_upload(uid)
        err = _gone(row)
        if err and row["status"] != "consumed":
            raise err
        if row["status"] in ("complete", "consumed"):
            return _view(row, public_base(request))           # completing twice is not an error
        app_.require_upload_size(int(row["size"]), row["filename"])
        declared = _sha_or_none(row.get("sha256"))
        given = _sha_or_none((body or {}).get("sha256"))
        if declared and given and declared != given:
            raise ServiceError(400, "checksum_conflict", "complete 提供的 sha256 与创建时声明的不一致", {"field": "sha256"})
        if int(row["received"]) != int(row["size"]):
            raise ServiceError(409, "upload_incomplete", f"只收到 {row['received']} / {row['size']} 字节",
                               {"received": row["received"], "size": row["size"]})
        path = part_path(store, uid)
        with _Locked(path) as fd:
            # Re-read under the lock: an abort, an expiry or another complete may have won.
            cur = store.one("SELECT * FROM uploads WHERE id=?", (uid,))
            if cur["status"] in ("complete", "consumed"):
                return _view(visible_upload(uid), public_base(request))
            if cur["status"] != "open":
                raise _gone(cur) or ServiceError(409, "upload_busy", "该上传刚被另一个请求改变")
            if int(cur["received"]) != int(row["received"]):
                raise ServiceError(409, "upload_busy", "该上传刚被另一个请求改变；请 GET /uploads/{id} 后重试")
            if os.fstat(fd).st_size < int(row["received"]):
                # Missing/truncated staging bytes are not a sparse file to fill with zeroes.
                # Reset the upload so its caller can explicitly resend the original content.
                os.ftruncate(fd, 0)
                store.execute("UPDATE uploads SET received=0, updated_at=? WHERE id=? AND status='open'", (now(), uid))
                raise ServiceError(409, "upload_data_missing", "暂存内容不完整，上传已重置；请重新发送全部字节",
                                   {"received": 0})
            os.ftruncate(fd, int(row["received"]))
            os.lseek(fd, 0, os.SEEK_SET)
            h = hashlib.sha256()
            while True:
                chunk = os.read(fd, _CHUNK)
                if not chunk:
                    break
                h.update(chunk)
            actual = h.hexdigest()
            expected = given or declared
            if expected and actual != expected:
                # Wrong bytes are not stored. Reset so the same upload can be sent again.
                os.ftruncate(fd, 0)
                store.execute("UPDATE uploads SET received=0, updated_at=? WHERE id=? AND status='open'", (now(), uid))
                raise ServiceError(400, "checksum_mismatch", "收到的内容与声明的 SHA-256 不一致；上传已重置，请重新发送全部字节",
                                   {"field": "sha256", "expected": expected, "actual": actual})
            if quarantine.verdict(store, actual):
                # Bytes a quarantine verdict took out of the store are not written back by a
                # completed upload (CI-90): the upload ends here, its staging bytes deleted.
                store.execute("UPDATE uploads SET status='aborted', received=0, updated_at=? WHERE id=? AND status='open'", (now(), uid))
                _unlink_part(store, uid)
                from ..services import earlier_malicious
                raise earlier_malicious(row.get("filename") or "upload")
            _copy_completed_blob(store, uid, fd, actual)
            done = store.execute("UPDATE uploads SET status='complete', blob=?, sha256=?, updated_at=? "
                                 "WHERE id=? AND status='open'", (actual, actual, now(), uid))
            if done != 1:
                # Report a competing lifecycle change instead of claiming completion.
                now_row = _load(store, uid)
                raise _gone(now_row) or ServiceError(409, "upload_busy", "该上传刚被另一个请求改变",
                                                     {"status": now_row["status"]})
            _unlink_part(store, uid)
        return _view(visible_upload(uid), public_base(request))

    @r.post("/uploads/{uid}/abort", response_model=UploadView,
            responses={409: {"$ref": "#/components/responses/Conflict"}},
            summary="取消上传并删除已收到的字节", openapi_extra={"x-scope": "write"})
    def abort_upload(request: Request, uid: str):
        scope(request, "write")
        row = visible_upload(uid)
        if row["status"] == "consumed":
            raise _gone(row)
        if row["status"] in ("open", "complete"):
            # Under the upload's lock: aborting while a PUT or complete holds it used to delete
            # the file under them (a 500 from complete, or a 200 "aborted" beside a stored blob).
            with _Locked(part_path(store, uid)):
                store.execute("UPDATE uploads SET status='aborted', updated_at=? WHERE id=? "
                              "AND status IN ('open','complete')", (now(), uid))
                _unlink_part(store, uid)
        return _view(visible_upload(uid), public_base(request))

    return r


def upload_body_schema() -> dict:
    """The JSON alternative to multipart on the two routes that take a file (for OpenAPI)."""
    return {"type": "object", "properties": {
        "upload_id": {"type": "string", "description": "已 complete 的可续传上传（POST /uploads）"}}}
