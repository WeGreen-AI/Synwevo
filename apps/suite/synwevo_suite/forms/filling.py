"""Filling in a form through its link — the part of Synwevo Forms a person without an account uses.

The link's token is the authority. What each kind of link shows:

* a **personal request** link shows that request's prefill, its one shared draft and, when changes
  were requested, the reviewer's notes and the previous answers. With ``require_sign_in`` only the
  addressed account can open it.
* a **public** link shows the form and nothing else: no prefill, and each browser's draft is keyed
  by a random filler key the browser keeps (``X-Synwevo-Filler``), so one filler can never read
  another's answers or files.

Saving a draft is not submitting (no event, nobody notified). Submitting is checked with the
same rules as every other entry (`definition.check_answers`); files must have been uploaded to
this request by this filler and passed the platform's upload check.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field as F

from synwevo_core.capabilities import canonical, fingerprint, record_event
from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id, now

from .definition import ACCEPTABLE, Definition, check_answers
from .service import APP, REQUEST_STATES_OPEN, Forms, _field_list, _j, token_hash
from ..common.files import available_blob, read_blob

log = logging.getLogger("synwevo.forms")

FILLER_KEY = re.compile(r"^[A-Za-z0-9_-]{24,100}$")
PERSONAL_FILLER = "personal"
MAX_UPLOAD = 20 * 1024 * 1024


class _Limiter:
    """Per-address request budget for the anonymous endpoints (in this process). A form link is
    public by nature; this keeps one address from flooding a workspace with submissions or uploads."""

    def __init__(self, per_window: int, window_s: float):
        self.per, self.window = per_window, window_s
        self.hits: OrderedDict[str, list[float]] = OrderedDict()
        self.lock = threading.Lock()

    def take(self, key: str) -> None:
        t = time.monotonic()
        with self.lock:
            hits = [h for h in self.hits.pop(key, []) if t - h < self.window]
            if len(hits) >= self.per:
                self.hits[key] = hits
                raise ServiceError(429, "rate_limited", "操作过于频繁，请稍后再试",
                                   {"retry_after": int(self.window - (t - hits[0])) + 1})
            hits.append(t)
            self.hits[key] = hits
            while len(self.hits) > 20_000:
                self.hits.popitem(last=False)


class DraftBody(BaseModel):
    answers: dict = F(default_factory=dict)
    step: int = F(default=0, ge=0, le=100)
    rev: int = F(default=0, ge=0)


class SubmitBody(BaseModel):
    answers: dict = F(default_factory=dict)
    version: int = F(ge=1)
    idempotency_key: str = F(min_length=8, max_length=100)
    submitter_label: str | None = F(default=None, max_length=200)


class Filling:
    def __init__(self, forms: Forms):
        self.forms = forms
        self.store = forms.store
        self.reads = _Limiter(300, 60)
        self.writes = _Limiter(60, 60)
        self.submits = _Limiter(20, 600)

    # ---------------------------------------------------------------- resolve the link
    def resolve(self, token: str, request: Request, filler_header: str | None) -> dict:
        if not isinstance(token, str) or not 16 <= len(token) <= 100:
            raise ServiceError(404, "link_not_found", "这个填写链接不存在或已失效")
        digest = token_hash(token)
        req = self.store.one("SELECT * FROM form_requests WHERE token_hash=? AND kind='personal'", (digest,))
        form = None
        if req is None:
            form = self.store.one("SELECT * FROM forms WHERE public_token_hash=?", (digest,))
            if not form or not form["public_request_id"]:
                raise ServiceError(404, "link_not_found", "这个填写链接不存在或已失效")
            req = self.store.one("SELECT * FROM form_requests WHERE id=?", (form["public_request_id"],))
            if not req:
                raise ServiceError(404, "link_not_found", "这个填写链接不存在或已失效")
        else:
            form = self.store.one("SELECT * FROM forms WHERE id=?", (req["form_id"],))
        req = self.forms._expire_if_due(req)
        ws = self.forms.identity.get_workspace(req["workspace_id"])
        if not ws or ws["status"] != "active":
            raise ServiceError(410, "link_unavailable", "这个表单所在的工作空间当前不可用")
        user = getattr(request.state, "user", None)
        if req["kind"] == "personal":
            if req["require_sign_in"]:
                if not user:
                    raise ServiceError(401, "sign_in_required", "这份表单要求登录后填写，请用收件邮箱对应的账户登录")
                if not (user["id"] == req["recipient_user_id"] or
                        (req["recipient_email"] or "").lower() == (user["email"] or "").lower()):
                    raise ServiceError(403, "not_the_recipient", "这份表单是发给其他人的，你不能填写")
            filler = user["id"] if (req["require_sign_in"] and user) else PERSONAL_FILLER
        else:
            if not filler_header or not FILLER_KEY.match(filler_header):
                raise ServiceError(400, "filler_key_required", "页面缺少填写标识，请刷新后重试")
            filler = "pub:" + hashlib.sha256(filler_header.encode()).hexdigest()
        return {"request": req, "form": form, "filler": filler, "user": user}

    def _version_for(self, ctx: dict, wanted: int | None = None) -> dict:
        req, form = ctx["request"], ctx["form"]
        if req["kind"] == "public":
            version = wanted or form["public_version"] or form["published_version"]
        else:
            version = req["form_version"]
            if wanted is not None and wanted != version:
                raise ServiceError(409, "form_version_changed", "这份请求使用的表单版本已变化，请刷新页面")
        if not version:
            raise ServiceError(410, "link_unavailable", "这个表单暂时没有可填写的版本")
        v = self.forms._version(req["form_id"], version)
        if req["kind"] == "public" and v["status"] != "published":
            raise ServiceError(409, "form_version_changed", "表单已更新，请刷新页面后填写新版本")
        return v

    def _shown(self, ctx: dict) -> dict | None:
        """The published version whose title and description this link shows, also when the
        link no longer accepts answers. Never the live form row: that is the editors' draft,
        and saving it must not change what anyone outside the workspace sees."""
        req, form = ctx["request"], ctx["form"]
        version = (form["public_version"] or form["published_version"]) if req["kind"] == "public" else req["form_version"]
        if not version:
            return None
        return self.store.one("SELECT title, description FROM form_versions WHERE form_id=? AND version=?",
                              (req["form_id"], version))

    def _state_refusal(self, req: dict) -> ServiceError | None:
        st = req["status"]
        if st == "revoked":
            return ServiceError(410, "link_revoked", "这个填写链接已被撤销")
        if st == "expired":
            return ServiceError(410, "link_expired", "这份表单已过截止时间，不能再填写。如需延期请联系发出请求的人")
        if st == "closed":
            return ServiceError(410, "request_closed", "这份表单已停止接收填写")
        return None

    # ---------------------------------------------------------------- read
    def load(self, ctx: dict) -> dict:
        req, filler = ctx["request"], ctx["filler"]
        refusal = self._state_refusal(req)
        shown = self._shown(ctx) or {"title": "", "description": ""}
        base = {"request": {"id": req["id"] if req["kind"] == "personal" else None, "kind": req["kind"],
                            "title": req["title"] or shown["title"], "status": req["status"],
                            "due_at": req["due_at"], "submission_policy": req["submission_policy"],
                            "require_sign_in": bool(req["require_sign_in"])},
                "form": {"title": shown["title"], "description": shown["description"] or ""}}
        if refusal:
            return {**base, "closed": {"code": refusal.code, "message": refusal.message}}
        v = self._version_for(ctx)
        draft = self.store.one("SELECT * FROM form_drafts WHERE request_id=? AND filler_key=?", (req["id"], filler))
        mine = self.store.all("SELECT id, revision, status, created_at, answers_json, review_note FROM form_submissions "
                              "WHERE request_id=? AND filler_key=? ORDER BY revision DESC LIMIT 20", (req["id"], filler))
        review = None
        if mine and mine[0]["status"] == "changes_requested":
            items = self.store.all("SELECT field_id, note FROM form_review_items WHERE submission_id=? ORDER BY created_at",
                                   (mine[0]["id"],))
            review = {"submission_id": mine[0]["id"], "note": mine[0]["review_note"], "items": items,
                      "previous_answers": _j(mine[0]["answers_json"], {})}
        files = self.store.all("SELECT id, field_id, filename, content_type, size FROM form_files "
                               "WHERE request_id=? AND filler_key=? AND state!='discarded' ORDER BY created_at",
                               (req["id"], filler))
        done = bool(mine) and req["kind"] == "personal" and req["submission_policy"] == "once" \
            and mine[0]["status"] in ("submitted", "accepted")
        return {**base, "version": v["version"], "definition": _j(v["definition_json"], {}),
                "prefill": _j(req["prefill_json"], {}) if req["kind"] == "personal" else {},
                "draft": {"answers": _j(draft["answers_json"], {}), "step": draft["step"], "rev": draft["rev"],
                          "updated_at": draft["updated_at"]} if draft else None,
                "review": review, "files": files,
                "submissions": [{"id": s["id"], "revision": s["revision"], "status": s["status"],
                                 "created_at": s["created_at"]} for s in mine],
                "submitted": done}

    # ---------------------------------------------------------------- draft
    def save_draft(self, ctx: dict, body: DraftBody) -> dict:
        req, filler = ctx["request"], ctx["filler"]
        refusal = self._state_refusal(req)
        if refusal:
            raise refusal
        v = self._version_for(ctx)
        if len(canonical(body.answers).encode("utf-8")) > 256 * 1024:
            raise ServiceError(413, "draft_too_large", "草稿过大")
        defn = Definition.model_validate(_j(v["definition_json"], {}))
        known = {f.id for f in defn.fields}
        answers = {k: val for k, val in body.answers.items() if k in known}
        _, errors = check_answers(defn, answers, files=self._files(req["id"], filler), final=False)
        ts = now()
        with self.store.tx() as t:
            cur = t.execute("SELECT rev FROM form_drafts WHERE request_id=? AND filler_key=?", (req["id"], filler)).fetchone()
            if cur is None:
                t.execute("INSERT INTO form_drafts (request_id,filler_key,answers_json,step,rev,updated_at) VALUES (?,?,?,?,1,?)",
                          (req["id"], filler, canonical(answers), body.step, ts))
                rev = 1
            else:
                if body.rev and cur[0] != body.rev:
                    # Another tab or person saved this personal request's draft meanwhile.
                    raise ServiceError(409, "draft_conflict", "草稿已在其他页面更新，请刷新后继续",
                                       {"rev": cur[0]})
                rev = cur[0] + 1
                t.execute("UPDATE form_drafts SET answers_json=?, step=?, rev=?, updated_at=? WHERE request_id=? AND filler_key=?",
                          (canonical(answers), body.step, rev, ts, req["id"], filler))
        return {"rev": rev, "saved_at": ts, "problems": errors}

    # ---------------------------------------------------------------- files
    def _files(self, request_id: str, filler: str) -> dict[str, dict]:
        rows = self.store.all("SELECT id, field_id, filename, size, blob FROM form_files WHERE request_id=? AND filler_key=? "
                              "AND state IN ('staged','attached')", (request_id, filler))
        from synwevo_core.quarantine import standing
        held = standing(self.store)
        return {r["id"]: r for r in rows if r["blob"] not in held}

    def upload(self, ctx: dict, field_id: str, filename: str, content_type: str | None, data: bytes) -> dict:
        req, filler = ctx["request"], ctx["filler"]
        refusal = self._state_refusal(req)
        if refusal:
            raise refusal
        v = self._version_for(ctx)
        defn = Definition.model_validate(_j(v["definition_json"], {}))
        field = next((f for f in defn.fields if f.id == field_id), None)
        if not field or field.type != "file":
            raise ServiceError(400, "not_a_file_field", "这个字段不接受附件")
        name = _safe_name(filename)
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        allowed = {a.lower() for a in field.accept} or ACCEPTABLE
        if ext not in allowed:
            raise ServiceError(400, "file_type_not_accepted", "这个字段不接受这种文件类型",
                               {"accept": sorted(allowed)})
        limit = min(field.max_size_mb * 1024 * 1024, MAX_UPLOAD)
        if len(data) == 0:
            raise ServiceError(400, "empty_file", "文件是空的")
        if len(data) > limit:
            raise ServiceError(413, "file_too_large", "文件超过这个字段允许的大小", {"max_mb": field.max_size_mb})
        count = self.store.one("SELECT COUNT(*) AS n FROM form_files WHERE request_id=? AND filler_key=? AND field_id=? "
                               "AND state IN ('staged','attached')", (req["id"], filler, field_id))["n"]
        if count >= field.max_files:
            raise ServiceError(409, "too_many_files", "这个字段的附件数量已达上限", {"max": field.max_files})
        # The platform's upload check (malware scan, password-protected containers): the same gate
        # member imports and message attachments pass. Size is checked above in this form's terms.
        from synwevo_core.member_import import accept_upload
        available_blob(self.store, hashlib.sha256(data).hexdigest())
        scan = accept_upload(name, data)
        blob = self.store.put_blob(data)
        fid = new_id("ffile")
        user = ctx.get("user")
        self.store.execute(
            "INSERT INTO form_files (id,workspace_id,request_id,field_id,filler_key,blob,filename,content_type,size,scan,"
            "state,uploaded_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,'staged',?,?)",
            (fid, req["workspace_id"], req["id"], field_id, filler, blob, name,
             (content_type or "application/octet-stream")[:100], len(data), canonical(scan),
             user["id"] if user else None, now()))
        return {"id": fid, "field_id": field_id, "filename": name, "size": len(data),
                "scan": "clean" if scan.get("status") == "clean" else "not_configured"}

    def discard(self, ctx: dict, file_id: str) -> dict:
        req, filler = ctx["request"], ctx["filler"]
        n = self.store.execute("UPDATE form_files SET state='discarded' WHERE id=? AND request_id=? AND filler_key=? "
                               "AND state='staged'", (file_id, req["id"], filler))
        if not n:
            raise ServiceError(404, "file_not_found", "附件不存在，或已随提交保存不能再删除")
        return {"id": file_id, "state": "discarded"}

    def file_bytes(self, ctx: dict, file_id: str) -> tuple[dict, bytes]:
        req, filler = ctx["request"], ctx["filler"]
        row = self.store.one("SELECT * FROM form_files WHERE id=? AND request_id=? AND filler_key=? AND state!='discarded'",
                             (file_id, req["id"], filler))
        if not row:
            raise ServiceError(404, "file_not_found", "附件不存在")
        return row, read_blob(self.store, row["blob"])

    # ---------------------------------------------------------------- submit
    def submit(self, ctx: dict, body: SubmitBody, capabilities) -> dict:
        req, filler, user = ctx["request"], ctx["filler"], ctx.get("user")
        refusal = self._state_refusal(req)
        if refusal:
            raise refusal
        prior = self.store.one("SELECT * FROM form_submissions WHERE request_id=? AND filler_key=? AND idempotency_key=?",
                               (req["id"], filler, body.idempotency_key))
        if prior:
            return self._receipt(ctx, prior, replayed=True)
        latest = self.store.one("SELECT * FROM form_submissions WHERE request_id=? AND filler_key=? "
                                "ORDER BY revision DESC LIMIT 1", (req["id"], filler))
        if req["kind"] == "personal" and req["submission_policy"] == "once" and latest \
                and latest["status"] in ("submitted", "accepted"):
            raise ServiceError(409, "already_submitted", "这份表单已经提交过，不能重复提交")
        v = self._version_for(ctx, body.version)
        defn = Definition.model_validate(_j(v["definition_json"], {}))
        files = self._files(req["id"], filler)
        answers, errors = check_answers(defn, body.answers, files=files, final=True)
        if errors:
            raise ServiceError(422, "answers_invalid", "还有需要填写或更正的内容", {"errors": errors})
        used = {fid for f in defn.fields if f.type == "file" for fid in (answers.get(f.id) or [])}
        resubmission = bool(latest and latest["status"] == "changes_requested")
        # A revision is an answer to "please change this"; another submission under a
        # multiple-submission link is a new submission of its own, revision 1.
        revision = latest["revision"] + 1 if resubmission else 1
        sid = new_id("fsub")
        ts = now()
        label = (body.submitter_label or "").strip() or ((user.get("display_name") or user["email"]) if user else None)
        with self.store.tx() as t:
            cur = t.execute("SELECT status FROM form_requests WHERE id=?", (req["id"],)).fetchone()
            if not cur or cur[0] not in REQUEST_STATES_OPEN + (("submitted",) if req["submission_policy"] == "multiple" else ()):
                raise ServiceError(409, "request_not_open", "这份表单当前不接收提交，请刷新页面查看状态")
            try:
                t.execute("INSERT INTO form_submissions (id,workspace_id,form_id,form_version,request_id,revision,previous_id,"
                          "answers_json,answers_hash,submitted_by,submitter_label,filler_key,status,idempotency_key,created_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'submitted',?,?)",
                          (sid, req["workspace_id"], req["form_id"], v["version"], req["id"],
                           revision,
                           latest["id"] if resubmission else None, canonical(answers), fingerprint(answers),
                           user["id"] if user else None, label, filler, body.idempotency_key, ts))
            except Exception as exc:  # noqa: BLE001 — the same key raced in from a second tab
                if "UNIQUE" in str(exc):
                    raise _Twin() from None
                raise
            if resubmission:
                t.execute("UPDATE form_submissions SET status='superseded' WHERE id=?", (latest["id"],))
            if used:
                marks = ",".join("?" for _ in used)
                t.execute(f"UPDATE form_files SET state='attached', submission_id=? WHERE id IN ({marks}) "
                          "AND request_id=? AND filler_key=?", (sid, *used, req["id"], filler))
            t.execute("UPDATE form_files SET state='discarded' WHERE request_id=? AND filler_key=? AND state='staged'",
                      (req["id"], filler))
            t.execute("DELETE FROM form_drafts WHERE request_id=? AND filler_key=?", (req["id"], filler))
            if req["kind"] == "personal":
                t.execute("UPDATE form_requests SET status='submitted', updated_at=? WHERE id=?", (ts, req["id"]))
            ev = record_event(t, type_="forms.submission.resubmitted" if resubmission else "forms.submission.created",
                              app=APP, workspace_id=req["workspace_id"], object_kind="form_submission", object_id=sid,
                              user_id=user["id"] if user else None, correlation_id=req["correlation_id"],
                              data={"submission_id": sid, "request_id": req["id"] if req["kind"] == "personal" else None,
                                    "form_id": req["form_id"], "version": v["version"],
                                    "revision": revision,
                                    "kind": req["kind"]})
        capabilities.announce([ev])
        form = ctx["form"]
        who = label or "—"
        self.forms._notify(req["created_by"], "forms.submission.received", {"form": form["title"], "who": who},
                           req["workspace_id"], "form_submission", sid,
                           f"/forms/w/{req['workspace_id']}/s/{sid}", dedupe=f"forms.received:{sid}")
        row = self.store.one("SELECT * FROM form_submissions WHERE id=?", (sid,))
        return self._receipt(ctx, row, replayed=False, confirmation=_j(v["definition_json"], {}).get("confirmation", ""))

    def _receipt(self, ctx: dict, row: dict, *, replayed: bool, confirmation: str = "") -> dict:
        return {"submission_id": row["id"], "revision": row["revision"], "status": row["status"],
                "submitted_at": row["created_at"], "replayed": replayed, "confirmation": confirmation}


class _Twin(Exception):
    pass


def _safe_name(filename: str | None) -> str:
    name = (filename or "file").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*').strip(" .") or "file"
    return name[:180]


def _client(request: Request) -> str:
    from synwevo_core.http import client_ip
    return client_ip(request) or "unknown"


def build_public_router(filling: Filling, capabilities) -> APIRouter:
    r = APIRouter(prefix="/api/suite/public/forms", tags=["forms-public"])

    def ctx_for(token: str, request: Request, filler: str | None, limiter: _Limiter) -> dict:
        limiter.take(f"{_client(request)}:{token_hash(token)[:16]}")
        return filling.resolve(token, request, filler)

    @r.get("/r/{token}")
    def load(token: str, request: Request, x_synwevo_filler: str | None = Header(default=None)):
        return filling.load(ctx_for(token, request, x_synwevo_filler, filling.reads))

    @r.put("/r/{token}/draft")
    def draft(token: str, body: DraftBody, request: Request, x_synwevo_filler: str | None = Header(default=None)):
        return filling.save_draft(ctx_for(token, request, x_synwevo_filler, filling.writes), body)

    @r.post("/r/{token}/files")
    async def upload(token: str, request: Request, field_id: str = Form(...), file: UploadFile = File(...),
                     x_synwevo_filler: str | None = Header(default=None)):
        ctx = ctx_for(token, request, x_synwevo_filler, filling.writes)
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise ServiceError(413, "file_too_large", "文件超过 20 MB 上限", {"max_mb": 20})
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(filling.upload, ctx, field_id, file.filename or "file", file.content_type, data)

    @r.delete("/r/{token}/files/{file_id}")
    def discard(token: str, file_id: str, request: Request, x_synwevo_filler: str | None = Header(default=None)):
        return filling.discard(ctx_for(token, request, x_synwevo_filler, filling.writes), file_id)

    @r.get("/r/{token}/files/{file_id}")
    def download(token: str, file_id: str, request: Request, x_synwevo_filler: str | None = Header(default=None)):
        row, data = filling.file_bytes(ctx_for(token, request, x_synwevo_filler, filling.reads), file_id)
        return _attachment(row, data)

    @r.post("/r/{token}/submit")
    def submit(token: str, body: SubmitBody, request: Request, x_synwevo_filler: str | None = Header(default=None)):
        ctx = ctx_for(token, request, x_synwevo_filler, filling.submits)
        try:
            return filling.submit(ctx, body, capabilities)
        except _Twin:
            prior = filling.store.one("SELECT * FROM form_submissions WHERE request_id=? AND filler_key=? AND idempotency_key=?",
                                      (ctx["request"]["id"], ctx["filler"], body.idempotency_key))
            return filling._receipt(ctx, prior, replayed=True)

    return r


def _attachment(row: dict, data: bytes) -> Response:
    from urllib.parse import quote
    name = row["filename"]
    return Response(content=data, media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
