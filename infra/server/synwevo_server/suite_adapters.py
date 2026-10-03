"""Deployment-owned compatibility operations for the existing Docs and Message services.

No application imports another application. Prepare services before Suite/Agent installation,
then pass the returned object to suite.install(docs=...). See docs/suite-adapters.md.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from synwevo_core import authority
from synwevo_core.apps import DOCS_APP_ID, MESSAGES_APP_ID
from synwevo_core.capabilities import AppManifest, Operation, fingerprint
from synwevo_core.context import as_tenant, reset_actor, reset_request_id, set_actor, set_request_id
from synwevo_core.errors import ServiceError


def T(zh, hant, en):
    return {"zh-Hans": zh, "zh-Hant": hant, "en": en}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Page(Input):
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=50, ge=1, le=100)


class Projects(Page):
    q: str = Field(default="", max_length=100)
    include_archived: bool = False


class Project(Input):
    project_id: str = Field(pattern=r"^p_[a-z0-9]{1,64}$")


class CreateJob(Project):
    configuration: str = Field(pattern=r"^[a-f0-9]{64}$")
    formats: list[Literal["docx", "xlsx", "pptx", "pdf", "png", "jpg"]] = Field(min_length=1, max_length=6)
    error_policy: Literal["block", "skip"] = "block"
    layout: Literal["off", "warn", "fit"] = "off"
    naming: str = Field(default="{序号}", min_length=1, max_length=200)


class Job(Input):
    job_id: str = Field(pattern=r"^j_[a-z0-9]{1,64}$")


class Artifacts(Job, Page):
    pass


class Artifact(Input):
    artifact_id: str = Field(pattern=r"^a_[a-z0-9]{1,64}$")


class Conversation(Input):
    conversation_id: str = Field(pattern=r"^cv_[a-z0-9]{1,64}$")


class Send(Conversation):
    audience: str = Field(pattern=r"^[a-f0-9]{64}$")
    body: str = Field(default="", max_length=6000)
    artifact_ids: list[Annotated[str, Field(pattern=r"^a_[a-z0-9]{1,64}$")]] = Field(default_factory=list, max_length=10)


class Receipt(Input):
    run_id: str = Field(pattern=r"^run_[a-z0-9]{1,64}$")


class JobResult(BaseModel):
    job_id: str
    project_id: str
    status: str
    state: str
    template: dict
    counts: dict
    error_code: str | None
    halt_reason: str | None


class SendResult(BaseModel):
    # queued: accepted and waiting for Message to deliver it; recorded: in the conversation;
    # failed: Message refused it at delivery (error_code says why); unknown: no queue record.
    delivery: Literal["queued", "recorded", "failed", "unknown"]
    conversation_id: str
    message_id: str | None = None
    seq: int | None = None
    error_code: str | None = None


class ProjectResult(BaseModel):
    project_id: str
    name: str
    workspace_id: str
    configuration: str
    template: dict | None
    sources: list[dict]


class ProjectsResult(BaseModel):
    items: list[dict]
    total: int
    offset: int
    limit: int
    next_offset: int | None


class ArtifactResult(BaseModel):
    artifact_id: str
    job_id: str
    filename: str
    format: str
    size: int
    sha256: str
    workspace_id: str


class ArtifactLink(ArtifactResult):
    download_url: str


class ArtifactsResult(BaseModel):
    items: list[ArtifactResult]
    total: int
    next_offset: int | None


class ConversationSummary(BaseModel):
    conversation_id: str
    name: str
    workspace_id: str
    organization_id: str
    archived: bool
    audience: str


class ConversationResult(ConversationSummary):
    members: list[dict]


class ConversationsResult(BaseModel):
    items: list[ConversationSummary]
    next_offset: int | None


JOB_STATES = {"completed": "succeeded", "completed_with_errors": "partial", "cancelling": "running"}


def _json(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


@contextmanager
def _context(call):
    actor = set_actor(call.user_id)
    trace = set_request_id(call.correlation_id or None)
    try:
        with as_tenant(call.workspace_id):
            yield
    finally:
        reset_actor(actor)
        reset_request_id(trace)


class SuiteAdapters:
    def __init__(self, docs_api, messages_api=None, *, public_url="", ident=None):
        from synwevo_docs.ingest import DocumentIngest
        self.ingest = DocumentIngest()
        self.docs_api = docs_api
        self.docs = docs_api.state.app
        self.jobs = docs_api.state.jobs
        self.store = self.docs.store
        self.messaging = getattr(getattr(messages_api, "state", None), "messaging", None)
        if self.messaging is not None and self.messaging.store is not self.store:
            raise ValueError("Adapters must share the composition root's Store instance")
        if self.messaging is None and ident is not None:
            # Message runs in its own process (the workplace origin in production). Reads use the
            # same service rules; sends are queued for that process, which owns fan-out and digests.
            from synwevo_core.messaging import Messaging
            self.messaging = Messaging(self.store, ident)
        self.public_url = public_url.rstrip("/")
        if self.public_url:
            u = urlsplit(self.public_url)
            if (u.scheme not in ("http", "https") or not u.netloc or u.username or u.password
                    or u.query or u.fragment or u.path):
                raise ValueError("public_url must be the Suite origin, without credentials, path or query")
        self._ctx = None

    def _authorize(self, c, app=DOCS_APP_ID, need="read"):
        # Recheck inside the write transaction too. Never accept a caller-supplied operator or
        # service-account descriptor as a substitute for the authenticated, delegated person.
        user = c.identity.get_user(c.user_id)
        if not user or user["status"] != "active":
            raise ServiceError(401, "account_unavailable", "账户不可用")
        c.capabilities.check_workspace(c.user_id, app, c.workspace_id, need)
        return authority.describe(user=user, tenant=c.workspace_id)

    def _key(self, c):
        row = self.store.one("SELECT idempotency_key FROM op_runs WHERE id=?", (c.run_id,))
        if not row or not row["idempotency_key"]:
            raise ServiceError(400, "idempotency_key_required", "该操作需要幂等键；重试请沿用同一个键")
        return c.run_id  # durable, user/op/workspace/key-scoped identity; no expiry in op_runs

    def _project(self, c, pid):
        self._authorize(c)
        with _context(c):
            p = self.docs.get_project(pid)
            template = None
            if p.get("template_id"):
                t = self.docs.get_template(p["template_id"])
                v = self.docs.get_version(t["id"], p.get("template_version") or t["head"])
                template = {"id": t["id"], "version": v["version_no"], "sha256": v["working_blob"],
                            "fill_options": self.docs.fill_options(t)}
            sources = self.docs.project_sources(pid, ready_only=False)
            # Metadata only: inspect must not parse, render, sign links, enqueue or repair data.
            source_state = [{k: s.get(k) for k in ("id", "version", "blob", "working_blob", "parsed_blob",
                                                  "status", "ocr", "corrections")} for s in sources]
            config = {"project": p, "template": template, "sources": source_state,
                      "mappings": self.docs.get_mappings(pid)}
            return {"project_id": pid, "name": p["name"], "workspace_id": c.workspace_id,
                    "configuration": fingerprint(config), "template": template,
                    "sources": [{k: s.get(k) for k in ("id", "filename", "status", "version")} for s in sources]}

    def projects(self, c, i):
        self._authorize(c)
        with _context(c):
            return self.docs.list_projects_page(**i.model_dump())

    def project(self, c, i):
        return self._project(c, i.project_id)

    def _job(self, c, jid):
        self._authorize(c)
        j = self.jobs.get(jid, c.workspace_id)
        # Withdrawal invalidates derived results, including old operation receipts.
        withdrawn = self.jobs.withdrawn_job(jid)
        if withdrawn:
            raise withdrawn
        state = JOB_STATES.get(j["status"], j["status"])
        return {"job_id": j["id"], "project_id": j["project_id"], "status": j["status"], "state": state,
                "template": j["template"], "counts": j["counts"] or {}, "error_code": j.get("error_code"),
                "halt_reason": j.get("halt_reason")}

    def create_job(self, c, i):
        self._key(c)
        principal = self._authorize(c, need="write")
        with _context(c):
            if self._project(c, i.project_id)["configuration"] != i.configuration:
                raise ServiceError(409, "configuration_changed", "模板、输入或项目配置已变化，请重新核实后提交")
            job = self.jobs.create(i.project_id, i.model_dump(exclude={"project_id", "configuration"}),
                                   tenant=c.workspace_id, idem_key=c.run_id, origin="suite", principal=principal)
        c.touches("job", job["id"])
        c.emit("docs.job.submitted", object_kind="job", object_id=job["id"], data={"job_id": job["id"]})
        out = self._job(c, job["id"])
        return {**out, "__run_status__": out["state"]}

    def job(self, c, i):
        return self._job(c, i.job_id)

    def job_guard(self, c, row):
        out = _json(row.get("output"))
        if not out.get("job_id"):
            raise ServiceError(409, "result_access_unverifiable", "无法核实原任务")
        self._job(c, out["job_id"])

    def _artifact(self, c, aid, *, export=False):
        from synwevo_docs import authz
        from synwevo_docs.api.v1 import _artifact_meta
        principal = self._authorize(c)
        if export:
            ok, reason = authority.check(self.store, principal, tenant=c.workspace_id, need="export")
            if not ok:
                raise ServiceError(403, "export_not_allowed", "你不能导出该产物", {"reason": reason})
        with _context(c):
            row = self.store.one("SELECT job_id, format FROM artifacts WHERE id=?", (aid,))
            if not row:
                raise ServiceError(404, "not_found", "找不到该产物")
            self._job(c, row["job_id"])
            a = _artifact_meta(self.store, aid)
            if not authz.blob_readable(self.store, a["blob"], c.workspace_id):
                raise ServiceError(404, "not_found", "找不到该产物")
        return {"artifact_id": a["id"], "job_id": row["job_id"], "filename": a["filename"],
                "format": row["format"], "size": a["size"], "sha256": a["blob"], "workspace_id": c.workspace_id}

    def _url(self, c, aid):
        return f"{self.public_url}/api/suite/{DOCS_APP_ID}/workspaces/{c.workspace_id}/artifacts/{aid}"

    def artifact(self, c, i):
        return {**self._artifact(c, i.artifact_id, export=True), "download_url": self._url(c, i.artifact_id)}

    def artifacts(self, c, i):
        self._job(c, i.job_id)
        args = (i.job_id,)
        total = self.store.one("SELECT COUNT(*) AS n FROM artifacts WHERE job_id=? AND deleted_at IS NULL", args)["n"]
        rows = self.store.all("SELECT id FROM artifacts WHERE job_id=? AND deleted_at IS NULL ORDER BY created_at,id LIMIT ? OFFSET ?",
                              (*args, i.limit, i.offset))
        return {"items": [self._artifact(c, r["id"]) for r in rows], "total": total,
                "next_offset": i.offset + len(rows) if i.offset + len(rows) < total else None}

    def _conversation(self, c, cid):
        self._authorize(c, MESSAGES_APP_ID)
        conv = self.messaging.get(cid, c.user_id)  # existing organization + conversation ACL
        # Narrow adapter contract: only explicitly workspace-bound groups. Organization-wide
        # DMs have no workspace and must not be silently treated as a workspace's audience.
        if conv["workspace_id"] != c.workspace_id:
            raise ServiceError(404, "no_conversation", "找不到该工作空间的会话")
        binding = self.store.one("SELECT app, kind, object_id FROM object_conversations WHERE conversation_id=?", (cid,))
        if binding:
            # Membership synchronization in the bridge is asynchronous. Never trust a stale
            # enrollment as authority to the linked document after its ACL was withdrawn.
            from synwevo_core.messaging_bridge import ObjectConversations
            obj = ObjectConversations(self.store, c.identity, self.messaging).object_of(cid, c.user_id)
            if not obj or obj.get("degraded"):
                raise ServiceError(404, "no_conversation", "找不到该工作空间的会话")
            if binding["app"] != DOCS_APP_ID or binding["kind"] != "project":
                raise ServiceError(409, "conversation_not_supported", "该对象讨论暂不支持自动发送")
            self._project(c, binding["object_id"])
        from synwevo_core.messaging_bridge import audience_fingerprint
        audience = audience_fingerprint(conv)
        return {"conversation_id": cid, "name": conv["name"], "workspace_id": conv["workspace_id"],
                "organization_id": conv["organization_id"], "archived": conv["archived"],
                "audience": audience, "members": [{"user_id": m["user_id"], "name": m["name"]} for m in conv["members"]]}

    def conversations(self, c, i):
        self._authorize(c, MESSAGES_APP_ID)
        # Page candidate ids, then apply the service ACL to each; never use the service's
        # silently capped 500-conversation list as an exhaustive catalogue.
        rows = self.store.all("SELECT c.id FROM conversations c JOIN conversation_members m ON m.conversation_id=c.id "
                              "WHERE c.workspace_id=? AND m.user_id=? AND m.left_at IS NULL ORDER BY c.id LIMIT ? OFFSET ?",
                              (c.workspace_id, c.user_id, i.limit + 1, i.offset))
        items = []
        for row in rows[:i.limit]:
            try:
                items.append(self._conversation(c, row["id"]))
            except ServiceError as e:
                if e.status not in (403, 404):
                    raise
        return {"items": items, "next_offset": i.offset + i.limit if len(rows) > i.limit else None}

    def conversation(self, c, i):
        return self._conversation(c, i.conversation_id)

    def _message(self, c, row):
        from synwevo_core.messaging_bridge import user_message_state
        i = Send.model_validate(_json(row["input"]))
        self._conversation(c, i.conversation_id)
        for aid in i.artifact_ids:
            self._artifact(c, aid, export=True)
        out = {"conversation_id": i.conversation_id, "message_id": None, "seq": None, "error_code": None}
        state = user_message_state(self.store, row["id"])
        found = self.store.one("SELECT id FROM messages WHERE conversation_id=? AND sender_id=? AND client_id=?",
                               (i.conversation_id, c.user_id, row["id"]))
        if not found:
            # Sends made before queueing existed have no queue row: their absence stays unknown.
            if state["state"] == "failed":
                return {**out, "delivery": "failed", "error_code": state["code"]}
            return {**out, "delivery": "queued" if state["state"] == "queued" else "unknown"}
        msg, _, _ = self.messaging._visible_message(found["id"], c.user_id)
        if msg.get("deleted_at") or msg.get("recalled_at"):
            raise ServiceError(404, "no_message", "消息已撤回或删除")
        return {**out, "delivery": "recorded", "message_id": msg["id"], "seq": msg["seq"]}

    def receipt(self, c, i):
        row = self.store.one("SELECT * FROM op_runs WHERE id=? AND op=? AND user_id=? AND workspace_id=?",
                              (i.run_id, f"{MESSAGES_APP_ID}.message.send", c.user_id, c.workspace_id))
        if not row:
            raise ServiceError(404, "not_found", "找不到该发送记录")
        return self._message(c, row)  # read-only; absence never means safe to resend

    def message_guard(self, c, row):
        current = self._message(c, row)
        old = _json(row.get("output"))
        if old.get("message_id") and current["message_id"] != old["message_id"]:
            raise ServiceError(409, "result_access_unverifiable", "无法核实原消息")

    def send(self, c, i):
        client_id = self._key(c)
        with self.store.tx():
            self._authorize(c, MESSAGES_APP_ID, "write")
            conv = self._conversation(c, i.conversation_id)
            if conv["audience"] != i.audience:
                raise ServiceError(409, "audience_changed", "会话受众已变化，请重新核实后发送")
            links = []
            for aid in i.artifact_ids:
                Artifact(artifact_id=aid)
                self._artifact(c, aid, export=True)
                if not self.public_url:
                    raise ServiceError(503, "adapter_url_unconfigured", "尚未配置产物链接的应用地址")
                links.append(self._url(c, aid))
            body = "\n".join([i.body, *links]).strip()
            if not body:
                raise ServiceError(400, "empty_message", "消息不能为空")
            # Same existing per-user send budget as the Message HTTP route; reads never touch it.
            from synwevo_messages.routes import MESSAGES_PER_MINUTE
            allowed, wait = c.identity.window_allows(f"msg:{c.user_id}", limit=MESSAGES_PER_MINUTE, window_s=60)
            if not allowed:
                raise ServiceError(429, "too_many_attempts", "发送过于频繁", {"retry_after": wait})
            if conv["archived"]:
                raise ServiceError(409, "archived", "会话已归档，不能再发消息")
            # Queued in this operation's own transaction: the receipt and the queued message commit
            # together, so a lost acknowledgement can never hide a sent message. Message's own process
            # delivers it as this person after checking access, archive state and audience again.
            from synwevo_core.messaging_bridge import emit_user_message
            with self.store.tx() as t:
                emit_user_message(t, event_id=client_id, conversation_id=i.conversation_id, workspace_id=conv["workspace_id"],
                                  organization_id=conv["organization_id"], sender_id=c.user_id, body=body, audience=i.audience)
        return {"__run_status__": "queued", "delivery": "queued", "conversation_id": i.conversation_id,
                "message_id": None, "seq": None, "error_code": None}

    def register(self, ctx):
        if self._ctx is ctx:
            return
        if self._ctx is not None or ctx.store is not self.store:
            raise ValueError("One prepared adapter belongs to one Suite context and one Store")
        self._ctx = ctx
        from .suite_files import register as register_files, register_dataset_input
        register_files(ctx)
        docs = AppManifest(DOCS_APP_ID, "Synwevo Docs", "/", "file-text",
                           T("文档生成与产物", "文件生成與產物", "Document jobs and artifacts"),
                           object_kinds=("project", "job", "artifact"))
        ctx.registry.register_app(docs)

        def op(app, suffix, title, summary, model, handler, **kw):
            ctx.registry.register(Operation(id=f"{app}.{suffix}", app=app, title=T(*title), summary=summary,
                                             input=model, handler=handler, **kw))

        op(DOCS_APP_ID, "project.list", ("列出文档项目", "列出文件專案", "List document projects"),
           "List existing Docs projects in the explicit workspace.", Projects, self.projects, output=ProjectsResult)
        op(DOCS_APP_ID, "project.get", ("读取文档配置", "讀取文件設定", "Read document configuration"),
           "Read the configured template, input references and configuration fingerprint.", Project, self.project, output=ProjectResult)
        op(DOCS_APP_ID, "job.create", ("生成文档", "生成文件", "Generate documents"),
           "Queue the existing Docs job using the confirmed configuration. Requires an idempotency key; poll job.get.",
           CreateJob, self.create_job, need="write", side_effect="write", agent="confirm", mode="async",
           result_guard=self.job_guard, output=JobResult, events=("docs.job.submitted",), object_kind="job")
        op(DOCS_APP_ID, "job.get", ("查询文档任务", "查詢文件任務", "Read document job"),
           "Read the actual Docs job state; queued and partial are not success.", Job, self.job, output=JobResult)
        op(DOCS_APP_ID, "job.artifacts", ("列出文档产物", "列出文件產物", "List document artifacts"),
           "Page the job's live artifacts through Docs ACLs.", Artifacts, self.artifacts, output=ArtifactsResult)
        op(DOCS_APP_ID, "artifact.get", ("读取产物链接", "讀取產物連結", "Read artifact link"),
           "Read artifact metadata and an authenticated download URL; requires export permission.", Artifact, self.artifact, output=ArtifactLink)
        register_dataset_input(ctx, self)
        if self.messaging is not None:
            ctx.registry.register_app(AppManifest(MESSAGES_APP_ID, "Synwevo Message", "/messages", "message-circle",
                T("工作空间会话", "工作空間會話", "Workspace conversations"), object_kinds=("conversation", "message")))
            op(MESSAGES_APP_ID, "conversation.list", ("列出会话", "列出會話", "List conversations"),
               "List this user's authorized workspace-bound groups; does not search organization-wide DMs.", Page, self.conversations, output=ConversationsResult)
            op(MESSAGES_APP_ID, "conversation.get", ("读取会话受众", "讀取會話受眾", "Read conversation audience"),
               "Read the controlled destination and its audience fingerprint before confirmation.", Conversation, self.conversation, output=ConversationResult)
            op(MESSAGES_APP_ID, "message.send", ("发送消息", "傳送訊息", "Send message"),
               "Send confirmed text and same-workspace protected artifact links to the exact audience. Requires idempotency key.",
               Send, self.send, need="write", side_effect="external", agent="confirm", mode="async",
               result_guard=self.message_guard, output=SendResult, events=("messages.message.recorded",), object_kind="message")
            op(MESSAGES_APP_ID, "message.inspect", ("核实发送结果", "核實傳送結果", "Inspect message receipt"),
               "Read-only reconciliation of an unknown send by its run_id. Never sends or retries.", Receipt, self.receipt, output=SendResult)
        self._install_download(ctx)
        if ctx.start_jobs:
            from synwevo_suite.common.worker import Ticker
            self.ticker = Ticker("suite-docs-receipts", self.sync_receipts, 2).start(ctx.api)

    def sync_receipts(self):
        self.sync_jobs()
        self.sync_inputs()
        if self.messaging is not None:
            self.sync_messages()

    def sync_inputs(self):
        """Settle dataset-to-document inputs from the source's real check/parse outcome."""
        rows = self.store.all("SELECT id,workspace_id,output FROM op_runs WHERE op=? AND status IN ('queued','running') "
                              "ORDER BY id LIMIT 200", (f"{DOCS_APP_ID}.project.from_dataset",))
        for row in rows:
            out = _json(row["output"])
            try:
                with as_tenant(row["workspace_id"]):
                    source = self.docs.get_source(out.get("source_id"))
            except ServiceError as err:
                if err.status == 404:
                    self._ctx.capabilities.update_run(row["id"], "failed", output={**out, "state": "failed", "error_code": "source_missing"},
                                                      error=ServiceError(409, "source_missing", "文档输入已不存在"))
                continue
            if source.get("status") == "ready":
                self._ctx.capabilities.update_run(row["id"], "succeeded", output={**out, "state": "ready", "error_code": None})
            elif source.get("status") in ("rejected", "scan_failed", "failed", "needs_ocr"):
                code = "source_" + source["status"]
                self._ctx.capabilities.update_run(row["id"], "failed", output={**out, "state": "failed", "error_code": code},
                                                  error=ServiceError(409, code, "文档输入未通过检查或解析"))

    def sync_messages(self):
        """Settle queued sends from what Message actually did; never sends or re-queues anything."""
        from synwevo_core.messaging_bridge import user_message_state
        rows = self.store.all("SELECT id,workspace_id,user_id,input,output FROM op_runs WHERE op=? "
                              "AND status IN ('queued','unknown') ORDER BY id LIMIT 200",
                              (f"{MESSAGES_APP_ID}.message.send",))
        for row in rows:
            i = Send.model_validate(_json(row["input"]))
            state = user_message_state(self.store, row["id"])
            found = self.store.one("SELECT id, seq FROM messages WHERE conversation_id=? AND sender_id=? AND client_id=?",
                                   (i.conversation_id, row["user_id"], row["id"]))
            base = {"conversation_id": i.conversation_id, "error_code": None}
            if found:
                with self.store.tx() as t:
                    self._ctx.capabilities.update_run(row["id"], "succeeded", output={
                        **base, "delivery": "recorded", "message_id": found["id"], "seq": found["seq"]})
                    t.execute("UPDATE op_runs SET object_kind='message', object_id=? WHERE id=?", (found["id"], row["id"]))
                    # The receipt's event, now that the message exists: the conversation and the message id only.
                    from synwevo_core.capabilities import record_event
                    record_event(t, type_="messages.message.recorded", app=MESSAGES_APP_ID,
                                 workspace_id=row["workspace_id"], user_id=row["user_id"], object_kind="message",
                                 object_id=found["id"], run_id=row["id"],
                                 data={"conversation_id": i.conversation_id, "message_id": found["id"]})
            elif state["state"] == "failed":
                self._ctx.capabilities.update_run(row["id"], "failed", output={**base, "delivery": "failed", "error_code": state["code"]},
                    error=ServiceError(409, state["code"], "消息未发送：会话、成员或受众已变化"))

    def sync_jobs(self):
        """Worker-side reconciliation only. Read/inspect operations never update run records.

        JobService owns execution and recovery; this projects its durable status onto the
        operation receipt. Scan by id with a cursor so a long queue cannot starve later runs.
        """
        cursor = getattr(self, "_job_cursor", "")
        rows = self.store.all("SELECT id,workspace_id,object_id,output FROM op_runs WHERE op=? "
                              "AND status IN ('queued','running','unknown') AND object_id IS NOT NULL "
                              "AND id>? ORDER BY id LIMIT 100", (f"{DOCS_APP_ID}.job.create", cursor))
        for row in rows:
            self._job_cursor = row["id"]
            try:
                j = self.jobs.get(row["object_id"], row["workspace_id"])
            except ServiceError as err:
                if err.status == 404:
                    self._ctx.capabilities.update_run(row["id"], "failed", error=err)
                continue
            state = JOB_STATES.get(j["status"], j["status"])
            old = _json(row["output"])
            out = {**old, "status": j["status"], "state": state, "counts": j["counts"] or {},
                   "error_code": j.get("error_code"), "halt_reason": j.get("halt_reason")}
            self._ctx.capabilities.update_run(row["id"], state, output=out)
        if len(rows) < 100:
            self._job_cursor = ""

    def _install_download(self, ctx):
        from synwevo_core.capabilities import Call
        from synwevo_suite.common.web import signed_in
        from synwevo_docs.api.app import attachment

        @ctx.api.get(f"/api/suite/{DOCS_APP_ID}/workspaces/{{workspace_id}}/artifacts/{{artifact_id}}")
        def download(request: Request, workspace_id: str, artifact_id: str):
            user = signed_in(request)
            op = ctx.registry.get(f"{DOCS_APP_ID}.artifact.get")
            user, role = ctx.capabilities.authorize(op, user["id"], workspace_id)
            c = Call(ctx.store, ctx.identity, op, user, workspace_id, role, "", "ui", "", {}, capabilities=ctx.capabilities)
            self._artifact(c, artifact_id, export=True)
            a, data = self.jobs.artifact(artifact_id, workspace_id)
            return attachment(data, a["filename"], a["format"])


