"""TEST FIXTURES shared by the webhook tests. This module holds no tests.

Everything here is a fixture and says so: a real HTTP server on 127.0.0.1 that records what it
receives, a clock the tests can move forward, rows written into `jobs` / `job_events` the way
`jobs.py` writes them, and an independent implementation of the Standard Webhooks signature so
the tests do not check the code with itself.

The private-address allow-list and plain http are enabled only inside the fixtures that need
them (monkeypatch), never globally.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from synwevo_core.auth import Auth
from synwevo_core.store import new_id

from synwevo_docs import webhooks as W


class Receiver:
    """TEST FIXTURE: a real loopback HTTP receiver that records every request.

    `behaviour(n)` gets the 1-based request number and returns a status code, or a
    (status, headers) tuple.
    """

    def __init__(self, behaviour=None, delay: float = 0.0):
        self.received: list[dict] = []
        self.behaviour = behaviour or (lambda n: 200)
        self.delay = delay
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                req = {"body": body, "headers": {k.lower(): v for k, v in self.headers.items()}, "path": self.path}
                with outer.lock:
                    outer.received.append(req)
                    n = len(outer.received)
                if outer.delay:
                    time.sleep(outer.delay)
                res = outer.behaviour(n)
                code, headers = res if isinstance(res, tuple) else (res, {})
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def bodies(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self.received]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class Clock:
    """TEST FIXTURE: real time plus an offset the test can advance."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


def standard_signature(secret: str, msg_id: str, ts: str, body: bytes) -> str:
    """Independent of `webhooks.sign_standard`: written from the Standard Webhooks spec."""
    key = base64.b64decode(secret[len("whsec_"):]) if secret.startswith("whsec_") else secret.encode()
    mac = hmac.new(key, msg_id.encode() + b"." + ts.encode() + b"." + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def signed_by(secret: str, req: dict) -> bool:
    h = req["headers"]
    return standard_signature(secret, h["webhook-id"], h["webhook-timestamp"], req["body"]) in h["webhook-signature"].split()


def fixture_event(store, tenant: str, etype: str = "job.completed", *, occurred_at: float | None = None,
                  job_id: str | None = None, data: dict | None = None) -> dict:
    """TEST FIXTURE: a job row and one event for it, written the way `jobs.py` writes them."""
    t = occurred_at if occurred_at is not None else time.time()
    jid = job_id or new_id("j")
    if not store.one("SELECT 1 FROM jobs WHERE id=?", (jid,)):
        store.execute("INSERT INTO jobs (id, project_id, status, created_at, counts, tenant, run_no) VALUES (?,?,?,?,?,?,1)",
                      (jid, "p_fixture", etype.split(".", 1)[1], t, json.dumps({"total": 1, "success": 1}), tenant))
    eid = new_id("ev")
    store.execute("INSERT INTO job_events (id, job_id, tenant, type, version, occurred_at, data) VALUES (?,?,?,?,1,?,?)",
                  (eid, jid, tenant, etype, t, json.dumps(data if data is not None else {"status": etype.split(".", 1)[1], "run_no": 1})))
    return {"id": eid, "job_id": jid}


def sub_rows(store, sid: str) -> list[dict]:
    return store.all("SELECT * FROM webhook_deliveries WHERE subscription_id=? ORDER BY rowid", (sid,))


def journal_entries(store) -> list[dict]:
    p = store.root / "security-journal.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text("utf-8").splitlines() if line.strip()]


@pytest.fixture()
def hub(client, monkeypatch):
    """An application with the machine API, keys of several scopes in two workspaces, a
    deterministic dispatcher factory and loopback receivers. The application's own background
    dispatcher is stopped so every delivery in a test is one the test caused."""
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_URLS", raising=False)
    client.app_state.webhooks.stop(wait=10)
    store = client.app_state.app.store
    auth = Auth(store)
    keys, key_ids = {}, {}
    for name, tenant, scopes in (("admin", "default", ("admin",)), ("rw", "default", ("read", "write")),
                                 ("ro", "default", ("read",)), ("b_admin", "tenant-b", ("admin",)),
                                 ("b_ro", "tenant-b", ("read",))):
        tok, row = auth.create_key(name, tenant, scopes)
        keys[name] = {"Authorization": f"Bearer {tok}"}
        key_ids[name] = row["id"]
    clock = Clock()
    receivers: list[Receiver] = []

    def dispatcher(**kw) -> W.WebhookDispatcher:
        d = W.WebhookDispatcher(store, start=False, clock=kw.pop("clock", clock), rng=kw.pop("rng", random.Random(7)),
                                concurrency=kw.pop("concurrency", 1), **kw)
        d.enqueue_new_events()          # make sure the cursor exists before the test writes events
        return d

    def receiver(behaviour=None, delay: float = 0.0) -> Receiver:
        rx = Receiver(behaviour, delay)
        receivers.append(rx)
        return rx

    def create(url: str, key: str = "admin", **body) -> dict:
        r = client.post("/api/v1/webhooks/subscriptions", json={"url": url, **body}, headers=keys[key])
        assert r.status_code == 201, (r.status_code, r.text)
        return r.json()

    ns = SimpleNamespace(c=client, store=store, auth=auth, keys=keys, key_ids=key_ids, clock=clock,
                         dispatcher=dispatcher, receiver=receiver, create=create)
    dispatcher()                         # prime the event cursor
    yield ns
    for rx in receivers:
        rx.stop()
