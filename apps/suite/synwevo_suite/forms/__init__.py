"""Synwevo Forms: forms and information collection.

Build a form, publish immutable versions, send personal fill-in requests (with prefill, deadline
and an optional sign-in requirement) or open a public link, collect submissions with attachments,
ask for changes and accept. Every submission records the version it answered and its answers by
stable field id, readable by DataPrep, Tables, Docs and the personal Agent through the operations.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response

from synwevo_core.capabilities import AppManifest, EventFeed, EventType

MANIFEST = AppManifest(
    id="forms", name="Synwevo Forms", path="/forms", icon="form",
    summary={"zh-Hans": "设计表单，发出填写请求，收集资料与附件，要求补充并确认。",
             "zh-Hant": "設計表單，發出填寫請求，收集資料與附件，要求補充並確認。",
             "en": "Design forms, send fill-in requests, collect answers and files, ask for changes and accept."},
    object_kinds=("form", "form_request", "form_submission"),
    object_paths=(("form", "/forms/w/{workspace}/f/{id}"), ("form_request", "/forms/w/{workspace}/r/{id}"),
                  ("form_submission", "/forms/w/{workspace}/s/{id}")),
    machine=True)

#: What a credential may read about Forms' events. Forms, requests and submissions are readable by
#: everyone who can read Forms in the workspace, so there is no per-record decision to make.
EVENTS = EventFeed(types=(
    EventType("forms.form.published", "form"),
    *(EventType(f"forms.request.{what}", "form_request", refs=("form_id",))
      for what in ("created", "closed", "revoked", "expired")),
    *(EventType(f"forms.submission.{what}", "form_submission", refs=("form_id", "request_id"))
      for what in ("created", "resubmitted", "changes_requested", "accepted"))))


def install(ctx) -> None:
    from synwevo_core.http import SUITE_V1
    from ..common.machine import acting
    from ..common.worker import Ticker
    from .filling import Filling, _attachment, build_public_router
    from .ops import register
    from .service import Forms

    forms = Forms(ctx.store, ctx.identity, ctx.capabilities)
    ctx.api.state.forms = forms
    register(ctx.registry, forms)
    ctx.registry.register_events(MANIFEST.id, EVENTS)
    ctx.api.include_router(build_public_router(Filling(forms), ctx.capabilities))

    @ctx.api.get("/api/suite/forms/files/{file_id}", tags=["forms"])
    @ctx.api.get(SUITE_V1 + "/forms/files/{file_id}", tags=["forms"])
    def download(file_id: str, workspace_id: str, request: Request) -> Response:
        """An attachment of a submission, for members of its workspace who may take files out
        (editors and above, or a member with the export right — the same rule as Docs)."""
        with acting(ctx, request, workspace_id, app="forms", scope="forms.read") as who:
            row, data = forms.file_bytes(who.user["id"], workspace_id, file_id)
        return _attachment(row, data)

    if ctx.start_jobs:
        Ticker("forms-expiry", forms.sweep_expired, 60).start(ctx.api)
