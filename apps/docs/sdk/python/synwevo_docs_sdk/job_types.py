"""Public static types for job lifecycle responses; runtime values remain dictionaries.

Counts in job lists are stored, sparse values; job details always include all ten counters (missing keys become zero, explicit nulls remain).
Historical options and format-specific diagnostics stay as open JSON rather than being
filled with defaults by the client. No server models or validation dependencies are used.
"""
from typing import Literal, TypeAlias, TypedDict, Union

JSONValue: TypeAlias = Union[None, bool, int, float, str, list["JSONValue"], dict[str, "JSONValue"]]
JobStatus = Literal["queued", "running", "cancelling", "completed", "completed_with_errors", "failed", "cancelled"]
JobPhase = Literal["planning", "generating"]
ItemStatus = Literal["pending", "running", "success", "failed", "skipped", "cancelled"]
JobEventType = Literal[
    "job.created", "job.started", "job.planned", "job.cancel_requested", "job.retry_requested",
    "job.recovered", "job.completed", "job.completed_with_errors", "job.failed", "job.cancelled",
]


class RawJobCounts(TypedDict, total=False):
    total: int | None
    planned: int | None
    unplanned: int | None
    files: int | None
    documents: int | None
    pending: int | None
    running: int | None
    success: int | None
    failed: int | None
    skipped: int | None
    cancelled: int | None


class JobCounts(TypedDict):
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


class JobOptions(TypedDict, total=False):
    formats: list[str] | None
    error_policy: str | None
    naming: str | None
    keep_fields: bool | None
    layout: str | None


class JobTemplate(TypedDict):
    id: str | None
    name: str | None
    format: str | None
    version_no: int | None


class _Job(TypedDict):
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


class Job(_Job, total=False):
    # Present only on replayed create/retry responses, not an unconditional false/null.
    idempotent_replay: bool


class JobCancel(TypedDict):
    accepted: bool
    reason: Literal["already_terminal", "already_requested"] | None


class CancelledJob(Job):
    cancel: JobCancel


class JobListItem(TypedDict):
    id: str
    status: JobStatus
    created_at: float | None
    started_at: float | None
    finished_at: float | None
    counts: RawJobCounts | None
    options: JobOptions | None
    run_no: int | None
    phase: JobPhase
    halt_reason: str | None
    error_code: str | None
    principal: str | None


class JobRun(TypedDict):
    run_no: int
    trigger: str
    requested_by: str | None
    # A large batch has no item count until planning finishes.
    items: int | None
    created_at: float | None
    started_at: float | None
    finished_at: float | None
    status: str | None
    requested_by_principal: str | None


class ItemArtifact(TypedDict):
    id: str
    item_id: str
    format: str | None
    filename: str | None
    sha256: str | None
    size: int | None
    verification: JSONValue
    run_no: int | None


class JobItem(TypedDict):
    id: str
    seq: int | None
    record_key: str | None
    label: str | None
    status: ItemStatus
    issues: JSONValue
    attempts: int | None
    file_base: str | None
    started_at: float | None
    finished_at: float | None
    run_no: int | None
    artifacts: list[ItemArtifact]


class Artifact(TypedDict):
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


class JobEvent(TypedDict):
    seq: int
    id: str
    type: str
    version: int
    job_id: str
    occurred_at: float
    data: JSONValue


class JobEventsPage(TypedDict):
    items: list[JobEvent]
    next_after: int


class _JobPage(TypedDict):
    total: int
    # Cursor pages report null here; offset pages report the requested integer.
    offset: int | None
    limit: int
    next_offset: int | None
    next_cursor: str | None


class JobsPage(_JobPage):
    items: list[JobListItem]


class JobItemsPage(_JobPage):
    items: list[JobItem]


class JobRunsPage(_JobPage):
    items: list[JobRun]


class ArtifactsPage(_JobPage):
    items: list[Artifact]
