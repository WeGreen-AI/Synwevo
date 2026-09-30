"""One typed operation contract for the UI, API and automation callers."""
from dataclasses import dataclass
from typing import Callable, Literal
from pydantic import BaseModel, ConfigDict, Field as F, field_validator
from synwevo_core.capabilities import Call, Operation, canonical
from .definition import Definition, USER_ID

@dataclass(frozen=True)
class ApprovalOperation(Operation):
    """Declares the coordinator's result_guard contract even on the pre-integration base.

    This field overrides the identical field once core adds it. Old core does not invoke it;
    history/replay enforcement therefore requires the coordinator's Capabilities update.
    """
    result_guard: Callable[[Call, dict], None] | None = None


TEMPLATE = r"^apt_[a-f0-9]{12}$"
REQUEST = r"^apr_[a-f0-9]{12}$"
TASK = r"^atk_[a-f0-9]{12}$"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(Input):
    offset: int = F(default=0, ge=0, le=1000000)
    limit: int = F(default=50, ge=1, le=100)
    q: str = F(default="", max_length=100)


class Templates(Page):
    status: Literal["active", "archived"] = "active"


class TemplateRef(Input):
    template_id: str = F(pattern=TEMPLATE)


class CreateTemplate(Input):
    title: str = F(min_length=1, max_length=200)
    description: str = F(default="", max_length=2000)
    definition: Definition = F(default_factory=Definition)


class UpdateTemplate(CreateTemplate, TemplateRef):
    draft_rev: int = F(ge=1)


class Publish(TemplateRef):
    draft_rev: int = F(ge=1)
    note: str = F(default="", max_length=500)


class Version(TemplateRef):
    version: int = F(ge=1)


class Archive(TemplateRef):
    archived: bool


class RequestRef(Input):
    request_id: str = F(pattern=REQUEST)


class Snapshot(Input):
    values: dict[str, str | int | None] = F(default_factory=dict, max_length=50)
    basis: str = F(default="", max_length=30000, description="Exact material submitted for review; immutable per revision")
    @field_validator("values", mode="before")
    @classmethod
    def bounded(cls, value):
        if not isinstance(value, dict) or any(not isinstance(k, str) or type(v) not in (str, int, type(None)) for k, v in value.items()):
            raise ValueError("values must be strings, integers or null; decimals must be strings")
        if len(canonical(value).encode()) > 65536:
            raise ValueError("values exceed 64 KiB")
        return value


class CreateRequest(Snapshot, TemplateRef):
    title: str = F(min_length=1, max_length=200)
    version: int | None = F(default=None, ge=1)


class Change(RequestRef):
    state_version: int = F(ge=1)
    note: str = F(default="", max_length=2000)


class Revise(Change, Snapshot):
    pass


class Decide(Change):
    task_id: str = F(pattern=TASK)
    decision: Literal["approve", "reject", "return"]


class Transfer(Change):
    task_id: str = F(pattern=TASK)
    to_user_id: str = F(pattern=USER_ID)


class Repair(Change):
    users: list[str] = F(min_length=1, max_length=50)
    @field_validator("users")
    @classmethod
    def ids(cls, value):
        import re
        if len(set(value)) != len(value) or any(not re.fullmatch(USER_ID, x) for x in value):
            raise ValueError("unique valid user ids required")
        return value


class Requests(Page):
    view: Literal["todo", "sent", "handled", "all"] = "todo"
    status: Literal["in_review", "approved", "rejected", "returned", "withdrawn", "stalled"] | None = None


class History(RequestRef):
    after: int = F(default=0, ge=0)
    limit: int = F(default=100, ge=1, le=200)


class Revision(RequestRef):
    revision: int = F(ge=1)


class Wait(RequestRef):
    revision: int = F(ge=1)
    timeout_hours: int = F(default=720, ge=1, le=2160)


EVENTS = tuple("approvals." + x for x in (
    "template.published", "request.created", "request.revised", "request.approved", "request.rejected",
    "request.returned", "request.withdrawn", "request.stalled", "request.resumed", "task.decided",
    "task.transferred", "task.reassigned", "task.revoked", "stage.opened", "stage.skipped",
    "stage.approved", "stage.reminder", "stage.escalated", "request.commented"))


def register(registry, s):
    def add(name, model, handler, zh, hant, en, *, need="read", person=False, summary=None):
        write = need != "read"
        registry.register(ApprovalOperation(id="approvals." + name, app="approvals",
            title={"zh-Hans": zh, "zh-Hant": hant, "en": en}, summary=summary or en,
            input=model, handler=handler, need=need, side_effect="write" if write else "none",
            agent="never" if person else "confirm" if write else "auto", unattended=not person,
            object_kind="approval_template" if name.startswith("template.") else "approval_request",
            events=EVENTS if write else (), mode="async" if name == "request.wait" else "sync",
            result_guard=s.result_guard if write else None))
    add("members", Page, s.members, "审批人候选", "審批人候選", "Eligible workspace approvers")
    add("template.list", Templates, s.templates, "审批模板", "審批範本", "List approval templates")
    add("template.get", TemplateRef, s.template, "查看审批模板", "查看審批範本", "Get approval template")
    add("template.create", CreateTemplate, s.create_template, "创建审批模板", "建立審批範本", "Create approval template", need="manage")
    add("template.update", UpdateTemplate, s.update_template, "保存审批草稿", "儲存審批草稿", "Save approval draft", need="manage")
    add("template.publish", Publish, s.publish, "发布审批版本", "發佈審批版本", "Publish approval version", need="manage")
    add("template.version", Version, s.version, "查看审批版本", "查看審批版本", "Get approval version")
    add("template.retire", Version, s.retire, "停用审批版本", "停用審批版本", "Retire approval version", need="manage")
    add("template.archive", Archive, s.archive, "归档审批模板", "封存審批範本", "Archive approval template", need="manage")
    add("request.create", CreateRequest, s.create, "发起审批", "發起審批", "Request approval", need="write")
    add("request.list", Requests, s.requests, "审批请求", "審批請求", "List approval requests")
    add("request.get", RequestRef, s.get, "审批详情", "審批詳情", "Read request, revisions and decisions")
    add("request.revision", Revision, s.revision, "查看申请修订", "查看申請修訂", "Read an immutable request revision")
    add("request.timeline", History, s.history, "审批时间线", "審批時間軸", "Read paginated approval history")
    add("request.revise", Revise, s.revise, "修订并重新审批", "修訂並重新審批", "Revise and restart all approval stages", need="write")
    add("request.withdraw", Change, s.withdraw, "撤回审批", "撤回審批", "Withdraw own pending request", need="write")
    add("request.decide", Decide, s.decide, "提交审批决定", "提交審批決定", "Decide as the assigned independent approver", need="write", person=True)
    add("request.transfer", Transfer, s.transfer, "转交审批", "轉交審批", "Transfer own pending task to an eligible person", need="write", person=True)
    add("request.repair", Repair, s.repair, "修正审批人", "修正審批人", "Repair a stalled stage without reducing its quorum", need="manage", person=True)
    add("request.comment", Change, s.comment, "添加审批意见", "新增審批意見", "Add a comment without deciding", need="write")
    add("request.wait", Wait, s.wait, "等待审批结果", "等待審批結果", "Persist a revision-bound wait, with timeout and live permission checks", need="write")
    add("alerts", Page, s.alerts, "审批提醒", "審批提醒", "Read persisted approval reminders and escalations")
