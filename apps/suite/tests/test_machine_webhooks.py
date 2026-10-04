"""Signed webhooks of the business applications' machine API (`/api/suite/v1/webhooks/subscriptions`).

Fixture accounts, real tokens, the real gate, applications and dispatcher. Every delivery goes to a
receiver on 127.0.0.1 started by the test (allowed for the test by the operator's switches, set
per fixture and never globally); nothing here reaches the internet. Signatures are checked with a
verifier written here, not with the code under test. What each test expects is stated from the
rule:

* a delivery says where to look and nothing of what is there, and is the feed's item under the
  feed's id;
* a subscription is its grant's alone; the secret is shown once and sealed at rest;
* authorization is asked when an event is queued and again before every send, so withdrawing
  consent, losing the workspace or losing sight of the record stops delivery at once;
* where a delivery may go is checked again at every send, pinned to what was checked;
* redelivery and ping are not ways around any of this.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import importlib.util
import json
import random
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from synwevo_core import sealed
from synwevo_core.auth import Auth
from synwevo_core.store import now


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location("suite_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_api = _sibling("test_machine_api")
register, bearer, m, refused = _api.register, _api.bearer, _api.m, _api.refused
BASE = "/api/suite/v1/webhooks/subscriptions"
SCOPES = "offline_access " + _api.ALL_SCOPES
ITEM = {"id", "type", "version", "app", "object", "refs", "occurred_at", "run_id", "correlation_id", "workspace_id",
        "subscription_id"}
DAY = 86400


# --------------------------------------------------------------------------------- fixtures
class Receiver:
    """A local HTTP receiver: records what it is sent and answers as it is told."""

    def __init__(self, behaviour=None, delay: float = 0.0):
        self.behaviour = behaviour or (lambda n: 200)
        self.delay = delay
        self.received: list[dict] = []
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with outer.lock:
                    outer.received.append({"body": body, "headers": {k.lower(): v for k, v in self.headers.items()},
                                           "path": self.path})
                    n = len(outer.received)
                if outer.delay:
                    time.sleep(outer.delay)
                answer = outer.behaviour(n)
                status, headers = answer if isinstance(answer, tuple) else (answer, {})
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def bodies(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self.received]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Clock:
    """Real time plus an offset the test moves."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self) -> float:
        return now() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


def signature(secret: str, msg_id: str, ts: str, body: bytes) -> str:
    """Standard Webhooks, written out independently of the code under test."""
    key = base64.b64decode(secret[len("whsec_"):]) if secret.startswith("whsec_") else secret.encode()
    return "v1," + base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()


def signed_by(secret: str, request: dict) -> bool:
    h = request["headers"]
    return signature(secret, h["webhook-id"], h["webhook-timestamp"], request["body"]) in h["webhook-signature"].split()


def grant(api, who, scopes=_api.ALL_SCOPES, client_id="crm-sync") -> dict:
    """A delegation with a refresh token, as the code exchange issues one."""
    from synwevo_core.oidc import Clients, Grants
    from synwevo_core.sessions import Sessions
    p = api.people
    _, session = Sessions(p.store).create(p.uid(who), workspace_id=None, amr="password")
    out = Grants(p.store).issue_tokens(
        client=Clients(p.store).get(client_id), with_refresh=True,
        code_row={"user_id": p.uid(who), "scope": scopes, "central_session_id": session["id"], "workspace_id": p.a})
    return {"auth": {"Authorization": "Bearer " + out["access_token"]}, "access": out["access_token"],
            "refresh": out["refresh_token"], "who": who}


@pytest.fixture()
def hub(suite, monkeypatch):
    from synwevo_suite.common import machine_webhooks
    register(suite, scopes=SCOPES)
    register(suite, client_id="bi-tool", scopes=SCOPES)
    suite.cookies.clear()
    monkeypatch.setenv("SYNWEVO_SUITE_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")
    monkeypatch.setenv("SYNWEVO_SUITE_WEBHOOK_ALLOW_HTTP", "1")
    service = suite.api.state.suite_webhooks
    clock = Clock()
    service.clock, service.rng, service.concurrency = clock, random.Random(7), 1
    receivers: list[Receiver] = []

    def receiver(behaviour=None, delay: float = 0.0) -> Receiver:
        receivers.append(Receiver(behaviour, delay))
        return receivers[-1]

    def subscribe(auth, url, events=("*",), expect=201, **body):
        r = suite.post(BASE, json={"url": url, "events": list(events), **body}, headers=auth)
        assert r.status_code == expect, (r.status_code, r.text)
        return r.json()

    def rows(sid):
        return suite.people.store.all("SELECT * FROM suite_webhook_deliveries WHERE subscription_id=? ORDER BY rowid", (sid,))

    yield SimpleNamespace(api=suite, p=suite.people, store=suite.people.store, service=service, clock=clock, W=machine_webhooks,
                          receiver=receiver, subscribe=subscribe, rows=rows)
    for r in receivers:
        r.stop()


def a_table(api, auth, name="Fixture contacts", key="table"):
    return m(api, auth, "tables.table.create", {"name": name}, key=key)["output"]["id"]


def a_record(api, auth, table, key, **extra):
    return m(api, auth, "tables.record.create", {"table_id": table, "cells": {}, **extra}, key=key)


def set_reading_role(api, auth, table, role, key):
    rev = m(api, auth, "tables.table.get", {"table_id": table})["output"]["schema_rev"]
    m(api, auth, "tables.table.update", {"table_id": table, "schema_rev": rev, "read_role": role}, key=key)


def journal(store) -> list[dict]:
    path = Path(store.root) / "security-journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@contextlib.contextmanager
def every_resolver_taken(hub, monkeypatch):
    """Fixture: every resolver slot of the process is held, as lookups that never return hold them
    (the pool is the real one, shared with Synwevo Docs), and an address check waits 0.3 s for one
    instead of its whole deadline. Nothing of the code under test is replaced."""
    from synwevo_core import webhook_delivery
    held = 0
    while webhook_delivery._DNS_SLOTS.acquire(blocking=False):
        held += 1
    try:
        assert held, "fixture: there was no resolver slot to take"
        with monkeypatch.context() as short:
            short.setattr(hub.W, "TIMEOUT_S", 0.3)
            yield
    finally:
        for _ in range(held):
            webhook_delivery._DNS_SLOTS.release()


