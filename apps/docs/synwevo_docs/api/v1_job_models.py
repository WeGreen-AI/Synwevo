"""Public job responses, preserving historical nulls and optional wire fields.

Routes exclude unset fields, not nulls: a first submission must not acquire a replay
marker, and old sparse counts/options must not acquire defaults. The service's public
views still own authority labels and trace removal; these models never read stored rows.
"""
from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, JsonValue

JobStatus = Literal["queued", "running", "cancelling", "completed", "completed_with_errors", "failed", "cancelled"]
JobPhase = Literal["planning", "generating"]
JobItemStatus = Literal["pending", "running", "success", "failed", "skipped", "cancelled"]


class JobResponse(BaseModel):
    model_config = ConfigDict(strict=True)


class JobOpenObject(JobResponse):
    model_config = ConfigDict(extra="allow")


class JobOptions(JobOpenObject):
    # Historical rows may omit options that newer submitters always write.
    formats: list[str] | None = None
    error_policy: str | None = None
    naming: str | None = None
    keep_fields: bool | None = None
    layout: str | None = None


class JobCounts(JobResponse):
    # The public view fills missing keys with zero but preserves explicit historical nulls.
    samples: int | None
    documents: int | None
    files: int | None
    failed: int | None
    skipped: int | None
    cancelled: int | None
    pending: int | None
    running: int | None
    planned: int | None
    unplanned: int | None


class JobRawCounts(JobOpenObject):
    # List rows and event payloads use stored status counts, not JobCounts.
    total: int | None = None
    planned: int | None = None
    unplanned: int | None = None
    files: int | None = None
    documents: int | None = None
    pending: int | None = None
    running: int | None = None
    success: int | None = None
    failed: int | None = None
    skipped: int | None = None
    cancelled: int | None = None


class JobTemplate(JobResponse):
    id: str | None
    name: str | None
    format: str | None
    version_no: int | None


class JobView(JobResponse):
    id: str
    project_id: str
    status: JobStatus
    run_no: int | None
    created_at: float | None
    started_at: float | None
    finished_at: float | None
    template: JobTemplate | None
    options: JobOptions | None
    error: str | None
    error_code: str | None
    cancel_requested: bool
    phase: JobPhase
    halt_reason: str | None
    principal: str | None
    request_id: str | None
    counts: JobCounts
    idempotent_replay: bool = False


class JobCancellation(JobResponse):
    accepted: bool
    reason: Literal["already_terminal", "already_requested"] | None


class JobCancelledView(JobView):
    cancel: JobCancellation


class JobListItem(JobResponse):
    id: str
    status: JobStatus
    created_at: float | None
    started_at: float | None
    finished_at: float | None
    counts: JobRawCounts | None
    options: JobOptions | None
    run_no: int | None
    phase: JobPhase
    halt_reason: str | None
    error_code: str | None
    principal: str | None


class JobRun(JobResponse):
    run_no: int
    trigger: str
    requested_by: str | None
    items: int | None
    created_at: float | None
    started_at: float | None
    finished_at: float | None
    status: str | None
    requested_by_principal: str | None


class JobItemArtifact(JobResponse):
    id: str
    item_id: str
    format: str | None
    filename: str | None
    sha256: str | None
    size: int | None
    verification: JsonValue
    run_no: int | None


class JobItem(JobResponse):
    id: str
    seq: int | None
    record_key: str | None
    label: str | None
    status: JobItemStatus
    # _public_issues removes traces from object entries and preserves older JSON shapes.
    issues: JsonValue
    attempts: int | None
    file_base: str | None
    started_at: float | None
    finished_at: float | None
    run_no: int | None
    artifacts: list[JobItemArtifact]


class JobArtifact(JobResponse):
    id: str
    item_id: str
    format: str | None
    filename: str | None
    size: int | None
    sha256: str | None
    created_at: float | None
    run_no: int | None
    seq: int | None
    label: str | None


class JobEvent(JobResponse):
    seq: int
    id: str
    type: str
    version: int
    job_id: str
    occurred_at: float
    # Event versions may add fields; their payload is already the published public data.
    data: JsonValue


class JobEventsPage(JobResponse):
    items: list[JobEvent]
    next_after: int


T = TypeVar("T")


class JobPage(JobResponse, Generic[T]):
    items: list[T]
    total: int
    offset: int | None
    limit: int
    next_offset: int | None
    next_cursor: str | None
