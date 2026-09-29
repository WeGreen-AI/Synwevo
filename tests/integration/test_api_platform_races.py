"""Deterministic interleavings around real authority reads and issuance transactions.

The barriers pause production checks; they never substitute an authorization result.
Identity role changes are synthetic fixture administration, not document fixtures.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
import asyncio
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from synwevo_core import api_platform as authority
from synwevo_core.errors import ServiceError
from synwevo_core.machine import SCOPE_READ, SCOPE_WRITE
from synwevo_core.oidc import Clients, Grants, docs_resource
from synwevo_core.apps import DOCS_APP_ID
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import Sessions
from synwevo_docs import control_plane
import httpx
from synwevo_identity import api_platform as routes
from tests.integration.platform_fixture import end_platform_grants
from tests.integration.test_api_platform import (
    BASE, OWNER, REASON, approve, call, issue, plane, post, request_approval,
    service_token,
)


def authority_state(e):
    """Compare authoritative writes without printing credential material on failure."""
    tables = ("api_platform_approvals", "api_platform_bindings", "api_keys", "oauth_clients", "oauth_tokens")
    rows = {table: e["store"].all(f"SELECT * FROM {table} ORDER BY rowid") for table in tables}
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("kind", ["api_key", "service_account"])
def test_admin_demoted_after_admission_cannot_finish_issuance(plane, monkeypatch, kind):
    e = plane
    approval = request_approval(e, kind)
    approve(e, approval)
    admitted, resume = Event(), Event()
    actual = routes.require_manage
    checks = []

    def pause_after_admission(ident, uid):
        result = actual(ident, uid)
        checks.append(uid)
        if len(checks) == 1:
            admitted.set()
            assert resume.wait(5), "issuance barrier was not released"
        return result

    monkeypatch.setattr(routes, "require_manage", pause_after_admission)
    before = {table: e["store"].one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
              for table in ("api_keys", "oauth_clients", "api_platform_bindings")}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(post, e["admin"], f"{BASE}/approvals/{approval['id']}/issue", {"reason": REASON})
        try:
            assert admitted.wait(5)
            end_platform_grants(e["store"], e["users"]["administrator"])
        finally:
            resume.set()
        assert future.result(timeout=5).status_code == 404
    for table, count in before.items():
        assert e["store"].one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == count
    assert authority.Approvals(e["store"]).get(approval["id"])["consumed_at"] is None


@pytest.mark.parametrize("kind", ["api_key", "service_account"])
def test_simultaneous_consumption_creates_exactly_one_bound_credential(plane, kind):
    e = plane
    approval = request_approval(e, kind)
    approve(e, approval)
    start = Event()

    def consume():
        with TestClient(e["api"]) as c:
            c.cookies.update(e["admin"].cookies)
            assert start.wait(5)
            return post(c, f"{BASE}/approvals/{approval['id']}/issue", {"reason": REASON})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume) for _ in range(2)]
        start.set()
        results = [f.result(timeout=10) for f in futures]
    assert sorted(r.status_code for r in results) == [201, 409]
    bindings = e["store"].all("SELECT * FROM api_platform_bindings WHERE approval_id=?", (approval["id"],))
    assert len(bindings) == 1
    table, col = ("api_keys", "tenant") if kind == "api_key" else ("oauth_clients", "workspace_id")
    assert e["store"].one(f"SELECT COUNT(*) AS n FROM {table} WHERE {col}=?", (e["ws"],))["n"] == 1


def test_owner_revocation_serializes_with_service_token_issuance(plane, monkeypatch):
    e = plane
    approval = request_approval(e)
    approve(e, approval)
    credential = issue(e, approval)
    checked, resume, revoking = Event(), Event(), Event()
    actual = authority.approved_service_scopes

    def pause_after_current_approval(store, client, scope):
        result = actual(store, client, scope)
        assert store._c().in_transaction, "approval read and token insertion must share the write transaction"
        checked.set()
        assert resume.wait(5)
        return result

    def revoke():
        revoking.set()
        return post(e["owner"], f"{OWNER}/{approval['id']}/revoke", {"reason": REASON})

    monkeypatch.setattr(authority, "approved_service_scopes", pause_after_current_approval)
    with ThreadPoolExecutor(max_workers=2) as pool:
        mint = pool.submit(service_token, e, credential)
        try:
            assert checked.wait(5)
            revoked = pool.submit(revoke)
            assert revoking.wait(5)
            with pytest.raises(TimeoutError):
                revoked.result(timeout=.2)
        finally:
            resume.set()
        minted = mint.result(timeout=10)
        assert minted.status_code == 200, minted.text
        assert revoked.result(timeout=10).status_code == 200
    token = minted.json()["access_token"]
    assert call(e, token).status_code in (401, 403)
    assert service_token(e, credential).status_code in (401, 403)
    assert e["store"].one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE client_id=? AND revoked_at IS NULL",
                           (credential["client_id"],))["n"] == 0


@pytest.mark.parametrize("change", ["disabled", "kind", "scope", "approval", "owner"])
def test_service_issuance_rechecks_current_authority_not_stale_client(plane, change):
    e = plane
    approval = request_approval(e)
    approve(e, approval)
    credential = issue(e, approval)
    cid = credential["client_id"]
    stale = Clients(e["store"]).get(cid)
    if change == "disabled":
        e["store"].execute("UPDATE oauth_clients SET disabled_at=1 WHERE client_id=?", (cid,))
    elif change == "kind":
        e["store"].execute("UPDATE oauth_clients SET kind='user' WHERE client_id=?", (cid,))
    elif change == "scope":
        e["store"].execute("UPDATE oauth_clients SET api_scopes=? WHERE client_id=?", (SCOPE_READ, cid))
    elif change == "approval":
        assert post(e["owner"], f"{OWNER}/{approval['id']}/revoke", {"reason": REASON}).status_code == 200
    else:
        e["store"].execute("UPDATE memberships SET role='admin' WHERE workspace_id=? AND user_id=?",
                           (e["ws"], e["users"]["owner"]["id"]))
    with pytest.raises(ServiceError):
        Grants(e["store"]).issue_for_service(client=stale, scope=f"{SCOPE_READ} {SCOPE_WRITE}", audience=docs_resource())
    assert e["store"].one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE client_id=?", (cid,))["n"] == 0


@pytest.mark.parametrize("managed", [False, True], ids=["historical", "approval-bound"])
@pytest.mark.parametrize("change", ["none", "audience", "workspace", "organization"])
def test_current_resource_authority_at_issuance_preserves_legal_legacy_clients(plane, managed, change):
    e = dict(plane)
    org = Organizations(e["store"], e["ident"]).create(e["users"]["owner"]["id"],
        "Synthetic issuance organization", "Synthetic issuance workspace", "stale-client-resource-race")
    e["ws"] = org["workspace"]["id"]
    if managed:
        approval = request_approval(e)
        approve(e, approval)
        cid = issue(e, approval)["client_id"]
    else:
        # Supported historical registration is deliberately not reclassified or bound.
        client, _ = Clients(e["store"]).register(client_id="legacy-synthetic-service", app_id=DOCS_APP_ID,
            name="Synthetic existing service", redirect_uris=[], confidential=True, kind="service", scopes="",
            workspace_id=e["ws"], api_scopes=SCOPE_READ, audiences=[docs_resource()])
        cid = client["client_id"]
    stale = Clients(e["store"]).get(cid)
    if change == "audience":
        e["store"].execute("UPDATE oauth_clients SET audiences='[]' WHERE client_id=?", (cid,))
    elif change == "workspace":
        e["store"].execute("UPDATE workspaces SET status='frozen' WHERE id=?", (e["ws"],))
    elif change == "organization":
        e["store"].execute("UPDATE organizations SET status='frozen' WHERE id=?", (org["organization"]["id"],))
    grants = Grants(e["store"])
    if change == "none":
        result = grants.issue_for_service(client=stale, scope=SCOPE_READ, audience=docs_resource())
        row = grants.load_access(result["access_token"])
        assert row["workspace_id"] == e["ws"] and row["audience"] == docs_resource()
        assert call(e, result["access_token"]).status_code == 200
        assert bool(authority.Approvals(e["store"]).binding("service_account", cid)) is managed
    else:
        with pytest.raises(ServiceError):
            grants.issue_for_service(client=stale, scope=SCOPE_READ, audience=docs_resource())
        assert e["store"].one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE client_id=?", (cid,))["n"] == 0


@pytest.mark.parametrize('change', ['demotion', 'logout'])
@pytest.mark.parametrize('action', ['request', 'issue', 'revoke', 'rotate', 'disable', 'enable', 'revoke-tokens'])
def test_admin_mutations_recheck_session_and_role_inside_write_transaction(plane, monkeypatch, change, action):
    e = plane
    approval = request_approval(e)
    approve(e, approval)
    credential = issue(e, approval) if action not in ('request', 'issue') else None
    if action == 'enable':
        assert post(e['admin'], f"{BASE}/credentials/service_account/{credential['client_id']}/disable", {'reason': REASON}).status_code == 200
    if action == 'request':
        path = BASE + '/approvals'
        body = {'reason': REASON, 'workspace_id': e['ws'], 'name': 'Synthetic stale administrator',
                'credential_kind': 'api_key', 'scopes': [SCOPE_READ], 'resources': [docs_resource()], 'expires_in_days': 1}
    else:
        path = (f"{BASE}/approvals/{approval['id']}/{action}" if action in ('issue', 'revoke') else
                f"{BASE}/credentials/service_account/{credential['client_id']}/{action}")
        body = {'reason': REASON}
    before = authority_state(e)
    admitted, resume = Event(), Event()
    original = routes.require_manage

    def admission(ident, uid):
        user = original(ident, uid)
        if not admitted.is_set():
            admitted.set()
            assert resume.wait(5)
        return user

    monkeypatch.setattr(routes, 'require_manage', admission)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(post, e['admin'], path, body)
        try:
            assert admitted.wait(5)
            if change == 'demotion':
                end_platform_grants(e['store'], e['users']['administrator'])
            else:
                Sessions(e['store']).revoke_all_for_user(e['users']['administrator']['id'], 'synthetic_concurrent_logout')
        finally:
            resume.set()
        assert request.result(timeout=10).status_code == (404 if change == 'demotion' else 401)
    assert authority_state(e) == before


@pytest.mark.parametrize('action', ['approve', 'reject', 'revoke'])
@pytest.mark.parametrize('change', ['membership', 'logout'])
def test_owner_decision_rechecks_authority_and_session_in_transaction(plane, monkeypatch, action, change):
    e = plane
    approval = request_approval(e)
    # A fresh ordinary owner's strict reauthentication is required by every decision.
    from tests.integration.test_api_platform import reauth
    reauth(e['owner'])
    if action == 'revoke':
        approve(e, approval)
    before = authority_state(e)
    admitted, resume = Event(), Event()
    original = Sessions.reauth_fresh

    def pause(session, scope, *, strict=False):
        result = original(session, scope, strict=strict)
        if session['user_id'] == e['users']['owner']['id'] and not admitted.is_set():
            admitted.set()
            assert resume.wait(5)
        return result

    monkeypatch.setattr(Sessions, 'reauth_fresh', staticmethod(pause))
    body = {'reason': REASON, 'scopes': [SCOPE_READ], 'resources': [docs_resource()]}
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(post, e['owner'], f"{OWNER}/{approval['id']}/{action}", body)
        try:
            assert admitted.wait(5)
            if change == 'membership':
                e['store'].execute("UPDATE memberships SET role='admin' WHERE workspace_id=? AND user_id=?",
                                   (e['ws'], e['users']['owner']['id']))
            else:
                Sessions(e['store']).revoke_all_for_user(e['users']['owner']['id'], 'synthetic_owner_logout')
        finally:
            resume.set()
        assert request.result(timeout=10).status_code == (403 if change == 'membership' else 401)
    assert authority_state(e) == before


def test_read_admission_does_not_reuse_revoked_gate_session(plane, monkeypatch):
    e = plane
    admitted, resume = Event(), Event()
    original = Sessions.load

    def pause(self, raw_cookie, *, touch=True):
        result = original(self, raw_cookie, touch=touch)
        if result and touch and result['user_id'] == e['users']['administrator']['id'] and not admitted.is_set():
            admitted.set()
            assert resume.wait(5)
        return result

    monkeypatch.setattr(Sessions, 'load', pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(e['admin'].get, BASE + '/overview')
        try:
            assert admitted.wait(5)
            Sessions(e['store']).revoke_all_for_user(e['users']['administrator']['id'], 'synthetic_gate_logout')
        finally:
            resume.set()
        assert request.result(timeout=10).status_code == 401


@pytest.mark.parametrize('change', ['demotion', 'logout', 'approval', 'scope'])
def test_debugger_withholds_real_response_after_inflight_authority_change(plane, monkeypatch, change):
    e = plane
    approval = request_approval(e, 'api_key', [SCOPE_READ, SCOPE_WRITE])
    approve(e, approval)
    credential = issue(e, approval)
    completed, resume = Event(), Event()
    original = httpx.ASGITransport.handle_async_request

    async def pause_after_real_handler(self, request):
        response = await original(self, request)
        if request.url.host == 'control-plane.internal':
            completed.set()
            assert await asyncio.to_thread(resume.wait, 5)
        return response

    monkeypatch.setattr(httpx.ASGITransport, 'handle_async_request', pause_after_real_handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(post, e['admin'], BASE + '/request', {'reason': REASON,
            'credential': credential['key'], 'path': '/api/v1/projects'})
        try:
            assert completed.wait(5)
            if change == 'demotion':
                end_platform_grants(e['store'], e['users']['administrator'])
            elif change == 'logout':
                Sessions(e['store']).revoke_all_for_user(e['users']['administrator']['id'], 'synthetic_debug_logout')
            elif change == 'approval':
                assert post(e['owner'], f"{OWNER}/{approval['id']}/revoke", {'reason': REASON}).status_code == 200
            else:
                e['store'].execute('UPDATE api_platform_approvals SET approved_scopes=? WHERE id=?',
                                   (json.dumps([SCOPE_WRITE]), approval['id']))
        finally:
            resume.set()
        response = request.result(timeout=10)
    assert response.status_code in (401, 403, 404)
    assert e['project']['id'] not in response.text
    assert e['project']['name'] not in response.text
    assert credential['key'] not in response.text


def test_asgi_debugger_has_real_coroutine_deadline(plane, monkeypatch):
    e = plane
    approval = request_approval(e, 'api_key', [SCOPE_READ])
    approve(e, approval)
    token = issue(e, approval)['key']
    cancelled = Event()
    original = httpx.ASGITransport.handle_async_request

    async def slow_dispatch(self, request):
        if request.url.host != 'control-plane.internal':
            return await original(self, request)
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    monkeypatch.setattr(httpx.ASGITransport, 'handle_async_request', slow_dispatch)
    monkeypatch.setattr(control_plane, 'DISPATCH_TIMEOUT_SECONDS', .05)
    response = post(e['admin'], BASE + '/request', {'reason': REASON, 'credential': token, 'path': '/api/v1/projects'})
    assert response.status_code == 504 and response.json()['error'] == 'debug_request_timeout'
    assert cancelled.is_set(), 'The ASGI coroutine itself must be cancelled, not just a socket timeout configured'