# --------------------------------------------------------------------------------- what is delivered
def test_an_event_is_delivered_signed_and_is_the_feeds_item_under_the_feeds_id(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    made = hub.subscribe(alice, rx.url, events=["tables.*"])
    secret = made["secret"]
    table = a_table(hub.api, alice, "Fixture payroll")
    record = m(hub.api, alice, "tables.record.create", {"table_id": table, "cells": {}}, key="r1",
               correlation_id="crm-batch-7")
    assert hub.service.tick() == 2
    assert [r["path"] for r in rx.received] == ["/hook", "/hook"]
    for request in rx.received:
        assert signed_by(secret, request)
        assert not signed_by("whsec_" + base64.b64encode(b"x" * 32).decode(), request)
        tampered = {**request, "body": request["body"].replace(b"tables", b"TABLES")}
        assert not signed_by(secret, tampered)
        assert request["headers"]["content-type"] == "application/json"
        assert request["headers"]["user-agent"] == "Synwevo-Webhook/1"
        assert abs(int(request["headers"]["webhook-timestamp"]) - hub.clock()) < 30
    sent = rx.bodies()
    assert all(set(b) == ITEM and (b["workspace_id"], b["subscription_id"]) == (hub.p.a, made["id"]) for b in sent)
    assert [(b["type"], b["object"]["id"]) for b in sent] == [("tables.table.changed", table),
                                                              ("tables.record.changed", record["output"]["id"])]
    # the same item the feed serves, under the same id: one table of seen ids covers both channels
    fed = hub.api.get("/api/suite/v1/events", headers=alice).json()["items"]
    assert [{**e, "workspace_id": hub.p.a, "subscription_id": made["id"]} for e in fed] == sent
    assert [r["headers"]["webhook-id"] for r in rx.received] == [e["id"] for e in fed]
    assert sent[1]["refs"] == {"table_id": table} and sent[1]["correlation_id"] == "crm-batch-7"
    assert sent[1]["run_id"] == record["run_id"]
    # nothing of the business: no name, no person
    wire = b"".join(r["body"] for r in rx.received).decode()
    for private in ("Fixture payroll", hub.p.uid("alice")):
        assert private not in wire
    log = hub.api.get(f"{BASE}/{made['id']}/deliveries", headers=alice).json()
    assert [(d["status"], d["attempts"], d["last_code"], d["webhook_id"]) for d in reversed(log["items"])] == [
        ("delivered", 1, 200, e["id"]) for e in fed]
    assert rx.received[0]["headers"]["x-synwevo-delivery"] == log["items"][-1]["id"]
    assert rx.received[0]["headers"]["x-synwevo-event"] == "tables.table.changed"
    assert hub.service.tick() == 0 and len(rx.received) == 2            # each once


def test_the_secret_is_shown_once_sealed_at_rest_and_a_rotation_overlaps(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    r = hub.api.post(BASE, json={"url": rx.url, "events": ["tables.*"]}, headers=alice)
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store" and r.headers["etag"] == '"1"'
    first = r.json()
    sid, old = first["id"], first["secret"]
    assert old.startswith("whsec_") and len(base64.b64decode(old[6:])) == 32
    # never again: not on read, not in the list, not in the delivery log, and not in the database as it is
    read = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    listed = hub.api.get(BASE, headers=alice).json()
    assert "secret" not in read and all("secret" not in s for s in listed["items"]) and listed["limit"] == 4
    stored = hub.store.one("SELECT secret_enc, created_by FROM suite_webhook_subscriptions WHERE id=?", (sid,))
    assert old not in json.dumps(stored) and sealed.unseal(hub.store, "webhook-secret", stored["secret_enc"]) == old
    # an answer that carries a secret may not be stored for replay
    for path, body in ((BASE, {"url": rx.url, "events": ["tables.*"]}), (f"{BASE}/{sid}/rotate-secret", {})):
        err = refused(hub.api.post(path, json=body, headers={**alice, "Idempotency-Key": "k-1"}), 400, "idempotency_not_supported")
        assert err["field"] == "Idempotency-Key"

    table = a_table(hub.api, alice)
    rotated = hub.api.post(f"{BASE}/{sid}/rotate-secret", json={"overlap_s": 600}, headers=alice)
    assert rotated.status_code == 200 and rotated.headers["cache-control"] == "no-store"
    new = rotated.json()["secret"]
    assert new != old and rotated.json()["previous_secret_expires_at"] is not None
    hub.service.tick()
    assert signed_by(new, rx.received[-1]) and signed_by(old, rx.received[-1])       # both, while they overlap
    hub.clock.advance(601)
    a_record(hub.api, alice, table, "r1")
    hub.service.tick()
    assert signed_by(new, rx.received[-1]) and not signed_by(old, rx.received[-1])
    assert hub.api.get(f"{BASE}/{sid}", headers=alice).json()["previous_secret_expires_at"] is None
    # without an overlap the old secret never signs again
    newest = hub.api.post(f"{BASE}/{sid}/rotate-secret", json={"overlap_s": 0}, headers=alice).json()["secret"]
    a_record(hub.api, alice, table, "r2")
    hub.service.tick()
    assert signed_by(newest, rx.received[-1]) and not signed_by(new, rx.received[-1])
    for wrong in (-1, 86401, "soon", True):
        refused(hub.api.post(f"{BASE}/{sid}/rotate-secret", json={"overlap_s": wrong}, headers=alice), 400, "invalid_overlap")


def test_rekeying_the_root_secret_carries_the_signing_secrets(hub):
    alice = grant(hub.api, "alice")["auth"]
    made = hub.subscribe(alice, hub.receiver().url, events=["tables.*"])
    rotated = hub.api.post(f"{BASE}/{made['id']}/rotate-secret", json={"overlap_s": 600}, headers=alice).json()
    old_root, new_root = Auth(hub.store).secret(), b"isolated-rekey-root-material-only"
    counts = sealed.rekey(hub.store, old_root, new_root)
    assert counts["suite_webhook_subscriptions.secret_enc"] == 1
    assert counts["suite_webhook_subscriptions.secret_prev_enc"] == 1
    row = hub.store.one("SELECT secret_enc, secret_prev_enc FROM suite_webhook_subscriptions WHERE id=?", (made["id"],))
    assert sealed.unseal_with(new_root, "webhook-secret", row["secret_enc"]) == rotated["secret"]
    assert sealed.unseal_with(new_root, "webhook-secret", row["secret_prev_enc"]) == made["secret"]


# --------------------------------------------------------------------------------- whose it is
def test_a_subscription_is_its_grants_alone(hub):
    p = hub.p
    mine = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sub = hub.subscribe(mine, rx.url, events=["tables.*"])
    sid = sub["id"]
    table = a_table(hub.api, mine)
    hub.service.tick()
    did = hub.api.get(f"{BASE}/{sid}/deliveries", headers=mine).json()["items"][0]["id"]
    others = {"another client of the same person": grant(hub.api, "alice", client_id="bi-tool")["auth"],
              "the same client for another person": grant(hub.api, "bob")["auth"],
              "the same person in another workspace": bearer(hub.api, "alice", _api.ALL_SCOPES, ws=p.b)}
    for label, auth in others.items():
        assert hub.api.get(BASE, headers=auth).json()["items"] == [], label
        for method, path, body in (("get", f"{BASE}/{sid}", None), ("patch", f"{BASE}/{sid}", {"description": "x"}),
                                   ("delete", f"{BASE}/{sid}", None), ("post", f"{BASE}/{sid}/pause", None),
                                   ("post", f"{BASE}/{sid}/resume", None), ("post", f"{BASE}/{sid}/rotate-secret", {}),
                                   ("post", f"{BASE}/{sid}/ping", None), ("get", f"{BASE}/{sid}/deliveries", None),
                                   ("get", f"{BASE}/{sid}/deliveries/{did}", None),
                                   ("post", f"{BASE}/{sid}/deliveries/{did}/redeliver", None)):
            kwargs = {"json": body} if body is not None else {}
            refused(getattr(hub.api, method)(path, headers=auth, **kwargs), 404, "subscription_not_found")
    # a session is not a credential here either
    hub.api.sign_in("alice")
    refused(hub.api.get(f"{BASE}/{sid}"), 401, "unauthorized")
    hub.api.cookies.clear()
    # nothing above changed it, and nothing was sent because someone else asked
    after = hub.api.get(f"{BASE}/{sid}", headers=mine).json()
    assert (after["status"], after["version"], after["description"]) == ("active", 1, "") and len(rx.received) == 1
    assert table and [s["id"] for s in hub.api.get(BASE, headers=mine).json()["items"]] == [sid]


def test_limits_per_grant_per_client_and_per_workspace(hub, monkeypatch):
    alice = grant(hub.api, "alice")["auth"]
    url = hub.receiver().url
    ids = [hub.subscribe(alice, f"{url}/{n}")["id"] for n in range(4)]
    err = refused(hub.api.post(BASE, json={"url": url + "/5", "events": ["*"]}, headers=alice), 409, "subscription_limit_reached")
    assert err["details"] == {"limit": 4, "scope": "grant"}
    listed = hub.api.get(BASE, headers=alice).json()
    assert (listed["limit"], listed["workspace_limit"]) == (4, 64)
    # a grant's own count includes its disabled ones (it can delete them); deleting one makes room again
    hub.service.disable(ids[0], "gone")
    refused(hub.api.post(BASE, json={"url": url + "/5", "events": ["*"]}, headers=alice), 409, "subscription_limit_reached")
    assert hub.api.delete(f"{BASE}/{ids[0]}", headers=alice).json() == {"deleted": ids[0]}
    ids[0] = hub.subscribe(alice, url + "/5")["id"]
    # the workspace's ceiling counts every grant's live ones, and says only the number
    monkeypatch.setenv("SYNWEVO_SUITE_WEBHOOK_MAX_PER_WORKSPACE", "5")
    bob = grant(hub.api, "bob")["auth"]
    hub.subscribe(bob, url)
    err = refused(hub.api.post(BASE, json={"url": url + "/b", "events": ["*"]}, headers=bob), 409, "workspace_subscription_limit_reached")
    assert err["details"] == {"limit": 5}
    # …but not the disabled ones: nobody may be left to remove those — and one may not come back into a full workspace
    hub.service.disable(ids[1], "gone")
    second = hub.subscribe(bob, url + "/b")["id"]
    refused(hub.api.post(f"{BASE}/{ids[1]}/resume", headers=alice), 409, "workspace_subscription_limit_reached")
    # one client may hold only so many of a workspace's
    monkeypatch.setenv("SYNWEVO_SUITE_WEBHOOK_MAX_PER_WORKSPACE", "50")
    monkeypatch.setattr(hub.W, "MAX_PER_CLIENT", 5)
    err = refused(hub.api.post(BASE, json={"url": url + "/c", "events": ["*"]}, headers=bob), 409, "subscription_limit_reached")
    assert err["details"] == {"limit": 5, "scope": "client"}
    other_client = grant(hub.api, "bob", client_id="bi-tool")["auth"]
    assert hub.subscribe(other_client, url + "/c")["id"] != second


def test_a_grant_has_one_subscription_per_address(hub):
    """Create answers with a secret and cannot be replayed from a stored answer; a lost answer and a
    second try must not leave two subscriptions, the first with a secret nobody received."""
    alice, bob = grant(hub.api, "alice")["auth"], grant(hub.api, "bob")["auth"]
    url = hub.receiver().url
    first = hub.subscribe(alice, url, events=["tables.*"])["id"]
    err = refused(hub.api.post(BASE, json={"url": url, "events": ["forms.*"]}, headers=alice), 409, "subscription_exists")
    assert err["details"] == {"id": first}
    # the documented way out: a new secret for the one that exists, the lost one ended at once
    again = hub.api.post(f"{BASE}/{first}/rotate-secret", json={"overlap_s": 0}, headers=alice).json()
    assert again["secret"].startswith("whsec_") and again["previous_secret_expires_at"] is None
    # another person's (or another client's) subscription to the same address is their own, and says nothing here
    theirs = hub.subscribe(bob, url, events=["tables.*"])["id"]
    assert theirs != first
    # changing an address onto one already taken is refused the same way; a deleted one frees its address
    second = hub.subscribe(alice, url + "/second", events=["tables.*"])["id"]
    err = refused(hub.api.patch(f"{BASE}/{second}", json={"url": url}, headers=alice), 409, "subscription_exists")
    assert err["details"] == {"id": first}
    hub.api.delete(f"{BASE}/{first}", headers=alice)
    assert hub.api.patch(f"{BASE}/{second}", json={"url": url}, headers=alice).status_code == 200


def test_changes_are_versioned_and_validated(hub):
    alice = grant(hub.api, "alice")["auth"]
    url = hub.receiver().url
    sid = hub.subscribe(alice, url, events=["tables.*"], description=" Fixture sync ")["id"]
    assert hub.api.get(f"{BASE}/{sid}", headers=alice).json()["description"] == "Fixture sync"
    changed = hub.api.patch(f"{BASE}/{sid}", json={"events": ["tables.record.changed"]}, headers={**alice, "If-Match": '"1"'})
    assert changed.status_code == 200 and changed.json()["version"] == 2 and changed.headers["etag"] == '"2"'
    stale = refused(hub.api.patch(f"{BASE}/{sid}", json={"description": "late"}, headers={**alice, "If-Match": '"1"'}),
                    409, "version_conflict")
    assert stale["details"] == {"current_version": 2}
    assert hub.api.patch(f"{BASE}/{sid}", json={"description": "x", "version": 2}, headers=alice).json()["version"] == 3
    for body, code in (({"secret": "mine"}, "unknown_field"), ({"events": []}, "invalid_events"),
                       ({"events": ["agent.consent.created"]}, "invalid_events"), ({"events": ["docs.*"]}, "invalid_events"),
                       ({"description": "x" * 501}, "invalid_description"), ({"url": "ftp://example.test/x"}, "invalid_url"),
                       ({"url": "https://user:pw@example.test/x"}, "invalid_url")):
        refused(hub.api.patch(f"{BASE}/{sid}", json=body, headers=alice), 400, code)
    assert hub.api.get(f"{BASE}/{sid}", headers=alice).json()["version"] == 3


# --------------------------------------------------------------------------------- what may be pushed
def test_only_what_the_grant_may_read_is_pushed(hub):
    owner = grant(hub.api, "alice")["auth"]
    forms_only = grant(hub.api, "alice", "forms.read forms.write", client_id="bi-tool")["auth"]
    bob = grant(hub.api, "bob")["auth"]
    rx_forms, rx_bob = hub.receiver(), hub.receiver()
    # naming an application whose read scope the credential lacks is refused by name; nothing may be read at all → refused
    err = refused(hub.api.post(BASE, json={"url": rx_forms.url, "events": ["tables.*"]}, headers=forms_only), 403, "insufficient_scope")
    assert err["details"] == {"required_scope": "tables.read"}
    none = grant(hub.api, "alice", "tables.write")["auth"]
    refused(hub.api.post(BASE, json={"url": rx_forms.url, "events": ["*"]}, headers=none), 403, "insufficient_scope")
    hub.subscribe(forms_only, rx_forms.url, events=["*"])
    hub.subscribe(bob, rx_bob.url, events=["*"])

    open_, closed = a_table(hub.api, owner, "Fixture contacts", "open"), a_table(hub.api, owner, "Fixture payroll", "closed")
    set_reading_role(hub.api, owner, closed, "admin", "lock")
    hidden = a_record(hub.api, owner, closed, "in-closed")["output"]["id"]
    plain = a_record(hub.api, owner, open_, "plain")["output"]["id"]
    private = a_record(hub.api, owner, open_, "private", read_role="admin")["output"]["id"]
    form = m(hub.api, owner, "forms.form.create", {"title": "Fixture intake", "definition": {
        "fields": [{"id": "f_amount01", "type": "text", "label": "ID"}]}}, key="form")["output"]["id"]
    m(hub.api, owner, "forms.form.publish", {"form_id": form, "draft_rev": 1}, key="publish")
    hub.service.tick()
    hub.service.tick()
    # "*" is everything the credential may read, not everything: Forms only for the Forms-only grant
    assert [(b["type"], b["object"]["id"]) for b in rx_forms.bodies()] == [("forms.form.published", form)]
    # Bob is an editor: the table closed to him, its records and the private record never reach his endpoint
    got = [(b["type"], b["object"]["id"]) for b in rx_bob.bodies()]
    assert ("tables.record.changed", plain) in got and ("forms.form.published", form) in got
    wire = b"".join(r["body"] for r in rx_bob.received).decode()
    for unseen in (hidden, private):
        assert unseen not in wire
    assert closed not in wire
    hidden_events = {r["id"] for r in hub.store.all("SELECT id FROM app_events WHERE object_id IN (?,?)", (hidden, private))}
    bobs = hub.api.get(BASE, headers=bob).json()["items"][0]["id"]
    assert hidden_events and not (hidden_events & {r["event_id"] for r in hub.rows(bobs)}), "nothing was even queued for him"


def test_authorization_is_asked_again_immediately_before_every_send(hub):
    """Queued while readable, no longer readable when its turn comes: dropped, not sent."""
    owner = grant(hub.api, "alice")["auth"]
    bob = grant(hub.api, "bob")["auth"]
    rx = hub.receiver(lambda n: 500)
    sid = hub.subscribe(bob, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, owner)
    assert hub.service.tick() == 0 and len(rx.received) == 1                   # tried once, will retry
    record = a_record(hub.api, owner, table, "r1")["output"]["id"]
    hub.service.fan_out()
    assert [r["status"] for r in hub.rows(sid)] == ["pending", "pending"]
    set_reading_role(hub.api, owner, table, "admin", "lock")                    # Bob may no longer read the table
    rx.behaviour = lambda n: 200
    hub.clock.advance(DAY)
    hub.service.deliver_due()
    assert len(rx.received) == 1, "nothing more was sent"
    assert [(r["status"], r["last_error"]) for r in hub.rows(sid)] == [("dropped", "no_longer_readable")] * 2
    assert record not in b"".join(r["body"] for r in rx.received).decode()
    # the subscription itself is fine: this was about the record, not the grant
    assert hub.api.get(f"{BASE}/{sid}", headers=bob).json()["status"] == "active"
    # a filter narrowed after queueing is honoured too
    set_reading_role(hub.api, owner, table, "viewer", "unlock")
    a_record(hub.api, owner, table, "r2")
    hub.service.fan_out()
    hub.api.patch(f"{BASE}/{sid}", json={"events": ["tables.schema.changed"]}, headers=bob)
    hub.service.deliver_due()
    assert len(rx.received) == 1 and hub.rows(sid)[-1]["last_error"] == "no_longer_readable"


def _token_revoked(hub, g):
    from synwevo_core.oidc import Grants
    assert Grants(hub.store).revoke_token(g["access"], "client_revoked", client_id="crm-sync")


def _consent_withdrawn(hub, g):
    # what "Connected apps → withdraw" does to the tokens (oauth_consent.withdraw), without the page
    assert hub.store.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='user_withdrew' "
                             "WHERE user_id=? AND client_id='crm-sync' AND revoked_at IS NULL", (now(), hub.p.uid(g["who"]))) >= 1


def _client_disabled(hub, g):
    from synwevo_core.oidc import Clients
    assert Clients(hub.store).disable("crm-sync")


def _member_removed(hub, g):
    assert hub.store.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (hub.p.a, hub.p.uid(g["who"]))) == 1


def _account_suspended(hub, g):
    hub.store.execute("UPDATE users SET status='suspended' WHERE id=?", (hub.p.uid(g["who"]),))


def _workspace_frozen(hub, g):
    hub.store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (hub.p.a,))


def _sign_in_ended(hub, g):
    # The person signed out (or an administrator ended the sign-in). The tokens are untouched: a
    # client that pulls is stopped at its next refresh, and this one never refreshes.
    from synwevo_core.sessions import Sessions
    for row in hub.store.all("SELECT DISTINCT central_session_id AS id FROM oauth_tokens WHERE client_id='crm-sync' AND user_id=?",
                             (hub.p.uid(g["who"]),)):
        assert Sessions(hub.store).revoke(row["id"], "user_signed_out")
    assert not hub.store.one("SELECT 1 FROM oauth_tokens WHERE client_id='crm-sync' AND user_id=? AND revoked_at IS NOT NULL",
                             (hub.p.uid(g["who"]),))


def _refresh_token_ran_out(hub, g):
    # Fourteen days without a refresh, moved onto the tokens instead of waiting them out.
    assert hub.store.execute("UPDATE oauth_tokens SET access_expires_at=?, refresh_expires_at=? WHERE client_id='crm-sync' "
                             "AND user_id=?", (now() - 14 * DAY, now() - 1, hub.p.uid(g["who"]))) >= 1


