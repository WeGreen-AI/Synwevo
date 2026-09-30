"""Published job types and unchanged dictionary transport; synthetic HTTP fixtures only."""
import unittest
from typing import Iterator, get_type_hints

from synwevo_docs_sdk import (
    ApiKey, Artifact, ArtifactsPage, CancelledJob, Client, Job, JobCounts, JobEvent,
    JobEventsPage, JobItem, JobItemsPage, JobListItem, JobOptions, JobRun, JobsPage,
    RawJobCounts,
)
from test_http import fixture


class JobTypeTests(unittest.TestCase):
    def test_public_return_annotations_and_required_wire_fields(self):
        expected = {
            "create_job": Job, "get_job": Job, "wait_for_job": Job, "retry_job": Job,
            "cancel_job": CancelledJob, "list_jobs": JobsPage, "iter_jobs": Iterator[JobListItem],
            "list_job_items": JobItemsPage, "iter_job_items": Iterator[JobItem],
            "iter_job_runs": Iterator[JobRun], "job_events": JobEventsPage,
            "iter_job_events": Iterator[JobEvent], "list_artifacts": ArtifactsPage,
            "iter_artifacts": Iterator[Artifact],
        }
        for name, shape in expected.items():
            with self.subTest(method=name):
                self.assertEqual(get_type_hints(getattr(Client, name))["return"], shape)
        self.assertEqual(RawJobCounts.__required_keys__, frozenset())
        self.assertEqual(JobOptions.__required_keys__, frozenset())
        self.assertEqual(JobCounts.__required_keys__, {
            "samples", "documents", "files", "failed", "skipped", "cancelled",
            "pending", "running", "planned", "unplanned",
        })
        self.assertEqual(Job.__optional_keys__, {"idempotent_replay"})
        self.assertIn("cancel", CancelledJob.__required_keys__)
        self.assertNotIn("cancel", Job.__required_keys__)
        self.assertEqual(get_type_hints(JobRun)["items"], int | None)
        self.assertEqual(get_type_hints(JobsPage)["offset"], int | None)

    def test_types_do_not_fill_sparse_responses_or_replace_dict_metadata(self):
        payload = {"items": [{"counts": {}, "options": {}, "created_at": None}], "total": 1,
                   "limit": 1, "offset": None, "next_offset": None, "next_cursor": None}

        def respond(h):
            h.answer(200, payload, {"X-Request-ID": "synthetic-job-contract"})

        with fixture(respond) as base, Client(base, ApiKey("fixture")) as client:
            page = client.list_jobs("synthetic-project", cursor="synthetic-cursor")
            self.assertIsInstance(page, dict)
            self.assertEqual(page, payload)
            self.assertEqual(getattr(page, "request_id"), "synthetic-job-contract")
            self.assertEqual(getattr(page, "status_code"), 200)


if __name__ == "__main__":
    unittest.main()
