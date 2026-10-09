"""Compile-only external consumer examples (not executed).

Run mypy --strict --follow-imports=silent --python-executable <installed-sdk-python>
apps/docs/sdk/python/tests/types_check.py. Unused ignores fail, so misuse must stay rejected.
"""
from typing import Iterator
from typing_extensions import assert_type

from synwevo_docs_sdk import (
    Artifact, ArtifactsPage, CancelledJob, Client, Job, JobCounts, JobEvent, JobEventsPage,
    JobItem, JobItemsPage, JobListItem, JobOptions, JobRun, JobsPage, RawJobCounts, Upload,
    ScanVerdict, SourceFile, SourceStatus,
)


def source_scan(client: Client, source: SourceFile) -> None:
    assert_type(client.retry_source_scan(source["id"], version=source["version"]), SourceFile)
    statuses: list[SourceStatus] = ["scanning", "scan_failed", "rejected"]
    legacy: ScanVerdict = {"status": "unscanned"}
    exhausted: ScanVerdict = {"status": "error", "attempts": 12, "cycle_attempts": 6, "retry_exhausted": True}
    source["scan"] = exhausted
    assert_type(source["scan"]["cycle_attempts"], int)
    assert_type(source["scan"]["retry_exhausted"], bool)
    invalid: ScanVerdict = {"status": "error", "retry_exhausted": "true"}  # type: ignore[typeddict-item]
    missing: ScanVerdict = {}  # type: ignore[typeddict-item]
    client.retry_source_scan(source["id"], version="1")  # type: ignore[arg-type]
    client.retry_source_scan(source["id"])  # type: ignore[call-arg]
    print(statuses, legacy, invalid, missing)


def lifecycle(client: Client, project_id: str) -> None:
    job = client.create_job(project_id, formats=["docx"], error_policy="skip")
    assert_type(job, Job)
    assert_type(client.get_job(job["id"]), Job)
    assert_type(client.list_jobs(project_id), JobsPage)
    assert_type(client.iter_jobs(project_id), Iterator[JobListItem])
    assert_type(client.list_job_items(job["id"]), JobItemsPage)
    assert_type(client.iter_job_items(job["id"]), Iterator[JobItem])
    assert_type(client.iter_job_runs(job["id"]), Iterator[JobRun])
    assert_type(client.job_events(job["id"]), JobEventsPage)
    assert_type(client.iter_job_events(job["id"]), Iterator[JobEvent])
    assert_type(client.cancel_job(job["id"]), CancelledJob)
    assert_type(client.retry_job(job["id"], failed_only=True), Job)
    events: list[JobEvent] = []
    assert_type(client.wait_for_job(job["id"], on_event=events.append), Job)
    assert_type(client.list_artifacts(job["id"]), ArtifactsPage)
    assert_type(client.iter_artifacts(job["id"]), Iterator[Artifact])
    for artifact in client.iter_artifacts(job["id"]):
        client.download_artifact(artifact, "synthetic-output.docx")
    for item in client.iter_job_items(job["id"]):
        for nested_artifact in item["artifacts"]:
            client.download_artifact(nested_artifact, "synthetic-output.docx")


def boundaries(job: Job, run: JobRun, item: JobItem, page: JobsPage) -> None:
    empty_counts: RawJobCounts = {}
    old_options: JobOptions = {"formats": ["docx"]}
    page["offset"] = None
    page["next_offset"] = None
    page["next_cursor"] = None
    run["items"] = None
    job["options"] = old_options
    job["counts"]["documents"] = None
    job["template"] = {"id": None, "name": None, "format": None, "version_no": None}
    item["issues"] = [{"code": "render_failed", "context": [None, 1, "legacy"]}, "legacy"]
    missing_counts: JobCounts = {}  # type: ignore[typeddict-item]
    definite_items: int = run["items"]  # type: ignore[assignment]
    item["issues"] = lambda: "not JSON"  # type: ignore[typeddict-item]
    print(empty_counts, missing_counts, definite_items)


def upload_session(client: Client, path: str) -> None:
    upload = client.create_upload("synthetic.xlsx", 100)
    assert_type(upload, Upload)
    assert_type(client.get_upload(upload["id"]), Upload)
    assert_type(client.put_upload_chunk(upload["id"], path, 0, 100, 100), Upload)
    assert_type(client.complete_upload(upload["id"]), Upload)
    assert_type(client.abort_upload(upload["id"]), Upload)
    assert_type(client.resumable_upload(path), Upload)
    upload["purpose"] = None
    upload["sha256"] = None
    upload["status"] = "consumed"
    upload["status"] = "ready"  # type: ignore[typeddict-item]
    digest: str = upload["sha256"]  # type: ignore[assignment]
    print(digest)