ENDS = [(_consent_withdrawn, "grant_revoked"), (_token_revoked, "grant_revoked"), (_client_disabled, "credential_revoked"),
        (_member_removed, "not_a_member"), (_account_suspended, "account_unavailable"), (_workspace_frozen, "workspace_unavailable"),
        (_sign_in_ended, "session_ended"), (_refresh_token_ran_out, "grant_expired")]


@pytest.mark.parametrize("end, code", ENDS, ids=[fn.__name__.strip("_") for fn, _ in ENDS])
@pytest.mark.parametrize("when", ["already queued", "not yet queued"])
def test_delivery_stops_at_once_when_the_grant_no_longer_covers_it(hub, end, code, when):
    """Withdrawing consent, revoking the token, disabling the client, leaving the workspace, a
    suspended account, a frozen workspace: nothing more is sent, what was queued is dropped, the
    subscription is disabled with the reason, and a restore cannot bring it back."""
    owner = grant(hub.api, "alice")["auth"]
    g = grant(hub.api, "bob")
    rx = hub.receiver()
    sid = hub.subscribe(g["auth"], rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, owner)
    if when == "already queued":
        assert hub.service.fan_out() == 1
    end(hub, g)
    if end is not _workspace_frozen:                       # something more happens, in the page (the client may be gone)
        hub.api.caps.invoke("tables.record.create", {"table_id": table, "cells": {}}, user_id=hub.p.uid("alice"),
                            workspace_id=hub.p.a)
    hub.service.tick()
    hub.service.tick()
    assert rx.received == [], "not one request after the grant ended"
    row = hub.store.one("SELECT status, disabled_reason FROM suite_webhook_subscriptions WHERE id=?", (sid,))
    assert (row["status"], row["disabled_reason"]) == ("disabled", f"authorization_revoked:{code}")
    assert all(r["status"] == "dropped" and code in r["last_error"] for r in hub.rows(sid))
    assert {"kind": "webhook_disabled", "subscription_id": sid} in [{k: e[k] for k in ("kind", "subscription_id") if k in e}
                                                                   for e in journal(hub.store)]


def test_a_refresh_rotation_or_an_expired_access_token_is_not_the_end_of_a_grant(hub):
    from synwevo_core.oidc import Clients, Grants
    owner = grant(hub.api, "alice")["auth"]
    g = grant(hub.api, "bob")
    rx = hub.receiver()
    sid = hub.subscribe(g["auth"], rx.url, events=["tables.*"])["id"]
    Grants(hub.store).rotate_refresh(g["refresh"], client=Clients(hub.store).get("crm-sync"))
    assert hub.store.execute("UPDATE oauth_tokens SET access_expires_at=? WHERE client_id='crm-sync' AND user_id=?",
                             (now() - 60, hub.p.uid("bob"))) >= 1
    # the token that was rotated away may be as old as it likes: the grant is as fresh as its newest
    assert hub.store.execute("UPDATE oauth_tokens SET refresh_expires_at=? WHERE client_id='crm-sync' AND user_id=? "
                             "AND revoked_reason='rotated'", (now() - 60, hub.p.uid("bob"))) == 1
    # a sign-in that merely ran out is not one that was ended (an offline grant outlives an idle browser)
    assert hub.store.execute("UPDATE sessions SET idle_expires_at=?, absolute_expires_at=? WHERE user_id=?",
                             (now() - 60, now() - 60, hub.p.uid("bob"))) >= 1
    a_table(hub.api, owner)
    assert hub.service.tick() == 1 and len(rx.received) == 1
    assert hub.store.one("SELECT status FROM suite_webhook_subscriptions WHERE id=?", (sid,))["status"] == "active"


def test_a_subscription_needs_a_grant_that_can_be_renewed(hub, monkeypatch):
    """A subscription has no end of its own, so the grant behind it must be one the client can keep
    alive: a token without a refresh token would leave it pushing for nobody a quarter of an hour on."""
    once = bearer(hub.api, "alice", _api.ALL_SCOPES)                    # no offline_access: an access token alone
    rx = hub.receiver()
    refused(hub.api.post(BASE, json={"url": rx.url, "events": ["tables.*"]}, headers=once), 403, "grant_not_renewable")
    assert hub.store.one("SELECT COUNT(*) AS n FROM suite_webhook_subscriptions")["n"] == 0
    alice = grant(hub.api, "alice")["auth"]
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    for path, body in ((f"{BASE}/{sid}/resume", None), (f"{BASE}/{sid}/rotate-secret", {}), (f"{BASE}/{sid}", {"url": rx.url + "/2"})):
        call = hub.api.patch if body and "url" in body else hub.api.post
        refused(call(path, headers=once, **({"json": body} if body is not None else {})), 403, "grant_not_renewable")
    row = hub.store.one("SELECT version, url FROM suite_webhook_subscriptions WHERE id=?", (sid,))
    assert (row["version"], row["url"]) == (1, rx.url), "an access token alone may read and describe it, not answer for it"
    assert hub.api.patch(f"{BASE}/{sid}", json={"description": "seen"}, headers=once).status_code == 200
    # the person's entitlement to the client's application is what a refresh asks last; so is it here
    from synwevo_core.apps import Apps, PLATFORM_APP_ID
    real = Apps.check_access
    monkeypatch.setattr(Apps, "check_access", lambda self, user_id, app_id, workspace_id=None: (
        (False, None, "fixture: entitlement withdrawn") if app_id == PLATFORM_APP_ID else real(self, user_id, app_id, workspace_id)))
    hub.api.caps.invoke("tables.table.create", {"name": "Fixture"}, user_id=hub.p.uid("alice"), workspace_id=hub.p.a)
    hub.service.tick()
    row = hub.store.one("SELECT status, disabled_reason FROM suite_webhook_subscriptions WHERE id=?", (sid,))
    assert (row["status"], row["disabled_reason"]) == ("disabled", "authorization_revoked:entitlement_revoked") and rx.received == []


def test_the_grant_that_answers_is_the_one_that_last_bound_it(hub):
    """Authorising the client again starts a second grant for the same person. The subscription is
    still theirs to manage with it, but deliveries are decided for the grant that bound it — until a
    call that binds (resume, a new address or filter, a new secret) is made with the new one."""
    first, second = grant(hub.api, "alice"), grant(hub.api, "alice")
    rx = hub.receiver(lambda n: 500 if n == 1 else 200)
    made = hub.subscribe(first["auth"], rx.url, events=["tables.*"])
    sid = made["id"]
    assert made["authorized_by_caller"] is True and made["authorized_at"] == made["created_at"]
    seen = hub.api.get(f"{BASE}/{sid}", headers=second["auth"]).json()
    assert seen["authorized_by_caller"] is False
    # a description is not an authorization
    assert hub.api.patch(f"{BASE}/{sid}", json={"description": "x"}, headers=second["auth"]).json()["authorized_by_caller"] is False
    # resume on an active subscription binds it and changes nothing else: its place and its queue stay
    table = a_table(hub.api, first["auth"])
    hub.service.tick()                                                   # queued, tried once, waiting for its retry
    assert [r["status"] for r in hub.rows(sid)] == ["pending"]
    hub.clock.advance(60)
    bound = hub.api.post(f"{BASE}/{sid}/resume", headers=second["auth"]).json()
    assert bound["authorized_by_caller"] is True and bound["authorized_at"] > made["authorized_at"]
    assert (bound["status"], bound["version"], bound["status_changed_at"]) == ("active", seen["version"] + 2, made["status_changed_at"])
    again = hub.api.post(f"{BASE}/{sid}/resume", headers=second["auth"]).json()
    assert again["version"] == bound["version"], "already the caller's: nothing changes"
    hub.service.tick()
    assert [r["status"] for r in hub.rows(sid)] == ["delivered"] and rx.bodies()[-1]["object"]["id"] == table
    # the first grant ending no longer matters
    _token_revoked(hub, first)
    a_record(hub.api, second["auth"], table, "after")
    hub.service.tick()
    assert hub.api.get(f"{BASE}/{sid}", headers=second["auth"]).json()["status"] == "active" and len(rx.received) == 3
    # and the grant that answers ending does, whoever else could still manage it
    third = grant(hub.api, "alice")
    assert hub.store.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='client_revoked' WHERE access_hash=?",
                             (now(), __import__("hashlib").sha256(second["access"].encode()).hexdigest())) == 1
    a_record(hub.api, third["auth"], table, "unsent")
    hub.service.tick()
    stopped = hub.api.get(f"{BASE}/{sid}", headers=third["auth"]).json()
    assert (stopped["status"], stopped["disabled_reason"]) == ("disabled", "authorization_revoked:grant_revoked")
    assert stopped["status_changed_at"] > bound["status_changed_at"] and len(rx.received) == 3
    back = hub.api.post(f"{BASE}/{sid}/resume", headers=third["auth"]).json()
    assert (back["status"], back["disabled_reason"], back["authorized_by_caller"]) == ("active", None, True)


@pytest.mark.parametrize("looking", ["fan-out", "a send", "the sweep"])
def test_a_grant_judged_on_an_earlier_reading_does_not_undo_a_rebind(hub, monkeypatch, looking):
    """The dispatcher reads a subscription and then asks whether its grant still stands. In between,
    the person has authorised the client again and bound the subscription to the new grant (200).
    The old grant's end is then no reason to stop it: nothing is disabled, nothing is dropped, and
    the event goes out under the grant that answers now."""
    first, second = grant(hub.api, "alice"), grant(hub.api, "alice")
    rx = hub.receiver()
    sid = hub.subscribe(first["auth"], rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, second["auth"])
    if looking == "a send":
        assert hub.service.fan_out() == 1
    _token_revoked(hub, first)                                           # ended, and nothing has noticed yet
    real, rebound = hub.service._principal, []

    def asked(sub):
        # fixture: the rebind lands after the row was read and before its grant is asked about
        if not rebound:
            rebound.append(hub.api.post(f"{BASE}/{sid}/resume", headers=second["auth"]).json())
        return real(sub)
    monkeypatch.setattr(hub.service, "_principal", asked)
    {"fan-out": hub.service.fan_out, "a send": hub.service.deliver_due, "the sweep": hub.service.sweep}[looking]()
    assert (rebound[0]["status"], rebound[0]["authorized_by_caller"], rebound[0]["version"]) == ("active", True, 2)
    after = hub.api.get(f"{BASE}/{sid}", headers=second["auth"]).json()
    assert (after["status"], after["disabled_reason"], after["version"]) == ("active", None, 2)
    assert rx.received == [] and not [r for r in hub.rows(sid) if r["status"] != "pending" or r["attempts"]]
    assert not [e for e in journal(hub.store) if e.get("subscription_id") == sid]
    hub.service.tick()
    hub.service.tick()
    assert objects(rx) == [table] and [(r["status"], r["attempts"]) for r in hub.rows(sid)] == [("delivered", 1)]


def test_a_backlog_judged_on_an_earlier_reading_does_not_stop_a_subscription_resumed_since(hub, monkeypatch):
    """Too far behind is said of where a subscription stood when it was read. One its holder has
    resumed since stands at the present, and is not stopped for the place it left."""
    monkeypatch.setattr(hub.W, "MAX_PENDING", 1)
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 500)
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    a_record(hub.api, alice, table, "r1")
    hub.service.tick()                                                   # one queued and refused; the other waits in the log
    hub.clock.advance(32 * DAY)
    rx.behaviour = lambda n: 200
    hub.service.tick()                                                   # the queued one is past the window: dropped
    real, resumed = hub.service._principal, []

    def asked(sub):
        # fixture: the holder pauses and resumes — "from the present" — after the row was read
        if not resumed:
            hub.api.post(f"{BASE}/{sid}/pause", headers=alice)
            resumed.append(hub.api.post(f"{BASE}/{sid}/resume", headers=alice).json())
        return real(sub)
    monkeypatch.setattr(hub.service, "_principal", asked)
    hub.service.tick()
    after = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert resumed[0]["status"] == "active" and (after["status"], after["disabled_reason"], after["version"]) == ("active", None, 3)
    hub.service.tick()                                                   # the next look is at the row as it stands
    assert hub.api.get(f"{BASE}/{sid}", headers=alice).json()["status"] == "active"


def test_an_answer_from_an_address_the_subscription_has_left_does_not_stop_it(hub):
    """410 is the address that was asked saying "stop", and three days of failures are that
    address's. A subscription moved to another address while the request was out is not disabled
    by either: it stays as its holder just left it, and the event goes on to the new address."""
    from synwevo_core.webhook_delivery import BACKOFF_S
    alice = grant(hub.api, "alice")["auth"]
    new = hub.receiver()
    moved = []

    def gone(n):
        # fixture: the holder moves the subscription while this request is out
        moved.append(hub.api.patch(f"{BASE}/{sid}", json={"url": new.url}, headers=alice).status_code)
        return 410
    old = hub.receiver(gone)
    sid = hub.subscribe(alice, old.url, events=["tables.table.changed"])["id"]
    table = a_table(hub.api, alice)
    hub.service.tick()
    after = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert moved == [200] and (after["status"], after["disabled_reason"], after["url"]) == ("active", None, new.url)
    waiting = hub.rows(sid)[0]
    assert (waiting["status"], waiting["attempts"], waiting["last_code"]) == ("pending", 1, 410), "refused there, not given up"
    hub.clock.advance(BACKOFF_S[0] * 1.2 + 1)
    hub.service.tick()
    assert objects(new) == [table] and len(old.received) == 1 and hub.rows(sid)[0]["status"] == "delivered"
    # the failing rule: the attempt that completes three days and a whole schedule is answered by
    # an address the subscription leaves while that request is out
    elsewhere = hub.receiver()

    def failing(n):
        if n == hub.W.MAX_ATTEMPTS:
            moved.append(hub.api.patch(f"{BASE}/{sid}", json={"url": elsewhere.url}, headers=alice).status_code)
        return 500
    new.received.clear()
    new.behaviour = failing
    a_table(hub.api, alice, "Second", "t2")
    hub.service.tick()
    for step in BACKOFF_S:
        hub.clock.advance(step * 1.2 + 1)
        hub.service.tick()
    after = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert moved == [200, 200] and len(new.received) == hub.W.MAX_ATTEMPTS and hub.clock.offset >= 3 * DAY
    assert (after["status"], after["disabled_reason"], after["url"]) == ("active", None, elsewhere.url)
    third = a_table(hub.api, alice, "Third", "t3")
    hub.service.tick()
    assert objects(elsewhere) == [third]


