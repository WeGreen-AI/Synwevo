"""Synwevo Forms' operations: the one contract its pages, the operations API, flows and the
personal Agent all call (`synwevo_core.capabilities`)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field as F

from synwevo_core.capabilities import Operation
from synwevo_core.errors import ServiceError

from .definition import Definition
from .service import Forms, _j

FORM_ID = r"^frm_[a-f0-9]{12}$"
REQ_ID = r"^freq_[a-f0-9]{12}$"
SUB_ID = r"^fsub_[a-f0-9]{12}$"
WS_ID = r"^[A-Za-z0-9_-]{1,100}$"


def T(zh: str, hant: str, en: str) -> dict:
    return {"zh-Hans": zh, "zh-Hant": hant, "en": en}


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListForms(_In):
    status: Literal["active", "archived"] = "active"
    q: str | None = F(default=None, max_length=100)


class FormRef(_In):
    form_id: str = F(pattern=FORM_ID)


class CreateForm(_In):
    title: str = F(min_length=1, max_length=200)
    description: str = F(default="", max_length=2000)
    definition: Definition = F(default_factory=Definition)


class UpdateDraft(_In):
    form_id: str = F(pattern=FORM_ID)
    draft_rev: int = F(ge=1)
    title: str | None = F(default=None, min_length=1, max_length=200)
    description: str | None = F(default=None, max_length=2000)
    definition: Definition | None = None


class Publish(_In):
    form_id: str = F(pattern=FORM_ID)
    draft_rev: int = F(ge=1)
    note: str = F(default="", max_length=500)
    # What the person publishing sees. Given, each must equal the stored draft, or nothing is
    # published (draft_unsaved): a refused or lost save must not publish an older draft.
    title: str | None = F(default=None, min_length=1, max_length=200,
                          description="The title as the publisher sees it; must equal the saved draft's")
    description: str | None = F(default=None, max_length=2000,
                                description="The description as the publisher sees it; must equal the saved draft's")
    definition: Definition | None = F(default=None,
                                      description="The definition as the publisher sees it; must equal the saved draft's")


class VersionRef(_In):
    form_id: str = F(pattern=FORM_ID)
    version: int = F(ge=1)


class Archive(_In):
    form_id: str = F(pattern=FORM_ID)
    archived: bool


class Duplicate(_In):
    form_id: str = F(pattern=FORM_ID)
    target_workspace_id: str | None = F(default=None, pattern=WS_ID)
    title: str | None = F(default=None, min_length=1, max_length=200)


class PublicLink(_In):
    form_id: str = F(pattern=FORM_ID)
    enabled: bool
    version: int | None = F(default=None, ge=1)


class Recipient(_In):
    email: str | None = F(default=None, max_length=254, pattern=r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s.]{2,63}$")
    user_id: str | None = F(default=None, max_length=100)
    name: str | None = F(default=None, max_length=200)


class CreateRequest(_In):
    form_id: str = F(pattern=FORM_ID)
    version: int | None = F(default=None, ge=1, description="Published version to pin; default: the latest")
    recipient: Recipient = F(default_factory=Recipient)
    prefill: dict = F(default_factory=dict, description="Answers to fill in ahead, keyed by field id")
    due_at: float | None = F(default=None, description="Deadline, seconds since the epoch (UTC)")
    submission_policy: Literal["once", "multiple"] = "once"
    require_sign_in: bool = False
    title: str | None = F(default=None, max_length=200)


class ListRequests(_In):
    form_id: str | None = F(default=None, pattern=FORM_ID)
    status: Literal["open", "submitted", "changes_requested", "accepted", "closed", "revoked", "expired"] | None = None
    limit: int = F(default=50, ge=1, le=200)
    before: float | None = None


class RequestRef(_In):
    request_id: str = F(pattern=REQ_ID)


class CloseRequest(_In):
    request_id: str = F(pattern=REQ_ID)
    mode: Literal["close", "revoke"] = "close"
    reason: str = F(default="", max_length=500)


class ExtendRequest(_In):
    request_id: str = F(pattern=REQ_ID)
    due_at: float | None = None


class ListSubmissions(_In):
    form_id: str | None = F(default=None, pattern=FORM_ID)
    request_id: str | None = F(default=None, pattern=REQ_ID)
    status: Literal["submitted", "changes_requested", "accepted", "superseded"] | None = None
    limit: int = F(default=50, ge=1, le=200)
    before: float | None = None


class SubmissionRef(_In):
    submission_id: str = F(pattern=SUB_ID)


class ReviewItem(_In):
    field_id: str | None = F(default=None, pattern=r"^f_[a-z0-9]{4,24}$")
    note: str = F(min_length=1, max_length=1000)


class RequestChanges(_In):
    submission_id: str = F(pattern=SUB_ID)
    note: str = F(default="", max_length=2000)
    items: list[ReviewItem] = F(min_length=1, max_length=50)


class Accept(_In):
    submission_id: str = F(pattern=SUB_ID)
    note: str = F(default="", max_length=2000)


def register(registry, forms: Forms) -> None:
    def result_guard(c, row):
        # A former editor's fill links must not escape via the operation receipt. The
        # invoker rechecks the original role; copies also require the destination's role.
        output = _j(row.get("output"), {})
        target = output.get("workspace_id") or c.workspace_id
        if target != c.workspace_id or c.op.writes_elsewhere:
            c.capabilities.check_workspace(c.user_id, "forms", target, "write")
        kind, oid = row.get("object_kind"), row.get("object_id")
        if kind == "form":
            forms._form(target, oid)
        elif kind == "form_request":
            forms._request(target, oid)
        elif kind == "form_submission":
            forms.get_submission(target, oid)
        else:
            raise ServiceError(409, "result_access_unverifiable", "无法核实原结果的访问权限")

    def op(**kw):
        if kw.get("side_effect", "none") != "none":
            kw.setdefault("result_guard", result_guard)
        registry.register(Operation(app="forms", **kw))

    op(id="forms.form.list", title=T("列出表单", "列出表單", "List forms"),
       summary="List the forms of a workspace with their request and submission counts.",
       input=ListForms, handler=lambda c, i: {"forms": forms.list_forms(c.workspace_id, status=i.status, q=i.q)})
    op(id="forms.form.get", title=T("查看表单", "查看表單", "Get a form"),
       summary="A form's current draft definition, published versions and public link state.",
       input=FormRef, object_kind="form",
       handler=lambda c, i: forms.form_json(forms._form(c.workspace_id, i.form_id), with_versions=True,
                                           can_write=c.can("write")))
    op(id="forms.form.version", title=T("查看表单版本", "查看表單版本", "Get a form version"),
       summary="The frozen definition of one published (or retired) version of a form.",
       input=VersionRef, object_kind="form",
       handler=lambda c, i: forms.version_json(c.workspace_id, i.form_id, i.version))
    op(id="forms.form.create", title=T("新建表单", "新建表單", "Create a form"),
       summary="Create a form as a draft. Publish it before sending requests.",
       input=CreateForm, need="write", side_effect="write", agent="confirm", object_kind="form",
       handler=lambda c, i: forms.create(c, title=i.title, description=i.description, definition=i.definition))
    op(id="forms.form.update_draft", title=T("保存表单草稿", "儲存表單草稿", "Save a form draft"),
       summary="Replace the draft definition, title or description. Fails with draft_conflict if draft_rev is stale.",
       input=UpdateDraft, need="write", side_effect="write", agent="confirm", object_kind="form",
       handler=lambda c, i: forms.update_draft(c, i.form_id, draft_rev=i.draft_rev, title=i.title,
                                              description=i.description, definition=i.definition))
    op(id="forms.form.publish", title=T("发布表单", "發佈表單", "Publish a form"),
       summary="Freeze the draft into a new immutable version. Existing requests keep the version they pinned. "
               "Pass the title, description and definition you are looking at to refuse (draft_unsaved) "
               "when the saved draft is not what you see.",
       input=Publish, need="write", side_effect="write", agent="confirm", object_kind="form",
       events=("forms.form.published",),
       handler=lambda c, i: forms.publish(c, i.form_id, draft_rev=i.draft_rev, note=i.note,
                                         seen={"title": i.title, "description": i.description,
                                               "definition": i.definition}))
    op(id="forms.form.retire_version", title=T("停用表单版本", "停用表單版本", "Retire a form version"),
       summary="Stop a published version from being used for new requests. Open requests on it continue.",
       input=VersionRef, need="write", side_effect="write", agent="confirm", object_kind="form",
       handler=lambda c, i: forms.retire_version(c, i.form_id, i.version))
    op(id="forms.form.archive", title=T("归档或恢复表单", "封存或還原表單", "Archive or restore a form"),
       summary="Archive (or restore) a form. Archived forms accept no new requests.",
       input=Archive, need="write", side_effect="write", agent="confirm", object_kind="form",
       handler=lambda c, i: forms.set_archived(c, i.form_id, i.archived))
    op(id="forms.form.duplicate", title=T("复制表单", "複製表單", "Duplicate a form"),
       summary="Copy a form's draft definition (never its requests, submissions, files or links), "
               "optionally into another workspace where the caller can create forms.",
       input=Duplicate, need="read", side_effect="write", agent="confirm", object_kind="form", writes_elsewhere=True,
       handler=lambda c, i: forms.duplicate(c, i.form_id, target_workspace_id=i.target_workspace_id, title=i.title))
    op(id="forms.public_link.set", title=T("设置公开填写链接", "設定公開填寫連結", "Set the public link"),
       summary="Turn a form's public fill-in link on (a new link each time) or off.",
       input=PublicLink, need="write", side_effect="external", agent="confirm", object_kind="form",
       # Switching the link off reaches nobody: a run that read another workspace may still do it.
       outward_when=lambda i: i.get("enabled") is not False,
       handler=lambda c, i: forms.set_public_link(c, i.form_id, enabled=i.enabled, version=i.version))
    op(id="forms.request.create", title=T("发出填写请求", "發出填寫請求", "Send a fill-in request"),
       summary="Create a personal fill-in request for a published form version, with optional prefill, "
               "deadline and recipient. Returns the link to send. Emits forms.request.created.",
       input=CreateRequest, need="write", side_effect="external", agent="confirm", object_kind="form_request",
       events=("forms.request.created",),
       handler=lambda c, i: forms.create_request(c, i.form_id, version=i.version, recipient=i.recipient.model_dump(),
                                                prefill=i.prefill, due_at=i.due_at, policy=i.submission_policy,
                                                require_sign_in=i.require_sign_in, title=i.title))
    op(id="forms.request.list", title=T("列出填写请求", "列出填寫請求", "List fill-in requests"),
       summary="Fill-in requests of the workspace, newest first, filterable by form and status.",
       input=ListRequests,
       handler=lambda c, i: forms.list_requests(c.workspace_id, form_id=i.form_id, status=i.status, limit=i.limit,
                                               before=i.before, can_write=c.can("write")))
    op(id="forms.request.get", title=T("查看填写请求", "查看填寫請求", "Get a fill-in request"),
       summary="A fill-in request with its status and submissions.", input=RequestRef, object_kind="form_request",
       handler=lambda c, i: forms.get_request(c.workspace_id, i.request_id, can_write=c.can("write")))
    op(id="forms.request.close", title=T("关闭或撤销填写请求", "關閉或撤銷填寫請求", "Close or revoke a request"),
       summary="Stop a request accepting answers (close), or invalidate its link entirely (revoke).",
       input=CloseRequest, need="write", side_effect="write", agent="confirm", object_kind="form_request",
       events=("forms.request.closed", "forms.request.revoked"),
       handler=lambda c, i: forms.close_request(c, i.request_id, mode=i.mode, reason=i.reason))
    op(id="forms.request.extend", title=T("修改截止时间", "修改截止時間", "Change the deadline"),
       summary="Change or clear a request's deadline; an expired request reopens.",
       input=ExtendRequest, need="write", side_effect="write", agent="confirm", object_kind="form_request",
       shown_to_link_holders=True,
       handler=lambda c, i: forms.extend_request(c, i.request_id, i.due_at))
    op(id="forms.request.reissue_link", title=T("重新生成链接", "重新產生連結", "Reissue the link"),
       summary="Replace a request's link; the old link stops working.",
       input=RequestRef, need="write", side_effect="external", agent="confirm", object_kind="form_request",
       # The new link goes back to the caller only; whatever sends it on is asked for itself.
       outward_when=lambda i: False,
       handler=lambda c, i: forms.reissue_link(c, i.request_id))
    op(id="forms.submission.list", title=T("列出提交", "列出提交", "List submissions"),
       summary="Submissions with their answers keyed by field id, newest first.", input=ListSubmissions,
       handler=lambda c, i: forms.list_submissions(c.workspace_id, form_id=i.form_id, request_id=i.request_id,
                                                  status=i.status, limit=i.limit, before=i.before))
    op(id="forms.submission.get", title=T("查看提交", "查看提交", "Get a submission"),
       summary="One submission: answers keyed by field id, field labels and types, attachments, review history.",
       input=SubmissionRef, object_kind="form_submission",
       handler=lambda c, i: forms.get_submission(c.workspace_id, i.submission_id))
    op(id="forms.submission.request_changes", title=T("要求补充或修改", "要求補充或修改", "Request changes"),
       summary="Ask the filler to correct or add to a submission; the link reopens for a new revision.",
       input=RequestChanges, need="write", side_effect="external", agent="confirm", object_kind="form_submission",
       events=("forms.submission.changes_requested",),
       handler=lambda c, i: forms.request_changes(c, i.submission_id, note=i.note,
                                                 items=[x.model_dump() for x in i.items]))
    op(id="forms.submission.accept", title=T("确认提交", "確認提交", "Accept a submission"),
       summary="Mark a submission as accepted (checked by a person). Acceptance is not an approval decision.",
       input=Accept, need="write", side_effect="write", agent="confirm", object_kind="form_submission",
       events=("forms.submission.accepted",),
       handler=lambda c, i: forms.accept(c, i.submission_id, note=i.note))
