"""Pure unit tests; all values and secrets below are synthetic fixtures."""
import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

from synwevo_docs_sdk import ApiKey, ValidationError, credentials_from_env, verify_webhook, verify_legacy_signature
from synwevo_docs_sdk._files import AtomicWriter, iter_slice, safe_filename, sha256_file
from synwevo_docs_sdk._http import Transport
from synwevo_docs_sdk.cli import parser
from synwevo_docs_sdk.errors import (
    BadRequestError, ConflictError, ServerError, WebhookVerificationError,
    error_from_response, parse_retry_after,
)


class UnitTests(unittest.TestCase):
    def test_errors(self):
        e = error_from_response(409, {"X-Request-ID": "fixture"}, json.dumps({"error": {
            "code": "stale", "message": "Conflict", "field": "base_rev", "details": {"rev": 2}}}).encode())
        self.assertIsInstance(e, ConflictError)
        self.assertEqual((e.code, e.field, e.request_id, e.details), ("stale", "base_rev", "fixture", {"rev": 2}))
        e = error_from_response(400, {"Content-Type": "application/problem+json"}, json.dumps({
            "type": "urn:synwevo:problem:invalid", "status": 400, "detail": "bad input",
            "errors": [{"pointer": "#/a~1b/c~0d"}]}).encode())
        self.assertIsInstance(e, BadRequestError)
        self.assertEqual(e.field, "a/b.c~d")
        self.assertIsInstance(error_from_response(502, {}, b"<html>proxy</html>"), ServerError)

    def test_retry_hints(self):
        self.assertEqual(parse_retry_after({"Retry-After": "2.5"}), 2.5)
        self.assertEqual(parse_retry_after({"Retry-After": "Thu, 01 Jan 1970 00:00:10 GMT"}, now=5), 5)
        self.assertEqual(parse_retry_after({"RateLimit": '"api";r=0;t=9'}), 9)
        self.assertIsNone(parse_retry_after({"RateLimit": '"api";r=2;t=9'}))
        # Several policies: the credential has budget left, the workspace total does not.
        self.assertEqual(parse_retry_after({"RateLimit": '"key";r=298;t=1, "workspace";r=0;t=20'}), 20)
        self.assertEqual(parse_retry_after({"RateLimit": '"key";r=0;t=3, "workspace";r=0;t=20'}), 20)
        self.assertIsNone(parse_retry_after({"RateLimit": '"key";r=5;t=1, "workspace";r=7;t=2'}))
        t = Transport("http://127.0.0.1", ApiKey("fixture"), user_agent="fixture", rand=lambda: .5)
        try:
            self.assertEqual(t.backoff(0), .25)
            self.assertEqual(t.backoff(10), 15)
        finally:
            t.close()

    def test_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "key"
            f.write_text("dw_fixture_private\n")
            a = credentials_from_env({"SYNWEVO_API_KEY_FILE": str(f)})
            self.assertIsInstance(a, ApiKey)
            self.assertNotIn("private", repr(a))
            oauth = credentials_from_env({"SYNWEVO_CLIENT_ID": "fixture", "SYNWEVO_CLIENT_SECRET_FILE": str(f),
                                          "SYNWEVO_TOKEN_URL": "http://127.0.0.1/token"})
            self.assertNotIn("private", repr(oauth))

    def test_standard_and_legacy_signatures(self):
        key, raw = b"synthetic-fixture-key", b'{"id":"event-1","type":"job.completed"}'
        # Independent protocol vector, not sign_webhook used to test itself.
        sig = base64.b64encode(hmac.new(key, b"event-1.1000." + raw, hashlib.sha256).digest()).decode()
        headers = {"Webhook-ID": "event-1", "Webhook-Timestamp": "1000", "Webhook-Signature": "v1,bad v2,bad v1," + sig}
        secret = "whsec_" + base64.b64encode(key).decode()
        self.assertEqual(verify_webhook(headers, raw, secret, now=1001)["id"], "event-1")
        for body, now in ((raw + b" ", 1001), (raw, 1301), (raw, 699)):
            with self.assertRaises(WebhookVerificationError):
                verify_webhook(headers, body, secret, now=now)
        signature = hmac.new(key, b"1000." + raw, hashlib.sha256).hexdigest()
        self.assertEqual(verify_legacy_signature({"X-DocWeave-Timestamp": "1000", "X-DocWeave-Signature": "v1=" + signature},
                                                 raw, key.decode(), now=1000)["id"], "event-1")

    def test_files_atomic_and_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "file"
            p.write_bytes(b"original")
            with AtomicWriter(p) as w:
                w.write(b"partial")
            self.assertEqual(p.read_bytes(), b"original")
            with AtomicWriter(p) as w:
                w.write(b"replacement")
                w.commit()
            self.assertEqual(sha256_file(p), hashlib.sha256(b"replacement").hexdigest())
            self.assertEqual(list(iter_slice(p, 1, 5, block=2)), [b"ep", b"la", b"c"])
            self.assertEqual(safe_filename("../../escape.docx", "fallback"), "escape.docx")
            self.assertEqual(list(Path(d).glob("*.part")), [])

    def test_cli_parser(self):
        p = parser()
        a = p.parse_args(["--timeout", "7", "templates", "fields", "put", "t", "-", "--json"])
        self.assertEqual((a.timeout, a.template_id, a.input, a.method), (7, "t", "-", "create_template_fields"))
        self.assertEqual(p.parse_args(["preflight", "p", "--simulate"]).simulate, True)
        with self.assertRaises(ValidationError):
            p.parse_args(["jobs", "submit", "p", "--layout", "typo"])
        a = p.parse_args(["jobs", "retry", "j", "--failed-only"])
        self.assertTrue(a.failed_only)
        a = p.parse_args(["preview", "p", "preview.docx", "--key", "sample-1", "--signature", "snapshot"])
        self.assertEqual((a.method, a.key, a.signature), ("download_preview", "sample-1", "snapshot"))
        with self.assertRaises(ValidationError):
            p.parse_args(["preview", "p", "preview.docx", "--key", "sample-1", "--index", "0"])
        a = p.parse_args(["templates", "builtin", "create", "builtin_sheet", "-"])
        self.assertEqual((a.method, a.kind, a.input), ("create_builtin_template", "builtin_sheet", "-"))
        a = p.parse_args(["templates", "builtin", "save", "t", "model.json", "--base-version", "2"])
        self.assertEqual((a.method, a.base_version), ("save_builtin_template", 2))
        a = p.parse_args(["templates", "builtin", "export", "t", "--version", "1"])
        self.assertEqual((a.method, a.version), ("get_builtin_template", 1))
        with self.assertRaises(ValidationError):
            p.parse_args(["templates", "builtin", "save", "t", "model.json"])


if __name__ == "__main__":
    unittest.main()



def test_a_verifier_refuses_empty_or_short_key_material():
    """CR-049 WH-4: an unset variable or "whsec_" alone used to verify with an empty HMAC key, so a
    signature anyone can make was accepted."""
    import time as _time
    from synwevo_docs_sdk.webhooks import verify_webhook, WebhookVerificationError
    body, ts = b'{"type":"fixture"}', str(int(_time.time()))
    forged = "v1," + base64.b64encode(hmac.new(b"", f"msg_1.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    headers = {"webhook-id": "msg_1", "webhook-timestamp": ts, "webhook-signature": forged}
    for weak in ("", "whsec_", b"", ["whsec_"], "short", "whsec_" + base64.b64encode(b"x" * 15).decode()):
        try:
            verify_webhook(headers, body, weak)
        except WebhookVerificationError:
            continue
        raise AssertionError(f"accepted weak secret {weak!r}")
