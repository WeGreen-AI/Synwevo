"""Composition-owned file handoffs. Each app keeps its existing validation and access policy."""
from pydantic import BaseModel, ConfigDict, Field
from synwevo_core.capabilities import Operation
from synwevo_core.errors import ServiceError


class FromForms(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    submission_id: str = Field(pattern=r"^fsub_[a-f0-9]{12}$")
    file_id: str = Field(pattern=r"^ffile_[a-f0-9]{12}$")


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
