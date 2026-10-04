"""The /api/v1 error contract and rate-limit signalling, at the platform gate.

* RFC 9457 problem details, opted into with `Accept: application/problem+json`; the existing
  envelope, byte for byte, for everyone else (the v1 promise does not allow removing it).
* `RateLimit-Policy` / `RateLimit` (draft-ietf-httpapi-ratelimit-headers) on every authenticated
  /api/v1 response, so a client can pace itself before it is refused; 429 keeps `Retry-After`.
* Unexpected failures, malformed parameters and unknown paths answer in the same contract —
  the unexpected-error handler used to return nothing, so a 500 had neither body nor request id.

Built through the deployment composer because the gate is shared by every role.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from synwevo_core.auth import Auth
from synwevo_core.store import Store


@pytest.fixture()
def v1(tmp_path, monkeypatch):
    d = tmp_path / "d"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
    monkeypatch.setenv("SYNWEVO_API_RATE_PER_MIN", "3")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    api = create_app(str(d), role="all", start_jobs=False)

    # Test fixture route: something that fails in a way nobody anticipated.
    def _boom():
        raise KeyError("internal detail that must not reach the caller")
    for path in ("/api/v1/__test_boom", "/api/__test_boom"):
        api.add_api_route(path, _boom, methods=["GET"])
        # ahead of the shell's catch-all, which would otherwise answer it
        api.router.routes.insert(0, api.router.routes.pop())

    tok, _ = Auth(Store(d)).create_key("fixture", "t-errors", ("read", "write"))
    c = TestClient(api, raise_server_exceptions=False)
    return {"c": c, "h": {"Authorization": f"Bearer {tok}"}, "api": api}


PROBLEM = {"Accept": "application/problem+json"}


# ---------------------------------------------------------------- RFC 9457
def test_problem_details_when_asked_for(v1):
    c, h = v1["c"], v1["h"]
    pid = c.post("/api/v1/projects", json={"name": "问题详情"}, headers=h).json()["id"]
    r = c.put(f"/api/v1/projects/{pid}/mappings", json={"mappings": "not-a-list"}, headers={**h, **PROBLEM})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "urn:synwevo:problem:invalid"
    assert body["status"] == 400 and body["title"] and body["detail"] == "mappings 必须是数组"
    assert body["errors"] == [{"detail": "mappings 必须是数组", "pointer": "#/mappings"}]
    assert body["code"] == "invalid"
    assert body["request_id"] == r.headers["X-Request-ID"]
    assert body["instance"] == f"urn:synwevo:request:{r.headers['X-Request-ID']}"


def test_the_envelope_is_unchanged_without_the_accept_header(v1):
    c, h = v1["c"], v1["h"]
    pid = c.post("/api/v1/projects", json={"name": "旧信封"}, headers=h).json()["id"]
    r = c.put(f"/api/v1/projects/{pid}/mappings", json={"mappings": "not-a-list"},
              headers={**h, "X-Request-ID": "snap-1"})
    assert r.status_code == 400 and r.headers["content-type"].startswith("application/json")
    assert r.json() == {"error": {"code": "invalid", "message": "mappings 必须是数组",
                                  "request_id": "snap-1", "field": "mappings"}}


def test_a_zero_q_is_not_asking(v1):
    c, h = v1["c"], v1["h"]
    r = c.get("/api/v1/projects/p_missing", headers={**h, "Accept": "application/problem+json;q=0, application/json"})
    assert r.status_code == 404 and "error" in r.json()


def test_the_browser_api_never_switches_format(v1):
    r = v1["c"].get("/api/projects/p_missing", headers=PROBLEM)
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert r.json()["error"] == "not_found"


def test_problem_details_for_a_refused_credential(v1):
    r = v1["c"].get("/api/v1/projects", headers=PROBLEM)
    assert r.status_code == 401 and r.json()["type"] == "urn:synwevo:problem:unauthorized"


# ---------------------------------------------------------------- malformed input, unknown paths
def test_a_malformed_parameter_is_a_400_in_the_envelope_not_a_bare_422(v1):
    c, h = v1["c"], v1["h"]
    r = c.get("/api/v1/projects?limit=100000", headers=h)
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid" and err["field"] == "limit" and err["request_id"]
    p = c.get("/api/v1/projects?limit=abc", headers={**h, **PROBLEM}).json()
    assert p["status"] == 400 and p["errors"][0]["pointer"] == "#/limit"


def test_an_unknown_v1_path_answers_in_the_envelope(v1):
    c, h = v1["c"], v1["h"]
    r = c.get("/api/v1/no-such-thing", headers=h)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert r.json()["error"]["request_id"] == r.headers["X-Request-ID"]
    r = c.delete("/api/v1/projects", headers=h)
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


# ---------------------------------------------------------------- unexpected failures
def test_an_unexpected_failure_keeps_the_contract_and_leaks_nothing(v1, capsys):
    c, h = v1["c"], v1["h"]
    r = c.get("/api/v1/__test_boom", headers={**h, "X-Request-ID": "boom-1"})
    assert r.status_code == 500
    assert r.headers["X-Request-ID"] == "boom-1"
    err = r.json()["error"]
    assert err["code"] == "internal_error" and err["request_id"] == "boom-1"
    assert "internal detail" not in r.text and "Traceback" not in r.text and "KeyError" not in r.text
    assert "RateLimit" in r.headers, "意外错误的响应同样要带限流头"
    # ...and the log has what the response withheld, under the same id.
    out = capsys.readouterr().out
    assert "request_id=boom-1" in out and "KeyError" in out

    p = c.get("/api/v1/__test_boom", headers={**h, **PROBLEM})
    assert p.status_code == 500 and p.headers["content-type"].startswith("application/problem+json")
    assert p.json()["type"] == "urn:synwevo:problem:internal_error" and "Traceback" not in p.text


def test_an_unexpected_failure_on_the_browser_api_has_a_body_too(v1):
    r = v1["c"].get("/api/__test_boom")
    assert r.status_code == 500 and r.json()["error"] == "internal_error"
    assert r.json()["request_id"] == r.headers["X-Request-ID"]
    assert "internal detail" not in r.text


# ---------------------------------------------------------------- rate limits
def test_every_response_says_how_much_budget_is_left(v1):
    c, h = v1["c"], v1["h"]
    seen = []
    started = time.monotonic()
    for _ in range(3):
        r = c.get("/api/v1/projects", headers=h)
        assert r.status_code == 200
        # Two policies since API-1: the credential's ("key", first, as before) and the workspace's.
        assert r.headers["RateLimit-Policy"] == '"key";q=3;w=60, "workspace";q=1200;w=60'
        seen.append(r.headers["RateLimit"])
    elapsed = time.monotonic() - started
    assert [s.split(";")[1] for s in seen] == ["r=2", "r=1", "r=0"], seen
    assert all(int(s.split(",")[0].split("t=")[1]) >= 1 for s in seen)
    # The workspace item: three requests spent from its (default) budget of 1200. The bucket
    # refills continuously (60 s / 1200 = one request every 50 ms), so on a slow runner part of
    # what was spent is already back — never more than the elapsed time allows, never all of it.
    ws = seen[-1].split(", ")[1]
    assert ws.startswith('"workspace";r='), seen
    left = int(ws.split(";")[1].removeprefix("r="))
    assert 1197 <= left <= min(1199, 1197 + int(elapsed / 0.05) + 1), (seen, elapsed)

    r = c.get("/api/v1/projects", headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["Retry-After"]) >= 1
    assert r.headers["RateLimit"].split(";")[1] == "r=0"
    assert r.headers["RateLimit-Policy"] == '"key";q=3;w=60, "workspace";q=1200;w=60'


def test_the_budget_is_per_credential(v1):
    c, h = v1["c"], v1["h"]
    for _ in range(3):
        c.get("/api/v1/projects", headers=h)
    other, _ = Auth(v1["api"].state.app.store).create_key("fixture-2", "t-errors", ("read",))
    r = c.get("/api/v1/projects", headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 200 and r.headers["RateLimit"].startswith('"key";r=2;')


def test_paths_outside_the_machine_api_carry_no_rate_limit_headers(v1):
    r = v1["c"].get("/api/projects")
    assert r.status_code == 200 and "RateLimit" not in r.headers and "RateLimit-Policy" not in r.headers


def test_a_refused_credential_is_not_counted_against_anyone(v1):
    r = v1["c"].get("/api/v1/projects", headers={"Authorization": "Bearer dw_nope_nope"})
    assert r.status_code == 401 and "RateLimit" not in r.headers
