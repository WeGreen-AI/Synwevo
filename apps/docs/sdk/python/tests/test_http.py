"""Real loopback HTTP fault fixture; not the Synwevo service or business acceptance.

Deliberately scripted responses exercise transport semantics without external user data.
"""
import hashlib
import json
import os
import socket
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from synwevo_docs_sdk import ApiKey, Client, ClientCredentials, IntegrityError, RateLimitError, TransportError, WaitTimeoutError
from synwevo_docs_sdk.errors import APIError


@contextmanager
def fixture(respond):
    old_bypass = os.environ.get("NO_PROXY")
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            respond(self)

        do_POST = do_PUT = do_PATCH = do_DELETE = do_GET

        def answer(self, status, value, headers=None):
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, str(v))
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the timeout test deliberately closes its client first

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        if old_bypass is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = old_bypass


class HTTPTests(unittest.TestCase):
    def test_builtin_keyed_revision_retry_and_validation(self):
        from synwevo_docs_sdk import ValidationError
        requests = []
        editor = {"body": {"type": "doc", "content": []}}
        def respond(h):
            requests.append((h.command, h.path, h.headers.get("Idempotency-Key"),
                             json.loads(h.body) if h.body else None))
            h.answer(503 if len(requests) in (1, 3) else 200, {"id": "t", "head": 2}, {"Retry-After": "0"})
        with fixture(respond) as base, Client(base, ApiKey("fixture"), sleep=lambda _: None) as c:
            c.create_builtin_template("builtin_doc", editor, idempotency_key="create")
            c.save_builtin_template("t", editor, base_version=1, name="Synthetic", idempotency_key="save")
            c.get_builtin_template("t", version=1)
            for version in (None, True, 1.5, "1", 0, -1):
                with self.assertRaises(ValidationError):
                    c.save_builtin_template("t", editor, base_version=version)
            for kind, model, name in (("unknown", editor, None), ("builtin_doc", [], None),
                                       ("builtin_sheet", {}, " "), ("builtin_doc", {}, "x" * 101)):
                with self.assertRaises(ValidationError):
                    c.create_builtin_template(kind, model, name)
            with self.assertRaises(ValidationError):
                c.get_builtin_template("t", version=True)
        self.assertEqual(len(requests), 5)
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(requests[2], requests[3])
        self.assertEqual(requests[0], ("POST", "/api/v1/templates/builtin", "create",
                                       {"kind": "builtin_doc", "editor": editor}))
        self.assertEqual(requests[2], ("PUT", "/api/v1/templates/t/builtin", "save",
                                       {"editor": editor, "base_version": 1, "name": "Synthetic"}))
        self.assertEqual(requests[4], ("GET", "/api/v1/templates/t/builtin?version=1", None, None))

    def test_saving_a_builtin_template_can_say_to_discard_live_edits(self):
        """CI-113: a save that would replace live edits nobody saved is refused unless told."""
        requests = []
        def respond(h):
            requests.append(json.loads(h.body))
            h.answer(200, {"id": "t", "head": 2})
        editor = {"body": {"type": "doc", "content": []}}
        with fixture(respond) as base, Client(base, ApiKey("fixture"), sleep=lambda _: None) as c:
            c.save_builtin_template("t", editor, base_version=1)
            c.save_builtin_template("t", editor, base_version=1, discard_live=True)
        self.assertNotIn("discard_live", requests[0])
        self.assertIs(requests[1]["discard_live"], True)

    def test_retry_reuses_key_and_rewinds_multipart(self):
        requests, delays = [], []
        def respond(h):
            requests.append((h.headers.get("Idempotency-Key"), h.body))
            h.answer(503 if len(requests) == 1 else 201, {"id": "t"}, {"Retry-After": "0"})
        with tempfile.TemporaryDirectory() as d, fixture(respond) as base:
            p = Path(d) / "input.docx"
            p.write_bytes(b"synthetic fixture bytes")
            with Client(base, ApiKey("fixture"), sleep=delays.append) as c:
                self.assertEqual(c.upload_template(p)["id"], "t")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0][0], requests[1][0])
        self.assertTrue(all(b"synthetic fixture bytes" in body for _, body in requests))
        self.assertEqual(delays, [0])

    def test_unsafe_write_is_never_retried(self):
        paths = []
        def respond(h):
            paths.append(h.path)
            h.answer(429, {"error": {"code": "limited", "message": "fixture"}}, {"Retry-After": "0"})
        with fixture(respond) as base, Client(base, ApiKey("fixture")) as c:
            with self.assertRaises(RateLimitError):
                c.create_webhook("https://example.invalid")
            with self.assertRaises(RateLimitError):
                c.put_mappings("p", [])
        self.assertEqual(len(paths), 2)

    def test_copy_requests_keep_one_key_across_retries_and_withdrawal_is_not_retried(self):
        seen = []
        def respond(h):
            seen.append((h.command, h.path, h.headers.get("Idempotency-Key"), h.body))
            if h.command == "POST" and h.path.endswith("/copy-requests") and len(seen) == 1:
                h.answer(503, {"error": {"code": "unavailable", "message": "fixture"}}, {"Retry-After": "0"})
            elif h.path.endswith("/withdraw"):
                h.answer(503, {"error": {"code": "unavailable", "message": "fixture"}}, {"Retry-After": "0"})
            else:
                h.answer(201 if h.command == "POST" else 200, {"id": "pc_1", "status": "pending"})
        with fixture(respond) as base, Client(base, ApiKey("fixture"), max_retries=2) as c:
            out = c.request_copy("p 1", "w_org", name="copy", sources=True)
            self.assertEqual(out["id"], "pc_1")
            with self.assertRaises(APIError):
                c.withdraw_copy_request("pc_1")
            c.copy_manifest("p1", sources=True)
            c.list_copy_requests("p1", status="withdrawn", limit=10)
        posts = [x for x in seen if x[1].endswith("/copy-requests")]
        self.assertEqual(len(posts), 2, "one retry of the request, with the same key")
        self.assertTrue(posts[0][2] and posts[0][2] == posts[1][2])
        self.assertEqual(json.loads(posts[0][3]), {"target_workspace_id": "w_org", "scope": {"sources": True, "corrections": False},
                                                   "name": "copy"})
        self.assertEqual(posts[0][1], "/api/v1/projects/p%201/copy-requests")
        self.assertEqual(len([x for x in seen if x[1].endswith("/withdraw")]), 1, "a withdrawal is never retried")
        self.assertEqual(seen[-2][1], "/api/v1/projects/p1/copy-manifest?sources=true&corrections=false")
        self.assertEqual(seen[-1][1], "/api/v1/projects/p1/copy-requests?limit=10&status=withdrawn")

    def test_the_gates_own_rate_limit_is_retried_even_for_unsafe_writes(self):
        """`429 rate_limited` is Synwevo's gate refusing before the route runs — nothing executed,
        so even a non-idempotent PUT is sent again after Retry-After. An acceptance flow of ~400
        calls in a minute tripped the 300/min limit on `definition put` and failed outright."""
        paths, delays = [], []
        def respond(h):
            paths.append(h.path)
            if len(paths) == 1:
                h.answer(429, {"error": {"code": "rate_limited", "message": "fixture", "details": {"retry_after": 0.05}}}, {"Retry-After": "0"})
            else:
                h.answer(200, {"rev": 2, "mappings": []})
        with fixture(respond) as base, Client(base, ApiKey("fixture"), sleep=delays.append) as c:
            self.assertEqual(c.put_mappings("p", [])["rev"], 2)
        self.assertEqual(len(paths), 2)
        self.assertEqual(delays, [0])
        # a limiter that never relents still ends in the error, after the bounded retries
        def always(h):
            h.answer(429, {"error": {"code": "rate_limited", "message": "fixture"}}, {"Retry-After": "0"})
        with fixture(always) as base, Client(base, ApiKey("fixture"), sleep=lambda _s: None, max_retries=2) as c:
            with self.assertRaises(RateLimitError):
                c.put_mappings("p", [])

    def test_cursor_and_offset_pagination(self):
        paths = []
        def respond(h):
            paths.append(h.path)
            if "cursor=" in h.path or "offset=" in h.path:
                h.answer(200, {"items": [{"id": 2}], "next_offset": None})
            elif "templates" in h.path:
                h.answer(200, {"items": [{"id": 1}], "next_offset": 1})
            else:
                h.answer(200, {"items": [{"id": 1}], "next_cursor": "opaque+cursor"})
        with fixture(respond) as base, Client(base, ApiKey("fixture")) as c:
            self.assertEqual(list(c.iter_projects(1)), [{"id": 1}, {"id": 2}])
            self.assertEqual(list(c.iter_templates(1)), [{"id": 1}, {"id": 2}])
        self.assertIn("cursor=opaque%2Bcursor", paths[1])
        self.assertIn("offset=1", paths[3])

    def test_resumable_recovers_server_offset_after_lost_reply(self):
        content, received, starts = b"synthetic-chunked-file", bytearray(), []
        def respond(h):
            if h.command == "POST" and h.path.endswith("/uploads"):
                h.answer(201, {"id": "u", "received": 0})
            elif h.command == "PUT":
                start = int(h.headers["Content-Range"].split()[1].split("-")[0])
                starts.append(start)
                received.extend(h.body)
                if len(starts) == 1:
                    h.connection.shutdown(socket.SHUT_RDWR)
                    h.connection.close()
                else:
                    h.answer(200, {"id": "u", "received": len(received)})
            else:
                h.answer(200, {"id": "u", "received": len(received), "status": "complete" if h.command == "POST" else "open"})
        with tempfile.TemporaryDirectory() as d, fixture(respond) as base:
            p = Path(d) / "source"
            p.write_bytes(content)
            with Client(base, ApiKey("fixture"), chunk_size=4, sleep=lambda _: None) as c:
                self.assertEqual(c.resumable_upload(p)["status"], "complete")
        self.assertEqual(bytes(received), content)
        self.assertEqual(starts, list(range(0, len(content), 4)))

    def test_download_integrity_redirect_and_atomic_destination(self):
        def respond(h):
            if "redirect" in h.path:
                h.answer(302, b"", {"Location": "/wrong"})
            else:
                h.answer(200, b"fixture", {"X-Content-SHA256": "0" * 64})
        with tempfile.TemporaryDirectory() as d, fixture(respond) as base, Client(base, ApiKey("fixture")) as c:
            p = Path(d) / "output"
            p.write_bytes(b"original")
            with self.assertRaises(IntegrityError):
                c.download_artifact("a", p)
            with self.assertRaises(APIError):
                c.download_artifact("redirect", p)
            self.assertEqual(p.read_bytes(), b"original")
            self.assertEqual(list(Path(d).glob("*.part")), [])

    def test_download_restarts_after_short_body(self):
        calls = []
        def respond(h):
            calls.append(h.path)
            if len(calls) == 1:
                h.send_response(200)
                h.send_header("Content-Length", "100")
                h.end_headers()
                h.wfile.write(b"short")
            else:
                h.answer(200, b"complete", {"X-Content-SHA256": hashlib.sha256(b"complete").hexdigest()})
        with tempfile.TemporaryDirectory() as d, fixture(respond) as base, Client(base, ApiKey("fixture"), sleep=lambda _: None) as c:
            p = Path(d) / "output"
            self.assertTrue(c.download_artifact("a", p)["verified"])
            self.assertEqual(p.read_bytes(), b"complete")
        self.assertEqual(len(calls), 2)

    def test_oauth_cache_expiry_and_401_refresh(self):
        tokens, requests, clock = [], [], [1000]
        def respond(h):
            if h.path == "/token":
                tokens.append(h.body)
                h.answer(200, {"access_token": f"synthetic-{len(tokens)}", "expires_in": 120, "token_type": "Bearer"})
            else:
                requests.append(h.headers["Authorization"])
                h.answer(401 if len(requests) == 2 else 200, {"tenant": "fixture"})
        with fixture(respond) as base:
            credentials = ClientCredentials(base + "/token", "synthetic", "secret", clock=lambda: clock[0])
            with Client(base, credentials) as c:
                c.whoami()
                c.whoami()  # rejected cached token is refreshed once
                c.whoami()  # still cached
                clock[0] += 61
                c.whoami()  # proactive expiry refresh
        self.assertEqual(len(tokens), 3)
        self.assertEqual(requests, ["Bearer synthetic-1", "Bearer synthetic-1", "Bearer synthetic-2", "Bearer synthetic-2", "Bearer synthetic-3"])

    def test_preview_post_stream_restart_and_metadata(self):
        requests = []
        content = b"synthetic preview attachment"
        def respond(h):
            requests.append((h.command, h.path, json.loads(h.body)))
            if len(requests) == 1:
                h.send_response(200)
                h.send_header("Content-Length", "100")
                h.end_headers()
                h.wfile.write(b"incomplete")
            else:
                h.answer(200, content, {"X-Content-SHA256": hashlib.sha256(content).hexdigest(),
                                       "X-Project-Signature": "snapshot", "X-Template-Version": "2",
                                       "X-Record-Key": "sample%2F%E5%90%88%E6%88%90"})
        with tempfile.TemporaryDirectory() as d, fixture(respond) as base, Client(base, ApiKey("fixture"), sleep=lambda _: None) as c:
            result = c.download_preview("p", Path(d) / "preview", index=0, signature="snapshot")
            self.assertEqual(Path(result["path"]).read_bytes(), content)
            self.assertEqual(result["record_key"], "sample/合成")
            self.assertEqual(result["template_version"], "2")
            self.assertEqual(result["project_signature"], "snapshot")
            self.assertEqual(result["verified"], ["X-Content-SHA256"])
        self.assertEqual(requests, [("POST", "/api/v1/projects/p/preview", {"index": 0, "signature": "snapshot"})] * 2)

    def test_retry_selection_and_preview_input_validation(self):
        from synwevo_docs_sdk import ValidationError
        requests = []
        def respond(h):
            requests.append(json.loads(h.body))
            h.answer(200, {"id": "j", "run_no": 2})
        with fixture(respond) as base, Client(base, ApiKey("fixture")) as c:
            for kw in ({}, {"failed_only": True}, {"include_skipped": True}):
                c.retry_job("j", **kw)
            for kw in ({"failed_only": True, "include_skipped": True}, {"failed_only": "false"}):
                with self.assertRaises(ValidationError):
                    c.retry_job("j", **kw)
            for kw in ({"index": -1}, {"index": True}, {"key": ""}, {"index": 0, "key": "k"}, {"signature": "x" * 65}):
                with self.assertRaises(ValidationError):
                    c.download_preview("p", "unused", **kw)
        self.assertEqual(requests, [{}, {"failed_only": True}, {"include_skipped": True}])

    def test_wait_timeout(self):
        def respond(h):
            h.answer(200, {"status": "running"}, {"Retry-After": "10"})
        with fixture(respond) as base, Client(base, ApiKey("fixture")) as c:
            start = time.monotonic()
            with self.assertRaises(WaitTimeoutError):
                c.wait_for_job("job", timeout=.05, poll_interval=.01)
            self.assertLess(time.monotonic() - start, 1)

    def test_http_timeout(self):
        def respond(h):
            time.sleep(.15)
            h.answer(200, {})
        with fixture(respond) as base, Client(base, ApiKey("fixture"), timeout=.02, max_retries=0) as c:
            with self.assertRaises(TransportError):
                c.whoami()

    def test_cli_exit_codes_and_interrupt_cancellation(self):
        polled, cancelled = threading.Event(), threading.Event()
        def respond(h):
            if h.path.endswith("/cancel"):
                cancelled.set()
                h.answer(200, {"status": "cancelling"})
            elif "/missing" in h.path:
                h.answer(404, {"error": {"code": "not_found", "message": "fixture"}})
            elif "/broken" in h.path:
                h.answer(503, {"error": {"code": "unavailable", "message": "fixture"}})
            else:
                if "/interrupt" in h.path:
                    polled.set()
                h.answer(200, {"status": "failed" if "/failed" in h.path else "running"})
        with fixture(respond) as base:
            env = {k: v for k, v in os.environ.items() if not k.startswith("SYNWEVO_")}
            env.update(SYNWEVO_BASE_URL=base, SYNWEVO_API_KEY="synthetic-fixture-key")
            cmd = [sys.executable, "-m", "synwevo_docs_sdk", "--max-retries", "0"]
            for args, code in ((["jobs", "get", "missing"], 3), (["jobs", "get", "broken"], 4),
                               (["jobs", "wait", "failed"], 5),
                               (["jobs", "wait", "running", "--wait-timeout", ".02"], 6),
                               (["jobs", "submit", "p", "--layout", "typo"], 2)):
                p = subprocess.run(cmd + args, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(p.returncode, code, p.stderr)
                self.assertIsInstance(json.loads(p.stdout or p.stderr), dict)
            p = subprocess.Popen(cmd + ["jobs", "wait", "interrupt", "--cancel-on-interrupt"],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(polled.wait(5), "CLI did not poll")
                p.send_signal(signal.SIGINT)
                stdout, stderr = p.communicate(timeout=5)
                self.assertEqual(p.returncode, 130, stderr)
                self.assertTrue(cancelled.is_set())
                self.assertEqual(json.loads(stderr)["error"]["code"], "interrupted")
            finally:
                if p.poll() is None:
                    p.kill()
                    p.communicate()


if __name__ == "__main__":
    unittest.main()
