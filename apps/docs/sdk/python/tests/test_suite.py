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
            with self.assertRaises(ValidationError):       # following without a pause would spin
                next(c.iter_events(follow=True, interval=0))
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

    def test_webhook_calls_that_carry_a_secret_or_start_a_send_are_sent_once_without_a_key(self):
        calls, seen = [], {}

        def respond(h):
            path, _, query = h.path.partition("?")
            calls.append((h.command, path, h.headers.get("Idempotency-Key"), h.headers.get("If-Match"),
                          json.loads(h.body or b"null"), {k: v[0] for k, v in parse_qs(query).items()}))
            seen[(h.command, path)] = seen.get((h.command, path), 0) + 1
            if h.command != "GET" and seen[(h.command, path)] == 1:      # every first attempt of a write fails
                h.answer(503, {"error": {"code": "unavailable", "message": "synthetic fixture"}}, {"Retry-After": "0"})
            else:
                h.answer(200, {"id": "whsub_1", "version": 2, "secret": "whsec_synthetic", "items": [],
                               "next_cursor": None, "has_more": False})
        base = "/api/suite/v1/webhooks/subscriptions"
        with fixture(respond) as url, SuiteClient(url, UserToken("synthetic-access"), sleep=lambda _: None) as c:
            for wrong in (lambda: c.create_webhook("https://hooks.invalid/x", "tables.*"), lambda: c.get_webhook("")):
                with self.assertRaises(ValidationError):
                    wrong()
            for once in (lambda: c.create_webhook("https://hooks.invalid/x", ["tables.*"], description="sync"),
                         lambda: c.rotate_webhook_secret("whsub 1", overlap_s=600), lambda: c.ping_webhook("whsub 1"),
                         lambda: c.redeliver_webhook("whsub 1", "whdel/1"),
                         lambda: c.update_webhook("whsub 1", events=["*"], version=3), lambda: c.delete_webhook("whsub 1")):
                with self.assertRaises(SynwevoError):                    # not tried again
                    once()
            c.pause_webhook("whsub 1")                                   # these two say the same thing twice safely
            c.resume_webhook("whsub 1")
            page = c.webhook_deliveries("whsub 1", status="failed", limit=10)
            c.webhook_delivery("whsub 1", "whdel/1")
            c.list_webhooks()
        self.assertEqual(page["items"], [])
        self.assertEqual([f"{m} {p}" for m, p, *_ in calls], [
            f"POST {base}", f"POST {base}/whsub%201/rotate-secret", f"POST {base}/whsub%201/ping",
            f"POST {base}/whsub%201/deliveries/whdel%2F1/redeliver", f"PATCH {base}/whsub%201", f"DELETE {base}/whsub%201",
            f"POST {base}/whsub%201/pause", f"POST {base}/whsub%201/pause", f"POST {base}/whsub%201/resume",
            f"POST {base}/whsub%201/resume", f"GET {base}/whsub%201/deliveries", f"GET {base}/whsub%201/deliveries/whdel%2F1",
            f"GET {base}"])
        self.assertTrue(all(key is None for _, _, key, *_ in calls), "no idempotency key on any webhook call")
        self.assertEqual(calls[0][4], {"url": "https://hooks.invalid/x", "events": ["tables.*"], "description": "sync"})
        self.assertEqual(calls[1][4], {"overlap_s": 600})
        self.assertEqual((calls[4][3], calls[4][4]), ('"3"', {"events": ["*"]}))
        self.assertEqual(calls[10][5], {"status": "failed", "limit": "10"})

    def test_a_missing_id_is_refused_before_sending_and_never_names_the_collection(self):
        """An id read from a store that does not have it arrives as None. Left out of the path it
        would turn "this subscription" into "the list" (and a write into a call on the collection)."""
        calls = []

        def respond(h):
            calls.append(f"{h.command} {h.path}")
            h.answer(200, {"items": [], "limit": 4, "workspace_limit": 64})
        with fixture(respond) as url, SuiteClient(url, UserToken("synthetic-access"), sleep=lambda _: None) as c:
            for missing in (None, "", 7):
                for call in (c.get_webhook, c.delete_webhook, c.pause_webhook, c.resume_webhook, c.rotate_webhook_secret,
                             c.ping_webhook, c.webhook_deliveries, lambda sid: c.update_webhook(sid, description="x"),
                             lambda sid: c.webhook_delivery(sid, "whdel_1"), lambda sid: c.redeliver_webhook(sid, "whdel_1"),
                             lambda did: c.webhook_delivery("whsub_1", did), lambda did: c.redeliver_webhook("whsub_1", did)):
                    with self.assertRaises(ValidationError):
                        call(missing)
            self.assertEqual(calls, [], "nothing was sent")
            c.list_webhooks()                                            # the collection is asked for by name only
        self.assertEqual(calls, ["GET /api/suite/v1/webhooks/subscriptions"])

    def test_the_lost_update_guard_goes_out_with_the_clients_own_headers(self):
        sent = []

        def respond(h):
            sent.append({k: h.headers.get(k) for k in ("If-Match", "Authorization", "User-Agent", "Accept")})
            h.answer(200, {"id": "whsub_1", "version": 4})
        with fixture(respond) as url, SuiteClient(url, UserToken("synthetic-access"), sleep=lambda _: None) as c:
            c.update_webhook("whsub_1", description="x", version=3)
            c.update_webhook("whsub_1", description="x")
        guarded, plain = sent
        self.assertEqual(guarded["If-Match"], '"3"')
        self.assertIsNone(plain["If-Match"], "no version asked for: no guard invented")
        for request in sent:                                             # the guard is added to the request, not put in its place
            self.assertEqual(request["Authorization"], "Bearer synthetic-access")
            self.assertTrue(request["User-Agent"] and request["Accept"])

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
