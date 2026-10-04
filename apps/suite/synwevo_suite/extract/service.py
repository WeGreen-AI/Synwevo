"""Extract workspace records, immutable versions, evidence and human review.

All workspace members can read all fields of these records; editors can mutate them. There
are no hidden field ACLs or view filters. Every history/download/result guard rechecks this
current policy. Human confirmation is a separate immutable revision, never a worker action.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import PurePath

from synwevo_core.capabilities import canonical, fingerprint, record_event
from synwevo_core.errors import ServiceError
from synwevo_core.member_import import accept_upload
from synwevo_core.store import new_id, now

from .definition import Definition, extract, value_error
from .worker import TIMEOUT, parse_bounded
from ..common.files import available_blob, read_blob

MAX_FILE_BYTES = 12 * 1024 * 1024
FORMATS = {"pdf": "application/pdf", "csv": "text/csv", "tsv": "text/tab-separated-values",
           "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
           "tif": "image/tiff", "tiff": "image/tiff"}


def fail(code, message="操作无法完成，请检查当前记录", status=409):
    raise ServiceError(status, code, message)


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


class Extract:
    def __init__(self, store, identity, caps, reader=None):
        self.store, self.identity, self.caps, self.reader = store, identity, caps, reader
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    @staticmethod
    def emit(call, event, *, data=None):
        call.emit(event, object_kind=call.object_kind, object_id=call.object_id, data=data)

    def auth(self, user_id, workspace_id, need="read"):
        user = self.identity.get_user(user_id)
        if not user or user["status"] != "active":
            fail("account_unavailable", "账户不可用", 401)
        return self.caps.check_workspace(user_id, "extract", workspace_id, need)

    def row(self, workspace_id, kind, identifier):
        table = {"template": "extract_templates", "task": "extract_tasks", "file": "extract_files"}[kind]
        row = self.store.one(f"SELECT * FROM {table} WHERE id=? AND workspace_id=?", (identifier, workspace_id))
        if not row:
            fail("extract_not_found", "记录不存在或不在该工作空间", 404)
        return row

    def version(self, workspace_id, template_id, version):
        self.row(workspace_id, "template", template_id)
        row = self.store.one("SELECT * FROM extract_versions WHERE workspace_id=? AND template_id=? AND version=?",
                             (workspace_id, template_id, version))
        if not row:
            fail("version_not_found", "模板版本不存在", 404)
        return row

    def template(self, workspace_id, identifier):
        r = self.row(workspace_id, "template", identifier)
        return {**{k: r[k] for k in ("id", "title", "workspace_id", "draft_rev", "published_version", "archived")},
                "draft": decode(r["draft_json"]),
                "versions": self.store.all("SELECT version, retired, published_at, definition_digest FROM extract_versions "
                                           "WHERE workspace_id=? AND template_id=? ORDER BY version DESC LIMIT 100",
                                           (workspace_id, identifier))}

    def list_templates(self, call, i):
        rows = self.store.all("SELECT id,title,draft_rev,published_version,archived FROM extract_templates "
                              "WHERE workspace_id=? AND archived=? AND id>? ORDER BY id LIMIT ?",
                              (call.workspace_id, int(i.archived), i.after, i.limit + 1))
        return {"items": rows[:i.limit], "next": rows[i.limit - 1]["id"] if len(rows) > i.limit else None}

    def create_template(self, call, i):
        tid, ts = new_id("extpl"), now()
        self.store.insert("extract_templates", {"id": tid, "workspace_id": call.workspace_id, "title": i.title,
                          "draft_json": canonical(i.definition.model_dump()), "created_by": call.user_id,
                          "created_at": ts, "updated_at": ts})
        call.touches("extract_template", tid)
        self.emit(call, "extract.template.created", data={"template_id": tid})
        return self.template(call.workspace_id, tid)

    def save_template(self, call, i):
        r = self.row(call.workspace_id, "template", i.template_id)
        if r["archived"]:
            fail("template_archived")
        if r["draft_rev"] != i.draft_rev:
            fail("draft_conflict", "模板已被修改，请重新载入")
        self.store.update("extract_templates", {"id": r["id"], "workspace_id": call.workspace_id},
                          {"title": i.title, "draft_json": canonical(i.definition.model_dump()),
                           "draft_rev": r["draft_rev"] + 1, "updated_at": now()})
        call.touches("extract_template", r["id"])
        self.emit(call, "extract.template.saved", data={"template_id": r["id"]})
        return self.template(call.workspace_id, r["id"])

    def publish(self, call, i):
        r = self.row(call.workspace_id, "template", i.template_id)
        if r["archived"]:
            fail("template_archived")
        if r["draft_rev"] != i.draft_rev:
            fail("draft_conflict")
        if r["published_rev"] == r["draft_rev"]:
            fail("nothing_to_publish")
        v = (r["published_version"] or 0) + 1
        if v > 100:
            fail("version_limit", "模板已达版本上限，请新建模板", 413)
        definition = Definition.model_validate_json(r["draft_json"]).model_dump()
        self.store.insert("extract_versions", {"template_id": r["id"], "workspace_id": call.workspace_id,
                          "version": v, "title": r["title"], "definition_json": canonical(definition),
                          "definition_digest": fingerprint(definition), "published_by": call.user_id, "published_at": now()})
        self.store.update("extract_templates", {"id": r["id"], "workspace_id": call.workspace_id},
                          {"published_version": v, "published_rev": r["draft_rev"], "updated_at": now()})
        call.touches("extract_template", r["id"])
        self.emit(call, "extract.template.published", data={"template_id": r["id"], "version": v})
        return {"id": r["id"], "version": v}

    def archive(self, call, i):
        self.row(call.workspace_id, "template", i.template_id)
        self.store.update("extract_templates", {"id": i.template_id, "workspace_id": call.workspace_id},
                          {"archived": int(i.archived), "updated_at": now()})
        call.touches("extract_template", i.template_id)
        self.emit(call, "extract.template.archived", data={"archived": i.archived})
        return {"id": i.template_id, "archived": i.archived}

    def retire(self, call, i):
        self.version(call.workspace_id, i.template_id, i.version)
        self.store.update("extract_versions", {"workspace_id": call.workspace_id, "template_id": i.template_id,
                          "version": i.version}, {"retired": 1})
        call.touches("extract_template", i.template_id)
        self.emit(call, "extract.template.retired", data={"version": i.version})
        return {"id": i.template_id, "version": i.version, "retired": True}

    def upload(self, user_id, workspace_id, filename, data, key, *, source=None):
        self.auth(user_id, workspace_id, "write")
        if not key or len(key) > 200:
            fail("idempotency_required", "上传需要有效幂等键", 422)
        filename = PurePath(filename.replace("\\", "/")).name
        if not filename or len(filename) > 180 or any(ord(c) < 32 for c in filename):
            fail("invalid_filename", "文件名无效", 422)
        extension = filename.rsplit(".", 1)[-1].lower()
        if extension not in FORMATS:
            fail("unsupported_format", "请选择 PDF、图片或 CSV/TSV 文件", 422)
        if not data or len(data) > MAX_FILE_BYTES:
            fail("file_limit", "文件为空或超过 12 MB", 413)
        digest = hashlib.sha256(data).hexdigest()
        available_blob(self.store, digest)
        provenance = canonical(source or {})
        # Replays do not re-scan, but still authorize and compare exact content and filename.
        prior = self.store.one("SELECT * FROM extract_files WHERE workspace_id=? AND created_by=? AND idempotency_key=?",
                               (workspace_id, user_id, key))
        if prior:
            if prior["content_digest"] != digest or prior["filename"] != filename or prior["source_json"] != provenance:
                fail("idempotency_conflict")
            return self.file_json(prior)
        scan = accept_upload(filename, data)
        with self.store.tx() as t:
            self.auth(user_id, workspace_id, "write")
            prior = self.store.one("SELECT * FROM extract_files WHERE workspace_id=? AND created_by=? AND idempotency_key=?",
                                   (workspace_id, user_id, key))
            if prior:
                if prior["content_digest"] != digest or prior["filename"] != filename or prior["source_json"] != provenance:
                    fail("idempotency_conflict")
                return self.file_json(prior)
            usage = self.store.one("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes FROM extract_files WHERE workspace_id=?", (workspace_id,))
            if usage["n"] >= 500 or usage["bytes"] + len(data) > 250 * 1024 * 1024:
                fail("storage_limit", "该空间已达提取文件上限", 413)
            available_blob(self.store, digest)
            fid = new_id("exfile")
            blob = self.store.put_blob(data)
            self.store.insert("extract_files", {"id": fid, "workspace_id": workspace_id, "filename": filename,
                              "content_type": FORMATS[extension], "blob": blob, "content_digest": digest,
                              "size": len(data), "scan_json": canonical(scan), "created_by": user_id,
                              "created_at": now(), "idempotency_key": key, "source_json": provenance})
            record_event(t, type_="extract.file.uploaded", app="extract", workspace_id=workspace_id,
                         object_kind="extract_file", object_id=fid, user_id=user_id, data={"file_id": fid})
        return self.file_json(self.row(workspace_id, "file", fid))

    @staticmethod
    def file_json(r):
        return {k: r[k] for k in ("id", "filename", "content_type", "size", "content_digest")} | {"scan": decode(r["scan_json"]), "source": decode(r.get("source_json") or "{}")}

    def task_file(self, task):
        row = self.row(task["workspace_id"], "file", task["file_id"])
        available_blob(self.store, row["blob"])
        return row

    def file_bytes(self, user_id, workspace_id, file_id):
        self.export_right(user_id, workspace_id)
        row = self.row(workspace_id, "file", file_id)
        return row, read_blob(self.store, row["blob"])

    def export_right(self, user_id, workspace_id):
        self.auth(user_id, workspace_id)
        m = self.identity.membership(workspace_id, user_id)
        if not (m and (m["role"] in ("owner", "admin", "editor") or m.get("can_export"))):
            fail("export_not_allowed", "没有导出权限", 403)

    def create_task(self, call, i):
        if self.reader is None:
            fail("parser_not_configured", "文件解析服务未配置", 503)
        tpl = self.row(call.workspace_id, "template", i.template_id)
        ver = self.version(call.workspace_id, i.template_id, i.version)
        if tpl["archived"] or ver["retired"]:
            fail("version_retired", "模板或版本已停用")
        file = self.row(call.workspace_id, "file", i.file_id)
        available_blob(self.store, file["blob"])
        active = self.store.one("SELECT COUNT(*) AS n FROM extract_tasks WHERE workspace_id=? AND status IN ('queued','running')", (call.workspace_id,))
        total = self.store.one("SELECT COUNT(*) AS n FROM extract_tasks WHERE status IN ('queued','running')")
        if active["n"] >= 20 or total["n"] >= 200:
            fail("queue_full", "提取队列已满，请稍后重试", 429)
        tid, ts = new_id("extask"), now()
        self.store.insert("extract_tasks", {"id": tid, "workspace_id": call.workspace_id, "template_id": i.template_id,
                          "template_version": i.version, "file_id": i.file_id, "created_by": call.user_id,
                          "created_at": ts, "updated_at": ts, "status": "queued", "ocr": int(i.ocr),
                          "operation_run_id": call.run_id, "correlation_id": call.correlation_id})
        call.touches("extract_task", tid)
        self.emit(call, "extract.task.queued", data={"task_id": tid, "version": i.version})
        return {"id": tid, "status": "queued", "template_version": i.version, "__run_status__": "queued"}

    def list_tasks(self, call, i):
        rows = self.store.all("SELECT t.id,t.status,t.revision,t.template_version,t.created_at,f.filename,v.title "
                              "FROM extract_tasks t JOIN extract_files f ON f.id=t.file_id "
                              "JOIN extract_versions v ON v.template_id=t.template_id AND v.version=t.template_version "
                              "WHERE t.workspace_id=? AND t.id>? AND (?='' OR t.status=?) ORDER BY t.id LIMIT ?",
                              (call.workspace_id, i.after, i.status, i.status, i.limit + 1))
        return {"items": rows[:i.limit], "next": rows[i.limit-1]["id"] if len(rows) > i.limit else None}

    def task(self, workspace_id, identifier):
        r = self.row(workspace_id, "task", identifier)
        self.task_file(r)
        ver = self.version(workspace_id, r["template_id"], r["template_version"])
        reviews = self.store.all("SELECT revision,kind,note,actor_id,created_at FROM extract_reviews WHERE workspace_id=? AND task_id=? ORDER BY revision", (workspace_id, identifier))
        names: dict[str, str | None] = {}
        for review in reviews:
            review["actor_name"] = self.actor_name(review["actor_id"], names)
        parsed = decode(r["parse_json"]) or {}
        return {**{k: r[k] for k in ("id", "workspace_id", "template_id", "template_version", "status", "revision",
                                   "confirmed_revision", "error_code", "attempts", "created_at")},
                "title": ver["title"], "definition": decode(ver["definition_json"]),
                "file": self.file_json(self.row(workspace_id, "file", r["file_id"])),
                "parser": {k: v for k, v in parsed.items() if k != "segments"},
                "machine": decode(r["machine_json"]), "values": decode(r["current_json"]), "history": reviews}

    def segments(self, call, i):
        r = self.row(call.workspace_id, "task", i.task_id)
        self.task_file(r)
        values = (decode(r["parse_json"]) or {}).get("segments", [])
        return {"items": values[i.offset:i.offset+i.limit], "total": len(values)}

    def review_version(self, call, i):
        review = self._review_record(call, i)
        return {**review, "actor_name": self.actor_name(review["actor_id"])}

    def _review_record(self, call, i):
        # The immutable record only: `result` embeds it and consumers fingerprint `result`, so a
        # reviewer's later change of name must not read as a changed source.
        self.task_file(self.row(call.workspace_id, "task", i.task_id))
        r = self.store.one("SELECT * FROM extract_reviews WHERE workspace_id=? AND task_id=? AND revision=?",
                           (call.workspace_id, i.task_id, i.revision))
        if not r:
            fail("revision_not_found", "复核版本不存在", 404)
        return {"task_id": i.task_id, "revision": r["revision"], "kind": r["kind"], "values": decode(r["values_json"]),
                "note": r["note"], "actor_id": r["actor_id"], "created_at": r["created_at"]}

    def actor_name(self, user_id, cache=None):
        """The name a reviewer is shown by in the history: their display name, else their email
        (everyone who reads the task is a member of its workspace). None when the account is gone."""
        if cache is not None and user_id in cache:
            return cache[user_id]
        user = self.identity.get_user(user_id) if user_id else None
        name = (user.get("display_name") or user.get("email")) if user else None
        if cache is not None:
            cache[user_id] = name
        return name

    def correct(self, call, i):
        r = self.row(call.workspace_id, "task", i.task_id)
        self.task_file(r)
        if r["status"] not in ("waiting_input", "succeeded") or r["revision"] != i.revision:
            fail("review_conflict", "结果状态或版本已变化，请重新载入")
        if r["revision"] >= 200:
            fail("review_limit", "该任务已达复核次数上限", 413)
        values = decode(r["current_json"])
        fields = {f.id: f for f in Definition.model_validate_json(self.version(call.workspace_id, r["template_id"], r["template_version"])["definition_json"]).fields}
        for key, value in i.values.items():
            field = fields.get(key)
            if field is None or field.many != isinstance(value, list):
                fail("invalid_correction", "字段或值类型不匹配", 422)
            parts = value if isinstance(value, list) else [value]
            if (field.required and not parts) or any(value_error(field, v) for v in parts):
                fail("invalid_correction", "修正值未通过字段校验", 422)
            # Human input has no automatically proven location. Original machine candidates stay intact.
            values[key] = {**values[key], "value": value, "status": "unverified", "origin": "human",
                           "requires_review": True, "correction_note": i.note}
        self._review(call, r, values, i.note, "correction", "waiting_input")
        return {"id": r["id"], "revision": r["revision"] + 1, "status": "waiting_input"}

    def confirm(self, call, i):
        r = self.row(call.workspace_id, "task", i.task_id)
        self.task_file(r)
        if r["status"] != "waiting_input" or r["revision"] != i.revision:
            fail("review_conflict", "结果状态或版本已变化，请重新载入")
        fields = Definition.model_validate_json(self.version(call.workspace_id, r["template_id"], r["template_version"])["definition_json"]).fields
        values = decode(r["current_json"])
        for field in fields:
            result = values[field.id]
            # `incomplete`: a label whose value was not read; `over_limit`: more matches than a
            # field holds. Both stay unresolved until a person corrects the field.
            if (result["status"] in ("conflict", "ambiguous", "invalid", "incomplete", "over_limit")
                    or (field.required and result["status"] == "missing")):
                fail("review_unresolved", "请先处理缺失、不完整、歧义、冲突、无效或匹配过多的字段", 422)
            if result["status"] == "unverified" and not i.acknowledge_unverified:
                fail("unverified_ack_required", "存在无可靠来源的值，请明确核对后确认", 422)
        if not i.note.strip():
            fail("review_note_required", "请填写复核说明", 422)
        self._review(call, r, values, i.note, "confirmation", "succeeded")
        return {"id": r["id"], "revision": r["revision"] + 1, "status": "succeeded"}

    def _review(self, call, r, values, note, kind, status):
        if call.via not in ("ui", "api"):
            fail("human_review_required", "人工复核必须在页面或交互 API 中完成", 403)
        if r["revision"] >= 200:
            fail("review_limit", "该任务已达复核次数上限", 413)
        rev = r["revision"] + 1
        self.store.insert("extract_reviews", {"task_id": r["id"], "workspace_id": call.workspace_id, "revision": rev,
                          "kind": kind, "values_json": canonical(values), "note": note, "actor_id": call.user_id, "created_at": now()})
        self.store.update("extract_tasks", {"id": r["id"], "workspace_id": call.workspace_id},
                          {"revision": rev, "current_json": canonical(values), "status": status,
                           "confirmed_revision": rev if kind == "confirmation" else None, "updated_at": now()})
        self._receipt(r)
        call.touches("extract_task", r["id"])
        self.emit(call, "extract.task." + kind, data={"task_id": r["id"], "revision": rev})

    def result(self, call, i):
        r = self.row(call.workspace_id, "task", i.task_id)
        self.task_file(r)
        # Consumers pin the accepted revision. After later corrections, old revisions remain historical,
        # but are no longer eligible as the current accepted output.
        if r["status"] != "succeeded" or r["confirmed_revision"] != i.revision:
            fail("result_not_confirmed", "该结果版本尚未确认或已被新修正替代")
        ver = self.version(call.workspace_id, r["template_id"], r["template_version"])
        return {"task_id": r["id"], "revision": i.revision, "template_id": r["template_id"],
                "template_version": r["template_version"], "definition_digest": ver["definition_digest"],
                "file": self.file_json(self.row(call.workspace_id, "file", r["file_id"])),
                "values": decode(r["current_json"]), "machine": decode(r["machine_json"]),
                "review": self._review_record(call, i)}

    def cancel(self, call, i):
        r = self.row(call.workspace_id, "task", i.task_id)
        if r["status"] not in ("queued", "running"):
            fail("task_not_cancellable")
        self.store.update("extract_tasks", {"id": r["id"], "workspace_id": call.workspace_id},
                          {"status": "cancelled", "lease_token": None, "lease_until": None, "updated_at": now()})
        self._receipt(r)
        call.touches("extract_task", r["id"])
        self.emit(call, "extract.task.cancelled", data={"task_id": r["id"]})
        return {"id": r["id"], "status": "cancelled"}

    def result_guard(self, call, row):
        # row is an op_runs history row, NOT the handler's output. Never invoke the handler.
        self.auth(call.user_id, call.workspace_id)
        kind = {"extract_template": "template", "extract_task": "task", "extract_file": "file"}.get(row.get("object_kind"))
        if kind is None or row.get("workspace_id") != call.workspace_id:
            fail("result_access_unverifiable")
        resource = self.row(call.workspace_id, kind, row.get("object_id"))
        if kind == "task":
            self.task_file(resource)
        elif kind == "file":
            available_blob(self.store, resource["blob"])

    def stop(self):
        self.stopping.set()

    def tick(self):
        if self.reader is None or self.stopping.is_set() or not self.lock.acquire(blocking=False):
            return
        try:
            self._run_one()
        finally:
            self.lock.release()

    def _receipt(self, row):
        current = self.row(row["workspace_id"], "task", row["id"])
        error = ServiceError(422, current["error_code"], "文件提取未完成") if current["error_code"] else None
        self.caps.update_run(row["operation_run_id"], current["status"], output={
            "id": current["id"], "status": current["status"], "template_version": current["template_version"],
            "revision": current["revision"], "confirmed_revision": current["confirmed_revision"],
        }, error=error)

    def _event(self, t, row, event):
        self._receipt(row)
        parent = self.store.one("SELECT depth FROM op_runs WHERE id=?", (row["operation_run_id"],))
        emitted = record_event(t, type_=event, app="extract", workspace_id=row["workspace_id"], object_kind="extract_task",
                     object_id=row["id"], user_id=row["created_by"], run_id=row["operation_run_id"],
                     correlation_id=row["correlation_id"], depth=parent["depth"] if parent else 0, data={"task_id": row["id"]})
        self.store.after_commit(lambda: self.caps.announce([emitted]))

    def _run_one(self):
        with self.store.tx() as t:
            active = self.store.one("SELECT COUNT(*) AS n FROM extract_tasks WHERE status='running' AND lease_until>=?", (now(),))
            if active["n"] >= 2:
                return
            row = self.store.one("SELECT * FROM extract_tasks WHERE status='queued' OR (status='running' AND lease_until<?) "
                                 "ORDER BY created_at,id LIMIT 1", (now(),))
            if not row:
                return
            token = new_id("exlease")
            if row["attempts"] >= 2:
                self.store.update("extract_tasks", {"id": row["id"]}, {"status": "failed", "error_code": "worker_interrupted", "updated_at": now()})
                self._event(t, row, "extract.task.failed")
                return
            # A task whose client's grant has already ended is not started, and nobody is told it
            # was: it fails here with the reason. `check` below asks again at every later step.
            try:
                self.caps.standing(row["operation_run_id"])
            except ServiceError as gone:
                self.store.update("extract_tasks", {"id": row["id"]}, {"status": "failed", "error_code": gone.code, "updated_at": now()})
                self._event(t, row, "extract.task.failed")
                return
            self.store.update("extract_tasks", {"id": row["id"]}, {"status": "running", "attempts": row["attempts"] + 1,
                              "lease_token": token, "lease_until": now() + TIMEOUT + 30, "updated_at": now()})
            self._event(t, row, "extract.task.started")
        def check():
            if self.stopping.is_set():
                fail("worker_stopped")
            current = self.row(row["workspace_id"], "task", row["id"])
            if current["status"] != "running" or current["lease_token"] != token:
                fail("task_cancelled")
            # A task a client started stops with that client's grant; a person's own does not.
            with self.caps.resumed(row["operation_run_id"]):
                self.auth(row["created_by"], row["workspace_id"], "write")
                self.task_file(row)
        try:
            check()
            file = self.row(row["workspace_id"], "file", row["file_id"])
            data = read_blob(self.store, file["blob"])
            if hashlib.sha256(data).hexdigest() != file["content_digest"]:
                fail("file_integrity_error")
            parsed = parse_bounded(self.reader, data, file["filename"], bool(row["ocr"]), check)
            definition = Definition.model_validate_json(self.version(row["workspace_id"], row["template_id"], row["template_version"])["definition_json"])
            values = extract(definition, parsed)
            with self.store.tx() as t:
                check()
                self.store.update("extract_tasks", {"id": row["id"]}, {"status": "waiting_input", "parse_json": canonical(parsed),
                                  "machine_json": canonical(values), "current_json": canonical(values), "revision": 1,
                                  "lease_token": None, "lease_until": None, "updated_at": now()})
                self._event(t, row, "extract.task.ready")
        except Exception as exc:
            code = exc.code if isinstance(exc, ServiceError) else "parse_failed"
            with self.store.tx() as t:
                current = self.row(row["workspace_id"], "task", row["id"])
                if current["status"] == "running" and current["lease_token"] == token:
                    # Parsing is a pure computation over fixed input. A graceful
                    # deployment interruption may resume within the same attempt
                    # budget; it must not masquerade as a bad user document.
                    resume = code == "worker_stopped" and current["attempts"] < 2
                    self.store.update("extract_tasks", {"id": row["id"]}, {"status": "queued" if resume else "failed", "error_code": None if resume else code,
                                      "lease_token": None, "lease_until": None, "updated_at": now()})
                    self._event(t, row, "extract.task.queued" if resume else "extract.task.failed")
