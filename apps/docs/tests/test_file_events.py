"""Source-file verdicts as events (API-2): a script hears ready / needs_ocr / rejected / failed from the
event feed and webhooks instead of polling, and each event is written in the transaction that made
the verdict (synwevo_core.docs_file_events)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synwevo_core.auth import Auth

from .clamd_double import EICAR, ClamdDouble
from . import encrypted_fixtures as F

TESTDATA = Path(__file__).resolve().parents[3] / "testdata"


@pytest.fixture()
def double():
    d = ClamdDouble()
    yield d
    d.close()


@pytest.fixture()
def api(tmp_path, monkeypatch, double):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")
    monkeypatch.setenv("SYNWEVO_API_RATE_PER_MIN", "10000")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")   # fixture receiver on loopback
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    from synwevo_docs.api.app import create_app
    app = create_app(str(tmp_path / "d"), start_jobs=False)
    with TestClient(app) as c:
        store = app.state.app.store
        auth = Auth(store)
        rw, _ = auth.create_key("rw", "default", ("read", "write", "admin"))
        other, _ = auth.create_key("other", "tenant-other", ("read", "write"))
        c.h = {"Authorization": f"Bearer {rw}"}
        c.other = {"Authorization": f"Bearer {other}"}
        c.store = store
        c.pid = c.post("/api/v1/projects", headers=c.h, json={"name": "file events"}).json()["id"]
        yield c


def upload(c, name: str, data: bytes, until=("ready", "needs_ocr", "rejected", "error")) -> dict:
    src = c.post(f"/api/v1/projects/{c.pid}/files", headers=c.h, files={"file": (name, data)})
    assert src.status_code == 201, src.text
    sid = src.json()["id"]
    deadline = time.time() + 20
    while time.time() < deadline:
        row = c.get(f"/api/v1/files/{sid}", headers=c.h).json()
        if row["status"] in until:
            return row
        time.sleep(0.05)
    raise AssertionError(f"{name} stuck in {row['status']}")


def file_events(c, sid: str, headers=None) -> list[dict]:
    feed = c.get("/api/v1/events?types=file.ready,file.needs_ocr,file.rejected,file.failed", headers=headers or c.h)
    assert feed.status_code == 200, feed.text
    return [e for e in feed.json()["items"] if (e["data"] or {}).get("file_id") == sid]


def test_a_usable_file_is_announced_once_with_ids_and_no_content(api):
    row = upload(api, "clients.csv", b"Name,Amount\nAlpha,10\n")
    assert row["status"] == "ready"
    evs = file_events(api, row["id"])
    assert [e["type"] for e in evs] == ["file.ready"]
    e = evs[0]
    assert e["data"] == {"file_id": row["id"], "project_id": api.pid, "status": "ready", "reason": "clean",
                         "version": row["version"]}
    assert e["file_id"] == row["id"] and e["project_id"] == api.pid and e["job_id"] is None
    assert "clients.csv" not in str(e) and "Alpha" not in str(e)
    # Another workspace sees none of it.
    assert file_events(api, row["id"], api.other) == []


def test_a_scan_needs_ocr_and_is_ready_after_recognition(api):
    row = upload(api, "scan.pdf", (TESTDATA / "测试_扫描单据.pdf").read_bytes())
    assert row["status"] == "needs_ocr"
    assert [e["type"] for e in file_events(api, row["id"])] == ["file.needs_ocr"]
    assert api.post(f"/api/v1/files/{row['id']}/ocr", headers=api.h, json={}).status_code == 200
    assert [e["type"] for e in file_events(api, row["id"])] == ["file.needs_ocr", "file.ready"]


@pytest.mark.parametrize("name,data,reason", [
    ("eicar.txt", EICAR, "infected"),
    ("locked.zip", F.flag_encrypted(F.zip_bytes({"a.txt": b"TEST FIXTURE"})), "encrypted"),
])
def test_a_refused_file_is_announced_as_rejected_with_the_reason(api, name, data, reason):
    row = upload(api, name, data)
    evs = file_events(api, row["id"])
    assert [e["type"] for e in evs] == ["file.rejected"], evs
    assert evs[0]["data"]["reason"] == reason and evs[0]["data"]["status"] == row["status"]


def test_a_verdict_that_rolls_back_leaves_no_event(api):
    row = upload(api, "clients.csv", b"Name,Amount\nAlpha,10\n")
    before = api.store.one("SELECT COUNT(*) AS n FROM job_events")["n"]
    with pytest.raises(RuntimeError):
        with api.store.tx() as c:
            c.execute("UPDATE source_files SET status='needs_ocr' WHERE id=?", (row["id"],))
            raise RuntimeError("fixture: the writer fails after the status change")
    assert api.store.one("SELECT COUNT(*) AS n FROM job_events")["n"] == before
    # A write that does not change the status writes nothing either.
    api.store.execute("UPDATE source_files SET status='ready' WHERE id=?", (row["id"],))
    assert api.store.one("SELECT COUNT(*) AS n FROM job_events")["n"] == before


def test_subscriptions_and_the_feed_know_the_file_types(api):
    ok = api.post("/api/v1/webhooks/subscriptions", headers=api.h,
                  json={"url": "http://127.0.0.1:9/in", "events": ["file.*", "job.completed"]})
    assert ok.status_code == 201, ok.text
    bad = api.post("/api/v1/webhooks/subscriptions", headers=api.h,
                   json={"url": "http://127.0.0.1:9/in", "events": ["file.readyy"]})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_events"
    r = api.get("/api/v1/events?types=file.nope", headers=api.h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_events"


def test_the_subscription_body_is_the_minimal_notification(api):
    import json
    row = upload(api, "clients.csv", b"Name,Amount\nAlpha,10\n")
    ev = api.store.one("SELECT * FROM job_events WHERE type='file.ready' AND json_extract(data,'$.file_id')=?", (row["id"],))
    hooks = api.app.state.webhooks
    doc = json.loads(hooks.subscription_payload({"event_type": "file.ready"}, {"tenant": "default", "id": "sub_fixture"}, ev))
    assert doc["type"] == "file.ready" and doc["job"] is None and doc["id"] == ev["id"]
    assert doc["data"] == {"file_id": row["id"], "project_id": api.pid, "status": "ready", "reason": "clean",
                           "version": row["version"]}
    deployment_body = hooks.payload(ev["id"])[1]
    assert deployment_body["job"] is None and deployment_body["type"] == "file.ready"


def test_the_sdk_knows_the_server_catalogue():
    """The Python SDK validates subscription filters locally; its list drifted once already
    (job.planned was missing)."""
    from synwevo_docs import webhooks as W
    from synwevo_docs_sdk import validation as V
    assert set(V.EVENT_TYPES) == set(W.EVENT_TYPES)
    assert set(V.EVENT_FILTERS) == {"*", *W.EVENT_PREFIXES, *W.EVENT_TYPES}