def test_gone_is_not_acted_on_once_the_subscription_has_changed_however_late(hub, monkeypatch):
    """The last moment a move can land: the 410 has been recorded against the delivery, the
    subscription has not been disabled yet. It is still not the subscription that was asked."""
    alice = grant(hub.api, "alice")["auth"]
    old, new = hub.receiver(lambda n: 410), hub.receiver()
    sid = hub.subscribe(alice, old.url, events=["tables.table.changed"])["id"]
    a_table(hub.api, alice)
    real, moved = hub.service._attempt_failed, []

    def recorded(d, *args, **kwargs):
        # fixture: the move lands between the two writes the 410 leads to
        moved.append(hub.api.patch(f"{BASE}/{sid}", json={"url": new.url}, headers=alice).status_code)
        return real(d, *args, **kwargs)
    monkeypatch.setattr(hub.service, "_attempt_failed", recorded)
    hub.service.tick()
    after = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert moved == [200] and (after["status"], after["disabled_reason"], after["url"]) == ("active", None, new.url)
    assert [(r["status"], r["last_code"]) for r in hub.rows(sid)] == [("failed", 410)], "what was observed stays recorded"


def test_binding_checks_the_whole_filter_against_the_grant_that_binds(hub):
    wide = grant(hub.api, "alice")
    narrow = grant(hub.api, "alice", scopes="forms.read")
    rx = hub.receiver()
    sid = hub.subscribe(wide["auth"], rx.url, events=["tables.*", "forms.*"])["id"]
    for call, path, body in ((hub.api.patch, f"{BASE}/{sid}", {"url": rx.url + "/moved"}),
                             (hub.api.post, f"{BASE}/{sid}/rotate-secret", {}), (hub.api.post, f"{BASE}/{sid}/resume", None)):
        err = refused(call(path, headers=narrow["auth"], **({"json": body} if body is not None else {})), 403, "insufficient_scope")
        assert err["details"] == {"required_scope": "tables.read"}
    row = hub.store.one("SELECT url, version, created_by FROM suite_webhook_subscriptions WHERE id=?", (sid,))
    assert (row["url"], row["version"]) == (rx.url, 1)
    # asking for less is how a narrower grant takes it over
    taken = hub.api.patch(f"{BASE}/{sid}", json={"events": ["forms.*"]}, headers=narrow["auth"]).json()
    assert taken["authorized_by_caller"] is True and taken["events"] == ["forms.*"]


def test_one_endpoint_serving_two_people_can_tell_their_deliveries_apart(hub):
    """Each person's grant has its own subscription and secret. The same event reaches both under the
    same webhook-id, decided for each: the header names the subscription, hence the secret and whose copy it is."""
    alice, bob = grant(hub.api, "alice")["auth"], grant(hub.api, "bob")["auth"]
    rx = hub.receiver()
    hers, his = hub.subscribe(alice, rx.url, events=["tables.*"]), hub.subscribe(bob, rx.url, events=["tables.*"])
    made = m(hub.api, alice, "tables.table.create", {"name": "Fixture shared"}, key="shared")
    hub.service.tick()
    hub.service.tick()
    by_subscription = {r["headers"]["x-synwevo-subscription"]: r for r in rx.received}
    assert set(by_subscription) == {hers["id"], his["id"]} and len(rx.received) == 2
    mine, theirs = by_subscription[hers["id"]], by_subscription[his["id"]]
    assert mine["headers"]["webhook-id"] == theirs["headers"]["webhook-id"]
    assert signed_by(hers["secret"], mine) and not signed_by(his["secret"], mine)
    assert signed_by(his["secret"], theirs) and not signed_by(hers["secret"], theirs)
    # what caused it is told only to the grant whose call it was
    assert json.loads(mine["body"])["run_id"] == made["run_id"] and json.loads(theirs["body"])["run_id"] is None


def test_rotating_again_inside_an_overlap_keeps_the_secret_the_receiver_holds(hub):
    """A rotation's answer can be lost. Repeating it must not end the only secret the receiver has."""
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    made = hub.subscribe(alice, rx.url, events=["tables.*"])
    sid, original = made["id"], made["secret"]
    lost = hub.api.post(f"{BASE}/{sid}/rotate-secret", json={"overlap_s": 600}, headers=alice).json()
    hub.clock.advance(60)
    kept = hub.api.post(f"{BASE}/{sid}/rotate-secret", json={"overlap_s": 600}, headers=alice).json()
    assert kept["previous_secret_expires_at"] == lost["previous_secret_expires_at"], "the first overlap is not extended"
    table = a_table(hub.api, alice)
    hub.service.tick()
    sent = rx.received[-1]
    assert signed_by(original, sent) and signed_by(kept["secret"], sent) and not signed_by(lost["secret"], sent)
    # once the overlap is over a rotation overlaps with the secret then current, as the first did
    hub.clock.advance(600)
    later = hub.api.post(f"{BASE}/{sid}/rotate-secret", json={"overlap_s": 600}, headers=alice).json()
    a_record(hub.api, alice, table, "r1")
    hub.service.tick()
    sent = rx.received[-1]
    assert signed_by(later["secret"], sent) and signed_by(kept["secret"], sent) and not signed_by(original, sent)


# --------------------------------------------------------------------------------- retries and health
def test_failures_are_retried_on_the_schedule_then_give_up(hub, monkeypatch):
    from synwevo_core.webhook_delivery import BACKOFF_S
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 503 if n == 1 else (429, {"Retry-After": "900"}) if n == 2 else 500 if n < 5 else 200)
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    a_table(hub.api, alice)
    hub.service.tick()
    first = hub.rows(sid)[0]
    assert (first["status"], first["attempts"], first["last_code"], first["last_error"]) == ("pending", 1, 503, "http_status")
    assert 0.8 * BACKOFF_S[0] <= first["next_attempt_at"] - hub.clock() <= 1.2 * BACKOFF_S[0] + 1
    hub.service.tick()
    assert len(rx.received) == 1, "not before its time"
    hub.clock.advance(BACKOFF_S[0] * 1.2 + 1)
    hub.service.tick()
    second = hub.rows(sid)[0]
    assert second["attempts"] == 2 and second["next_attempt_at"] - hub.clock() >= 899, "Retry-After is a minimum"
    hub.clock.advance(901)
    hub.service.tick()
    hub.clock.advance(BACKOFF_S[2] * 1.2 + 1)
    hub.service.tick()
    hub.clock.advance(BACKOFF_S[3] * 1.2 + 1)
    hub.service.tick()
    done = hub.rows(sid)[0]
    assert (done["status"], done["attempts"], done["last_code"]) == ("delivered", 5, 200)
    # the same webhook-id on every attempt, a fresh timestamp and signature each time
    assert len({r["headers"]["webhook-id"] for r in rx.received}) == 1
    assert len({r["headers"]["webhook-signature"] for r in rx.received}) == len(rx.received) == 5
    health = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert health["consecutive_failures"] == 0 and health["last_success_at"] and health["last_failure_at"]
    # the receiver's answer is not kept
    assert all(set(d) == {"id", "webhook_id", "event_type", "status", "attempts", "last_code", "last_error", "duration_ms",
                          "created_at", "delivered_at", "next_attempt_at", "redelivery", "redelivery_of"}
               for d in hub.api.get(f"{BASE}/{sid}/deliveries", headers=alice).json()["items"])


@pytest.mark.parametrize("header", ["²", "1³"], ids=["a superscript digit", "digits and a superscript"])
def test_an_unreadable_retry_after_is_no_retry_after(hub, header):
    """Whatever a receiver writes in Retry-After, its 429 is a 429: recorded with its status,
    retried on the schedule, and counted against the subscription like any other refusal."""
    from synwevo_core.webhook_delivery import BACKOFF_S
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: (429, {"Retry-After": header}))
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    a_table(hub.api, alice)
    hub.service.tick()
    row = hub.rows(sid)[0]
    assert (row["status"], row["attempts"], row["last_code"], row["last_error"]) == ("pending", 1, 429, "http_status")
    assert 0.8 * BACKOFF_S[0] - 1 <= row["next_attempt_at"] - hub.clock() <= 1.2 * BACKOFF_S[0]
    health = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert health["consecutive_failures"] == 1 and health["last_failure_at"] is not None


def test_410_and_three_days_of_failures_disable_the_subscription(hub):
    from synwevo_core.webhook_delivery import BACKOFF_S
    alice = grant(hub.api, "alice")["auth"]
    gone, failing = hub.receiver(lambda n: 410), hub.receiver(lambda n: 500)
    sid_gone = hub.subscribe(alice, gone.url, events=["tables.table.changed"])["id"]
    sid_failing = hub.subscribe(alice, failing.url, events=["tables.table.changed"])["id"]
    a_table(hub.api, alice)
    hub.service.tick()
    row = hub.api.get(f"{BASE}/{sid_gone}", headers=alice).json()
    assert (row["status"], row["disabled_reason"]) == ("disabled", "gone") and hub.rows(sid_gone)[0]["status"] == "failed"
    a_table(hub.api, alice, "Second", "t2")
    hub.service.tick()
    assert len(gone.received) == 1 and len(hub.rows(sid_gone)) == 1, "nothing queued or sent after 410"
    # a whole schedule of attempts over more than three days, and never a success
    for step in BACKOFF_S:
        assert hub.api.get(f"{BASE}/{sid_failing}", headers=alice).json()["status"] == "active"
        hub.clock.advance(step * 1.2 + 1)
        hub.service.tick()
    row = hub.api.get(f"{BASE}/{sid_failing}", headers=alice).json()
    assert (row["status"], row["disabled_reason"], row["consecutive_failures"]) == ("disabled", "failing", 10)
    assert hub.clock.offset >= 3 * DAY and len(failing.received) == 10
    assert [(r["status"], r["attempts"]) for r in hub.rows(sid_failing)][0] == ("failed", 10)
    # resuming starts a fresh clock, from the present
    failing.behaviour = lambda n: 200
    resumed = hub.api.post(f"{BASE}/{sid_failing}/resume", headers=alice).json()
    assert (resumed["status"], resumed["disabled_reason"], resumed["consecutive_failures"]) == ("active", None, 0)
    before = len(failing.received)
    hub.service.tick()
    assert len(failing.received) == before, "what happened while it was disabled is not delivered afterwards"
    a_table(hub.api, alice, "Third", "t3")
    hub.service.tick()
    assert len(failing.received) == before + 1


def test_time_alone_does_not_disable_a_subscription(hub):
    """One failure, then days in which nothing was tried — the worker was down, a backup was restored,
    the clock stepped — then another: two failures are not three days of failing."""
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 500)
    sid = hub.subscribe(alice, rx.url, events=["tables.table.changed"])["id"]
    a_table(hub.api, alice)
    hub.service.tick()
    hub.clock.advance(5 * DAY)
    hub.service.tick()
    row = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert (row["status"], row["consecutive_failures"]) == ("active", 2) and len(rx.received) == 2
    assert hub.rows(sid)[0]["status"] == "pending", "and its delivery goes on through its schedule"


def test_pause_resume_and_delete(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 500)
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    hub.service.tick()
    assert hub.api.post(f"{BASE}/{sid}/pause", headers=alice).json()["status"] == "paused"
    assert [(r["status"], r["last_error"]) for r in hub.rows(sid)] == [("dropped", "subscription_paused")]
    a_record(hub.api, alice, table, "during")
    hub.clock.advance(DAY)
    hub.service.tick()
    assert len(rx.received) == 1, "paused: no retries, nothing new"
    rx.behaviour = lambda n: 200
    assert hub.api.post(f"{BASE}/{sid}/resume", headers=alice).json()["status"] == "active"
    hub.service.tick()
    assert len(rx.received) == 1, "what happened while paused is never sent afterwards"
    after = a_record(hub.api, alice, table, "after")["output"]["id"]
    hub.service.tick()
    assert rx.bodies()[-1]["object"]["id"] == after
    # delete: gone for its owner, its queue stopped, its secret gone, journalled
    rx.behaviour = lambda n: 500
    a_record(hub.api, alice, table, "queued")
    hub.service.tick()
    assert hub.api.delete(f"{BASE}/{sid}", headers=alice).json() == {"deleted": sid}
    refused(hub.api.get(f"{BASE}/{sid}", headers=alice), 404, "subscription_not_found")
    refused(hub.api.delete(f"{BASE}/{sid}", headers=alice), 404, "subscription_not_found")
    assert hub.rows(sid)[-1]["status"] == "dropped" and hub.rows(sid)[-1]["last_error"] == "subscription_deleted"
    stored = hub.store.one("SELECT secret_enc, secret_prev_enc FROM suite_webhook_subscriptions WHERE id=?", (sid,))
    assert sealed.unseal(hub.store, "webhook-secret", stored["secret_enc"]) == "" and stored["secret_prev_enc"] is None
    assert any(e["kind"] == "webhook_disabled" and e.get("subscription_id") == sid for e in journal(hub.store))
    sent = len(rx.received)
    hub.clock.advance(DAY)
    hub.service.tick()
    assert len(rx.received) == sent


