"""One operation surface for the Extract UI, API, automation and Agent."""
from __future__ import annotations

import inspect
from typing import Annotated, Literal

from pydantic import Field, model_validator
from synwevo_core.capabilities import Operation
from .definition import Definition, Strict

TID = r"^extpl_[a-f0-9]{12}$"
RID = r"^extask_[a-f0-9]{12}$"
FID = r"^exfile_[a-f0-9]{12}$"


class Page(Strict):
    after: str = Field(default="", max_length=50)
    limit: int = Field(default=30, ge=1, le=100)


class Templates(Page):
    archived: bool = False


class TemplateRef(Strict):
    template_id: str = Field(pattern=TID)


class TemplateCreate(Strict):
    title: str = Field(min_length=1, max_length=160)
    definition: Definition


class TemplateSave(TemplateCreate, TemplateRef):
    draft_rev: int = Field(ge=1)


class Publish(TemplateRef):
    draft_rev: int = Field(ge=1)


class Version(TemplateRef):
    version: int = Field(ge=1, le=100)


class Archive(TemplateRef):
    archived: bool


class TaskCreate(Version):
    file_id: str = Field(pattern=FID)
    ocr: bool = False


class Tasks(Page):
    status: Literal["", "queued", "running", "waiting_input", "succeeded", "failed", "cancelled"] = ""


class TaskRef(Strict):
    task_id: str = Field(pattern=RID)


class Segments(TaskRef):
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=50, ge=1, le=100)


class Revision(TaskRef):
    revision: int = Field(ge=1, le=201)


Value = Annotated[str, Field(max_length=2000)]


class Correction(Revision):
    values: dict[Annotated[str, Field(max_length=40)], Value | Annotated[list[Value], Field(max_length=100)]] = Field(min_length=1, max_length=40)
    note: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def bounded(self):
        import json
        if not self.note.strip() or len(json.dumps(self.values).encode()) > 64000:
            raise ValueError("correction too large or empty note")
        return self


class Confirm(Revision):
    note: str = Field(min_length=1, max_length=1000)
    acknowledge_unverified: bool = False


def register(registry, service):
    def add(id, title, schema, handler, *, kind=None, write=False, human=False, events=(), **kw):
        if "result_guard" in inspect.signature(Operation).parameters:
            kw["result_guard"] = service.result_guard
        registry.register(Operation(id=f"extract.{id}", app="extract",
            title={"zh-Hans": title[0], "zh-Hant": title[1], "en": title[2]}, summary=title[2],
            input=schema, handler=handler, object_kind=kind, need="write" if write else "read",
            side_effect="write" if write else "none", agent="never" if human else "confirm" if write else "auto",
            unattended=not human, events=tuple(f"extract.{e}" for e in events), **kw))

    add("template.list", ("提取模板", "提取範本", "List extraction templates"), Templates, service.list_templates)
    add("template.get", ("查看模板", "查看範本", "Read template and versions"), TemplateRef,
        lambda c, i: service.template(c.workspace_id, i.template_id), kind="extract_template")
    add("template.create", ("新建模板", "新增範本", "Create extraction template"), TemplateCreate,
        service.create_template, write=True, kind="extract_template", events=("template.created",))
    add("template.save", ("保存模板", "儲存範本", "Save template draft with revision check"), TemplateSave,
        service.save_template, write=True, kind="extract_template", events=("template.saved",))
    add("template.publish", ("发布模板", "發佈範本", "Publish immutable extraction version"), Publish,
        service.publish, write=True, kind="extract_template", events=("template.published",))
    add("template.retire", ("停用版本", "停用版本", "Retire version for new tasks"), Version,
        service.retire, write=True, kind="extract_template", events=("template.retired",))
    add("template.archive", ("归档模板", "封存範本", "Archive or restore template"), Archive,
        service.archive, write=True, kind="extract_template", events=("template.archived",))
    add("task.create", ("开始提取", "開始提取", "Queue extraction of one file with a pinned template version"), TaskCreate,
        service.create_task, write=True, kind="extract_task", mode="async", cancellable=True,
        events=("task.queued", "task.started", "task.ready", "task.failed"))
    add("task.list", ("提取任务", "提取工作", "List extraction tasks"), Tasks, service.list_tasks)
    add("task.get", ("查看提取", "查看提取", "Read extraction results and review history"), TaskRef,
        lambda c, i: service.task(c.workspace_id, i.task_id), kind="extract_task")
    add("task.segments", ("查看原文", "查看原文", "Read paginated source segments"), Segments, service.segments, kind="extract_task")
    add("task.review_version", ("查看复核版本", "查看覆核版本", "Read an immutable review revision"), Revision,
        service.review_version, kind="extract_task")
    add("task.correct", ("保存人工修正", "儲存人工修正", "Create a human correction revision"), Correction,
        service.correct, write=True, human=True, kind="extract_task", events=("task.correction",))
    add("task.confirm", ("确认复核结果", "確認覆核結果", "Confirm a reviewed result revision"), Confirm,
        service.confirm, write=True, human=True, kind="extract_task", events=("task.confirmation",))
    add("task.result", ("读取已确认结果", "讀取已確認結果", "Read the current confirmed result at an explicit revision"), Revision,
        service.result, kind="extract_task")
    add("task.cancel", ("取消提取", "取消提取", "Cancel queued or running extraction"), TaskRef,
        service.cancel, write=True, kind="extract_task", events=("task.cancelled",))
