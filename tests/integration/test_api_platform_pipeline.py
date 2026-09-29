"""Owner-approved automation through real HTTP handlers, engines and loopback Webhooks.

Only accounts are fixture-provisioned. Every project, source, template, slot, mapping,
job, artifact, event and subscription is created through its production HTTP route.
The shared SDK acceptance kit supplies explicitly synthetic XLSX/DOCX inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import pytest

from tests.integration.platform_fixture import make_platform_admin
from fastapi.testclient import TestClient

from apps.docs.sdk.acceptance.make_kit import check_docx, make as make_kit
from apps.docs.tests.test_webhook_support import Receiver, signed_by
from synwevo_core.identity import Identity
from synwevo_core.machine import SCOPE_MANAGE, SCOPE_READ, SCOPE_WRITE
from synwevo_core.store import Store
from synwevo_server.app import create_app
from tests.integration.test_api_platform import (BASE, OWNER, PW, REASON, approve, issue,
                                                  post, reauth, request_approval, service_token)


@pytest.fixture()
def pipeline(tmp_path, monkeypatch):
    # Remove inherited deployment/OIDC/Webhook configuration before any worker starts.
    for name in tuple(os.environ):
        if name.startswith(("SYNWEVO_", "DOCWEAVE_")):
            monkeypatch.delenv(name)
    data = tmp_path / "isolated-pipeline"
    settings = {"DATA_DIR": str(data), "UI_AUTH": "session", "COOKIE_SECURE": "0",
        "TEST_INSTANCE": "1", "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
        "ISSUER": "http://testserver", "API_RATE_PER_MIN": "3000",
        "DOCS_WEBHOOK_ALLOW_PRIVATE": "127.0.0.1", "DOCS_WEBHOOK_ALLOW_HTTP": "1",
        "DOCS_WEBHOOK_URLS": ""}
    for name, value in settings.items():
        monkeypatch.setenv("SYNWEVO_" + name, value)
    api = create_app(str(data), role="all", start_jobs=True)
    store, users, clients = Store(data), {}, {}
    ident = Identity(store)
    try:
        for name, role in (("administrator", "admin"), ("owner", "none")):
            users[name] = ident.create_user(f"pipeline-{name}@example.test", PW, status="active")
            if role == "admin":
                users[name] = make_platform_admin(store, users[name])
            client = TestClient(api)
            response = post(client, "/api/auth/login", {"email": users[name]["email"], "password": PW})
            assert response.status_code == 200, response.text
            clients[name] = client
        ws = ident.workspaces_for(users["owner"]["id"])[0]["id"]
        yield {"api": api, "store": store, "ident": ident, "users": users,
               "admin": clients["administrator"], "owner": clients["owner"], "ws": ws}
    finally:
        for client in clients.values():
            client.close()
        api.state.webhooks.stop(wait=10)
        api.state.jobs.stop()
        api.state.mailer.stop()
        api.state.auth_sweeper.stop()
        # Join the real workers before pytest removes their temporary data directory.
        for service in (api.state.jobs, api.state.mailer, api.state.auth_sweeper):
            for attr in ("_thread", "_supervisor"):
                thread = getattr(service, attr, None)
                if thread:
                    thread.join(timeout=6)


def ok(response):
    assert response.status_code < 400, (response.status_code, response.text[:1000])
    return response.json()


def wait_for(fetch, settled, *, timeout=60):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fetch()
        if settled(last):
            return last
        time.sleep(.1)
    raise AssertionError(f"Real pipeline did not settle: {last}")


def granted(e, kind, scopes):
    approval = request_approval(e, kind, scopes)
    approve(e, approval)
    credential = issue(e, approval)
    token = credential["key"] if kind == "api_key" else ok(service_token(e, credential))["access_token"]
    return approval, credential, token


@pytest.mark.parametrize("kind", ["api_key", "service_account"])
def test_owner_approved_http_generation_checksum_webhook_rotation_revoke(pipeline, tmp_path, kind):
    e = pipeline
    expected = make_kit(tmp_path / "kit")
    fields = json.loads((tmp_path / "kit" / "fields.json").read_text())
    definition = json.loads((tmp_path / "kit" / "project.json").read_text())
    approval, credential, token = granted(e, kind, [SCOPE_READ, SCOPE_WRITE])
    c = TestClient(e["api"])
    headers = {"Authorization": f"Bearer {token}"}
    receiver = Receiver()  # Real socket bound only to 127.0.0.1; no external dispatch.
    try:
        who = ok(c.get("/api/v1/whoami", headers=headers))
        assert set(who["scopes"]) == ({"read", "write"} if kind == "api_key" else {SCOPE_READ, SCOPE_WRITE})
        assert who["tenant"] == e["ws"]
        assert ok(c.get("/api/v1/projects", headers=headers))["items"] == []
        assert ok(c.get("/api/v1/templates", headers=headers))["items"] == []

        # The generation credential cannot manage subscriptions. Obtain a separate,
        # explicitly approved service credential with just read+manage for this receiver.
        assert c.post("/api/v1/webhooks/subscriptions", headers=headers,
                      json={"url": receiver.url, "events": ["job.completed"]}).status_code == 403
        hook_approval, _, hook_token = granted(e, "service_account", [SCOPE_READ, SCOPE_MANAGE])
        hook_response = ok(post(e["admin"], BASE + "/request", {"reason": REASON,
            "credential": hook_token, "method": "POST", "path": "/api/v1/webhooks/subscriptions",
            "body": {"url": receiver.url, "description": "Isolated pipeline receiver", "events": ["job.completed"]}}))
        assert hook_response["status"] == 201
        subscription = hook_response["data"]

        project = ok(c.post("/api/v1/projects", headers=headers, json={"name": "Synthetic approved pipeline"}))
        pid = project["id"]
        source_bytes = (tmp_path / "kit" / "customers.xlsx").read_bytes()
        source_response = c.post(f"/api/v1/projects/{pid}/files",
            headers={**headers, "X-Content-SHA256": hashlib.sha256(source_bytes).hexdigest()},
            files={"file": ("customers.xlsx", source_bytes)})
        source = ok(source_response)
        source = wait_for(lambda: ok(c.get(f"/api/v1/files/{source['id']}", headers=headers)),
                          lambda row: row["status"] != "parsing")
        assert source["status"] == "ready"
        template_bytes = (tmp_path / "kit" / "template.docx").read_bytes()
        template = ok(c.post("/api/v1/templates", headers=headers,
            files={"file": ("template.docx", template_bytes)}))
        tid = template["id"]
        imported = ok(c.post(f"/api/v1/templates/{tid}/fields", headers=headers, json=fields))
        assert len(imported["created"]) == len(fields["fields"])
        ok(c.put(f"/api/v1/projects/{pid}/template", headers=headers, json={"template_id": tid}))
        rule = ok(c.put(f"/api/v1/projects/{pid}/rule", headers=headers, json={"rule": definition["rule"]}))
        assert rule["records"] == expected["samples"]
        slots = {f["name"]: f for f in ok(c.get(f"/api/v1/templates/{tid}", headers=headers))["fields"]}
        mappings = [{"type": mapping["type"], "sources": mapping["sources"],
                     "target": {"binding": slots[mapping["field"]]["binding"]}} for mapping in definition["mappings"]]
        saved = ok(c.put(f"/api/v1/projects/{pid}/mappings", headers=headers, json={"mappings": mappings}))
        assert len(saved["mappings"]) == len(mappings)
        job_body = {"formats": ["docx"], "naming": expected["naming"]}
        preflight = ok(c.post(f"/api/v1/projects/{pid}/preflight", headers=headers,
            json={**job_body, "simulate": True}))
        assert (preflight["records"], preflight["valid"], preflight["invalid"]) == (expected["samples"], expected["samples"], 0)
        assert preflight["simulation"]["checked"] > 0
        job_response = c.post(f"/api/v1/projects/{pid}/jobs", headers={**headers, "Idempotency-Key": "approved-pipeline-job"}, json=job_body)
        job = ok(job_response)
        assert job_response.status_code == 202
        jid = job["id"]
        job = wait_for(lambda: ok(c.get(f"/api/v1/jobs/{jid}", headers=headers)),
                       lambda row: row["status"] not in ("queued", "running", "cancelling"))
        assert job["status"] == "completed", job
        assert job["counts"]["documents"] == expected["samples"]

        page = ok(c.get(f"/api/v1/jobs/{jid}/artifacts", headers=headers, params={"limit": 5}))
        artifacts = list(page["items"])
        while page.get("next_cursor"):
            page = ok(c.get(f"/api/v1/jobs/{jid}/artifacts", headers=headers, params={"limit": 5, "cursor": page["next_cursor"]}))
            artifacts.extend(page["items"])
        assert len(artifacts) == expected["samples"]
        by_name = {a["filename"]: a for a in artifacts}
        checks = []
        for item in expected["items"]:
            artifact = by_name[item["file_base"] + ".docx"]
            downloaded = c.get(f"/api/v1/artifacts/{artifact['id']}/content", headers=headers)
            assert downloaded.status_code == 200
            digest = hashlib.sha256(downloaded.content).hexdigest()
            assert digest == artifact["sha256"] == downloaded.headers["X-Content-SHA256"]
            assert len(downloaded.content) == artifact["size"]
            assert not check_docx(downloaded.content, item, expected["forbidden_text"])
            checks.append({"artifact_id": artifact["id"], "sha256": digest, "content_verified": True})
        # Neither a platform admin browser cookie nor the restricted key can expand scope.
        assert e["admin"].get(f"/api/projects/{pid}").status_code == 404
        assert e["admin"].get(f"/api/v1/artifacts/{artifacts[0]['id']}/content").status_code == 401
        assert c.delete(f"/api/v1/projects/{pid}", headers=headers).status_code == 403

        hook_headers = {"Authorization": f"Bearer {hook_token}"}
        deliveries = wait_for(lambda: ok(c.get(f"/api/v1/webhooks/subscriptions/{subscription['id']}/deliveries", headers=hook_headers)),
            lambda page: any(d["status"] == "delivered" for d in page["items"]))
        assert len(receiver.received) == 1
        received = receiver.received[0]
        assert signed_by(subscription["secret"], received)
        event = json.loads(received["body"])
        assert event["type"] == "job.completed" and event["job"]["id"] == jid
        assert event["tenant"] == e["ws"] and event["job"]["counts"]["documents"] == expected["samples"]
        assert not any(word in received["body"] for word in (token.encode(), b"download_url", b"client_secret"))
        assert event["id"] in {d["webhook_id"] for d in deliveries["items"]}

        calls = ok(e["admin"].get(BASE + "/calls", params={"workspace_id": e["ws"], "limit": 100}))
        call_rows = list(calls["items"])
        call_page = calls
        while call_page.get("next_offset") is not None:
            call_page = ok(e["admin"].get(BASE + "/calls", params={"workspace_id": e["ws"],
                "limit": 100, "offset": call_page["next_offset"]}))
            call_rows.extend(call_page["items"])
        assert source_response.headers["X-Request-ID"] in {row["request_id"] for row in call_rows}
        assert any(row["route"] == "/api/v1/artifacts/{aid}/content" and row["status"] == 200 for row in call_rows)
        assert len(call_rows) > expected["samples"]
        assert token not in json.dumps(call_rows) and subscription["secret"] not in json.dumps(call_rows)

        cid = credential["credential"]["id"] if kind == "api_key" else credential["client_id"]
        rotated = ok(post(e["admin"], f"{BASE}/credentials/{kind}/{cid}/rotate", {"reason": REASON, "grace_seconds": 0}))
        assert c.get(f"/api/v1/artifacts/{artifacts[0]['id']}/content", headers=headers).status_code in (401, 403)
        if kind == "api_key":
            new_token = rotated["key"]
        else:
            assert service_token(e, credential).status_code == 401
            new_token = ok(service_token(e, {**credential, "client_secret": rotated["client_secret"]}))["access_token"]
        new_headers = {"Authorization": f"Bearer {new_token}"}
        final_download = c.get(f"/api/v1/artifacts/{artifacts[0]['id']}/content", headers=new_headers)
        assert final_download.status_code == 200 and hashlib.sha256(final_download.content).hexdigest() == artifacts[0]["sha256"]
        assert post(e["owner"], f"{OWNER}/{approval['id']}/revoke", {"reason": REASON}).status_code == 200
        assert c.get(f"/api/v1/artifacts/{artifacts[0]['id']}/content", headers=new_headers).status_code in (401, 403)
        assert post(e["owner"], f"{OWNER}/{hook_approval['id']}/revoke", {"reason": REASON}).status_code == 200

        (tmp_path / "pipeline-evidence.json").write_text(json.dumps({"kind": kind, "documents": checks,
            "job_id": jid, "event_id": event["id"], "webhook_signed_delivery": True,
            "machine_calls": calls["total"], "rotation_and_revocation": "verified",
            "business_setup": "HTTP only; no business-table writes"}, indent=2))
        assert not c.cookies, "Machine requests must not establish a browser session"
    finally:
        receiver.stop()
        c.close()
