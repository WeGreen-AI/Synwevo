"""Workspace SDK transport-fault fixtures, separate from real directory acceptance."""
import json
import unittest

from synwevo_docs_sdk import ApiKey, WorkspaceClient, ValidationError
from synwevo_docs_sdk.errors import APIError
from test_http import fixture


class WorkspaceTests(unittest.TestCase):
    def test_directory_environment_does_not_inherit_docs_defaults(self):
        env = {"SYNWEVO_BASE_URL": "https://docs.invalid", "SYNWEVO_API_KEY": "docs-only-fixture",
               "SYNWEVO_WORKSPACE_CLIENT_ID": "directory-fixture", "SYNWEVO_WORKSPACE_CLIENT_SECRET": "synthetic-secret"}
        with WorkspaceClient.from_env(env) as client:
            self.assertEqual(client.base_url, "https://accounts.synwevo.com")
            credentials = client._client._t.credentials
            self.assertEqual(credentials.resource, "urn:synwevo:platform:workspace")
            self.assertEqual(credentials.scope, "workspace.read")
            self.assertFalse(hasattr(client, "create_project"))
        with self.assertRaises(ValidationError):
            WorkspaceClient.from_env({"SYNWEVO_CLIENT_ID": "docs-only-fixture"})

    def test_only_invitation_create_and_resend_retry_with_the_same_key(self):
        calls = []
        def respond(h):
            calls.append((h.command, h.path, h.headers.get("Idempotency-Key"), h.body))
            same = sum(c[:2] == calls[-1][:2] for c in calls)
            retryable = h.path == "/api/workspace/v1/invitations" or h.path.endswith("/resend")
            status = 200 if retryable and same == 2 else 503
            h.answer(status, {"id": "inv_fixture"} if status == 200 else {"error": {"code": "unavailable", "message": "synthetic fixture"}},
                     {"Retry-After": "0"})
        with fixture(respond) as base, WorkspaceClient(base, ApiKey("synthetic-token"), sleep=lambda _: None) as c:
            c.create_invitation("new@example.invalid", "viewer")
            c.resend_invitation("inv 1")
            for action in (lambda: c.revoke_invitation("inv 1"),
                           lambda: c.change_member("user 1", "editor", expected_role="viewer"),
                           lambda: c.remove_member("user 1", expected_role="viewer", reason="Synthetic removal")):
                with self.assertRaises(APIError):
                    action()
            with self.assertRaises(TypeError):
                c.change_member("user 1", "viewer")
            with self.assertRaises(ValidationError):
                c.change_member("user 1", "viewer", expected_role=None)
        self.assertEqual(len(calls), 7)
        for first, retry in ((calls[0], calls[1]), (calls[2], calls[3])):
            self.assertTrue(first[2])
            self.assertEqual(first, retry)
        self.assertEqual(calls[2][1], "/api/workspace/v1/invitations/inv%201/resend")
        self.assertEqual(json.loads(calls[5][3]), {"role": "editor", "expected_role": "viewer"})
        self.assertTrue(all(c[2] is None for c in calls[4:]))

    def test_directory_members_follow_offset_pages(self):
        paths = []
        def respond(h):
            paths.append(h.path)
            end = "offset=1" in h.path
            h.answer(200, {"items": [{"user_id": "second" if end else "first"}], "next_offset": None if end else 1})
        with fixture(respond) as base, WorkspaceClient(base, ApiKey("synthetic-token")) as c:
            self.assertEqual([m["user_id"] for m in c.iter_members(role="viewer", page_size=1)], ["first", "second"])
        self.assertEqual(paths, ["/api/workspace/v1/members?role=viewer&limit=1", "/api/workspace/v1/members?role=viewer&limit=1&offset=1"])
