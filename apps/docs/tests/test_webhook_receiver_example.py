"""The shipped reference receiver (apps/docs/examples/webhook_receiver.py) really verifies what
Synwevo sends: it accepts a genuine delivery, deduplicates a redelivery by webhook-id, and
rejects a tampered body, a stale timestamp and an unknown secret."""
import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from .test_webhook_support import fixture_event, hub, standard_signature, sub_rows  # noqa: F401  (hub is a fixture)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "webhook_receiver.py"


@pytest.fixture()
def example():
    # Loaded by path: the example is a standalone script for integrators, not part of a package.
    spec = importlib.util.spec_from_file_location("synwevo_webhook_receiver_example", EXAMPLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _post(url, body: bytes, headers: dict) -> int:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        # TEST FIXTURE traffic is always loopback, including on macOS with a system proxy.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        e.close()
        return e.code


def test_example_receiver_verifies_and_dedupes_real_deliveries(hub, example):
    secrets: list[str] = []
    handled = []
    rx = example.Receiver(secrets)
    srv = example.make_server("127.0.0.1", 0, rx, on_event=handled.append)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/synwevo"
    try:
        sub = hub.create(url, events=["*"])
        secrets.append(sub["secret"])
        d = hub.dispatcher()
        fixture_event(hub.store, "default", "job.completed")
        d.run_once()
        first = sub_rows(hub.store, sub["id"])[0]
        assert first["status"] == "delivered" and first["last_code"] == 204
        # A redelivery carries the same webhook-id: answered 2xx, processed once.
        r = hub.c.post(f"/api/v1/webhooks/subscriptions/{sub['id']}/deliveries/{first['id']}/redeliver",
                       headers=hub.keys["admin"])
        assert r.status_code == 202
        d.run_once()
        assert sub_rows(hub.store, sub["id"])[-1]["status"] == "delivered"
        for _ in range(50):
            if handled:
                break
            time.sleep(0.02)
        assert rx.accepted == 1 and rx.duplicates == 1 and len(handled) == 1
        assert handled[0]["id"] == first["event_id"]
        # Rotation: the receiver holding only the new secret still accepts during the overlap.
        new = hub.c.post(f"/api/v1/webhooks/subscriptions/{sub['id']}/rotate-secret", json={"overlap_s": 600},
                         headers=hub.keys["admin"]).json()["secret"]
        secrets[:] = [new]
        fixture_event(hub.store, "default", "job.failed")
        d.run_once()
        assert sub_rows(hub.store, sub["id"])[-1]["status"] == "delivered" and rx.accepted == 2

        # Hand-made requests: what an attacker or a broken proxy would send.
        body = json.dumps({"id": "ev_forged", "type": "job.completed"}).encode()
        ts = str(int(time.time()))
        good = {"webhook-id": "ev_forged", "webhook-timestamp": ts,
                "webhook-signature": standard_signature(new, "ev_forged", ts, body), "Content-Type": "application/json"}
        assert _post(url, body + b" ", good) == 401                                        # one byte changed
        stale = str(int(time.time()) - 600)
        assert _post(url, body, {**good, "webhook-timestamp": stale,
                                 "webhook-signature": standard_signature(new, "ev_forged", stale, body)}) == 401
        wrong = "whsec_" + "B" * 43 + "="
        assert _post(url, body, {**good, "webhook-signature": standard_signature(wrong, "ev_forged", ts, body)}) == 401
        assert _post(url, body, {"Content-Type": "application/json"}) == 400
        assert _post(url, body, good) == 204                                               # the genuine one
        assert rx.rejected == 4
    finally:
        srv.shutdown()
        srv.server_close()


def test_handler_failure_can_retry_and_success_survives_restart(example, tmp_path):
    """TEST FIXTURE: real HTTP with a failing handler; only completed work enters the seen-file."""
    secret = "fixture-receiver-key"
    seen = tmp_path / "seen"
    rx = example.Receiver([secret], seen_file=str(seen))
    entered, release = threading.Event(), threading.Event()
    attempts = []

    def handle(event):
        attempts.append(event["id"])
        if len(attempts) == 1:
            entered.set()
            assert release.wait(5)
            raise RuntimeError("fixture document value, never echoed")

    srv = example.make_server("127.0.0.1", 0, rx, on_event=handle)
    serving = threading.Thread(target=srv.serve_forever, daemon=True)
    serving.start()
    url = f"http://127.0.0.1:{srv.server_port}/"
    body = json.dumps({"id": "ev_retry", "type": "job.completed"}).encode()
    ts = str(int(time.time()))
    headers = {"webhook-id": "ev_retry", "webhook-timestamp": ts,
               "webhook-signature": standard_signature(secret, "ev_retry", ts, body)}
    result = []
    first = threading.Thread(target=lambda: result.append(_post(url, body, headers)))
    try:
        first.start()
        assert entered.wait(5)
        assert _post(url, body, headers) == 503     # work is not yet safely acknowledged
        assert not seen.exists()
        release.set()
        first.join(5)
        assert result == [503] and rx.accepted == 0 and not rx.seen
        assert _post(url, body, headers) == 204
        assert _post(url, body, headers) == 204
        assert attempts == ["ev_retry", "ev_retry"] and rx.accepted == 1 and rx.duplicates == 1
        restarted = example.Receiver([secret], seen_file=str(seen))
        restarted.process(json.loads(body), lambda event: pytest.fail("processed twice after restart"))
        assert restarted.duplicates == 1
    finally:
        release.set()
        first.join(5)
        srv.shutdown()
        srv.server_close()
        serving.join(5)
