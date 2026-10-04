"""Composition-owned file handoffs. Each app keeps its existing validation and access policy."""
import json

from pydantic import BaseModel, ConfigDict, Field
from synwevo_core.apps import DOCS_APP_ID
from synwevo_core.capabilities import Operation
from synwevo_core.errors import ServiceError


class FromForms(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    submission_id: str = Field(pattern=r"^fsub_[a-f0-9]{12}$")
    file_id: str = Field(pattern=r"^ffile_[a-f0-9]{12}$")


class FromDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    template_project_id: str = Field(pattern=r"^p_[a-f0-9]{12}$")
    dataset_id: str = Field(pattern=r"^dset_[a-f0-9]+$")
    name: str | None = Field(default=None, min_length=1, max_length=100)


class Materialized(BaseModel):
    project_id: str
    source_id: str
    template_project_id: str
    dataset_id: str
    state: str
    error_code: str | None = None


def register(ctx):
    if not (ctx.registry.app("forms") and ctx.registry.app("extract")):
        return
    forms, extract = ctx.api.state.forms, ctx.api.state.extract

    def copy(call, i):
        key = ctx.store.one("SELECT idempotency_key FROM op_runs WHERE id=?", (call.run_id,))
        if not key or not key["idempotency_key"]:
            raise ServiceError(400, "idempotency_key_required", "导入附件需要幂等键")
        submission = ctx.capabilities.inspect("forms.submission.get", {"submission_id": i.submission_id},
                                              user_id=call.user_id, workspace_id=call.workspace_id)
        row, data = forms.file_bytes(call.user_id, call.workspace_id, i.file_id, submission_id=i.submission_id)
        # Authorized export into a same-workspace independent input: no credentials, links or
        # other answers copied. Later source app revocation is not a target ownership change.
        source = {"app": "forms", "workspace_id": call.workspace_id, "submission_id": i.submission_id,
                  "file_id": i.file_id, "answers_hash": submission["answers_hash"],
                  "form_id": submission["form_id"], "form_version": submission["form_version"]}
        file = extract.upload(call.user_id, call.workspace_id, row["filename"], data, call.run_id, source=source)
        call.touches("extract_file", file["id"])
        call.emit("extract.file.imported", object_kind="extract_file", object_id=file["id"],
                  data={"file_id": file["id"], "source_app": "forms", "submission_id": i.submission_id})
        return file

    ctx.registry.register(Operation(id="extract.file.from_forms", app="extract",
        title={"zh-Hans": "导入表单附件", "zh-Hant": "匯入表單附件", "en": "Import a form attachment"},
        summary="Import an authorized submitted attachment in this workspace; reuses upload safety and quotas. Requires an idempotency key.",
        input=FromForms, handler=copy, need="write", side_effect="write", agent="confirm",
        object_kind="extract_file", result_guard=extract.result_guard, events=("extract.file.imported",),
        requires_key=True, reaches=("forms.read",)))


def register_dataset_input(ctx, adapters):
    """A DataPrep dataset becomes the only input of a new Docs project configured like a template
    project. Each business run gets its own project and immutable input (docs/suite-adapters.md
    §输入物化): no run edits a shared, active source, and the template version is pinned."""
    if not (ctx.registry.app("dataprep") and ctx.registry.app(DOCS_APP_ID)):
        return
    from synwevo_suite.dataprep.files import export_xlsx
    from synwevo_suite.dataprep.history import check_sources
    from .suite_adapters import _context
    dataprep, docs = ctx.api.state.dataprep, adapters.docs

    def materialize(call, i):
        adapters._key(call)
        template = adapters._project(call, i.template_project_id)
        adapters._authorize(call, need="write")
        # The same rules as DataPrep's own export: read, export right, live source access.
        ctx.capabilities.inspect("dataprep.dataset.get", {"dataset_id": i.dataset_id},
                                 user_id=call.user_id, workspace_id=call.workspace_id)
        member = call.identity.membership(call.workspace_id, call.user_id)
        if not member or not (member["role"] in ("owner", "admin", "editor") or member.get("can_export")):
            raise ServiceError(403, "export_not_allowed", "你在该工作空间没有导出权限")
        check_sources(dataprep, call.workspace_id, call.user_id, i.dataset_id)
        with _context(call):
            source_project = docs.get_project(i.template_project_id)
            rule = source_project.get("record_rule") or {}
            sheet = (rule.get("table") or {}).get("sheet")
            if not template["template"] or not sheet:
                raise ServiceError(409, "template_project_incomplete", "模板项目尚未配置模板与数据表规则")
            mappings = docs.get_mappings(i.template_project_id)["mappings"]
            needed = sorted({src.get("header") for m in mappings for src in m.get("sources") or []
                             if src.get("kind") == "column" and src.get("sheet") == sheet and src.get("header")})
            table = dataprep.load(call.workspace_id, i.dataset_id)
            missing = [h for h in needed if h not in table.columns]
            if missing:
                raise ServiceError(409, "dataset_columns_missing", "数据集缺少模板映射所需的列", {"missing": missing[:50]})
            data = export_xlsx(table, sheet_name=sheet)
            project = docs.create_project(i.name or source_project["name"], origin=f"suite:{call.run_id}"[:80])
            docs.update_project(project["id"], {"template_id": source_project["template_id"],
                                                "template_version": template["template"]["version"],
                                                "record_rule": rule, "output": source_project.get("output")})
            if mappings:
                docs.save_mappings(project["id"], mappings, note="按模板项目复制")
            # The existing ingest path: size limits, security check and parsing, as for an upload.
            source = docs.add_source(project["id"], f"{i.dataset_id}.xlsx", data, actor=call.user_id)
        call.touches("project", project["id"])
        call.emit("docs.project.materialized", object_kind="project", object_id=project["id"],
                  data={"project_id": project["id"], "template_project_id": i.template_project_id, "dataset_id": i.dataset_id})
        return {"__run_status__": "queued", "project_id": project["id"], "source_id": source["id"],
                "template_project_id": i.template_project_id, "dataset_id": i.dataset_id, "state": "queued"}

    def guard(call, row):
        out = row.get("output")
        out = json.loads(out) if isinstance(out, str) else (out or {})
        if not out.get("project_id"):
            raise ServiceError(409, "result_access_unverifiable", "无法核实生成的文档项目")
        adapters._project(call, out["project_id"])

    ctx.registry.register(Operation(id=f"{DOCS_APP_ID}.project.from_dataset", app=DOCS_APP_ID,
        title={"zh-Hans": "以数据集创建文档输入", "zh-Hant": "以資料集建立文件輸入", "en": "Create a document input from a dataset"},
        summary="Copy a configured template project into a new project whose only input is this workspace's DataPrep "
                "dataset (template version pinned). Async until the input is checked and parsed; then read project.get. "
                "Requires an idempotency key.",
        input=FromDataset, handler=materialize, need="write", side_effect="write", agent="confirm", mode="async",
        object_kind="project", result_guard=guard, output=Materialized, events=("docs.project.materialized",),
        requires_key=True, reaches=("dataprep.read",)))
