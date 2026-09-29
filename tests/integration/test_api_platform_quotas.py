"""Traffic and capacity policies (API-1): an administrator's change really changes what callers get.

Every credential here is issued through the console's owner-approval flow (or, where a test needs
an unmanaged caller in another workspace, the legacy key store), every policy change goes through
the console's HTTP route with re-authentication and a reason, and every limit is observed on real
`/api/v1` calls — never by reading the policy table back.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from synwevo_core.auth import Auth
from synwevo_core.machine import SCOPE_READ, SCOPE_WRITE
from synwevo_server.app import create_app
from tests.integration.test_api_platform import (BASE, REASON, approve, issue, plane, post, reauth,  # noqa: F401
                                                  request_approval)


def put(c, path, body):
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    return c.put(path, json=body, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def api_key(e):
    """A managed access key for the fixture workspace: request → owner approval → issue."""
    row = request_approval(e, kind="api_key", scopes=[SCOPE_READ, SCOPE_WRITE])
    approve(e, row)
    out = issue(e, row)
    return out["key"], out["credential"]


def whoami(api, key, **headers):
    return TestClient(api).get("/api/v1/whoami", headers={"Authorization": f"Bearer {key}", **headers})


def set_policy(e, scope, body, *, base_version=0):
    reauth(e["admin"])
    r = put(e["admin"], f"{BASE}/quotas/{scope}", {"reason": REASON, "base_version": base_version, **body})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("role", ["owner", "stranger", "operator"])
def test_only_platform_admins_see_or_change_policies(plane, role):
    c = plane["clients"][role]
    reauth(c)
    assert c.get(f"{BASE}/quotas").status_code == 404
    assert c.get(f"{BASE}/quotas/effective?workspace_id={plane['ws']}").status_code == 404
    assert put(c, f"{BASE}/quotas/workspace/{plane['ws']}", {"reason": REASON, "base_version": 0,
                                                             "workspace_rpm": 1}).status_code == 404
    assert plane["store"].one("SELECT COUNT(*) AS n FROM api_quota_policies")["n"] == 0


def test_a_workspace_budget_holds_across_its_credentials_and_nowhere_else(plane):
    e = plane
    k1, _ = api_key(e)
    k2, _ = api_key(e)
    # An unmanaged caller in another workspace (legacy key store): a different budget entirely.
    other, _ = Auth(e["store"]).create_key("other-workspace fixture", e["other_ws"], ("read",))
    out = set_policy(e, f"workspace/{e['ws']}", {"workspace_rpm": 3})
    assert out["after"]["workspace_rpm"] == {"value": 3, "source": "workspace"}
    assert out["before"]["workspace_rpm"]["source"] == "deployment" and out["applies"] == "next_request"

    assert [whoami(e["api"], k).status_code for k in (k1, k1, k2)] == [200, 200, 200]
    r = whoami(e["api"], k2, **{"X-Request-ID": "quota-diag-1"})
    assert r.status_code == 429, r.text
    err = r.json()["error"]
    assert err["code"] == "rate_limited" and err["details"]["limit_scope"] == "workspace"
    assert err["details"]["limit"] == 3 and err["details"]["policy_source"] == "workspace"
    assert int(r.headers["Retry-After"]) >= 1
    assert '"workspace";q=3;w=60' in r.headers["RateLimit-Policy"]
    # The other workspace is untouched by this one's budget.
    assert whoami(e["api"], other).status_code == 200

    # Diagnosis by request ID finds the refusal and says why.
    found = e["admin"].get(f"{BASE}/calls?request_id=quota-diag-1").json()
    assert found["total"] == 1
    assert found["items"][0]["status"] == 429 and found["items"][0]["error_code"] == "rate_limited"
    assert found["items"][0]["workspace_id"] == e["ws"]
    # Refused before routing, yet recorded under the operation's template (never the raw path).
    assert found["items"][0]["route"] == "/api/v1/whoami" and found["items"][0]["method"] == "GET"
    assert e["admin"].get(f"{BASE}/calls?status=429&error_code=rate_limited").json()["total"] >= 1

    # Clearing the override restores the platform/deployment budget on the next request.
    set_policy(e, f"workspace/{e['ws']}", {"workspace_rpm": None}, base_version=1)
    assert whoami(e["api"], k2).status_code == 200


def test_rotation_does_not_buy_a_fresh_credential_budget(plane):
    e = plane
    key, cred = api_key(e)
    lineage = cred.get("lineage") or cred["id"]
    set_policy(e, f"credential/api_key:{lineage}", {"credential_rpm": 2})
    assert [whoami(e["api"], key).status_code for _ in range(2)] == [200, 200]
    reauth(e["admin"])
    r = post(e["admin"], f"{BASE}/credentials/api_key/{cred['id']}/rotate", {"reason": REASON, "grace_seconds": 600})
    assert r.status_code == 200, r.text
    new_key = r.json()["key"]
    refused = whoami(e["api"], new_key)
    assert refused.status_code == 429, refused.text
    assert refused.json()["error"]["details"]["limit_scope"] == "credential"
    assert refused.json()["error"]["details"]["policy_source"] == "credential"


def test_every_process_shares_one_budget(plane, tmp_path):
    """Two application instances over one database stand in for two worker processes: the budget
    is 3 in total, not 3 each (the per-process bucket this replaces allowed N × the limit)."""
    e = plane
    key, _ = api_key(e)
    set_policy(e, f"workspace/{e['ws']}", {"workspace_rpm": 3})
    second = create_app(str(e["store"].root), role="all", start_jobs=False)
    codes = [whoami(api, key).status_code for api in (e["api"], second, e["api"], second)]
    assert codes == [200, 200, 200, 429], codes


def test_stale_versions_and_bad_values_are_refused_and_audited(plane):
    e = plane
    first = set_policy(e, f"workspace/{e['ws']}", {"active_jobs": 4})
    assert first["version"] == 1
    reauth(e["admin"])
    # A second editor who read version 0 does not silently overwrite version 1.
    stale = put(e["admin"], f"{BASE}/quotas/workspace/{e['ws']}", {"reason": REASON, "base_version": 0, "active_jobs": 9})
    assert stale.status_code == 409 and stale.json()["error"] == "stale_policy"
    assert stale.json()["detail"]["version"] == 1 and stale.json()["detail"]["current"]["active_jobs"] == 4
    cases = [
        ({"active_jobs": 0}, "invalid_limit"), ({"active_jobs": 10**6}, "invalid_limit"),
        ({"workspace_rpm": "5"}, "invalid_limit"), ({"workspace_rpm": True}, "invalid_limit"),
        ({"surprise": 1}, "unknown_field"),
    ]
    for body, code in cases:
        r = put(e["admin"], f"{BASE}/quotas/workspace/{e['ws']}", {"reason": REASON, "base_version": 1, **body})
        assert r.status_code == 400 and r.json()["error"] == code, (body, r.text)
        if code == "invalid_limit":
            # The console words the range from these (refusals.test.mjs SAMPLE_DETAIL).
            field = next(iter(body))
            assert r.json()["detail"] == {"field": field, "min": 1, "max": 1000 if field == "active_jobs" else 1_000_000}
    key, cred = api_key(e)
    reauth(e["admin"])
    r = put(e["admin"], f"{BASE}/quotas/credential/api_key:{cred.get('lineage') or cred['id']}",
            {"reason": REASON, "base_version": 0, "active_jobs": 1})
    assert r.status_code == 400 and r.json()["error"] == "field_not_at_scope"
    r = put(e["admin"], f"{BASE}/quotas/workspace/w_does_not_exist", {"reason": REASON, "base_version": 0, "active_jobs": 1})
    assert r.status_code == 404
    r = put(e["admin"], f"{BASE}/quotas/workspace/{e['ws']}", {"reason": "short", "base_version": 1, "active_jobs": 2})
    assert r.status_code == 400 and r.json()["error"] == "missing_reason"
    # Every attempt is in the audit, refusals as denied; the policy is still the first change.
    rows = e["store"].all("SELECT result, meta FROM audit_events WHERE action='api_platform.quota.change' ORDER BY seq")
    results = [r["result"] for r in rows]
    assert results.count("ok") == 1 and results.count("denied") == 9, results
    assert e["admin"].get(f"{BASE}/quotas/effective?workspace_id={e['ws']}").json()["effective"]["active_jobs"] == \
        {"value": 4, "source": "workspace"}


def test_preview_changes_nothing_and_history_keeps_before_and_after(plane):
    e = plane
    r = post(e["admin"], f"{BASE}/quotas/preview/workspace/{e['ws']}", {"base_version": 0, "active_jobs": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True and body["after"]["active_jobs"] == {"value": 2, "source": "workspace"}
    assert body["impact"] == {"stops_running_work": False, "active_jobs_now": 0, "over_new_limit": 0}
    assert e["store"].one("SELECT COUNT(*) AS n FROM api_quota_policies")["n"] == 0
    set_policy(e, f"workspace/{e['ws']}", {"active_jobs": 2})
    set_policy(e, f"workspace/{e['ws']}", {"active_jobs": None, "workspace_rpm": 50}, base_version=1)
    hist = e["admin"].get(f"{BASE}/quotas/history/workspace/{e['ws']}").json()
    assert hist["total"] == 2
    newest, oldest = hist["items"]
    assert oldest["before"]["active_jobs"] is None and oldest["after"]["active_jobs"] == 2
    assert newest["before"]["active_jobs"] == 2 and newest["after"] == {"credential_rpm": None, "workspace_rpm": 50, "active_jobs": None}
    assert newest["actor_id"] == e["users"]["administrator"]["id"] and newest["reason"] == REASON
    listing = e["admin"].get(f"{BASE}/quotas").json()
    assert listing["overrides"]["total"] == 1 and listing["deployment_defaults"]["active_jobs"] >= 1


def test_the_effective_view_explains_each_value_and_live_usage(plane):
    e = plane
    key, cred = api_key(e)
    lineage = cred.get("lineage") or cred["id"]
    set_policy(e, "platform", {"workspace_rpm": 500})
    set_policy(e, f"credential/api_key:{lineage}", {"credential_rpm": 10})
    for _ in range(4):
        assert whoami(e["api"], key).status_code == 200
    view = e["admin"].get(f"{BASE}/quotas/effective?credential=api_key:{lineage}").json()
    assert view["workspace_id"] == e["ws"]
    assert view["effective"]["credential_rpm"] == {"value": 10, "source": "credential"}
    assert view["effective"]["workspace_rpm"] == {"value": 500, "source": "platform"}
    assert view["effective"]["active_jobs"]["source"] == "deployment"
    assert view["usage"]["key"]["limit"] == 10 and view["usage"]["key"]["remaining"] == 6
    assert view["usage"]["workspace"]["remaining"] == 496
    # A credential from another workspace is not "in" this one.
    r = e["admin"].get(f"{BASE}/quotas/effective?workspace_id={e['other_ws']}&credential=api_key:{lineage}")
    assert r.status_code == 400 and r.json()["error"] == "credential_not_in_workspace"


@pytest.mark.parametrize("cap", [1, 3, 7, 300, 1200, 100_000])
def test_the_arithmetic_admits_exactly_the_budget_at_any_size(cap):
    """Neither refuse the last request of a full budget nor report one fewer remaining than there
    is. Float seconds did both: a first request at 300/min reported 298, and at 100 000/min the
    last request of the budget was refused (drift from adding an interval to an epoch-sized float)."""
    from synwevo_core.api_quotas import _gcra
    t, tat, seen = 1_790_000_000 * 10**9, None, []       # a realistic epoch, in nanoseconds
    for _ in range(cap):
        ok, tat, remaining, reset, _ = _gcra(tat, t, cap)
        assert ok
        seen.append(remaining)
    assert seen[0] == cap - 1 and seen[-1] == 0 and seen == sorted(seen, reverse=True)
    ok, _, remaining, reset, wait = _gcra(tat, t, cap)
    assert not ok and remaining == 0 and 0 < wait <= 60 / cap + 1e-9
    assert reset == max(1, -(-int(wait * 1e9) // 10**9))
    # One emission interval later exactly one more request fits, and then none.
    later = t + (60 * 10**9) // cap
    ok, tat2, remaining, _, _ = _gcra(tat, later, cap)
    assert ok and remaining == 0
    assert not _gcra(tat2, later, cap)[0]
