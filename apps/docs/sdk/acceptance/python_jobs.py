"""Installed-SDK job acceptance worker; all documents and credentials are synthetic.

The parent controls a temporary storage outage between submission and execution. This
worker only calls public HTTP endpoints and communicates test checkpoints using files.
"""
import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from synwevo_docs_sdk import Client, JobCounts, Upload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True, type=Path)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir()
    evidence = {}

    def record(name, value):
        evidence[name] = value
        (args.out / "responses.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        return value

    def checkpoint(name, value):
        # Atomic rename: the coordinating process must never read half a JSON response.
        tmp = args.control / (name + ".part")
        tmp.write_text(json.dumps(value))
        tmp.replace(args.control / (name + ".json"))

    def detail(value):
        assert isinstance(value, dict) and getattr(value, "request_id", None)
        assert set(value["counts"]) == JobCounts.__required_keys__
        assert all(type(n) is int for n in value["counts"].values())
        return value

    with Client.from_env(chunk_size=2048) as client:
        assert client.health()["instance"] == "test"
        source_path = args.kit / "customers.xlsx"
        abandoned = record("upload_open", client.create_upload("SYNTHETIC cancelled.xlsx", source_path.stat().st_size))
        assert set(abandoned) == Upload.__required_keys__
        assert abandoned["sha256"] is None and abandoned["purpose"] is None and not abandoned["complete"]
        part = record("upload_partial", client.put_upload_chunk(abandoned["id"], source_path, 0, 100, source_path.stat().st_size))
        assert part["received"] == 100 and part["status"] == "open"
        assert client.get_upload(abandoned["id"]) == part
        aborted = record("upload_aborted", client.abort_upload(abandoned["id"]))
        assert aborted["status"] == "aborted" and not aborted["complete"]
        assert client.get_upload(abandoned["id"]) == aborted
        project = client.create_project("SYNTHETIC failed-only SDK contract")
        source = client.upload_source(project["id"], source_path, resumable=True)
        client.wait_for_file(source["id"], timeout=60)
        template = client.upload_template(args.kit / "template.docx", "SYNTHETIC recoverable template", resumable=True)
        client.create_template_fields(template["id"], json.loads((args.kit / "fields.json").read_text()))
        client.set_project_template(project["id"], template["id"])
        client.put_definition(project["id"], json.loads((args.kit / "project.json").read_text()))
        job = detail(record("create", client.create_job(project["id"], formats=["docx"], error_policy="skip",
                                                       idempotency_key="synthetic-failure")))
        assert "idempotent_replay" not in job and "cancel" not in job
        replay = detail(record("create_replay", client.create_job(project["id"], formats=["docx"], error_policy="skip",
                                                                  idempotency_key="synthetic-failure")))
        assert replay["id"] == job["id"] and replay["idempotent_replay"] is True
        assert getattr(replay, "replayed") is True
        other = client.create_job(project["id"], formats=["docx"], error_policy="skip")
        cancel = detail(record("cancel", client.cancel_job(other["id"])))
        assert cancel["cancel"] == {"accepted": True, "reason": None}
        assert "cancel" not in client.get_job(other["id"])
        page = record("list_offset", client.list_jobs(project["id"], limit=1))
        assert page["offset"] == 0 and page["next_offset"] == 1 and page["next_cursor"]
        cursor = record("list_cursor", client.list_jobs(project["id"], limit=1, cursor=page["next_cursor"]))
        assert cursor["offset"] is None and cursor["next_offset"] is None and cursor["next_cursor"] is None
        assert "samples" not in page["items"][0]["counts"]
        assert {j["id"] for j in client.iter_jobs(project["id"], page_size=1)} == {job["id"], other["id"]}
        checkpoint("created", {"job_id": job["id"]})
        failed = detail(record("failed", client.wait_for_job(job["id"], timeout=90, poll_interval=.05)))
        before = record("failed_items", list(client.iter_job_items(job["id"], page_size=2)))
        failed_items = [i for i in before if i["status"] == "failed"]
        skipped_items = [i for i in before if i["status"] == "skipped"]
        assert len(failed_items) == 11 and len(skipped_items) == 1
        assert failed["status"] == "failed"
        assert failed["counts"]["failed"] == len(failed_items) and failed["counts"]["files"] == 0
        assert all(i["issues"] and all("trace" not in issue for issue in i["issues"]) for i in before)
        checkpoint("failed", {"job_id": job["id"], "failed_items": len(failed_items)})
        deadline = time.monotonic() + 30
        while not (args.control / "restored.json").exists():
            if time.monotonic() > deadline:
                raise AssertionError("parent did not restore the synthetic blob")
            time.sleep(.02)
        retry = detail(record("retry", client.retry_job(job["id"], failed_only=True, idempotency_key="recover-failed")))
        retry_replay = detail(record("retry_replay", client.retry_job(job["id"], failed_only=True,
                                                                     idempotency_key="recover-failed")))
        assert retry["id"] == job["id"] and retry["run_no"] == 2
        assert "idempotent_replay" not in retry and retry_replay["idempotent_replay"] is True
        done = detail(record("done", client.wait_for_job(job["id"], timeout=90, poll_interval=.05)))
        assert done["status"] == "completed_with_errors" and done["counts"]["documents"] == len(failed_items)
        after = record("successful_items", list(client.iter_job_items(job["id"], page_size=2)))
        assert [i["id"] for i in after] == [i["id"] for i in before]
        for old, new in zip(before, after):
            if old["status"] == "failed":
                assert new["status"] == "success" and new["attempts"] == old["attempts"] + 1 and new["run_no"] == 2
            else:
                assert new == old, "failed_only must leave the skipped sample untouched"
        items_page = client.list_job_items(job["id"], limit=1)
        next_items = client.list_job_items(job["id"], limit=1, cursor=items_page["next_cursor"])
        assert next_items["offset"] is None
        runs = record("runs", list(client.iter_job_runs(job["id"], page_size=1)))
        assert [r["run_no"] for r in runs] == [1, 2] and runs[1]["items"] == len(failed_items)
        assert [r["status"] for r in runs] == ["failed", "completed_with_errors"]
        events = record("events", client.job_events(job["id"], limit=1))
        all_events = record("all_events", list(client.iter_job_events(job["id"])))
        assert events["next_after"] == events["items"][0]["seq"]
        assert "job.retry_requested" in {e["type"] for e in all_events}
        artifacts = record("artifacts", list(client.iter_artifacts(job["id"], page_size=2)))
        assert len(artifacts) == len(failed_items)
        assert "verification" in after[0]["artifacts"][0] and "created_at" not in after[0]["artifacts"][0]
        assert "created_at" in artifacts[0] and "verification" not in artifacts[0]
        manifest = client.list_artifacts(job["id"], limit=1)
        assert client.list_artifacts(job["id"], limit=1, cursor=manifest["next_cursor"])["offset"] is None
        expected = json.loads((args.kit / "expected.json").read_text())
        downloads = []
        for artifact in artifacts:
            saved = client.download_artifact(artifact, args.out / artifact["filename"])
            assert hashlib.sha256(Path(saved["path"]).read_bytes()).hexdigest() == artifact["sha256"]
            with zipfile.ZipFile(saved["path"]) as archive:
                text = "".join(ElementTree.fromstring(archive.read("word/document.xml")).itertext())
            assert any(all(v in text for v in item["must_contain"]) for item in expected["items"])
            assert all(v not in text for v in expected["forbidden_text"])
            downloads.append(saved)
        record("downloads", downloads)
        (args.out / "acceptance.json").write_text(json.dumps({"passed": True, "operations": 9, "upload_operations": 5,
            "failed_items": len(failed_items), "recovered_artifacts": len(downloads), "same_snapshot": True,
            "retry_selection": "failed_only", "skipped_items_unchanged": len(skipped_items), "run_numbers": [1, 2]}, indent=2))


if __name__ == "__main__":
    main()