# --------------------------------------------------------------------------------- where it may go
def test_where_a_delivery_may_go_is_checked_when_it_is_set(hub, monkeypatch):
    alice = grant(hub.api, "alice")["auth"]
    url = hub.receiver().url
    monkeypatch.delenv("SYNWEVO_SUITE_WEBHOOK_ALLOW_HTTP")
    err = refused(hub.api.post(BASE, json={"url": url, "events": ["*"]}, headers=alice), 400, "invalid_url")
    assert err["field"] == "url" and "https" in err["message"]
    monkeypatch.setenv("SYNWEVO_SUITE_WEBHOOK_ALLOW_HTTP", "1")
    monkeypatch.delenv("SYNWEVO_SUITE_WEBHOOK_ALLOW_PRIVATE")
    private = refused(hub.api.post(BASE, json={"url": url, "events": ["*"]}, headers=alice), 400, "invalid_url")
    # a name that does not resolve and a name that resolves somewhere it may not go read the same:
    # the refusal must not tell a caller which internal names exist
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror(socket.EAI_NONAME, "no")))
    missing = refused(hub.api.post(BASE, json={"url": "http://intranet.fixture.test/hook", "events": ["*"]}, headers=alice),
                      400, "invalid_url")
    assert missing["message"] == private["message"] and "127.0.0.1" not in private["message"]
    for bad in ("", "ftp://example.test/x", "http://user:pw@example.test/x", "http://exa mple.test/x", "x" * 3000):
        refused(hub.api.post(BASE, json={"url": bad, "events": ["*"]}, headers=alice), 400, "invalid_url")
    assert hub.store.one("SELECT COUNT(*) AS n FROM suite_webhook_subscriptions")["n"] == 0


def test_the_address_is_checked_again_at_every_send_and_the_connection_is_pinned(hub, monkeypatch):
    alice = grant(hub.api, "alice")["auth"]
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
    metadata = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]
    answers = {"now": public}
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answers["now"])
    monkeypatch.delenv("SYNWEVO_SUITE_WEBHOOK_ALLOW_PRIVATE")
    sid = hub.subscribe(alice, "http://rebind.fixture.test/hook", events=["tables.*"])["id"]
    connected = []
    monkeypatch.setattr(hub.W, "_connect_pinned", lambda address, *a, **k: (connected.append(address), (_ for _ in ()).throw(
        OSError("fixture: no connection is made")))[1])
    a_table(hub.api, alice)
    hub.service.tick()
    # the address that was validated is the address connected to — the name is not resolved a second time
    assert connected == [("93.184.216.34", 80)]
    assert (hub.rows(sid)[0]["status"], hub.rows(sid)[0]["last_error"]) == ("pending", "connect_failed")
    # the same name now answers with the metadata address: refused without a connection
    answers["now"] = metadata
    hub.clock.advance(DAY)
    hub.service.tick()
    assert connected == [("93.184.216.34", 80)]
    refused_row = hub.rows(sid)[0]
    assert (refused_row["status"], refused_row["attempts"], refused_row["last_error"]) == ("pending", 2, "destination_unavailable")
    # …and a name that stops resolving is told and treated no differently: the log is not a way to
    # ask this server which of the two happened
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("fixture: no such host")))
    hub.clock.advance(DAY)
    hub.service.tick()
    unresolved = hub.rows(sid)[0]
    assert (unresolved["status"], unresolved["attempts"], unresolved["last_error"]) == ("pending", 3, "destination_unavailable")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answers["now"])
    # resuming a subscription checks its address as creating one does
    hub.api.post(f"{BASE}/{sid}/pause", headers=alice)
    refused(hub.api.post(f"{BASE}/{sid}/resume", headers=alice), 400, "invalid_url")


def test_redirects_are_not_followed(hub):
    alice = grant(hub.api, "alice")["auth"]
    elsewhere = hub.receiver()
    rx = hub.receiver(lambda n: (302, {"Location": elsewhere.url}))
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    a_table(hub.api, alice)
    hub.service.tick()
    assert elsewhere.received == [] and len(rx.received) == 1
    assert (hub.rows(sid)[0]["status"], hub.rows(sid)[0]["last_code"]) == ("pending", 302)


# --------------------------------------------------------------------------------- redelivery and ping
def test_redelivery_is_not_a_way_around_authorization_or_the_window(hub, monkeypatch):
    owner = grant(hub.api, "alice")["auth"]
    bob = grant(hub.api, "bob")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(bob, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, owner)
    hub.service.tick()
    first = hub.api.get(f"{BASE}/{sid}/deliveries", headers=bob).json()["items"][0]
    again = hub.api.post(f"{BASE}/{sid}/deliveries/{first['id']}/redeliver", headers=bob)
    assert again.status_code == 202 and again.json()["redelivery_of"] == first["id"] and again.json()["webhook_id"] == first["webhook_id"]
    hub.service.tick()
    assert len(rx.received) == 2 and rx.received[0]["headers"]["webhook-id"] == rx.received[1]["headers"]["webhook-id"]
    assert rx.received[0]["headers"]["x-synwevo-delivery"] != rx.received[1]["headers"]["x-synwevo-delivery"]
    # the table is closed to Bob: asking again does not get it sent again
    set_reading_role(hub.api, owner, table, "admin", "lock")
    queued = hub.api.post(f"{BASE}/{sid}/deliveries/{first['id']}/redeliver", headers=bob).json()
    hub.service.tick()
    assert len(rx.received) == 2
    assert hub.api.get(f"{BASE}/{sid}/deliveries/{queued['id']}", headers=bob).json()["last_error"] == "no_longer_readable"
    set_reading_role(hub.api, owner, table, "viewer", "unlock")
    # no more than a handful of times, never for a ping, never while stopped, never past the window
    for _ in range(3):
        assert hub.api.post(f"{BASE}/{sid}/deliveries/{first['id']}/redeliver", headers=bob).status_code == 202
    err = refused(hub.api.post(f"{BASE}/{sid}/deliveries/{first['id']}/redeliver", headers=bob), 409, "redelivery_limit_reached")
    assert err["details"] == {"limit": 5}
    refused(hub.api.post(f"{BASE}/{sid}/deliveries/{first['id']}/redeliver", headers={**bob, "Idempotency-Key": "k"}),
            400, "idempotency_not_supported")
    ping = hub.api.post(f"{BASE}/{sid}/ping", headers=bob).json()
    refused(hub.api.post(f"{BASE}/{sid}/deliveries/{ping['id']}/redeliver", headers=bob), 409, "not_redeliverable")
    refused(hub.api.post(f"{BASE}/{sid}/deliveries/whdel_000000000000/redeliver", headers=bob), 404, "delivery_not_found")
    monkeypatch.setattr(hub.W, "MAX_REDELIVERIES", 99)
    hub.api.post(f"{BASE}/{sid}/pause", headers=bob)
    refused(hub.api.post(f"{BASE}/{sid}/deliveries/{first['id']}/redeliver", headers=bob), 409, "subscription_not_active")
    hub.api.post(f"{BASE}/{sid}/resume", headers=bob)
    hub.clock.advance(31 * DAY)
    err = refused(hub.api.post(f"{BASE}/{sid}/deliveries/{first['id']}/redeliver", headers=bob), 409, "redelivery_window_expired")
    assert err["details"] == {"window_days": 30}


def test_a_ping_is_signed_single_and_apart_from_the_subscriptions_health(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 200 if n == 1 else 410 if n == 3 else 500)
    made = hub.subscribe(alice, rx.url, events=["tables.*"])
    sid = made["id"]
    ok = hub.api.post(f"{BASE}/{sid}/ping", headers=alice).json()
    assert (ok["event_type"], ok["status"], ok["attempts"], ok["last_code"]) == ("webhook.ping", "delivered", 1, 200)
    body = rx.bodies()[0]
    assert signed_by(made["secret"], rx.received[0]) and set(body) == ITEM
    assert (body["type"], body["app"], body["object"], body["workspace_id"]) == ("webhook.ping", None, None, hub.p.a)
    assert body["subscription_id"] == sid
    assert rx.received[0]["headers"]["x-synwevo-subscription"] == sid
    hub.clock.advance(11)
    failed = hub.api.post(f"{BASE}/{sid}/ping", headers=alice).json()
    assert (failed["status"], failed["attempts"], failed["last_code"], failed["next_attempt_at"]) == ("failed", 1, 500, None)
    hub.clock.advance(DAY)
    hub.service.tick()
    assert len(rx.received) == 2, "a ping is not retried"
    # "410 Gone" to a ping is an answer to the ping: it does not unsubscribe anything
    gone = hub.api.post(f"{BASE}/{sid}/ping", headers=alice).json()
    assert (gone["status"], gone["last_code"]) == ("failed", 410)
    health = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert health["status"] == "active" and health["disabled_reason"] is None
    assert health["consecutive_failures"] == 0 and health["last_success_at"] is None and health["last_failure_at"] is None
    # allowed while paused (to test a receiver before resuming), not once disabled
    hub.clock.advance(11)
    hub.api.post(f"{BASE}/{sid}/pause", headers=alice)
    assert hub.api.post(f"{BASE}/{sid}/ping", headers=alice).status_code == 200
    hub.service.disable(sid, "gone")
    refused(hub.api.post(f"{BASE}/{sid}/ping", headers=alice), 409, "subscription_disabled")
    refused(hub.api.post(f"{BASE}/{sid}/ping", headers={**alice, "Idempotency-Key": "k"}), 400, "idempotency_not_supported")


def test_pings_are_rationed_per_subscription(hub):
    """The address never proved it wants these: a client may not make the platform post to it at will."""
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    other = hub.subscribe(alice, rx.url + "/other", events=["tables.*"])["id"]
    assert hub.api.post(f"{BASE}/{sid}/ping", headers=alice).status_code == 200
    hub.clock.advance(4)
    r = hub.api.post(f"{BASE}/{sid}/ping", headers=alice)
    err = refused(r, 429, "ping_rate_limited")
    assert err["details"]["reason"] == "interval" and r.headers["retry-after"] == "6"
    assert len(rx.received) == 1 and len(hub.rows(sid)) == 1, "a refused ping sends nothing and records nothing"
    assert hub.api.post(f"{BASE}/{other}/ping", headers=alice).status_code == 200, "another subscription has its own ration"
    for _ in range(19):
        hub.clock.advance(11)
        assert hub.api.post(f"{BASE}/{sid}/ping", headers=alice).status_code == 200
    hub.clock.advance(11)
    r = hub.api.post(f"{BASE}/{sid}/ping", headers=alice)
    assert refused(r, 429, "ping_rate_limited")["details"]["reason"] == "hourly" and int(r.headers["retry-after"]) > 3000
    hub.clock.advance(int(r.headers["retry-after"]))
    assert hub.api.post(f"{BASE}/{sid}/ping", headers=alice).status_code == 200
    # as many as the server sends at once, and no more: the request that asks waits for the send
    for _ in range(hub.W.PING_CONCURRENCY):
        assert hub.service._pings.acquire(blocking=False)
    hub.clock.advance(11)
    r = hub.api.post(f"{BASE}/{sid}/ping", headers=alice)
    assert refused(r, 429, "ping_rate_limited")["details"]["reason"] == "busy" and r.headers["retry-after"] == "5"
    for _ in range(hub.W.PING_CONCURRENCY):
        hub.service._pings.release()


