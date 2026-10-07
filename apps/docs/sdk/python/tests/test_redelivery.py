"""Docs redelivery transport contracts on real loopback HTTP fault fixtures.

Scripted receipts stand in for the D20 server contract; no live webhook is delivered.
"""
import inspect
import socket
import unittest
from uuid import UUID

from synwevo_docs_sdk import ApiKey, Client, TransportError, ValidationError
from synwevo_docs_sdk.errors import APIError
from test_http import fixture


def lose_response(handler, *, partial=False):
    if partial:
        handler.send_response(200)
        handler.send_header("Content-Length", "1000")
        handler.end_headers()
        handler.wfile.write(b'{"id":')
        handler.wfile.flush()
    handler.connection.shutdown(socket.SHUT_RDWR)
    handler.connection.close()


class RedeliveryTests(unittest.TestCase):
    def test_lost_response_keeps_key_but_new_method_call_gets_new_key(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                calls, receipts = [], {}

                def respond(h):
                    key = h.headers.get("Idempotency-Key")
                    calls.append((h.command, h.path, key, h.body))
                    if key not in receipts:
                        receipts[key] = {"id": f"fixture_delivery_{len(receipts)}", "redelivery": True}
                        lose_response(h, partial=partial)
                    else:
                        h.answer(200, receipts[key], {"Idempotent-Replayed": "true"})

                with fixture(respond) as base, Client(base, ApiKey("fixture"), max_retries=2,
                                                      sleep=lambda _: None) as c:
                    first = c.redeliver_webhook("sub 1", "delivery/1")
                    second = c.redeliver_webhook("sub 1", "delivery/1")
                self.assertEqual(len(calls), 4)
                self.assertEqual(calls[0], calls[1])
                self.assertEqual(calls[2], calls[3])
                self.assertEqual(calls[0][:2], ("POST", "/api/v1/webhooks/subscriptions/sub%201/deliveries/delivery%2F1/redeliver"))
                self.assertEqual(calls[0][3], b"")
                self.assertNotEqual(calls[0][2], calls[2][2])
                self.assertTrue(all(UUID(key).version == 4 for key in receipts))
                self.assertNotEqual(first["id"], second["id"])
                self.assertTrue(first.replayed and second.replayed)

    def test_explicit_key_survives_retry_and_repeated_method_calls(self):
        keys = []

        def respond(h):
            keys.append(h.headers.get("Idempotency-Key"))
            if len(keys) == 1:
                lose_response(h)
            else:
                h.answer(200, {"id": "fixture_original_receipt"}, {"Idempotent-Replayed": "true"})

        with fixture(respond) as base, Client(base, ApiKey("fixture"), sleep=lambda _: None) as c:
            first = c.redeliver_webhook("s", "d", idempotency_key="fixture:operator-intent")
            second = c.redeliver_webhook("s", "d", idempotency_key="fixture:operator-intent")
        self.assertEqual(first, second)
        self.assertEqual(keys, ["fixture:operator-intent"] * 3)
        self.assertTrue(first.replayed and second.replayed)

    def test_key_validation_and_keyword_only_compatibility(self):
        keys = []

        def respond(h):
            keys.append(h.headers.get("Idempotency-Key"))
            h.answer(200, {"id": "fixture_receipt"})

        valid = ["x", "x" * 129, "x" * 255, "fixture with space"]
        with fixture(respond) as base, Client(base, ApiKey("fixture"), validate=False) as c:
            for key in valid:
                c.redeliver_webhook("s", "d", idempotency_key=key)
            for key in ("", "x" * 256, "line\n", "line\r", "\t", "\x7f", "非ASCII", 1, False, []):
                with self.subTest(key=repr(key)), self.assertRaises(ValidationError) as err:
                    c.redeliver_webhook("s", "d", idempotency_key=key)
                self.assertEqual(err.exception.field, "Idempotency-Key")
            with self.assertRaises(TypeError):
                c.redeliver_webhook("s", "d", "not-keyword")
        self.assertEqual(keys, valid, "invalid keys must fail before sending, even with validate=False")
        parameter = inspect.signature(Client.redeliver_webhook).parameters["idempotency_key"]
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameter.default)

    def test_conflict_permission_and_unsupported_errors_never_fall_back(self):
        cases = [(400, "idempotency_not_supported"), (400, "invalid_idempotency_key"),
                 (401, "unauthorized"), (403, "permission_denied"), (404, "not_found"),
                 (409, "idempotency_conflict")]
        calls = []

        def respond(h):
            calls.append((h.path, h.headers.get("Idempotency-Key")))
            status, code = cases[int(h.path.split("/")[-2])]
            h.answer(status, {"error": {"code": code, "message": "synthetic refusal"}}, {"Retry-After": "0"})

        with fixture(respond) as base, Client(base, ApiKey("fixture"), max_retries=2, sleep=lambda _: None) as c:
            for index, (status, code) in enumerate(cases):
                with self.subTest(code=code), self.assertRaises(APIError) as err:
                    c.redeliver_webhook("s", str(index), idempotency_key="fixture:stable")
                self.assertEqual((err.exception.status, err.exception.code), (status, code))
        self.assertEqual(len(calls), len(cases))
        self.assertTrue(all(key == "fixture:stable" for _, key in calls))

    def test_revocation_on_retry_surfaces_instead_of_returning_or_repeating_a_receipt(self):
        keys = []

        def respond(h):
            keys.append(h.headers.get("Idempotency-Key"))
            if len(keys) == 1:
                lose_response(h)
            else:
                h.answer(403, {"error": {"code": "permission_denied", "message": "fixture access revoked"}})

        with fixture(respond) as base, Client(base, ApiKey("fixture"), max_retries=3, sleep=lambda _: None) as c:
            with self.assertRaises(APIError) as err:
                c.redeliver_webhook("s", "d", idempotency_key="fixture:revoked")
        self.assertEqual((err.exception.status, err.exception.code), (403, "permission_denied"))
        self.assertEqual(keys, ["fixture:revoked"] * 2)

    def test_retry_budget_and_wait_limit_keep_the_same_key(self):
        calls, delays = [], []
        responses = {"busy": (409, "idempotency_in_progress", "0"), "unavailable": (503, "unavailable", "0"),
                     "limited": (429, "rate_limited", "0"), "long-wait": (503, "unavailable", "121")}

        def respond(h):
            name = h.path.split("/")[-2]
            calls.append((name, h.headers.get("Idempotency-Key")))
            status, code, wait = responses[name]
            h.answer(status, {"error": {"code": code, "message": "synthetic refusal"}}, {"Retry-After": wait})

        with fixture(respond) as base, Client(base, ApiKey("fixture"), max_retries=2,
                                              max_retry_wait=120, sleep=delays.append) as c:
            for name, (status, code, _) in responses.items():
                with self.subTest(name=name), self.assertRaises(APIError) as err:
                    c.redeliver_webhook("s", name, idempotency_key=f"fixture:{name}")
                self.assertEqual((err.exception.status, err.exception.code), (status, code))
        self.assertEqual(calls, [(name, f"fixture:{name}") for name in responses
                                for _ in range(1 if name == "long-wait" else 3)])
        self.assertEqual(delays, [0] * 6)

    def test_network_failure_retries_are_bounded_and_can_be_disabled(self):
        for retries in (0, 2):
            with self.subTest(retries=retries):
                keys = []

                def respond(h):
                    keys.append(h.headers.get("Idempotency-Key"))
                    lose_response(h)

                with fixture(respond) as base, Client(base, ApiKey("fixture"), max_retries=retries,
                                                      sleep=lambda _: None) as c:
                    with self.assertRaises(TransportError):
                        c.redeliver_webhook("s", "d", idempotency_key="fixture:network")
                self.assertEqual(keys, ["fixture:network"] * (retries + 1))
