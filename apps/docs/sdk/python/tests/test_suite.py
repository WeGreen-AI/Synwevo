"""Suite SDK behaviour against a local fault-injecting HTTP fixture (not the real service).

What the real service does with these calls is covered where the real application runs
(`apps/suite/tests/test_machine_sdk.py`); here the subject is the client: which variables it
reads, what it retries and with which key, and how it keeps a rotating refresh token.
"""
import json
import unittest
from urllib.parse import parse_qs

from synwevo_docs_sdk import SuiteClient, UserToken, ValidationError
from synwevo_docs_sdk.errors import (AuthenticationError, GoneError, PermissionDeniedError, SynwevoError,
                                     WaitTimeoutError)
from test_http import fixture


class SuiteTests(unittest.TestCase):
    def test_environment_is_its_own_and_a_delegation_is_required(self):
        env = {"SYNWEVO_BASE_URL": "https://docs.invalid", "SYNWEVO_API_KEY": "docs-only-fixture",
               "SYNWEVO_WORKSPACE_CLIENT_ID": "directory-fixture", "SYNWEVO_WORKSPACE_CLIENT_SECRET": "synthetic"}
        with self.assertRaises(ValidationError):
            SuiteClient.from_env(env)                      # other APIs' credentials are not borrowed
        with self.assertRaises(ValidationError):
            SuiteClient("https://workplace.invalid")
        env.update({"SYNWEVO_SUITE_REFRESH_TOKEN": "synthetic-refresh", "SYNWEVO_SUITE_CLIENT_ID": "crm-sync"})
        with SuiteClient.from_env(env) as c:
            self.assertEqual(c.base_url, "https://workplace.synwevo.com")
            token = c._client._t.credentials
            self.assertEqual((token.resource, token.token_url, token.client_id),
                             ("urn:synwevo:platform:suite", "https://accounts.synwevo.com/oidc/token", "crm-sync"))
            self.assertFalse(hasattr(c, "create_project"))
            self.assertNotIn("synthetic-refresh", repr(token))
        with self.assertRaises(SynwevoError):              # a refresh token alone cannot be used
            SuiteClient.from_env({"SYNWEVO_SUITE_REFRESH_TOKEN": "synthetic-refresh"})

    def test_every_attempt_of_one_call_carries_the_same_key(self):
        calls = []

        def respond(h):
            calls.append((h.command, h.path, h.headers.get("Idempotency-Key"), json.loads(h.body or b"{}")))
            if len(calls) % 2:
                h.answer(503, {"error": {"code": "unavailable", "message": "synthetic fixture"}}, {"Retry-After": "0"})
            else:
                h.answer(200, {"run_id": "run_1", "operation": "tables.record.create", "status": "succeeded",
                               "output": {"id": "rec_1"}, "replayed": len(calls) > 2, "workspace_id": "w_1"})
        with fixture(respond) as base, SuiteClient(base, UserToken("synthetic-access"), sleep=lambda _: None) as c:
            first = c.invoke("tables.record.create", {"table_id": "t 1", "cells": {}})
            mine = c.invoke("tables.record.create", {"table_id": "t 1", "cells": {}}, idempotency_key="order-42",
                            correlation_id="sync-7")
        self.assertEqual([(m, p) for m, p, _, _ in calls], [("POST", "/api/suite/v1/ops/tables.record.create")] * 4)
        self.assertTrue(calls[0][2])
        self.assertEqual(calls[0][2], calls[1][2])
        self.assertEqual((calls[2][2], calls[3][2]), ("order-42", "order-42"))
        self.assertNotEqual(calls[0][2], calls[2][2])
        self.assertEqual(calls[0][3], {"input": {"table_id": "t 1", "cells": {}}})
        self.assertEqual(calls[2][3]["correlation_id"], "sync-7")
        self.assertEqual((first["output"], first.replayed, mine.replayed), ({"id": "rec_1"}, False, True))

    def test_what_is_refused_before_sending(self):
        sent = []
        with fixture(lambda h: (sent.append(h.path), h.answer(200, {}))) as base, \
                SuiteClient(base, UserToken("synthetic-access")) as c:
            for bad in ("forms", "Forms.form.list", "forms.form.list/../../x", "forms..list", "", None):
                with self.assertRaises(ValidationError):
                    c.invoke(bad)
            for key in ("with space", "x" * 129, "", "换行"):
                with self.assertRaises(ValidationError):
                    c.invoke("forms.form.list", idempotency_key=key)
            with self.assertRaises(ValidationError):
                c.invoke("forms.form.list", ["not", "an", "object"])
        self.assertEqual(sent, [])

    def test_a_refusal_is_a_typed_error_with_the_request_id(self):
        def respond(h):
            h.answer(403, {"error": {"code": "insufficient_scope", "message": "synthetic", "request_id": "req-9",
                                     "details": {"required_scope": "forms.write"}}}, {"X-Request-ID": "req-9"})
        with fixture(respond) as base, SuiteClient(base, UserToken("synthetic-access"), sleep=lambda _: None) as c:
            with self.assertRaises(PermissionDeniedError) as e:
                c.invoke("forms.form.create", {"title": "x"})
        self.assertEqual((e.exception.code, e.exception.request_id, e.exception.details),
                         ("insufficient_scope", "req-9", {"required_scope": "forms.write"}))

    def test_an_expired_access_token_is_replaced_once_and_the_successor_is_handed_over(self):
        seen, stored = [], []

        def respond(h):
            if h.path == "/oidc/token":
                form = {k: v[0] for k, v in parse_qs(h.body.decode()).items()}
                seen.append(("token", form))
                if form["refresh_token"] != "refresh-1":
                    return h.answer(400, {"error": "invalid_grant", "error_description": "spent"})
                return h.answer(200, {"access_token": "access-2", "refresh_token": "refresh-2", "token_type": "Bearer",
                                      "expires_in": 900, "scope": "forms.read"})
            seen.append(("api", h.headers.get("Authorization")))
            if h.headers.get("Authorization") != "Bearer access-2":
                return h.answer(401, {"error": {"code": "unauthorized", "message": "synthetic"}})
            h.answer(200, {"kind": "delegated", "scopes": ["forms.read"]})
        with fixture(respond) as base:
            token = UserToken("access-1", refresh_token="refresh-1", token_url=base + "/oidc/token",
                              client_id="crm-sync", client_secret="synthetic-secret",
                              resource="urn:synwevo:platform:suite", on_rotate=stored.append)
            with SuiteClient(base, token) as c:
                self.assertEqual(c.whoami()["scopes"], ["forms.read"])
                self.assertEqual(c.whoami()["scopes"], ["forms.read"])       # no second refresh
        self.assertEqual([kind for kind, _ in seen], ["api", "token", "api", "api"])
        self.assertEqual(seen[1][1], {"grant_type": "refresh_token", "refresh_token": "refresh-1",
                                      "client_id": "crm-sync", "client_secret": "synthetic-secret",
                                      "resource": "urn:synwevo:platform:suite"})
        self.assertEqual(len(stored), 1)
        self.assertEqual((stored[0]["access_token"], stored[0]["refresh_token"], stored[0]["scope"]),
                         ("access-2", "refresh-2", "forms.read"))
        self.assertGreater(stored[0]["expires_at"], 0)

    def test_a_refused_refresh_token_is_not_presented_again(self):
        hits = []

        def respond(h):
            hits.append(h.path)
            if h.path == "/oidc/token":
                return h.answer(400, {"error": "invalid_grant", "error_description": "synthetic"})
            h.answer(401, {"error": {"code": "unauthorized", "message": "synthetic"}})
        with fixture(respond) as base:
            token = UserToken("access-1", refresh_token="spent", token_url=base + "/oidc/token", client_id="crm-sync")
            with SuiteClient(base, token) as c:
                with self.assertRaises(AuthenticationError):
                    c.whoami()
                with self.assertRaises(AuthenticationError):
                    c.whoami()
        self.assertEqual(hits.count("/oidc/token"), 1)

    def test_the_event_feed_is_followed_page_by_page_with_the_services_cursor(self):
        asked, naps = [], []
        pages = [{"workspace_id": "w_1", "items": [{"id": "evt_1"}, {"id": "evt_2"}], "next_cursor": "s1.A", "has_more": True},
                 {"workspace_id": "w_1", "items": [], "next_cursor": "s1.B", "has_more": True},      # stopped part-way
                 {"workspace_id": "w_1", "items": [{"id": "evt_3"}], "next_cursor": "s1.C", "has_more": False},
                 {"workspace_id": "w_1", "items": [{"id": "evt_4"}], "next_cursor": "s1.D", "has_more": False}]

        def respond(h):
            path, _, query = h.path.partition("?")
            asked.append((h.command, path, {k: v[0] for k, v in parse_qs(query).items()}))
            if len(asked) > len(pages):
                h.answer(410, {"error": {"code": "cursor_expired", "message": "synthetic fixture", "request_id": "req_gone",
                                         "field": "cursor", "details": {"retention_seconds": 2592000}}})
            else:
                h.answer(200, pages[len(asked) - 1])
        with fixture(respond) as base, SuiteClient(base, UserToken("synthetic-access"), sleep=naps.append) as c:
            seen = [e["id"] for e in c.iter_events(types=["tables.record.changed", "forms.submission.created"],
                                                   start="now", page_size=2)]
            follow = c.iter_events(cursor="s1.C", follow=True, interval=7.5)
            later = next(follow)
            with self.assertRaises(GoneError) as gone:
                next(follow)
            for wrong in ({"types": "tables.record.changed"}, {"types": [""]}, {"start": "yesterday"},
                          {"start": "now", "cursor": "s1.A"}):
                with self.assertRaises(ValidationError):
                    c.events_page(**wrong)
        self.assertEqual(seen, ["evt_1", "evt_2", "evt_3"])
        self.assertEqual(later["id"], "evt_4")
        self.assertEqual((gone.exception.code, gone.exception.request_id), ("cursor_expired", "req_gone"))
        self.assertEqual(naps, [7.5])                      # slept only when there was nothing more to read
        self.assertEqual({(m, p) for m, p, _ in asked}, {("GET", "/api/suite/v1/events")})
        wanted = "tables.record.changed,forms.submission.created"
        self.assertEqual([q for _, _, q in asked], [
            {"types": wanted, "limit": "2", "start": "now"},                 # start only without a cursor
            {"types": wanted, "limit": "2", "cursor": "s1.A"},
            {"types": wanted, "limit": "2", "cursor": "s1.B"},
            {"cursor": "s1.C"}, {"cursor": "s1.D"}])

    def test_waiting_for_a_run_stops_at_a_terminal_state_or_the_deadline(self):
        states = iter(["queued", "running", "succeeded"])
        now = [0.0]

        def respond(h):
            h.answer(200, {"id": "run_1", "status": next(states, "running")})
        with fixture(respond) as base, SuiteClient(base, UserToken("synthetic-access"), clock=lambda: now[0],
                                                    sleep=lambda s: now.__setitem__(0, now[0] + s)) as c:
            self.assertEqual(c.wait("run_1", timeout=30, poll=2)["status"], "succeeded")
            self.assertEqual(now[0], 4.0)
            with self.assertRaises(WaitTimeoutError) as e:
                c.wait("run_1", timeout=5, poll=2)
            self.assertEqual(e.exception.job["status"], "running")


if __name__ == "__main__":
    unittest.main()
