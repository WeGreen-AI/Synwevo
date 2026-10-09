"""Synthetic loopback HTTP fixtures for scan contracts; no scanner or user data."""
import json
import socket
import unittest
from typing import get_args, get_type_hints

from synwevo_docs_sdk import (
    APIError, ApiKey, Client, ScanVerdict, SourceFile, SourceStatus, TransportError,
    ValidationError, WaitTimeoutError,
)
from test_http import fixture


def source(status="scan_failed", scan=None, **extra):
    return {"id": "source_fixture", "project_id": "project_fixture", "filename": "fixture.csv",
            "ord": 0, "size": 12, "format": "csv", "family": "sheet", "status": status,
            "error": "Synthetic scan failure", "notes": ["fixture note"], "structure": {},
            "version": 7, "representative": False, "created_at": 1.0, "updated_at": 2.0,
            "options": {}, "ocr_pages": [], "corrections": {}, "sha256": "a" * 64,
            "scan": scan if scan is not None else {
                "status": "error", "attempts": 12, "cycle_attempts": 6, "retry_exhausted": True},
            "future_detail": {"preserve": ["fixture", 1]}, **extra}


class RefreshableFixtureKey(ApiKey):
    invalidations = 0

    def invalidate(self):
        self.invalidations += 1
        return True


