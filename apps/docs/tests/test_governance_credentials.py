"""Machine API governance: usage, the caller's own credential, self-rotation and self-revocation.

These run against Synwevo Docs' own factory with legacy `dw_` keys; the platform-token variants
(service accounts, delegations) need the account service's client registry and live in
tests/integration/test_credentials_self_service.py.
"""
from __future__ import annotations

import time

import pytest

from synwevo_core import authority, journal
from synwevo_core.auth import MAX_ROTATION_GRACE_S, Auth


@pytest.fixture()
def keys(client):
    auth = Auth(client.app_state.app.store)
    admin, admin_row = auth.create_key("ops", "default", ("read", "write", "admin"), expires_in_days=30)
    ro, ro_row = auth.create_key("ro", "default", ("read",))
    other, other_row = auth.create_key("other", "tenant-b", ("read",))
    return {"auth": auth, "store": client.app_state.app.store,
            "admin": admin, "admin_row": admin_row, "ro": ro, "ro_row": ro_row,
            "other": other, "other_row": other_row}


def h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def err(r) -> str:
    return (r.json().get("error") or {}).get("code")


# ------------------------------------------------------------------ credentials/self
def test_self_describes_the_key_without_any_secret(client, keys):
    r = client.get("/api/v1/credentials/self", headers=h(keys["ro"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "api_key" and body["key_id"] == keys["ro_row"]["id"]
    assert body["workspace"] == "default" and body["scopes"] == ["read"]
    assert body["lineage"] == keys["ro_row"]["id"] and body["status"] == "active"
    assert body["rotation"]["self_service"] is False, "只读密钥不能自行轮换"
    text = r.text
    assert keys["ro"].split("_", 2)[2] not in text and "secret_hash" not in text
    row = keys["store"].one("SELECT secret_hash FROM api_keys WHERE id=?", (keys["ro_row"]["id"],))
    assert row["secret_hash"] not in text


def test_self_is_scoped_to_the_caller(client, keys):
    body = client.get("/api/v1/credentials/self", headers=h(keys["other"])).json()
    assert body["key_id"] == keys["other_row"]["id"] and body["workspace"] == "tenant-b"
    assert [k["key_id"] for k in body["lineage_keys"]] == [keys["other_row"]["id"]]


# ------------------------------------------------------------------ rotate
def test_rotation_needs_admin_or_manage(client, keys):
    r = client.post("/api/v1/credentials/self/rotate", headers=h(keys["ro"]), json={})
    assert r.status_code == 403 and err(r) == "forbidden"
    assert client.get("/api/v1/projects", headers=h(keys["ro"])).status_code == 200


def test_rotation_returns_the_new_key_once_and_keeps_the_lineage(client, keys):
    old = keys["admin"]
    r = client.post("/api/v1/credentials/self/rotate", headers=h(old), json={})
    assert r.status_code == 201, r.text
    body = r.json()
    new = body["key"]
    assert new.startswith("dw_") and body["key_id"] == new.split("_", 2)[1]
    assert body["lineage"] == keys["admin_row"]["id"]
    assert body["previous"] == {"key_id": keys["admin_row"]["id"], "status": "rotated",
                                "valid_until": body["previous"]["valid_until"]}
    # the holder's rotation cannot extend the key: same absolute expiry
    assert body["expires_at"] == keys["admin_row"]["expires_at"]
    gone = client.get("/api/v1/projects", headers=h(old))
    assert gone.status_code == 401 and err(gone) == "key_rotated"
    assert client.get("/api/v1/projects", headers=h(new)).status_code == 200
    # The secret is not retrievable afterwards.
    me = client.get("/api/v1/credentials/self", headers=h(new))
    assert new.split("_", 2)[2] not in me.text
    assert me.json()["lineage"] == keys["admin_row"]["id"]
    assert [k["status"] for k in me.json()["lineage_keys"]] == ["rotated", "active"]


def test_rotation_with_a_bounded_grace(client, keys):
    old = keys["admin"]
    r = client.post("/api/v1/credentials/self/rotate", headers=h(old), json={"grace_minutes": 5})
    assert r.status_code == 201, r.text
    prev = r.json()["previous"]
    assert prev["status"] == "grace" and prev["valid_until"] <= time.time() + 5 * 60 + 1
    assert client.get("/api/v1/projects", headers=h(old)).status_code == 200
    again = client.post("/api/v1/credentials/self/rotate", headers=h(old), json={})
    assert again.status_code == 409 and err(again) == "already_rotated", "一把密钥只有一个继任者"


@pytest.mark.parametrize("grace", [-1, MAX_ROTATION_GRACE_S // 60 + 1, "5", True])
def test_rotation_refuses_a_bad_grace(client, keys, grace):
    r = client.post("/api/v1/credentials/self/rotate", headers=h(keys["admin"]), json={"grace_minutes": grace})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["field"] == "grace_minutes"
    assert client.get("/api/v1/projects", headers=h(keys["admin"])).status_code == 200


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_rotation_refuses_a_non_finite_grace(client, keys, literal):
    """Python's JSON parser accepts these non-standard literals. NaN passed the range check and
    left the old key in grace for ever: valid_until null, still accepted ten years later."""
    r = client.post("/api/v1/credentials/self/rotate",
                    headers={**h(keys["admin"]), "Content-Type": "application/json"},
                    content=('{"grace_minutes": %s}' % literal).encode())
    assert r.status_code == 400, r.text
    assert r.json()["error"]["field"] == "grace_minutes" and err(r) == "bad_grace"
    row = keys["auth"].get_key(keys["admin_row"]["id"])
    assert row["status"] == "active" and row["replaced_by"] is None
    assert client.get("/api/v1/projects", headers=h(keys["admin"])).status_code == 200


def test_self_rotation_is_journalled(client, keys):
    before = time.time()
    r = client.post("/api/v1/credentials/self/rotate", headers=h(keys["admin"]), json={"grace_minutes": 5})
    assert r.status_code == 201, r.text
    entries = [e for e in journal.read(keys["store"].root, since=before - 1) if e["kind"] == "api_key_superseded"]
    assert [e["key_id"] for e in entries] == [keys["admin_row"]["id"]]


def test_rotation_is_audited_without_secrets(client, keys):
    r = client.post("/api/v1/credentials/self/rotate", headers=h(keys["admin"]), json={})
    new = r.json()["key"]
    row = keys["store"].one("SELECT * FROM audit_events WHERE action='api.credential.rotate'")
    assert row and row["actor_kind"] == "machine" and row["actor_id"] == f"api:{keys['admin_row']['id']}"
    assert row["workspace_id"] == "default" and row["request_id"]
    for secret in (keys["admin"].split("_", 2)[2], new.split("_", 2)[2]):
        assert secret not in (row["meta"] or "")


# ------------------------------------------------------------------ revoke
def test_any_scope_may_revoke_itself_and_the_lineage_ends(client, keys):
    auth = keys["auth"]
    # a rotated predecessor inside its grace belongs to the same lineage
    _, new_row, _ = auth.rotate_key(keys["ro_row"]["id"], grace_s=600)
    assert client.get("/api/v1/projects", headers=h(keys["ro"])).status_code == 200
    r = client.post("/api/v1/credentials/self/revoke", headers=h(keys["ro"]))
    assert r.status_code == 200, r.text
    assert r.json()["keys_revoked"] == 2 and r.json()["lineage"] == keys["ro_row"]["id"]
    after = client.get("/api/v1/projects", headers=h(keys["ro"]))
    assert after.status_code == 401 and err(after) == "key_revoked"
    assert auth.get_key(new_row["id"])["status"] == "revoked"
    # deferred work of that lineage loses its authority
    desc = {"kind": "api_key", "key_id": keys["ro_row"]["id"], "tenant": "default"}
    assert authority.check(keys["store"], desc, need="read", tenant="default") == (False, "credential_revoked")
    # other keys are untouched
    assert client.get("/api/v1/projects", headers=h(keys["admin"])).status_code == 200


def test_revocation_is_journalled_and_audited(client, keys):
    before = time.time()
    r = client.post("/api/v1/credentials/self/revoke", headers=h(keys["other"]))
    assert r.json()["queued_work"] == "halted"
    entries = [e for e in journal.read(keys["store"].root, since=before - 1) if e["kind"] == "api_key_revoked"]
    assert [e["key_id"] for e in entries] == [keys["other_row"]["id"]]
    row = keys["store"].one("SELECT * FROM audit_events WHERE action='api.credential.revoke'")
    assert row["result"] == "ok" and row["workspace_id"] == "tenant-b"


@pytest.mark.parametrize("hops, expected", [("1", "10.0.0.2"), ("2", "203.0.113.9")])
def test_governance_audit_rows_record_the_client_behind_a_trusted_proxy(client, keys, monkeypatch, hops, expected):
    """The same address rule as every other audit row (`http.client_ip`): behind the
    recommended reverse proxy the socket peer is the proxy."""
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", hops)
    monkeypatch.delenv("DOCWEAVE_TRUST_PROXY", raising=False)
    client.post("/api/v1/credentials/self/rotate",
                headers={**h(keys["admin"]), "X-Forwarded-For": "203.0.113.9, 10.0.0.2"}, json={})
    row = keys["store"].one("SELECT ip FROM audit_events WHERE action='api.credential.rotate'")
    assert row["ip"] == expected
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "0")
    client.post("/api/v1/credentials/self/revoke", headers={**h(keys["ro"]), "X-Forwarded-For": "198.51.100.1"})
    row = keys["store"].one("SELECT ip FROM audit_events WHERE action='api.credential.revoke'")
    assert row["ip"] != "198.51.100.1", "没有声明可信代理时不采信 X-Forwarded-For"


# ------------------------------------------------------------------ usage
def test_usage_reports_the_callers_workspace_and_the_limits(client, keys):
    jobs = client.app_state.jobs
    jobs.stop()                       # the raw rows below are a fixture; the runner must not claim them
    store = keys["store"]
    t = time.time()
    for jid, tenant in (("j_fixture_1", "default"), ("j_fixture_2", "default"), ("j_fixture_3", "tenant-b")):
        store.execute("INSERT INTO jobs (id, project_id, status, created_at, tenant) VALUES (?,?,?,?,?)",
                      (jid, "p_fixture", "queued", t, tenant))
        store.execute("INSERT INTO job_items (id, job_id, seq, status) VALUES (?,?,1,'pending')", (jid + "_i", jid))

    r = client.get("/api/v1/usage", headers=h(keys["ro"]))
    assert r.status_code == 200, r.text
    body = r.json()
    want = jobs.usage("default")
    assert body["workspace"] == "default"
    for k in ("active_jobs", "queued_jobs", "running_jobs", "pending_items"):
        assert k in body, k
    for k, v in want.items():
        if k != "limits":
            assert body[k] == v, k
    assert body["queued_jobs"] == 2 and body["active_jobs"] >= 2
    assert body["limits"]["requests_per_minute"] > 0 and body["limits"]["max_file_mb"] > 0
    assert body["limits"]["max_records_per_job"] > 0
    for k, v in (want.get("limits") or {}).items():
        assert body["limits"][k] == v

    other = client.get("/api/v1/usage", headers=h(keys["other"])).json()
    assert other["workspace"] == "tenant-b" and other["queued_jobs"] == 1


def test_usage_needs_a_credential(client):
    assert client.get("/api/v1/usage").status_code == 401


def test_usage_reports_the_rate_the_gate_enforces(client, keys):
    """The gate reads SYNWEVO_API_RATE_PER_MIN when the app is built; the module constant was
    fixed at import. /usage reported the constant (300) while the gate enforced another number."""
    buckets = client.app_state.rate_buckets
    buckets.cap = 7
    body = client.get("/api/v1/usage", headers=h(keys["ro"])).json()
    assert body["limits"]["requests_per_minute"] == 7