def test_the_delivery_log_is_paged_with_a_cursor_that_is_its_holders(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    other = hub.subscribe(alice, rx.url + "/other", events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    for i in range(4):
        a_record(hub.api, alice, table, f"r{i}")
    hub.service.tick()
    hub.service.tick()
    first = hub.api.get(f"{BASE}/{sid}/deliveries", params={"limit": 2}, headers=alice).json()
    assert len(first["items"]) == 2 and first["has_more"] is True
    rest = hub.api.get(f"{BASE}/{sid}/deliveries", params={"limit": 50, "cursor": first["next_cursor"]}, headers=alice).json()
    ids = [d["id"] for d in first["items"] + rest["items"]]
    assert len(ids) == len(set(ids)) == 5 and rest["has_more"] is False and rest["next_cursor"] is None
    assert [d["status"] for d in hub.api.get(f"{BASE}/{sid}/deliveries", params={"status": "delivered"}, headers=alice).json()["items"]] \
        == ["delivered"] * 5
    for bad in ({"cursor": first["next_cursor"][:-2] + "AA"}, {"cursor": "nonsense"}, {"limit": 0}, {"limit": 101},
                {"status": "sent"}):
        code = "invalid_cursor" if "cursor" in bad else "invalid_limit" if "limit" in bad else "invalid_status"
        refused(hub.api.get(f"{BASE}/{sid}/deliveries", params=bad, headers=alice), 400, code)
    # a cursor of one subscription is not a cursor of another, and it does not show a row number
    refused(hub.api.get(f"{BASE}/{other}/deliveries", params={"cursor": first["next_cursor"]}, headers=alice), 400, "invalid_cursor")
    assert len(base64.urlsafe_b64decode(first["next_cursor"] + "=" * (-len(first["next_cursor"]) % 4))) == 12 + 8 + 16


# --------------------------------------------------------------------------------- workers, restore, faults
def test_two_workers_never_send_the_same_delivery(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(delay=0.05)
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    for i in range(5):
        a_record(hub.api, alice, table, f"r{i}")
    hub.service.fan_out()
    second = hub.W.Webhooks(hub.api.caps, clock=hub.clock, concurrency=2)
    hub.service.concurrency = 2
    threads = [threading.Thread(target=w.deliver_due) for w in (hub.service, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    hub.service.deliver_due()
    ids = [r["headers"]["webhook-id"] for r in rx.received]
    assert len(ids) == len(set(ids)) == 6
    assert all(r["status"] == "delivered" and r["claimed_by"] is None for r in hub.rows(sid))


def test_a_restored_log_neither_revives_nor_skips(hub):
    from synwevo_core import journal as security_journal
    from synwevo_core.capabilities import record_event
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    a_record(hub.api, alice, table, "r1")
    hub.service.tick()
    assert len(rx.received) == 2
    # the log comes back shorter than the cursor, then grows past it again with other events
    top = hub.store.one("SELECT MAX(seq) AS s FROM app_events")["s"]
    hub.store.execute("DELETE FROM app_events WHERE seq>?", (top - 1,))
    hub.store.execute("UPDATE sqlite_sequence SET seq=? WHERE name='app_events'", (top - 1,))
    with hub.store.tx() as t:
        regrown = [record_event(t, type_="tables.record.changed", app="tables", workspace_id=hub.p.a,
                                object_kind="table_record", object_id=f"rec_regrown{i}", data={"table_id": table})["id"]
                   for i in range(2)]
    hub.store.execute("UPDATE app_events SET created_at=? WHERE seq>?", (hub.clock() + 3600, top - 1))
    hub.service.tick()
    queued = {r["event_id"] for r in hub.rows(sid)}
    # the rows written into positions the cursor had passed were looked at, not passed over
    assert hub.store.one("SELECT cursor FROM suite_webhook_subscriptions WHERE id=?", (sid,))["cursor"] == top + 1
    assert not (set(regrown) & queued), "they are about records Alice cannot read (they do not exist), so none was queued"
    # a subscription disabled after the backup was taken stays disabled when the journal is replayed
    hub.store.execute("UPDATE suite_webhook_subscriptions SET status='active', disabled_reason=NULL WHERE id=?", (sid,))
    counts = security_journal.apply(hub.store, [{"at": now(), "kind": "webhook_disabled", "subscription_id": sid}])
    row = hub.store.one("SELECT status, disabled_reason FROM suite_webhook_subscriptions WHERE id=?", (sid,))
    assert (row["status"], row["disabled_reason"]) == ("disabled", "journal_replay") and counts["webhook_disabled"] == 1


def test_a_fault_while_deciding_holds_that_subscription_and_no_other(hub, monkeypatch):
    import dataclasses
    alice = grant(hub.api, "alice")["auth"]
    tables_rx, forms_rx = hub.receiver(), hub.receiver()
    tables_sub = hub.subscribe(alice, tables_rx.url, events=["tables.*"])["id"]
    hub.subscribe(alice, forms_rx.url, events=["forms.*"])
    table = a_table(hub.api, alice)
    form = m(hub.api, alice, "forms.form.create", {"title": "Fixture intake", "definition": {
        "fields": [{"id": "f_amount01", "type": "text", "label": "ID"}]}}, key="form")["output"]["id"]
    m(hub.api, alice, "forms.form.publish", {"form_id": form, "draft_rev": 1}, key="publish")
    registry = hub.api.caps.registry
    sound = registry.events("tables")

    def broken(user_id, workspace_id, events):
        raise RuntimeError("fixture fault in the application's check")
    monkeypatch.setitem(registry._feeds, "tables", dataclasses.replace(sound, visible=broken))
    before = hub.store.one("SELECT cursor FROM suite_webhook_subscriptions WHERE id=?", (tables_sub,))["cursor"]
    hub.service.tick()
    hub.service.tick()
    assert [b["object"]["id"] for b in forms_rx.bodies()] == [form], "the other subscription was not held up"
    assert tables_rx.received == [] and hub.rows(tables_sub) == []
    assert hub.store.one("SELECT cursor FROM suite_webhook_subscriptions WHERE id=?", (tables_sub,))["cursor"] == before
    monkeypatch.setitem(registry._feeds, "tables", sound)
    hub.service.tick()
    assert [b["object"]["id"] for b in tables_rx.bodies()] == [table]


def test_settled_deliveries_leave_the_log_after_thirty_days(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    a_table(hub.api, alice)
    hub.service.tick()
    assert hub.service.sweep() == 0 and len(hub.rows(sid)) == 1
    hub.clock.advance(31 * DAY)
    assert hub.service.sweep() == 1 and hub.rows(sid) == []


def test_housekeeping_disables_what_no_event_would_and_removes_what_nobody_can(hub):
    """A grant can end in a quiet workspace: nothing happens for the dispatcher to notice by. The
    hourly sweep asks anyway — of paused subscriptions too — and a subscription whose grant stayed
    ended for the log's thirty days is removed with its secret, since nobody may be left to do it."""
    g, other = grant(hub.api, "bob"), grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    active = hub.subscribe(g["auth"], rx.url, events=["tables.*"])["id"]
    paused = hub.subscribe(g["auth"], rx.url + "/paused", events=["tables.*"])["id"]
    kept = hub.subscribe(other, rx.url, events=["tables.*"])["id"]
    assert hub.api.post(f"{BASE}/{paused}/pause", headers=g["auth"]).json()["status"] == "paused"
    hub.service.sweep()
    assert {r["status"] for r in hub.store.all("SELECT status FROM suite_webhook_subscriptions")} == {"active", "paused"}
    _consent_withdrawn(hub, g)
    hub.service.tick()                                                   # no event: the dispatcher has nothing to look at
    assert hub.store.one("SELECT status FROM suite_webhook_subscriptions WHERE id=?", (active,))["status"] == "active"
    hub.service.sweep()
    rows = {r["id"]: r for r in hub.store.all("SELECT * FROM suite_webhook_subscriptions")}
    for sid in (active, paused):
        assert (rows[sid]["status"], rows[sid]["disabled_reason"]) == ("disabled", "authorization_revoked:grant_revoked")
        assert rows[sid]["deleted_at"] is None
    assert rows[kept]["status"] == "active"
    # a disabled one for any other reason is its holder's to deal with, and stays
    hub.service.disable(kept, "failing")
    hub.clock.advance(29 * DAY)
    hub.service.sweep()
    assert all(r["deleted_at"] is None for r in hub.store.all("SELECT deleted_at FROM suite_webhook_subscriptions"))
    hub.clock.advance(2 * DAY)
    hub.service.sweep()
    rows = {r["id"]: r for r in hub.store.all("SELECT * FROM suite_webhook_subscriptions")}
    for sid in (active, paused):
        assert rows[sid]["deleted_at"] is not None and rows[sid]["secret_prev_enc"] is None
        assert sealed.unseal(hub.store, "webhook-secret", rows[sid]["secret_enc"]) == ""
    assert rows[kept]["deleted_at"] is None and rows[kept]["disabled_reason"] == "failing"
    # the person authorises the client again: the removed ones are gone, and their addresses are free
    again = grant(hub.api, "bob")["auth"]
    assert hub.api.get(BASE, headers=again).json()["items"] == []
    assert hub.subscribe(again, rx.url, events=["tables.*"])["id"] not in (active, paused)


def test_pausing_what_is_paused_and_resuming_what_is_active_change_nothing(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    made = hub.subscribe(alice, rx.url, events=["tables.*"])
    sid = made["id"]
    table = a_table(hub.api, alice)                                      # an event the subscription has not looked at yet
    hub.clock.advance(5)
    same = hub.api.post(f"{BASE}/{sid}/resume", headers=alice).json()
    assert (same["version"], same["status_changed_at"], same["updated_at"]) == (1, made["status_changed_at"], made["updated_at"])
    hub.service.tick()
    assert rx.bodies()[0]["object"]["id"] == table, "resuming an active subscription does not move it to the present"
    paused = hub.api.post(f"{BASE}/{sid}/pause", headers=alice).json()
    assert (paused["status"], paused["version"]) == ("paused", 2) and paused["status_changed_at"] > made["status_changed_at"]
    hub.clock.advance(5)
    still = hub.api.post(f"{BASE}/{sid}/pause", headers=alice).json()
    assert (still["version"], still["status_changed_at"]) == (2, paused["status_changed_at"])
    resumed = hub.api.post(f"{BASE}/{sid}/resume", headers=alice).json()
    assert (resumed["status"], resumed["version"]) == ("active", 3) and resumed["status_changed_at"] > paused["status_changed_at"]


# --------------------------------------------------------------------------------- order, backlog, gaps
def objects(rx) -> list[str]:
    return [b["object"]["id"] for b in rx.bodies()]


def test_a_subscriptions_deliveries_go_out_in_the_order_they_were_queued(hub):
    """While a delivery waits for its retry nothing queued after it is sent — whichever worker looks,
    and although the later one became due first."""
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 503 if n == 1 else 200)
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    hub.service.tick()
    record = a_record(hub.api, alice, table, "r1")["output"]["id"]
    other = hub.W.Webhooks(hub.api.caps, clock=hub.clock, concurrency=1)
    hub.service.tick()
    assert other.deliver_due() == 0 and objects(rx) == [table], "the second waits behind the first, for every worker"
    head, later = hub.rows(sid)
    assert later["next_attempt_at"] < head["next_attempt_at"], "…although it is the one that is due"
    hub.clock.advance(7)
    assert other.deliver_due() == 2
    assert objects(rx) == [table, table, record]


def test_a_receiver_slower_than_the_round_holds_up_nobody_else(hub, monkeypatch):
    """One receiver takes longer to answer than a round lasts (it answers 2xx, so it never backs
    off). Its worker waits for it; every other subscription still gets turn after turn in that
    round, in its own order, instead of one delivery each and then the slow one's whole answer."""
    monkeypatch.setattr(hub.W, "ROUND_S", 2.0)
    hub.service.concurrency = 4
    alice = grant(hub.api, "alice")["auth"]
    slow, fast = hub.receiver(delay=2.5), hub.receiver()
    slow_sub = hub.subscribe(alice, slow.url, events=["tables.table.changed"])["id"]
    fast_sub = hub.subscribe(alice, fast.url, events=["tables.record.changed"])["id"]
    table = a_table(hub.api, alice)
    records = [a_record(hub.api, alice, table, f"r{i}")["output"]["id"] for i in range(6)]
    assert hub.service.fan_out() == 7
    started = time.monotonic()
    assert hub.service.deliver_due() == 7
    assert 2.5 <= time.monotonic() - started < hub.W.TIMEOUT_S, "one round: it ended when the slow answer came"
    assert objects(fast) == records and [r["status"] for r in hub.rows(fast_sub)] == ["delivered"] * 6
    assert objects(slow) == [table] and [r["status"] for r in hub.rows(slow_sub)] == ["delivered"]


def test_the_cursor_waits_while_the_queue_is_full(hub, monkeypatch):
    """A receiver slower than what it subscribed to costs a bounded number of rows; the rest stay in
    the log, behind the cursor, and follow in order when there is room. Nothing is lost."""
    monkeypatch.setattr(hub.W, "MAX_PENDING", 3)
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 500 if n == 1 else 200)
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    records = [a_record(hub.api, alice, table, f"r{i}")["output"]["id"] for i in range(7)]
    hub.service.tick()
    assert [r["status"] for r in hub.rows(sid)] == ["pending"] * 3
    held = hub.store.one("SELECT cursor, scanned_at FROM suite_webhook_subscriptions WHERE id=?", (sid,))
    hub.clock.advance(1)
    hub.service.tick()
    assert len(hub.rows(sid)) == 3, "full: nothing more is queued"
    assert hub.store.one("SELECT cursor, scanned_at FROM suite_webhook_subscriptions WHERE id=?", (sid,)) == held, \
        "…and it is not even looked at: a full queue does not take another subscription's turn"
    hub.clock.advance(7)
    for _ in range(4):
        hub.service.tick()
        assert sum(r["status"] == "pending" for r in hub.rows(sid)) <= 3
    assert objects(rx) == [table, table, *records]


def test_a_subscription_further_behind_than_the_feed_keeps_is_stopped_not_skipped(hub, monkeypatch):
    monkeypatch.setattr(hub.W, "MAX_PENDING", 1)
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 500)
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    a_record(hub.api, alice, table, "r1")
    hub.service.tick()
    hub.clock.advance(32 * DAY)
    rx.behaviour = lambda n: 200
    hub.service.tick()
    # what the feed would no longer serve is not pushed either, however it came to wait this long
    assert [(r["status"], r["last_error"]) for r in hub.rows(sid)] == [("dropped", "event_out_of_window")] and len(rx.received) == 1
    hub.service.tick()
    row = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert (row["status"], row["disabled_reason"]) == ("disabled", "backlog_expired")
    assert row["missed_events_cursor"] is None, "nothing a feed cursor that old could still be asked for"
    assert len(rx.received) == 1 and len(hub.rows(sid)) == 1


def test_where_pushing_stopped_is_a_feed_cursor_for_what_was_not_pushed(hub):
    """A pause or a disable drops what was waiting and delivers nothing from the time it was stopped.
    The subscription says where it stopped, as a cursor of the event feed: the gap, and nothing else."""
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 200 if n == 1 else 500)
    made = hub.subscribe(alice, rx.url, events=["tables.*"])
    sid = made["id"]
    assert made["missed_events_cursor"] is None

    def pulled(cursor):
        r = hub.api.get("/api/suite/v1/events", params={"cursor": cursor}, headers=alice)
        assert r.status_code == 200, r.text
        return [e["object"]["id"] for e in r.json()["items"]]

    table = a_table(hub.api, alice)
    hub.service.tick()                                                   # delivered
    r1 = a_record(hub.api, alice, table, "r1")["output"]["id"]
    hub.service.tick()                                                   # tried and refused: waits for its retry
    r2 = a_record(hub.api, alice, table, "r2")["output"]["id"]
    hub.service.tick()                                                   # queued behind it
    r3 = a_record(hub.api, alice, table, "r3")["output"]["id"]           # not yet looked at
    assert hub.api.get(f"{BASE}/{sid}", headers=alice).json()["missed_events_cursor"] is None
    paused = hub.api.post(f"{BASE}/{sid}/pause", headers=alice).json()
    r4 = a_record(hub.api, alice, table, "r4")["output"]["id"]           # while it is paused
    assert pulled(paused["missed_events_cursor"]) == [r1, r2, r3, r4]
    # stopped again while it is stopped (its grant ends, say): where it first stopped is what counts
    hub.service.disable(sid, "failing")
    assert pulled(hub.api.get(f"{BASE}/{sid}", headers=alice).json()["missed_events_cursor"]) == [r1, r2, r3, r4]
    # it is the caller's own feed cursor: nobody else's token opens it
    bob = grant(hub.api, "bob")["auth"]
    refused(hub.api.get("/api/suite/v1/events", params={"cursor": paused["missed_events_cursor"]}, headers=bob), 400, "invalid_cursor")
    rx.behaviour = lambda n: 200
    resumed = hub.api.post(f"{BASE}/{sid}/resume", headers=alice).json()
    r5 = a_record(hub.api, alice, table, "r5")["output"]["id"]
    hub.service.tick()
    assert objects(rx)[-1] == r5 and r4 not in objects(rx)
    assert pulled(resumed["missed_events_cursor"]) == [r1, r2, r3, r4, r5], "still there after the resume, to fill the gap from"
    # a receiver that answers 410 did not take that event either: the gap starts before it
    rx.behaviour = lambda n: 410
    r6 = a_record(hub.api, alice, table, "r6")["output"]["id"]
    hub.service.tick()
    gone = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert (gone["status"], gone["disabled_reason"]) == ("disabled", "gone") and pulled(gone["missed_events_cursor"]) == [r6]


def test_an_event_that_cannot_be_decided_holds_the_subscription_there_visibly(hub, monkeypatch):
    """An application's check that fails is not a refusal. The event is neither sent nor passed
    over: what came before it is delivered, the subscription waits at it and says since when."""
    import dataclasses
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    good = a_record(hub.api, alice, table, "good")["output"]["id"]
    bad = a_record(hub.api, alice, table, "bad")["output"]["id"]
    after = a_record(hub.api, alice, table, "after")["output"]["id"]
    registry = hub.api.caps.registry
    sound = registry.events("tables")

    def faulty(user_id, workspace_id, events):
        if any(e["object_id"] == bad for e in events):
            raise RuntimeError("fixture fault in the application's check")
        return sound.visible(user_id, workspace_id, events)
    monkeypatch.setitem(registry._feeds, "tables", dataclasses.replace(sound, visible=faulty))
    hub.service.tick()
    hub.service.tick()
    assert objects(rx) == [table, good]
    waiting = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert waiting["status"] == "active" and waiting["stalled_since"] is not None
    since = waiting["stalled_since"]
    hub.clock.advance(60)
    hub.service.tick()
    assert hub.api.get(f"{BASE}/{sid}", headers=alice).json()["stalled_since"] == since and objects(rx) == [table, good]
    monkeypatch.setitem(registry._feeds, "tables", sound)
    hub.service.tick()
    assert objects(rx) == [table, good, bad, after]
    assert hub.api.get(f"{BASE}/{sid}", headers=alice).json()["stalled_since"] is None


def test_another_workspaces_events_cost_a_subscription_nothing(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    before = hub.store.one("SELECT cursor, cursor_at, scanned_at, version FROM suite_webhook_subscriptions WHERE id=?", (sid,))
    elsewhere = bearer(hub.api, "oscar", _api.ALL_SCOPES, ws=hub.p.b)
    a_table(hub.api, elsewhere, "Fixture elsewhere", "elsewhere")
    assert hub.service.tick() == 0
    assert hub.store.one("SELECT cursor, cursor_at, scanned_at, version FROM suite_webhook_subscriptions WHERE id=?", (sid,)) == before
    table = a_table(hub.api, alice)
    assert hub.service.tick() == 1 and objects(rx) == [table]


# --------------------------------------------------------------------------------- whose fault it is
def test_what_is_this_servers_fault_is_not_an_attempt(hub, monkeypatch):
    """No resolver is free for the whole of the send's deadline. The delivery goes through the
    address check a real send makes and comes back as not tried: nothing counted, nobody blamed."""
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    with every_resolver_taken(hub, monkeypatch):
        hub.service.tick()
    row = hub.rows(sid)[0]
    assert (row["status"], row["attempts"], row["last_error"], row["last_code"]) == ("pending", 0, "resolver_busy", None)
    assert 0 < row["next_attempt_at"] - hub.clock() <= hub.W.LOCAL_RETRY_S
    health = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    assert (health["consecutive_failures"], health["last_failure_at"]) == (0, None) and rx.received == []
    assert health["status"] == "active" and hub.store.one(
        "SELECT failing_since FROM suite_webhook_subscriptions WHERE id=?", (sid,))["failing_since"] is None
    hub.clock.advance(hub.W.LOCAL_RETRY_S + 1)
    hub.service.tick()
    row = hub.rows(sid)[0]
    assert (row["status"], row["attempts"]) == ("delivered", 1) and objects(rx) == [table]
    # a ping that could not be tried says so at once, instead of waiting where nobody looks
    with every_resolver_taken(hub, monkeypatch):
        ping = hub.api.post(f"{BASE}/{sid}/ping", headers=alice).json()
    assert (ping["status"], ping["attempts"], ping["last_error"]) == ("failed", 0, "resolver_busy")
    assert len(rx.received) == 1


def test_a_busy_resolver_is_not_a_verdict_on_the_address(hub, monkeypatch):
    """Creating, moving and resuming check the address on the request's thread. With no resolver
    free the answer is "come back" — never that the URL is invalid — and nothing is changed."""
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    hub.api.post(f"{BASE}/{sid}/pause", headers=alice)
    before = hub.store.all("SELECT * FROM suite_webhook_subscriptions")
    with every_resolver_taken(hub, monkeypatch):
        for call, path, body in ((hub.api.post, BASE, {"url": rx.url + "/new", "events": ["tables.*"]}),
                                 (hub.api.patch, f"{BASE}/{sid}", {"url": rx.url + "/moved"}),
                                 (hub.api.post, f"{BASE}/{sid}/resume", None)):
            r = call(path, headers=alice, **({"json": body} if body is not None else {}))
            assert refused(r, 429, "destination_check_busy")["details"] == {"retry_after": hub.W.LOCAL_RETRY_S}
            assert r.headers["retry-after"] == str(hub.W.LOCAL_RETRY_S)
    assert hub.store.all("SELECT * FROM suite_webhook_subscriptions") == before
    # the same calls a moment later, with a resolver free
    assert hub.api.patch(f"{BASE}/{sid}", json={"url": rx.url + "/moved"}, headers=alice).json()["url"] == rx.url + "/moved"
    assert hub.api.post(f"{BASE}/{sid}/resume", headers=alice).json()["status"] == "active"


def test_one_host_gets_only_so_many_requests_a_minute_whoever_subscribes(hub, monkeypatch):
    """The address never proved it wants the traffic: however many subscriptions name it, this
    process sends it a bounded number of requests, and the rest wait without being failures."""
    monkeypatch.setattr(hub.W, "HOST_PER_MINUTE", 2)
    alice, bob = grant(hub.api, "alice")["auth"], grant(hub.api, "bob")["auth"]
    rx = hub.receiver()
    subs = [hub.subscribe(alice, rx.url, events=["tables.table.changed"])["id"],
            hub.subscribe(alice, rx.url + "/second", events=["tables.table.changed"])["id"],
            hub.subscribe(bob, rx.url, events=["tables.table.changed"])["id"]]
    a_table(hub.api, alice)
    hub.service.tick()
    hub.service.tick()
    rows = [hub.rows(sid)[0] for sid in subs]
    assert sorted(r["status"] for r in rows) == ["delivered", "delivered", "pending"] and len(rx.received) == 2
    waiting = next(r for r in rows if r["status"] == "pending")
    assert (waiting["attempts"], waiting["last_error"]) == (0, "destination_busy")
    assert 0 < waiting["next_attempt_at"] - hub.clock() <= 60
    assert all(hub.store.one("SELECT consecutive_failures AS n FROM suite_webhook_subscriptions WHERE id=?", (sid,))["n"] == 0
               for sid in subs)
    hub.service._hosts.clear()                                           # a minute later
    hub.clock.advance(61)
    hub.service.tick()
    assert len(rx.received) == 3 and all(hub.rows(sid)[0]["status"] == "delivered" for sid in subs)


def test_an_attempt_that_never_reports_back_is_still_an_attempt(hub, monkeypatch):
    """The process can die between claiming a delivery and recording what happened. The claim has
    already counted the attempt, so the delivery reaches the end of its schedule instead of being
    claimed for ever — and a fault inside the sender is recorded, not left to hold the queue."""
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.table.changed"])["id"]
    other = hub.subscribe(alice, rx.url + "/other", events=["tables.record.changed"])["id"]
    table = a_table(hub.api, alice)
    hub.service.fan_out()
    for n in range(1, hub.W.MAX_ATTEMPTS + 1):
        claimed = hub.service.claim_next()
        assert claimed["attempts"] == n and hub.service.claim_next() is None, "claimed: nobody else's, and counted"
        hub.clock.advance(hub.W.LEASE_S + 1)                              # …and never heard of again
    assert hub.service.claim_next() is None
    row = hub.rows(sid)[0]
    assert (row["status"], row["attempts"], row["last_error"]) == ("failed", hub.W.MAX_ATTEMPTS, "attempts_exhausted")
    assert rx.received == []
    # a fault inside the sender: the attempt is recorded, the round goes on, the receiver is not blamed
    hub.api.patch(f"{BASE}/{sid}", json={"events": ["tables.record.changed"]}, headers=alice)
    a_record(hub.api, alice, table, "r1")
    real = hub.service._send

    def broken(d):
        if d["subscription_id"] == sid:
            raise RuntimeError("fixture fault inside the sender")
        return real(d)
    monkeypatch.setattr(hub.service, "_send", broken)
    hub.service.tick()
    mine, theirs = hub.rows(sid)[-1], hub.rows(other)[-1]
    assert (mine["status"], mine["attempts"], mine["last_error"]) == ("pending", 1, "internal_error")
    assert theirs["status"] == "delivered" and len(rx.received) == 1
    assert hub.store.one("SELECT consecutive_failures AS n FROM suite_webhook_subscriptions WHERE id=?", (sid,))["n"] == 0


def test_settled_deliveries_are_removed_in_pieces(hub, monkeypatch):
    monkeypatch.setattr(hub.W, "SWEEP_CHUNK", 2)
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    table = a_table(hub.api, alice)
    for i in range(4):
        a_record(hub.api, alice, table, f"r{i}")
    hub.service.tick()
    assert hub.service.sweep() == 0 and len(hub.rows(sid)) == 5
    hub.clock.advance(31 * DAY)
    assert hub.service.sweep() == 5 and hub.rows(sid) == []


# --------------------------------------------------------------------------------- durable, audited, bounded
def test_what_was_taken_back_stays_taken_back_after_a_restore(hub, monkeypatch):
    """A pause, another address, another secret: each is journalled before it is made, so a database
    restored from before it brings the subscription back disabled, not as it was — and a change
    that cannot be journalled is not made."""
    from synwevo_core import journal as security_journal
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    made = hub.subscribe(alice, rx.url, events=["tables.*"])
    sid = made["id"]
    changes = ((hub.api.patch, f"{BASE}/{sid}", {"url": rx.url + "/moved"}), (hub.api.post, f"{BASE}/{sid}/rotate-secret", {}),
               (hub.api.post, f"{BASE}/{sid}/pause", None))
    # nothing below journals a description or a filter: they take nothing back
    assert hub.api.patch(f"{BASE}/{sid}", json={"description": "x", "events": ["tables.table.changed"]}, headers=alice).status_code == 200
    assert not [e for e in journal(hub.store) if e.get("subscription_id") == sid]

    def unavailable(*a, **k):
        raise OSError("fixture: the journal cannot be written")
    with monkeypatch.context() as broken:
        broken.setattr(security_journal, "record", unavailable)
        before = hub.store.one("SELECT * FROM suite_webhook_subscriptions WHERE id=?", (sid,))
        for call, path, body in (*changes, (hub.api.delete, f"{BASE}/{sid}", None)):
            refused(call(path, headers=alice, **({"json": body} if body is not None else {})), 503, "journal_unavailable")
        assert hub.store.one("SELECT * FROM suite_webhook_subscriptions WHERE id=?", (sid,)) == before
    for n, (call, path, body) in enumerate(changes, 1):
        assert call(path, headers=alice, **({"json": body} if body is not None else {})).status_code == 200
        assert len([e for e in journal(hub.store) if e["kind"] == "webhook_disabled" and e.get("subscription_id") == sid]) == n
    # the restore: the row as the backup had it, then the journal
    hub.store.execute("UPDATE suite_webhook_subscriptions SET status='active', url=?, secret_enc=? WHERE id=?",
                      (before["url"], before["secret_enc"], sid))
    security_journal.apply(hub.store, [e for e in journal(hub.store) if e.get("subscription_id") == sid])
    row = hub.store.one("SELECT status, disabled_reason FROM suite_webhook_subscriptions WHERE id=?", (sid,))
    assert (row["status"], row["disabled_reason"]) == ("disabled", "journal_replay")
    a_table(hub.api, alice)
    hub.service.tick()
    assert rx.received == []


def _as_the_backup_had_it(store, table: str, rows: list[dict]) -> None:
    """Fixture: put rows back the way a restored backup holds them."""
    for row in rows:
        store.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                      tuple(row.values()))


def test_a_stop_replayed_over_a_restore_is_a_stop_like_any_other(hub):
    """The backup was taken while a delivery waited for its retry; the subscription was paused
    afterwards. Restored and replayed, it is stopped as a live disable would have stopped it: what
    waited is dropped, where it stopped is a feed cursor, the version and the time of the change
    moved — and after the documented way back, nothing from before the stop is sent."""
    from synwevo_core import journal as security_journal
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver(lambda n: 200 if n == 1 else 500)
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    already = hub.subscribe(alice, rx.url + "/gone", events=["forms.*"])["id"]
    hub.service.disable(already, "gone")
    table = a_table(hub.api, alice)
    hub.service.tick()                                                   # delivered
    r1 = a_record(hub.api, alice, table, "r1")["output"]["id"]
    hub.service.tick()                                                   # refused: waits for its retry
    r2 = a_record(hub.api, alice, table, "r2")["output"]["id"]           # not yet looked at
    backup = hub.api.get(f"{BASE}/{sid}", headers=alice).json()
    held = {t: hub.store.all(f"SELECT * FROM {t}") for t in ("suite_webhook_subscriptions", "suite_webhook_deliveries")}
    assert (backup["status"], backup["version"]) == ("active", 1)
    assert [(r["status"], r["attempts"]) for r in hub.rows(sid)] == [("delivered", 1), ("pending", 1)]
    hub.api.post(f"{BASE}/{sid}/pause", headers=alice)                   # after the backup: journalled
    for name, rows in held.items():                                      # the restore…
        _as_the_backup_had_it(hub.store, name, rows)
    security_journal.apply(hub.store, [e for e in journal(hub.store) if e["kind"] == "webhook_disabled"])   # …and the replay
    r = hub.api.get(f"{BASE}/{sid}", headers=alice)
    stopped = r.json()
    assert (stopped["status"], stopped["disabled_reason"]) == ("disabled", "journal_replay")
    assert stopped["version"] == 2 and r.headers["etag"] == '"2"' and stopped["status_changed_at"] > backup["status_changed_at"]
    refused(hub.api.patch(f"{BASE}/{sid}", json={"description": "x"}, headers={**alice, "If-Match": '"1"'}), 409, "version_conflict")
    assert [(d["status"], d["last_error"], d["next_attempt_at"]) for d in hub.rows(sid)] == [
        ("delivered", None, None), ("dropped", "subscription_disabled:journal_replay", None)]
    assert hub.api.get(f"{BASE}/{sid}/deliveries", params={"status": "pending"}, headers=alice).json()["items"] == []
    # where it stopped: before the delivery that was waiting, so what was not pushed can be pulled
    pulled = hub.api.get("/api/suite/v1/events", params={"cursor": stopped["missed_events_cursor"]}, headers=alice).json()
    assert [e["object"]["id"] for e in pulled["items"]] == [r1, r2]
    # one that was disabled before the backup keeps its reason, its version and when it stopped
    was, now_ = next(x for x in held["suite_webhook_subscriptions"] if x["id"] == already), \
        hub.store.one("SELECT * FROM suite_webhook_subscriptions WHERE id=?", (already,))
    assert (now_["status"], now_["disabled_reason"], now_["version"], now_["status_changed_at"]) == (
        "disabled", "gone", was["version"], was["status_changed_at"])
    # the documented way back: rotate the secret, resume — and it starts from the present
    rx.behaviour = lambda n: 200
    assert hub.api.post(f"{BASE}/{sid}/rotate-secret", json={}, headers=alice).status_code == 200
    assert hub.api.post(f"{BASE}/{sid}/resume", headers=alice).json()["status"] == "active"
    hub.clock.advance(DAY)                                               # past any retry the backup had scheduled
    hub.service.tick()
    assert objects(rx) == [table, r1], "the delivery the backup held is not sent after the resume"
    r3 = a_record(hub.api, alice, table, "r3")["output"]["id"]
    hub.service.tick()
    assert objects(rx) == [table, r1, r3]


def test_a_delivery_found_waiting_on_a_stopped_subscription_is_dropped_in_the_documented_words(hub):
    """A redelivery checks the subscription's status before its own transaction, so one can land
    just after a stop. The dispatcher drops it unsent, and says why in the contract's closed words."""
    from synwevo_suite.common.machine_contract import contract
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    a_table(hub.api, alice)
    hub.service.tick()
    first = hub.rows(sid)[0]["id"]
    words = contract(hub.api.caps.registry)["components"]["schemas"]["WebhookDelivery"]["properties"]["last_error"]["description"]
    for stop, said, documented in (
            (lambda: hub.service.disable(sid, "gone"), "subscription_disabled:gone", "subscription_disabled:<reason>"),
            (lambda: hub.api.post(f"{BASE}/{sid}/pause", headers=alice), "subscription_paused", "subscription_paused")):
        late = hub.api.post(f"{BASE}/{sid}/deliveries/{first}/redeliver", headers=alice).json()["id"]
        stop()
        # fixture: the redelivery as it stands when it was queued a moment after the stop
        hub.store.execute("UPDATE suite_webhook_deliveries SET status='pending', last_error=NULL, next_attempt_at=? WHERE id=?",
                          (hub.clock(), late))
        hub.service.tick()
        row = hub.store.one("SELECT status, attempts, last_error FROM suite_webhook_deliveries WHERE id=?", (late,))
        assert (row["status"], row["attempts"], row["last_error"]) == ("dropped", 0, said) and documented in words
        hub.api.post(f"{BASE}/{sid}/resume", headers=alice)
    assert len(rx.received) == 1


def test_changes_to_where_a_persons_events_go_are_in_the_audit_trail(hub, monkeypatch):
    monkeypatch.setenv("SYNWEVO_SUITE_WEBHOOK_ALLOW_PRIVATE", "127.0.0.0/8")
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    made = hub.subscribe(alice, rx.url + "?token=fixture-path-secret", events=["tables.*"])
    sid = made["id"]
    hub.api.patch(f"{BASE}/{sid}", json={"description": "not an event for the trail"}, headers=alice)
    assert hub.api.patch(f"{BASE}/{sid}", json={"url": "http://127.0.0.2:9/elsewhere"}, headers=alice).status_code == 200
    rotated = hub.api.post(f"{BASE}/{sid}/rotate-secret", json={"overlap_s": 60}, headers=alice).json()
    hub.api.post(f"{BASE}/{sid}/pause", headers=alice)
    hub.api.post(f"{BASE}/{sid}/pause", headers=alice)                    # changes nothing: not recorded twice
    hub.api.post(f"{BASE}/{sid}/resume", headers=alice)
    hub.api.delete(f"{BASE}/{sid}", headers=alice)
    trail = hub.store.all("SELECT * FROM audit_events WHERE object_kind='webhook_subscription' ORDER BY rowid")
    assert [e["action"] for e in trail] == ["suite.webhook.create", "suite.webhook.update", "suite.webhook.rotate_secret",
                                            "suite.webhook.pause", "suite.webhook.resume", "suite.webhook.delete"]
    for e in trail:
        meta = json.loads(e["meta"])
        assert (e["actor_id"], e["workspace_id"], e["object_id"], e["result"]) == (hub.p.uid("alice"), hub.p.a, sid, "ok")
        assert meta["delegation"]["kind"] == "oauth_client" and meta["delegation"]["client_id"] == "crm-sync"
        assert e["request_id"]
    assert json.loads(trail[0]["meta"])["host"] == "127.0.0.1" and json.loads(trail[1]["meta"])["host"] == "127.0.0.2"
    assert json.loads(trail[1]["meta"])["previous_host"] == "127.0.0.1"
    written = json.dumps([dict(e) for e in trail])
    for private in (made["secret"], rotated["secret"], "fixture-path-secret", "/elsewhere"):
        assert private not in written


def test_only_so_many_addresses_are_checked_at_once(hub):
    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    sid = hub.subscribe(alice, rx.url, events=["tables.*"])["id"]
    hub.api.post(f"{BASE}/{sid}/pause", headers=alice)
    for _ in range(hub.W.VALIDATION_CONCURRENCY):
        assert hub.service._validations.acquire(blocking=False)
    for call, path, body in ((hub.api.post, BASE, {"url": rx.url + "/new", "events": ["tables.*"]}),
                             (hub.api.patch, f"{BASE}/{sid}", {"url": rx.url + "/moved"}), (hub.api.post, f"{BASE}/{sid}/resume", None)):
        r = call(path, headers=alice, **({"json": body} if body is not None else {}))
        assert refused(r, 429, "destination_check_busy") and r.headers["retry-after"] == "2"
    for _ in range(hub.W.VALIDATION_CONCURRENCY):
        hub.service._validations.release()
    assert hub.api.post(f"{BASE}/{sid}/resume", headers=alice).json()["status"] == "active"


# --------------------------------------------------------------------------------- the published contract
def test_the_contract_describes_subscriptions_deliveries_and_the_push_as_they_are(hub):
    from synwevo_suite.common.machine_contract import contract
    doc = contract(hub.api.caps.registry)
    schemas = doc["components"]["schemas"]

    def described(name: str, got: dict) -> dict:
        assert set(got) == set(schemas[name]["required"]) == set(schemas[name]["properties"]), name
        return got

    alice = grant(hub.api, "alice")["auth"]
    rx = hub.receiver()
    made = described("WebhookSubscriptionWithSecret", hub.subscribe(alice, rx.url, events=["tables.*"]))
    sid = made["id"]
    listing = described("WebhookSubscriptionList", hub.api.get(BASE, headers=alice).json())
    described("WebhookSubscription", listing["items"][0])
    described("WebhookSubscription", hub.api.get(f"{BASE}/{sid}", headers=alice).json())
    described("WebhookSubscription", hub.api.patch(f"{BASE}/{sid}", json={"description": "x"}, headers=alice).json())
    described("WebhookSubscriptionWithSecret", hub.api.post(f"{BASE}/{sid}/rotate-secret", json={}, headers=alice).json())
    a_table(hub.api, alice)
    hub.service.tick()
    described("WebhookDelivery", hub.api.post(f"{BASE}/{sid}/ping", headers=alice).json())
    page = described("WebhookDeliveryPage", hub.api.get(f"{BASE}/{sid}/deliveries", headers=alice).json())
    first = described("WebhookDelivery", page["items"][-1])
    described("WebhookDelivery", hub.api.get(f"{BASE}/{sid}/deliveries/{first['id']}", headers=alice).json())
    again = hub.api.post(f"{BASE}/{sid}/deliveries/{first['id']}/redeliver", headers=alice)
    assert again.status_code == 202 and described("WebhookDelivery", again.json())["redelivery_of"] == first["id"]
    paused = described("WebhookSubscription", hub.api.post(f"{BASE}/{sid}/pause", headers=alice).json())
    assert isinstance(paused["missed_events_cursor"], str)
    described("WebhookSubscription", hub.api.post(f"{BASE}/{sid}/resume", headers=alice).json())
    described("WebhookDeleted", hub.api.delete(f"{BASE}/{sid}", headers=alice).json())
    # what a receiver is sent: the documented body and the documented headers, for an event and for a ping
    push = doc["webhooks"]["event"]["post"]
    assert push["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/WebhookEvent"}
    for request in rx.received:
        body = described("WebhookEvent", json.loads(request["body"]))
        assert body["type"] in schemas["WebhookEvent"]["properties"]["type"]["enum"]
        assert {h["name"].lower() for h in push["parameters"]} <= set(request["headers"])
    assert {b["type"] for b in rx.bodies()} == {"tables.table.changed", "webhook.ping"}
    # the words a client may meet are all written down
    reasons = schemas["WebhookSubscription"]["properties"]["disabled_reason"]["description"]
    assert all(code in reasons for code in (*hub.W.ENDED, "gone", "failing", "backlog_expired", "journal_replay"))
    errors = schemas["WebhookDelivery"]["properties"]["last_error"]["description"]
    assert all(word in errors for word in (*hub.W.ATTEMPT_ERRORS, *hub.W.LOCAL_ERRORS))
    assert set(hub.W.DELIVERY_STATUSES) == set(schemas["WebhookDelivery"]["properties"]["status"]["enum"])
