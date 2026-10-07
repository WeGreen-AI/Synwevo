"""D18: actual attachment rows, shared bytes and Message's existing worker lifecycle.

All uploads/accounts use the isolated Message fixture. Clock changes only set expiry
boundaries; Event barriers schedule real Store transactions, never fake a successful send.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest
from fastapi.testclient import TestClient

from synwevo_core import messaging, messaging_bridge
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.messaging import Messaging
from synwevo_core.store import Store, now

M = "/api/messaging"
DATA = b"fixture attachment bytes shared by pending and sent rows\n"


@pytest.fixture()
def pending(org):
    c = org
    headers = c.sign_in("ann")
    dm = c.post(M + "/dm", headers=headers,
                json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]})
    assert dm.status_code == 201, dm.text
    cid = dm.json()["id"]
    upload = c.post(f"{M}/conversations/{cid}/attachments", headers=headers,
                    files={"file": ("fixture.txt", DATA, "text/plain")})
    assert upload.status_code == 201, upload.text
    return {"client": c, "store": c.ident.store, "svc": c.api.state.messaging,
            "uid": c.users["ann"]["id"], "cid": cid, "aid": upload.json()["id"], "headers": headers}


def attachment(w, aid=None):
    return w["store"].one("SELECT * FROM message_attachments WHERE id=?", (aid or w["aid"],))


def expire(w, aid=None, at=None):
    w["store"].execute("UPDATE message_attachments SET expires_at=? WHERE id=?", (at if at is not None else now()-1, aid or w["aid"]))


def another(w):
    # The service acceptance fixture has the same real bytes as the HTTP upload.
    return w["svc"].attach(w["cid"], w["uid"], filename="fixture.txt", data=DATA, scan={"status": "not_configured"})["id"]


def send(w, svc=None, **extra):
    return (svc or w["svc"]).send(w["cid"], w["uid"], body="", attachment_id=w["aid"], **extra)


def test_sweep_only_expired_unbound_rows_keeps_shared_bytes_and_is_idempotent(pending):
    w = pending
    sent, _ = send(w, client_id="fixture-sent")
    expire(w)  # Historic sent row with a stale deadline must still be retained.
    future, no_deadline, expired, rejected = [another(w) for _ in range(4)]
    w["store"].execute("UPDATE message_attachments SET expires_at=NULL WHERE id=?", (no_deadline,))
    for aid in (expired, rejected):
        expire(w, aid)
    w["store"].execute("UPDATE message_attachments SET status='rejected' WHERE id=?", (rejected,))
    before = attachment(w)
    assert w["svc"].sweep_unused_attachments() == 2
    assert attachment(w) == before
    assert attachment(w, future) and attachment(w, no_deadline)
    assert attachment(w, expired) is None and attachment(w, rejected) is None
    assert w["store"].get_blob(before["blob"]) == DATA
    assert w["svc"].attachment_for_download(w["aid"], w["client"].users["ben"]["id"])["message_id"] == sent["id"]
    replay, created = send(w, client_id="fixture-sent")
    assert not created and replay["id"] == sent["id"]
    assert w["svc"].sweep_unused_attachments() == 0


def test_sweep_is_bounded_and_drains_in_repeated_batches(pending):
    w = pending
    # 101 real service uploads, all sharing one blob; the per-tick contract is 100 rows.
    ids = [w["aid"], *(another(w) for _ in range(100))]
    w["store"].execute("UPDATE message_attachments SET expires_at=?", (now()-1,))
    assert w["svc"].sweep_unused_attachments() == 100
    assert w["store"].one("SELECT COUNT(*) AS n FROM message_attachments")["n"] == 1
    assert w["svc"].sweep_unused_attachments() == 1
    assert w["svc"].sweep_unused_attachments() == 0
    assert all(attachment(w, aid) is None for aid in ids)


def test_store_upgrade_adds_expiry_index_without_rewriting_attachments(pending):
    w = pending
    send(w, client_id="fixture-upgrade-sent")
    unused = another(w)
    expire(w, unused)
    # Reproduce the deployed schema, which has only the conversation index.
    w["store"].execute("DROP INDEX IF EXISTS ix_message_attachments_unused_expiry")
    before = w["store"].all("SELECT * FROM message_attachments ORDER BY id")
    version = w["store"].schema_version()

    upgraded = Store(w["store"].root)
    assert upgraded.schema_version() == version, "additive indexes do not rewrite the schema version"
    assert upgraded.all("SELECT * FROM message_attachments ORDER BY id") == before
    index = upgraded.one("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                         ("ix_message_attachments_unused_expiry",))
    assert index is not None, "current-version databases must receive the new additive index"
    plan = upgraded.all(
        "EXPLAIN QUERY PLAN SELECT id FROM message_attachments "
        "WHERE message_id IS NULL AND expires_at<=? ORDER BY expires_at,id LIMIT 100", (now(),))
    assert any("ix_message_attachments_unused_expiry" in row["detail"] for row in plan)
    assert not any("TEMP B-TREE" in row["detail"] for row in plan)

    reopened = Store(w["store"].root)
    assert reopened.all("SELECT * FROM message_attachments ORDER BY id") == before
    assert reopened.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    assert Messaging(reopened, Identity(reopened)).sweep_unused_attachments() == 1
    assert reopened.one("SELECT message_id FROM message_attachments WHERE id=?", (w["aid"],))["message_id"]


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_expiry_is_enforced_before_cleanup_for_send_download_and_inline(pending, monkeypatch, offset):
    w, c = pending, pending["client"]
    deadline = now()+100
    expire(w, at=deadline)
    monkeypatch.setattr(messaging, "now", lambda: deadline+offset)
    c.sign_in("ben")
    assert c.get(f"{M}/attachments/{w['aid']}").status_code == 404, "pending uploads belong only to their uploader"
    headers = c.sign_in("ann")
    for suffix in ("", "?inline=1"):
        response = c.get(f"{M}/attachments/{w['aid']}{suffix}")
        assert response.status_code == (200 if offset < 0 else 410), response.text
        if offset >= 0:
            assert response.json()["error"] == "attachment_expired"
    response = c.post(f"{M}/conversations/{w['cid']}/messages", headers=headers,
                      json={"body": "", "attachment_id": w["aid"]})
    assert response.status_code == (201 if offset < 0 else 410), response.text
    if offset >= 0:
        assert response.json()["error"] == "attachment_expired"
        assert not w["store"].one("SELECT 1 FROM messages WHERE attachment_id=?", (w["aid"],))
        assert w["svc"].sweep_unused_attachments() == 1
        assert c.get(f"{M}/attachments/{w['aid']}").status_code == 404


def test_send_rechecks_deadline_after_waiting_for_writer(pending, monkeypatch):
    w = pending
    deadline = now()+100
    expire(w, at=deadline)
    clock = [deadline-1]
    monkeypatch.setattr(messaging, "now", lambda: clock[0])
    other = Store(w["store"].root)
    svc = Messaging(other, Identity(other))
    waiting, release = Event(), Event()
    original = Store.tx

    @contextmanager
    def queued(store):
        if store is other:
            waiting.set()
            assert release.wait(5)
        with original(store) as tx:
            yield tx

    monkeypatch.setattr(Store, "tx", queued)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(send, w, svc)
        try:
            assert waiting.wait(5)
            clock[0] = deadline
        finally:
            release.set()
        with pytest.raises(ServiceError) as caught:
            future.result(timeout=5)
    assert (caught.value.status, caught.value.code) == (410, "attachment_expired")
    assert attachment(w)["message_id"] is None


def test_send_winning_the_writer_lock_cannot_lose_its_attachment_to_stale_cleanup(pending, monkeypatch):
    w = pending
    deadline = now()+100
    expire(w, at=deadline)
    clock = [deadline-1]
    monkeypatch.setattr(messaging, "now", lambda: clock[0])
    other = Store(w["store"].root)
    cleaner = Messaging(other, Identity(other))
    waiting, original = Event(), Store.tx

    @contextmanager
    def observe_writer(store):
        if store is other:
            waiting.set()
        with original(store) as tx:
            yield tx

    monkeypatch.setattr(Store, "tx", observe_writer)
    with ThreadPoolExecutor(max_workers=1) as pool:
        # A real binding is written while valid, but not committed when the sweep starts.
        with w["store"].tx():
            msg, created = send(w)
            assert created
            clock[0] = deadline+1
            sweeping = pool.submit(cleaner.sweep_unused_attachments)
            assert waiting.wait(5)
        assert sweeping.result(timeout=5) == 0
    assert attachment(w)["message_id"] == msg["id"]
    assert w["svc"].attachment_for_download(w["aid"], w["uid"])["blob"] == attachment(w)["blob"]


def test_cleanup_winning_the_writer_lock_refuses_send_without_a_dangling_message(pending, monkeypatch):
    w = pending
    expire(w)
    other = Store(w["store"].root)
    sender = Messaging(other, Identity(other))
    waiting, original = Event(), Store.tx

    @contextmanager
    def observe_writer(store):
        if store is other:
            waiting.set()
        with original(store) as tx:
            yield tx

    monkeypatch.setattr(Store, "tx", observe_writer)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with w["store"].tx():
            assert w["svc"].sweep_unused_attachments() == 1
            sending = pool.submit(send, w, sender)
            assert waiting.wait(5)
        with pytest.raises(ServiceError) as caught:
            sending.result(timeout=5)
    assert (caught.value.status, caught.value.code) == (404, "no_attachment")
    assert not w["store"].one("SELECT 1 FROM messages WHERE attachment_id=?", (w["aid"],))


def test_passive_worker_only_cleans_when_explicitly_driven(pending):
    w = pending
    worker = w["client"].api.state.bridge_worker
    expire(w)
    assert worker._thread is None and attachment(w)
    worker.sweep()
    assert attachment(w) is None
    worker.sweep()


def test_factory_worker_cleans_without_outbox_traffic_and_stops_with_lifespan(pending, monkeypatch):
    w = pending
    expire(w)
    monkeypatch.setattr(messaging_bridge.BridgeWorker, "POLL_S", 0.01)
    monkeypatch.setattr(messaging_bridge.BridgeWorker, "SWEEP_S", 0.02)
    from synwevo_messages.app import create_app
    baseline = tuple(messaging_bridge._wakers)
    api = create_app(str(w["store"].root), start_jobs=True)
    worker = api.state.bridge_worker
    with TestClient(api):
        until = time.monotonic()+2
        while attachment(w) and time.monotonic() < until:
            time.sleep(0.01)
        assert attachment(w) is None
        assert worker._thread.is_alive()
        # Maintenance must recur while idle, not just run once at startup.
        next_aid = another(w)
        expire(w, next_aid)
        until = time.monotonic()+2
        while attachment(w, next_aid) and time.monotonic() < until:
            time.sleep(0.01)
        assert attachment(w, next_aid) is None
    assert not worker._thread.is_alive()
    assert tuple(messaging_bridge._wakers) == baseline
    aid = another(w)
    expire(w, aid)
    worker.wake()
    worker.stop()
    assert attachment(w, aid), "a stopped worker cannot keep cleaning"