class SourceScanTests(unittest.TestCase):
    def test_retry_encodes_version_and_returns_full_202_source(self):
        calls = []
        payload = source("scanning", {"status": "pending", "attempts": 12, "cycle_attempts": 0,
                                      "retry_exhausted": False}, version=8, error=None)

        def respond(h):
            calls.append((h.command, h.path, json.loads(h.body), h.headers.get("Idempotency-Key")))
            h.answer(202, payload, {"X-Request-ID": "scan-fixture-request"})

        with fixture(respond) as base, Client(base, ApiKey("fixture")) as client:
            result = client.retry_source_scan("source /fixture", version=7)
        self.assertEqual(calls, [("POST", "/api/v1/files/source%20%2Ffixture/scan/retry", {"version": 7}, None)])
        self.assertEqual(result, payload)
        self.assertEqual(result.status_code, 202)
        self.assertEqual(result.request_id, "scan-fixture-request")

    def test_retry_rejects_invalid_versions_before_sending(self):
        calls = []

        def respond(h):
            calls.append(h.path)
            h.answer(202, source())

        with fixture(respond) as base, Client(base, ApiKey("fixture")) as client:
            for version in (None, True, False, 0, -1, 1.5, "7", [], {}):
                with self.subTest(version=version), self.assertRaises(ValidationError) as caught:
                    client.retry_source_scan("fixture", version=version)
                self.assertEqual(caught.exception.field, "version")
        self.assertEqual(calls, [])

    def test_retry_never_repeats_http_errors_or_refreshes_after_401(self):
        for status, code in ((429, "rate_limited"), (401, "unauthorized"), (503, "unavailable"),
                             (409, "source_changed"), (409, "scan_not_retryable")):
            with self.subTest(status=status, code=code):
                calls, sleeps = [], []
                key = RefreshableFixtureKey("fixture")
                details = {"source": source()}

                def respond(h):
                    calls.append((h.command, h.path, json.loads(h.body)))
                    h.answer(status, {"error": {"code": code, "message": "Synthetic fault", "details": details}},
                             {"Retry-After": "0", "X-Request-ID": "scan-fixture-fault"})

                with fixture(respond) as base, Client(base, key, max_retries=3, sleep=sleeps.append) as client:
                    with self.assertRaises(APIError) as caught:
                        client.retry_source_scan("fixture", version=7)
                self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
                self.assertEqual(caught.exception.details, details)
                self.assertEqual(caught.exception.request_id, "scan-fixture-fault")
                self.assertEqual(calls, [("POST", "/api/v1/files/fixture/scan/retry", {"version": 7})])
                self.assertEqual(sleeps, [])
                self.assertEqual(key.invalidations, 0)

    def test_retry_never_repeats_a_lost_response(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                calls, sleeps = [], []

                def respond(h):
                    calls.append(h.command)
                    if partial:
                        h.send_response(202)
                        h.send_header("Content-Length", "1000")
                        h.end_headers()
                        h.wfile.write(b'{"id":')
                        h.wfile.flush()
                    h.connection.shutdown(socket.SHUT_RDWR)
                    h.connection.close()

                with fixture(respond) as base, Client(base, ApiKey("fixture"), max_retries=3, sleep=sleeps.append) as client:
                    with self.assertRaises(TransportError):
                        client.retry_source_scan("fixture", version=7)
                self.assertEqual(calls, ["POST"])
                self.assertEqual(sleeps, [])

    def test_terminal_wait_fails_on_first_read_with_full_source_details(self):
        for scan, code in ((source()["scan"], "scan_retry_exhausted"),
                           ({"status": "too_large"}, "scan_too_large"),
                           ({"status": "too_large", "retry_exhausted": True}, "scan_too_large")):
            with self.subTest(scan=scan):
                calls, sleeps = [], []
                payload = source(scan=scan)

                def respond(h):
                    calls.append((h.command, h.path))
                    h.answer(200, payload if len(calls) == 1 else source("ready"))

                with fixture(respond) as base, Client(base, ApiKey("fixture"), sleep=sleeps.append) as client:
                    with self.assertRaises(APIError) as caught:
                        client.wait_for_file("fixture", timeout=60)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.details, payload)
                self.assertEqual(caught.exception.to_dict()["details"], payload)
                self.assertIn(payload["error"], caught.exception.message)
                self.assertEqual(calls, [("GET", "/api/v1/files/fixture")])
                self.assertEqual(sleeps, [])

    def test_other_pending_states_keep_waiting_without_starting_a_cycle(self):
        states = [source("scanning", {"status": "pending"}),
                  source(scan={"status": "error", "retry_exhausted": False}),
                  source(scan={"status": "error", "attempts": 99}),
                  source(scan={"status": "not_configured"}),
                  source(scan={"status": "error", "retry_exhausted": 1}),
                  source(scan={"status": "error", "retry_exhausted": "true"}),
                  source(scan={}), source("parsing", {"status": "clean"}),
                  source("ready", {"status": "clean"}, error=None)]
        calls, sleeps = [], []

        def respond(h):
            calls.append((h.command, h.path))
            h.answer(200, states[min(len(calls) - 1, len(states) - 1)])

        with fixture(respond) as base, Client(base, ApiKey("fixture"), sleep=sleeps.append) as client:
            result = client.wait_for_file("fixture", timeout=60)
        self.assertEqual(result, states[-1])
        self.assertEqual(calls, [("GET", "/api/v1/files/fixture")] * len(states))
        self.assertEqual(len(sleeps), len(states) - 1)

    def test_pending_wait_still_times_out_with_last_source(self):
        elapsed = [0.0]
        payload = source(scan={"status": "error", "retry_exhausted": False})

        def sleep(delay):
            elapsed[0] += delay

        with fixture(lambda h: h.answer(200, payload)) as base, Client(
                base, ApiKey("fixture"), sleep=sleep, clock=lambda: elapsed[0]) as client:
            with self.assertRaises(WaitTimeoutError) as caught:
                client.wait_for_file("fixture", timeout=1)
        self.assertEqual(caught.exception.job, payload)

    def test_source_types_include_scan_states_and_optional_cycle_fields(self):
        self.assertTrue({"scanning", "scan_failed", "rejected"}.issubset(get_args(SourceStatus)))
        self.assertIs(get_type_hints(SourceFile)["scan"], ScanVerdict)
        self.assertEqual(ScanVerdict.__required_keys__, {"status"})
        self.assertTrue({"cycle_attempts", "retry_exhausted"}.issubset(ScanVerdict.__optional_keys__))
        self.assertIs(get_type_hints(ScanVerdict)["cycle_attempts"], int)
        self.assertIs(get_type_hints(ScanVerdict)["retry_exhausted"], bool)


if __name__ == "__main__":
    unittest.main()