def prepare(api, plat, *, docs_api=None, messages_api=None, start_jobs=True, public_url=""):
    """Prepare before suite.install; reuses actual services, creates no application grants.

    A private Docs app is owned/shut down by `api` when Docs is absent on this origin. Message
    is registered only when its real owning application instance is supplied (or is `api`).
    """
    from synwevo_core.http import new_api
    from synwevo_docs.api import app as docs_module
    from synwevo_docs.jobs import JobService

    if getattr(api.state, "suite_adapters", None) is not None:
        raise ValueError("prepare must be called once, before Suite installation")
    docs_api = docs_api or (api if getattr(api.state, "app", None) is not None else None)
    if docs_api is None:
        docs_api = new_api("Synwevo Docs adapter")
        docs_module.install(docs_api, plat, start_jobs=start_jobs)
        api.state.shutdown_hooks.extend(docs_api.state.shutdown_hooks)
    if docs_api.state.app.store is not plat.store:
        raise ValueError("Docs and Suite must share the same Store instance")
    if docs_api.state.jobs is None:
        # Docs' factory intentionally has jobs=None when start_jobs=False. Build an inert
        # service once at preparation, never on inspect (constructor performs lease recovery).
        docs_api.state.jobs = JobService(docs_api.state.app, start=start_jobs,
                                        notifier=getattr(docs_api.state, "notifier", None))
        api.state.shutdown_hooks.append(docs_api.state.jobs.stop)
    adapters = SuiteAdapters(docs_api, messages_api or api, public_url=public_url, ident=plat.identity)
    api.state.suite_adapters = adapters
    return adapters


def install(ctx, adapters):
    """Explicit late-registration alternative; prefer suite.install(docs=adapters)."""
    adapters.register(ctx)
    return adapters
