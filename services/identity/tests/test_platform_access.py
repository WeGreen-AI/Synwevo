"""Delegated platform permissions (synwevo_core.platform_access), through the account service.

Each built-in role against every `/api/admin` route and the API console; the 404 that disguises
administration from everyone without a platform permission (organization owners and workspace
administrators included); the audited, rate-limited 403 for a permission a caller lacks; what an
account must be to receive a role; no self-escalation or self-demotion; the last usable super
administrator under revocation, suspension, self-deletion and two administrators revoking each
other at once; the v26 → v27 migration and the rollback mirror; session tightening on grant;
revocation on the next request; expiry.

Isolated data directory and file mail only; the grants in fixtures go through the service
(`platform_fixture`), and the grants under test go through the real routes.
"""
from __future__ import annotations

import json
import time
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .platform_fixture import legacy_platform_role_row, make_platform_admin
from .test_admin_runs import flow as flow_run_fixture, run as op_run_fixture
from synwevo_core import platform_access
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.notifications import Notifier, RETRY_DELAYS_S
from synwevo_core.organizations import Organizations
from synwevo_core.platform_access import (PERMISSIONS, ROLE_ORDER, ROLES, SUPER_ADMIN, PlatformAccess,
                                          catalogue, sorted_permissions, clock)
from synwevo_core.sessions import ADMIN_IDLE_SECONDS, CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now

PW = "isolated-platform-fixture-passphrase-31"
REASON = "Isolated fixture: platform role change"

#: Every /api/admin route outside the API console: (method, path, permission, dangerous).
#: `None` = any platform permission (the caller describing themselves).
ROUTES = [
    ("GET", "/api/admin/overview", "platform.overview.view", False),
    ("GET", "/api/admin/session", None, False),
    ("GET", "/api/admin/users", "platform.users.view", False),
    ("GET", "/api/admin/users/{uid}", "platform.users.view", False),
    ("GET", "/api/admin/diagnostics/registration", "platform.users.view", False),
    ("GET", "/api/admin/audit/actions", "platform.audit.view", False),
    ("GET", "/api/admin/users/{uid}/access", "platform.roles.view", False),
    ("POST", "/api/admin/users/{uid}/status-preview", "platform.users.manage", False),
    ("POST", "/api/admin/users/{uid}/status", "platform.users.manage", True),
    # A18: a batch needs its operation's permission (admin_bulk.py); the matrix sends a session
    # revocation (`platform.users.support`). A suspension's `platform.users.manage` is proved in
    # test_admin_bulk_accounts. The preview changes nothing; the act is dangerous. The preview also
    # needs `platform.users.view`, checked after the operation's permission; every built-in role
    # holding `platform.users.support` holds it, so the expectation below is the operation's.
    ("POST", "/api/admin/users/bulk-preview", "platform.users.support", False),
    ("POST", "/api/admin/users/bulk", "platform.users.support", True),
    ("POST", "/api/admin/users/{uid}/recovery", "platform.users.support", True),
    ("POST", "/api/admin/users/{uid}/revoke-sessions", "platform.users.support", True),
    ("POST", "/api/admin/users/{uid}/sessions/{sid}/revoke", "platform.users.manage", True),
    ("POST", "/api/admin/users/{uid}/passkeys/{pid}/remove", "platform.users.manage", True),
    ("POST", "/api/admin/users/{uid}/external-identities/{iid}/unlink", "platform.users.manage", True),
    ("POST", "/api/admin/users/{uid}/deletion/cancellation-mail", "platform.users.support", True),
    # A26 (admin_second_factor.py): the second-factor state reads with `platform.users.view`; the
    # reset's preview and its two acts need `platform.users.recover`, the super administrator's
    # only. The preview of the plain account, which has no second factor, is the state refusal
    # `nothing_to_reset` (RISK_STATE_REFUSALS); the request and the cancel stop at the strict
    # re-authentication before their bodies are read.
    ("GET", "/api/admin/users/{uid}/second-factor", "platform.users.view", False),
    ("POST", "/api/admin/users/{uid}/second-factor-reset/preview", "platform.users.recover", False),
    ("POST", "/api/admin/users/{uid}/second-factor-reset", "platform.users.recover", True),
    ("POST", "/api/admin/users/{uid}/second-factor-reset/{rid}/cancel", "platform.users.recover", True),
    ("POST", "/api/admin/users/{uid}/push-subscriptions/revoke", "platform.users.manage", True),
    ("POST", "/api/admin/account-deletions/finalize", "platform.users.manage", True),
    ("GET", "/api/admin/workspaces", "platform.workspaces.view", False),
    ("GET", "/api/admin/workspaces/{wid}/members", "platform.workspaces.view", False),
    ("POST", "/api/admin/workspaces/{wid}/claim", "platform.workspaces.claim", True),
    ("GET", "/api/admin/workspaces/{wid}/unclaim-preview", "platform.workspaces.claim", False),
    ("POST", "/api/admin/workspaces/{wid}/unclaim", "platform.workspaces.claim", True),
    ("POST", "/api/admin/workspaces/{wid}/freeze", "platform.workspaces.manage", True),
    ("POST", "/api/admin/workspaces/{wid}/unfreeze", "platform.workspaces.manage", True),
    # A44 (admin_workspaces.py): the lifecycle preview changes nothing; the act (rename, delete,
    # recover) is confirmed and dangerous, so it stops at the strict re-authentication.
    ("POST", "/api/admin/workspaces/{wid}/lifecycle/preview", "platform.workspaces.manage", False),
    ("POST", "/api/admin/workspaces/{wid}/lifecycle/{action}", "platform.workspaces.manage", True),
    # A37: the orphan list reads with both view permissions (checked in this order); the handovers
    # are the object's manage permission and the strict re-authentication (admin_ownership.py).
    ("GET", "/api/admin/ownership/orphans", "platform.workspaces.view", False),
    ("POST", "/api/admin/workspaces/{wid}/recover-owner", "platform.workspaces.manage", True),
    # A47 (admin_invitations.py): every workspace's invitations read with the workspace listing's
    # permission; the single and the bulk revoke are the freeze's permission and the strict
    # re-authentication, which stops them before their bodies are read.
    ("GET", "/api/admin/invitations", "platform.workspaces.view", False),
    ("POST", "/api/admin/invitations/{iid}/revoke", "platform.workspaces.manage", True),
    ("POST", "/api/admin/invitations/revoke", "platform.workspaces.manage", True),
    ("GET", "/api/admin/organizations", "platform.orgs.view", False),
    # A34 (admin_org_detail.py): any organization's governance metadata, read with the listing's
    # permission by an administrator who is not a member. The plain account is no member of the
    # matrix organization, so the effective-permissions route answers its state refusal.
    ("GET", "/api/admin/organizations/{oid}", "platform.orgs.view", False),
    ("GET", "/api/admin/organizations/{oid}/workspaces", "platform.orgs.view", False),
    ("GET", "/api/admin/organizations/{oid}/members", "platform.orgs.view", False),
    ("GET", "/api/admin/organizations/{oid}/members/{uid}/permissions", "platform.orgs.view", False),
    # The lifecycle preview changes nothing; the act is confirmed and dangerous (A35).
    ("POST", "/api/admin/organizations/{oid}/preview", "platform.orgs.manage", False),
    ("POST", "/api/admin/organizations/{oid}/{action}", "platform.orgs.manage", True),
    ("POST", "/api/admin/organizations/{oid}/recover-owner", "platform.orgs.manage", True),
    ("GET", "/api/admin/super-admin-offers", "platform.roles.assign_super", False),
    ("POST", "/api/admin/super-admin-offers/preview", "platform.roles.assign_super", False),
    ("POST", "/api/admin/super-admin-offers", "platform.roles.assign_super", True),
    ("POST", "/api/admin/super-admin-offers/{oid}/cancel", "platform.roles.assign_super", True),
    ("GET", "/api/admin/super-admin-offers/{oid}/readiness", "platform.roles.assign_super", False),
    ("POST", "/api/admin/super-admin-offers/{oid}/ready/begin", "platform.roles.assign_super", False),
    ("POST", "/api/admin/super-admin-offers/{oid}/ready/finish", "platform.roles.assign_super", False),
    ("GET", "/api/admin/settings", "platform.config.view", False),
    ("GET", "/api/admin/settings/history", "platform.config.view", False),
    ("POST", "/api/admin/settings/preview", "platform.config.manage", False),
    ("POST", "/api/admin/settings/publish", "platform.config.manage", True),
    # MCP: the platform policy (signed preview, then publish), masked oversight of connections, and
    # the platform-wide stop of a connection
    ("GET", "/api/admin/mcp/policy", "platform.config.view", False),
    ("GET", "/api/admin/mcp/policy/history", "platform.config.view", False),
    ("POST", "/api/admin/mcp/policy/preview", "platform.config.manage", False),
    ("POST", "/api/admin/mcp/policy/publish", "platform.config.manage", True),
    ("GET", "/api/admin/mcp/overview", "platform.config.view", False),
    ("GET", "/api/admin/mcp/connections/{cid}/calls", "platform.config.view", False),
    ("POST", "/api/admin/mcp/connections/{cid}/disable", "platform.config.manage", True),
    # lifting a platform stop: as dangerous as the stop itself (re-authentication, reason, audit)
    ("POST", "/api/admin/mcp/connections/{cid}/lift", "platform.config.manage", True),
    ("GET", "/api/admin/billing/prices", "platform.config.view", False),
    ("GET", "/api/admin/billing/prices/drafts/{draft_id}", "platform.config.view", False),
    ("POST", "/api/admin/billing/prices/drafts", "platform.config.manage", False),
    ("PATCH", "/api/admin/billing/prices/drafts/{draft_id}", "platform.config.manage", False),
    ("POST", "/api/admin/billing/prices/drafts/{draft_id}/calculate", "platform.config.view", False),
    ("POST", "/api/admin/billing/prices/drafts/{draft_id}/preview", "platform.config.manage", False),
    ("POST", "/api/admin/billing/prices/drafts/{draft_id}/publish", "platform.config.manage", True),
    ("GET", "/api/admin/billing/prices/schedule", "platform.config.view", False),
    ("GET", "/api/admin/billing/prices/schedule/history", "platform.config.view", False),
    ("POST", "/api/admin/billing/prices/schedule/preview", "platform.config.manage", False),
    ("POST", "/api/admin/billing/prices/schedule/publish", "platform.config.manage", True),
    ("GET", "/api/admin/billing/prices/markups", "platform.config.view", False),
    ("POST", "/api/admin/billing/prices/markups/preview", "platform.config.manage", False),
    ("POST", "/api/admin/billing/prices/markups/publish", "platform.config.manage", True),
    ("GET", "/api/admin/billing/cost-policy", "platform.config.view", False),
    ("GET", "/api/admin/billing/cost-policy/history", "platform.config.view", False),
    ("GET", "/api/admin/billing/cost-policy/traffic", "platform.config.view", False),
    ("POST", "/api/admin/billing/cost-policy/preview", "platform.config.manage", False),
    ("POST", "/api/admin/billing/cost-policy/publish", "platform.config.manage", True),
    ("GET", "/api/admin/billing/payments", "platform.billing.view", False),
    ("GET", "/api/admin/billing/payments/readiness", "platform.billing.view", False),
    ("POST", "/api/admin/billing/payments/publish", "platform.billing.manage", True),
    ("GET", "/api/admin/billing/finance", "platform.billing.view", False),
    ("POST", "/api/admin/billing/grants", "platform.billing.manage", True),
    ("POST", "/api/admin/billing/freezes/{freeze_id}/lift", "platform.billing.manage", True),
    ("GET", "/api/admin/billing/model-provider", "platform.config.view", False),
    ("GET", "/api/admin/billing/model-provider/history", "platform.config.view", False),
    ("POST", "/api/admin/billing/model-provider/preview", "platform.config.manage", False),
    ("POST", "/api/admin/billing/model-provider/publish", "platform.config.manage", True),
    # Paid-model risk controls (docs/platform/model-risk.md §10): reopening paid calls is dangerous.
    ("GET", "/api/admin/billing/risk", "platform.config.view", False),
    ("GET", "/api/admin/billing/risk/suspensions", "platform.config.view", False),
    ("POST", "/api/admin/billing/risk/suspensions/{suspension_id}/lift", "platform.config.manage", True),
    ("GET", "/api/admin/billing/risk/breaker", "platform.config.view", False),
    ("POST", "/api/admin/billing/risk/breaker/open", "platform.config.manage", False),
    ("POST", "/api/admin/billing/risk/breaker/close", "platform.config.manage", True),
    ("GET", "/api/admin/billing/risk/allowlist", "platform.config.view", False),
    ("POST", "/api/admin/billing/risk/allowlist", "platform.config.manage", True),
    ("POST", "/api/admin/billing/risk/allowlist/{entry_id}/remove", "platform.config.manage", False),
    ("GET", "/api/admin/billing/risk/balance", "platform.config.view", False),
    ("POST", "/api/admin/billing/risk/topups", "platform.config.manage", False),
    ("GET", "/api/admin/billing/risk/reconciliations", "platform.config.view", False),
    ("POST", "/api/admin/billing/risk/reconciliations/run", "platform.config.manage", False),
    ("GET", "/api/admin/registration", "platform.config.view", False),
    ("POST", "/api/admin/registration", "platform.config.manage", True),
    ("GET", "/api/admin/security/second-factor", "platform.config.view", False),
    ("POST", "/api/admin/security/second-factor", "platform.config.manage", True),
    ("GET", "/api/admin/security/throttles", "platform.config.view", False),
    ("POST", "/api/admin/security/throttles/lift", "platform.config.manage", True),
    ("GET", "/api/admin/identity-providers", "platform.config.view", False),
    ("GET", "/api/admin/mail", "platform.mail.view", False),
    ("POST", "/api/admin/mail/{mid}/retry", "platform.mail.manage", True),
    ("GET", "/api/admin/notification-recovery", "platform.mail.view", False),
    ("POST", "/api/admin/notification-recovery/{nid}/preview", "platform.mail.manage", False),
    ("POST", "/api/admin/notification-recovery/{nid}/execute", "platform.mail.manage", True),
    ("GET", "/api/admin/app-runs", "platform.apps.view", False),
    ("GET", "/api/admin/app-runs/summary", "platform.apps.view", False),
    ("GET", "/api/admin/app-runs/{run_id}", "platform.apps.view", False),
    ("GET", "/api/admin/flow-runs", "platform.apps.view", False),
    ("GET", "/api/admin/flow-runs/{flow_run_id}", "platform.apps.view", False),
    # B1/B2 (admin_apps.py): the application catalogue reads with the run records' permission. The
    # matrix sends a change for one workspace (`platform.apps.manage`): its preview changes nothing
    # (200), the change itself stops at the strict re-authentication before its body is read. A
    # platform-scope change also needs `platform.config.manage` (D12): see
    # test_platform_scope_application_changes_need_configuration_authority.
    ("GET", "/api/admin/apps", "platform.apps.view", False),
    ("GET", "/api/admin/apps/{app_id}/workspaces", "platform.apps.view", False),
    ("GET", "/api/admin/apps/{app_id}/organizations", "platform.apps.view", False),
    ("POST", "/api/admin/apps/{app_id}/preview", "platform.apps.manage", False),
    ("POST", "/api/admin/apps/{app_id}/{change}", "platform.apps.manage", True),
    ("GET", "/api/admin/audit", "platform.audit.view", False),
    ("GET", "/api/admin/platform/roles", "platform.roles.view", False),
    ("GET", "/api/admin/users/{uid}/platform-access", "platform.roles.view", False),
    ("GET", "/api/admin/users/{uid}/platform-access/simulate", "platform.roles.view", False),
    ("POST", "/api/admin/users/{uid}/platform-grants", "platform.roles.assign", True),
    ("POST", "/api/admin/platform-grants/{gid}/revoke", "platform.roles.assign", True),
    ("POST", "/api/admin/users/{uid}/platform-role", "platform.roles.assign", True),
    # Agent content safety (admin_agent_safety.py): rule lists are configuration, reports and
    # suspensions account governance. Suspend precedes lift so the lift has one to end.
    ("GET", "/api/admin/agent-safety/rules", "platform.config.view", False),
    ("PUT", "/api/admin/agent-safety/rules", "platform.config.manage", False),
    ("GET", "/api/admin/agent-safety/reports", "platform.users.view", False),
    ("POST", "/api/admin/agent-safety/reports/{report_id}/decide", "platform.users.manage", False),
    ("GET", "/api/admin/agent-safety/stops", "platform.users.view", False),
    ("GET", "/api/admin/agent-safety/suspensions", "platform.users.view", False),
    ("POST", "/api/admin/agent-safety/users/{user_id}/suspend", "platform.users.manage", False),
    ("POST", "/api/admin/agent-safety/users/{user_id}/lift", "platform.users.manage", False),
]
CONSOLE_GET = "/api/admin/api-platform/overview"
CONSOLE_POST = "/api/admin/api-platform/approvals"

# Synthetic configuration only: no funds, provider credential or model request.
PRICE_SPEC = {"name": "Permission matrix fixture", "purpose": "cost", "provider": "deepseek",
              "model": "fixture-route", "unit": {"code": "FIX_MATRIX", "scale": 6},
              "input_uncached": "1", "input_cached": "0.1", "output": "2",
              "terms_ref": "fixture:matrix", "effective_from": 0, "previous_price_id": None}
# The provider's published peak windows and one synthetic holiday.
CALENDAR_SPEC = {"peak_windows": [{"weekdays": [1, 2, 3, 4, 5], "start": "01:00", "end": "04:00"}],
                 "holiday_utc_offset_minutes": 480, "holidays": [{"date": "2026-10-01", "name": "Fixture holiday"}],
                 "source_ref": "fixture:matrix-calendar"}
COST_SPEC = {"enabled": False, "unit": PRICE_SPEC["unit"], "daily_budget": "100",
             "weekly_budget": "100", "monthly_budget": "100", "unknown_budget": "0",
             "max_inflight": 1, "max_unknown": 1}


def post(client, path, body=None):
    return client.post(path, json=body if body is not None else {},
                       headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "test")
    # Explicit shell fixture (the account page's own index.html): the tests run before or after a
    # front-end build, and the /admin gate is decided before the shell is served.
    dist = tmp_path / "fixture-build"
    dist.mkdir()
    dist.joinpath("index.html").write_text(
        (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
    for key, value in {"DATA_DIR": str(tmp_path / "d"), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                       "PUBLIC_URL": "http://testserver", "ACCOUNTS_UI_DIR": str(dist)}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    root = make_platform_admin(store, ident.create_user("root@example.test", PW, status="active"))
    from synwevo_core.billing.catalogue import PriceCatalogue
    catalogue = PriceCatalogue(store, environment="test")
    with store.tx():
        published, _ = catalogue.create(spec=PRICE_SPEC, actor_id=root["id"],
                                        idempotency_key="matrix_cost_fixture")
        cost_price_id = catalogue.publish(published["id"], expected_revision=1, actor_id=root["id"],
                                          operation_id="matrix_cost_publish", reason=REASON)["price_id"]
        draft, _ = catalogue.create(spec=PRICE_SPEC, actor_id=root["id"],
                                    idempotency_key="matrix_draft_fixture")
    plain = verified(store, ident, "plain@example.test")
    # Synthetic exhausted email enqueue fixture, through the real notifier. A permitted
    # preview must succeed; a permitted execute still requires strict re-authentication.
    notifier = Notifier(store)
    notification = notifier.security_change(plain["id"], "password_changed")
    for _ in range(len(RETRY_DELAYS_S) + 1):
        notifier.retry_delivery(notification["id"], "notification_enqueue_failed")
    # One run of each kind, so the run routes have a row to answer about (admin_runs).
    op_run_fixture(store, "run_matrix_fixture", user=plain, workspace=ident.workspaces_for(plain["id"])[0]["id"])
    flow_run_fixture(store, "fr_matrix_fixture", user=plain)
    # FIXTURE: one Agent report by the plain account, so the decide route has one to settle.
    from synwevo_core.agent_safety import AgentReports
    report = AgentReports(store).create(plain["id"], conversation_id="conv_000000000fix", message_id="msg_000000000fix",
                                        category="wrong", note=None, excerpt=None)
    # FIXTURE: an active organization owned by an ordinary account, so a permitted role reaches
    # the lifecycle preview (200, nothing changes) while the act stops at the re-authentication.
    matrix_org = Organizations(store, ident).create(verified(store, ident, "matrix-org-owner@example.test")["id"],
                                                    "Matrix fixture organization", "Matrix fixture workspace", "matrix-org-fixture")
    clients: list[TestClient] = []

    def login(user, *, remember=False):
        c = TestClient(api)
        r = post(c, "/api/auth/login", {"email": user["email"], "password": PW, "remember": remember})
        assert r.status_code == 200, r.text
        clients.append(c)
        return c

    w = {"api": api, "store": store, "ident": ident, "root": root, "plain": plain, "login": login,
         "nid": notification["id"], "oid": matrix_org["organization"]["id"],
         "draft_id": draft["id"], "cost_price_id": cost_price_id, "report_id": report["id"],
         "wid": ident.workspaces_for(plain["id"])[0]["id"]}
    yield w
    for c in clients:
        c.close()


def verified(store, ident, email, password=PW, **kw):
    u = ident.create_user(email, password, status="active", **kw)
    store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
    return ident.get_user(u["id"])


def reauth(client):
    r = post(client, "/api/auth/reauth", {"password": PW, "scope": "admin"})
    assert r.status_code == 200, r.text


def holder(w, role, email=None):
    u = verified(w["store"], w["ident"], email or f"{role}@example.test")
    return make_platform_admin(w["store"], u, role=role)


def grant_of(w, user, role):
    return next(g for g in PlatformAccess(w["store"]).live_grants(user["id"]) if g["role"] == role)


#: The historical-data routes (claim, and its undo) act on the legacy workspace. Pointed at an
#: ordinary one they answer a state refusal (409 `not_legacy`) once past the permission check.
HISTORICAL = ("/claim", "/unclaim-preview", "/unclaim")
LEGACY_WID = "default"


def _path(template, w, *, legacy: str | None = None):
    wid = legacy if legacy and template.endswith(HISTORICAL) else w["wid"]
    return template.format(uid=w["plain"]["id"], wid=wid, mid="mail_fixture_missing", nid=w["nid"],
                           draft_id=w["draft_id"], run_id="run_matrix_fixture", flow_run_id="fr_matrix_fixture",
                           oid=w["oid"], action="freeze", gid="pg_fixture_missing",
                           freeze_id="freeze_fixture_missing",
                           suspension_id="rsus_fixture_missing", entry_id="rallow_fixture_missing",
                           report_id=w.get("report_id", "arp_fixture_missing"), user_id=w["plain"]["id"],
                           sid="s_fixture_missing", pid="pk_fixture_missing", iid="xi_fixture_missing",
                           rid="sfr_fixture_missing", app_id="docweave", change="disable",
                           cid="mcpc_fixture_missing")


#: Risk changes the matrix reaches with valid bodies but no matching state: past the permission
#: check they answer a state refusal, which nothing else could produce.
RISK_STATE_REFUSALS = {
                       # The diagnosis needs an address; the matrix supplies none.
                       "/api/admin/diagnostics/registration": (400, "bad_email"),
                       "/api/admin/users/{uid}/platform-access/simulate": (400, "bad_simulation"),
                       "/api/admin/users/{uid}/access": (400, "bad_access_query"),
                       # The plain account is no member of the matrix organization (A34).
                       "/api/admin/organizations/{oid}/members/{uid}/permissions": (404, "not_member"),
                       # The plain account has no second factor to reset (A26).
                       "/api/admin/users/{uid}/second-factor-reset/preview": (409, "nothing_to_reset"),
                       # No stored payment key in the matrix; authorized readers reach this refusal.
                       "/api/admin/billing/payments/readiness": (409, "payment_readiness_no_keys"),
                       "/api/admin/billing/risk/allowlist/{entry_id}/remove": (404, "risk_allowlist_not_found"),
                       "/api/admin/billing/risk/topups": (409, "model_cost_policy_required")}


def _risk_body(path):
    # Keys are per administrator; one key per route keeps each a distinct operation.
    body = {"reason": REASON, "idempotency_key": "matrix" + re.sub(r"[^A-Za-z0-9]", "_", path)[-100:]}
    if path.endswith("/risk/allowlist"):
        body.update(payer_kind="user", email="nobody@example.test")
    elif path.endswith("/topups"):
        body.update(amount_units="1", topped_up_at=0)
    elif path.endswith("/reconciliations/run"):
        body.update(kind="ledger_audit")
    return body


def _call(client, method, path, w):
    if method == "GET":
        return client.get(path)
    if path.startswith("/api/admin/workspaces/") and path.endswith("/lifecycle/preview"):
        # The plain account's personal workspace: a permitted role gets the rename preview (200,
        # nothing changes); a personal workspace is never deleted.
        return post(client, path, {"action": "rename", "name": "Matrix fixture rename"})
    if path.startswith("/api/admin/organizations/") and path.endswith("/preview"):
        # The fixture organization is active: a permitted role gets the freeze preview, 200.
        return post(client, path, {"action": "freeze"})
    if path in ("/api/admin/users/bulk-preview", "/api/admin/users/bulk"):
        # A session revocation of the plain account: a permitted role gets the preview (200,
        # nothing changes) while the act stops at the strict re-authentication.
        return post(client, path, {"operation": "revoke_sessions", "user_ids": [w["plain"]["id"]]}
                    if path.endswith("-preview") else
                    {"operation": "revoke_sessions", "reason": REASON, "preview_token": "matrix-not-confirmed",
                     "idempotency_key": "matrix_bulk_accounts_0001"})
    if path.startswith("/api/admin/invitations/"):
        # A47: a well-formed revoke (a reason, and for the batch one id) of an invitation that does
        # not exist; a permitted role stops at the strict re-authentication, so nothing is revoked.
        return post(client, path, {"reason": REASON, "ids": ["iv_fixture_missing"]}
                    if path == "/api/admin/invitations/revoke" else {"reason": REASON})
    if path.startswith("/api/admin/apps/"):
        # B1/B2: a permitted role previews switching Synwevo Docs off in the plain account's
        # workspace (200, nothing changes); the change carries a well-formed but unconfirmed body
        # and stops at the re-authentication.
        return post(client, path, {"change": "disable", "workspace_id": w["wid"]} if path.endswith("/preview") else
                    {"reason": REASON, "preview_token": "matrix-not-confirmed", "idempotency_key": "matrix_app_change_0001",
                     "workspace_id": w["wid"]})
    if path.startswith("/api/admin/notification-recovery/"):
        return post(client, path, {"reason": REASON, "preview_token": "matrix-not-confirmed",
                                   "idempotency_key": "matrix_notification_recovery_0001"}
                    if path.endswith("/execute") else {})
    if path.startswith(("/api/admin/billing/payments", "/api/admin/billing/grants", "/api/admin/billing/freezes")):
        # Money administration: a well-formed but unconfirmed request (no strict re-authentication).
        return client.request(method, path, json={"reason": REASON, "idempotency_key": "matrix_billing_admin_0001"},
                              headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})
    if path.startswith("/api/admin/billing/risk/"):
        return post(client, path, _risk_body(path))
    if path.startswith("/api/admin/billing/"):
        if "/prices/schedule/" in path:
            body = {"expected_revision": 0, "spec": CALENDAR_SPEC}
        elif "/prices/markups/" in path:
            body = {"spec": {"name": "Permission matrix fixture", "cost_price_id": w["cost_price_id"], "markup": "1.5",
                             "off_peak_markup": None, "terms_ref": "fixture:matrix", "effective_from": 0}}
        elif path.endswith("/drafts"):
            body = {"spec": PRICE_SPEC, "idempotency_key": "matrix_http_draft"}
        elif "/prices/drafts/" in path:
            body = {"expected_revision": 1}
            if method == "PATCH":
                body["spec"] = PRICE_SPEC  # No-op keeps this fixture's revision fixed.
            elif path.endswith("/calculate"):
                body.update(input_uncached=10, input_cached=1, output=2)
        elif "/cost-policy/" in path:
            body = {"expected_revision": 0, "spec": COST_SPEC}
        else:
            body = {"expected_revision": 0, "spec": {"enabled": False, "route": None},
                    "credential": {"action": "keep", "api_key": None}}
        if path.endswith("/publish"):
            body.update(preview_token="matrix-not-confirmed", idempotency_key="matrix_unconfirmed",
                        reason=REASON)
        return client.request(method, path, json=body,
                              headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})
    if path.startswith("/api/admin/agent-safety/"):
        if method == "PUT":
            return client.put(path, json={"rules": [], "reason": REASON, "expected_version": 0},
                              headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})
        return post(client, path, {"decision": "dismissed"} if path.endswith("/decide") else {"reason": REASON})
    if path == "/api/admin/settings/preview":
        return post(client, path, {"expected_revision": 0, "changes": {"notifications.conversation_digest_daily_cap": 9}})
    if path == "/api/admin/mcp/policy/preview":
        from synwevo_core.mcp_policy import McpPolicy
        return post(client, path, {"expected_revision": 0, "spec": {**McpPolicy.default().model_dump(), "outbound_enabled": True}})
    return post(client, path, {"reason": REASON, "status": "suspended", "mode": "open", "role": "auditor",
                               "user_id": "u_fixture_missing"})


# ---------------------------------------------------------------- the catalogue and the matrix
def test_catalogue_roles_are_sets_of_catalogued_permissions_and_super_admin_is_all_of_them():
    assert ROLES[SUPER_ADMIN] == frozenset(PERMISSIONS)
    for role, perms in ROLES.items():
        assert perms <= set(PERMISSIONS), role
        assert not any(p.startswith(("workspace.", "docs.", "org.", "document")) for p in perms), \
            "平台角色不带任何业务内容权限"
    # only the super administrator can grant roles; API management is its own role's, never implied
    assert [r for r in ROLES if ROLES[r] & {"platform.roles.assign", "platform.roles.assign_super"}] == [SUPER_ADMIN]
    assert [r for r in ROLES if "api_platform.manage" in ROLES[r]] == [SUPER_ADMIN, "api_platform_admin"]
    assert ROLES["api_platform_admin"] == {"platform.overview.view", "api_platform.manage"}
    # money is its own role's: no other delegated role reads or moves it, and finance holds nothing else
    assert [r for r in ROLES if ROLES[r] & {"platform.billing.view", "platform.billing.manage"}] == [SUPER_ADMIN, "finance"]
    assert ROLES["finance"] == {"platform.overview.view", "platform.billing.view", "platform.billing.manage"}
    assert [r["id"] for r in catalogue()["roles"]] == list(ROLE_ORDER)


def test_every_admin_route_is_in_the_matrix(world):
    mounted = {(m.upper(), p) for p, ops in world["api"].openapi()["paths"].items()
               if p.startswith("/api/admin/") and not p.startswith("/api/admin/api-platform") for m in ops}
    assert mounted == {(m, p) for m, p, _, _ in ROUTES}, "新的管理路由必须在这里写明它需要的平台权限"


@pytest.mark.parametrize("role", ROLE_ORDER)
def test_each_built_in_role_is_allowed_exactly_its_permissions(world, role):
    # The historical data is assigned (to an ordinary account, through the real claim) before the
    # matrix runs, so the undo routes have something to act on: a permitted caller reaches the
    # preview (200) and the strict re-authentication (reauth_required), not a state refusal.
    legacy_owner = _assigned_legacy_workspace(world)
    notification_before = Notifier(world["store"]).email_recovery_row(world["nid"])
    c = world["login"](holder(world, role))
    for method, template, permission, dangerous in ROUTES:
        path = _path(template, world, legacy=LEGACY_WID)
        r = _call(c, method, path, world)
        if permission is not None and permission not in ROLES[role]:
            assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", (role, method, path, r.text)
            assert r.json()["detail"]["permission"] == permission
        elif dangerous:
            # past the permission check, stopped by the strict re-authentication: nothing changed
            assert r.status_code == 403 and r.json()["error"] == "reauth_required", (role, method, path, r.text)
        elif template.startswith("/api/admin/super-admin-offers/"):
            # These routes require a recipient-owned offer or a valid target. The matrix
            # intentionally supplies neither; all lower roles must still fail permission first.
            code = "super_offer_bad_target" if template.endswith("/preview") else "admin_confirmation_required" \
                if template.endswith("/finish") else "super_offer_not_found"
            assert r.status_code == (400 if code != "super_offer_not_found" else 404) and r.json()["error"] == code, (role, path, r.text)
        elif template == "/api/admin/apps/{app_id}/organizations":
            # B3: past the permission check, the matrix's application (Synwevo Docs) is not switched
            # per organization — the list itself is test_admin_apps_organizations'
            assert r.status_code == 400 and r.json()["error"] == "bad_change", (role, method, path, r.text)
        elif template in RISK_STATE_REFUSALS:
            status, code = RISK_STATE_REFUSALS[template]
            assert r.status_code == status and r.json()["error"] == code, (role, method, path, r.text)
        else:
            assert r.status_code == 200, (role, method, path, r.text)
    overview, mutation = c.get(CONSOLE_GET), post(c, CONSOLE_POST, {"reason": REASON})
    if "api_platform.manage" in ROLES[role]:
        assert overview.status_code == 200 and mutation.json()["error"] == "reauth_required"
    else:
        for r in (overview, mutation):
            assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", (role, r.text)
    me = c.get("/api/auth/me").json()
    assert me["platform_roles"] == [role] and set(me["platform_permissions"]) == ROLES[role]
    assert me["permissions"] == (["api_platform.manage"] if "api_platform.manage" in ROLES[role] else [])
    assert me["platform_role"] == ("admin" if role == SUPER_ADMIN else "none"), "委派角色的回滚镜像保持 none"
    # nothing was written by any of the refused or unconfirmed calls
    assert world["ident"].get_user(world["plain"]["id"])["status"] == "active"
    assert world["ident"].get_workspace(world["wid"])["status"] == "active"
    assert world["ident"].get_workspace(LEGACY_WID)["status"] == "active"
    assert {r["user_id"] for r in world["store"].all("SELECT user_id FROM memberships WHERE workspace_id=?",
                                                     (LEGACY_WID,))} == {legacy_owner["id"]}
    assert Notifier(world["store"]).email_recovery_row(world["nid"]) == notification_before
    assert not world["store"].one("SELECT 1 FROM admin_operations WHERE action='admin.notification.recover_enqueue'")
    assert world["store"].one("SELECT status FROM organizations WHERE id=?", (world["oid"],))["status"] == "active"
    assert not world["store"].one("SELECT 1 FROM admin_operations WHERE action='admin.organization.lifecycle'")
    assert not world["store"].one("SELECT 1 FROM admin_operations WHERE action='admin.user.bulk'")
    assert not world["store"].one("SELECT 1 FROM audit_events WHERE action LIKE 'admin.invitation.%' AND result='ok'")
    assert world["store"].one("SELECT status FROM apps WHERE id='docweave'")["status"] == "active"
    assert not world["store"].one("SELECT 1 FROM admin_operations WHERE action LIKE 'admin.app.%'")


@pytest.mark.parametrize("role", ROLE_ORDER)
def test_platform_scope_application_changes_need_configuration_authority(world, role):
    """D12 (admin_apps.py): switching an application for the whole platform or changing its policy
    needs `platform.config.manage` besides `platform.apps.manage` — the super administrator's alone
    among the built-in roles — at the preview and at the change, both before the strict
    re-authentication. The access administrator keeps the switch for one workspace (the matrix)."""
    from synwevo_core.apps import DOCS_APP_ID
    c = world["login"](holder(world, role))
    preview = post(c, f"/api/admin/apps/{DOCS_APP_ID}/preview", {"change": "policy_explicit"})
    change = post(c, f"/api/admin/apps/{DOCS_APP_ID}/policy_explicit",
                  {"reason": REASON, "preview_token": "matrix-not-confirmed", "idempotency_key": "matrix_app_scope_0001"})
    missing = next((p for p in ("platform.apps.manage", "platform.config.manage") if p not in ROLES[role]), None)
    if missing:
        for r in (preview, change):
            assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", (role, r.text)
            assert r.json()["detail"]["permission"] == missing, role
    else:
        assert preview.status_code == 200 and preview.json()["scope"] == "platform", preview.text
        assert change.status_code == 403 and change.json()["error"] == "reauth_required", change.text
    assert [r for r in ROLES if {"platform.apps.manage", "platform.config.manage"} <= ROLES[r]] == [SUPER_ADMIN]
    assert world["store"].one("SELECT access_policy FROM apps WHERE id='docweave'")["access_policy"] == "members"
    assert not world["store"].one("SELECT 1 FROM admin_operations WHERE action LIKE 'admin.app.%'")


def test_no_platform_permission_sees_404_everywhere_including_owners_and_workspace_admins(world):
    store, ident = world["store"], world["ident"]
    owner = verified(store, ident, "org-owner@example.test")
    org = Organizations(store, ident).create(owner["id"], "Fixture organization", "Fixture workspace", "fixture-org-pa1")
    ws_admin = verified(store, ident, "ws-admin@example.test")
    ident.add_member(org["workspace"]["id"], ws_admin["id"], "admin", actor_id=owner["id"])
    for who in (world["plain"], owner, ws_admin):
        c = world["login"](who)
        for method, template, _, _ in ROUTES:
            r = _call(c, method, _path(template, world), world)
            assert r.status_code == 404 and r.json()["error"] == "not_found", (who["email"], template, r.text)
        assert c.get(CONSOLE_GET).status_code == 404
        assert post(c, CONSOLE_POST, {"reason": REASON}).status_code == 404
        assert c.get("/admin").status_code == 404 and c.get("/admin/api-platform").status_code == 404
        # Nor does request validation, which runs before a route's own check, tell them apart: a
        # bad parameter, a body that is not JSON, a method the route lacks and a path that does
        # not exist all meet the same 404 (the role pattern used to come back in a 422).
        missing = c.get("/api/admin/no-such-page")
        for r in (c.get("/api/admin/users?role=bogus"), c.get("/api/admin/users?limit=many"),
                  c.post(f"/api/admin/users/{world['plain']['id']}/platform-grants", content=b"{not json",
                         headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or "", "content-type": "application/json"}),
                  c.put("/api/admin/overview", headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""}), missing):
            assert r.status_code == 404 and r.json()["error"] == "not_found", (who["email"], r.request.url, r.text)
            assert "super_admin" not in r.text and r.json()["message"] == missing.json()["message"]
        me = c.get("/api/auth/me").json()
        assert me["platform_permissions"] == [] and me["platform_roles"] == [] and me["permissions"] == []
        assert me["admin_locked"] is False
    assert not store.one("SELECT 1 AS x FROM audit_events WHERE action='admin.access.denied'"), \
        "没有平台权限的人得到的是 404，不留下「被拒绝的管理操作」"
    # a holder still gets the ordinary validation answer: the gate only disguises, it does not validate
    root = world["login"](world["root"])
    assert root.get("/api/admin/users?role=bogus").status_code == 422


def test_the_admin_shell_opens_for_any_platform_permission(world):
    """The /admin shell is served to anyone a grant gives a platform permission — an auditor or an
    API platform administrator as much as a super administrator — and to nobody else; what each
    of them may use inside is decided per request by the routes above."""
    for role in ("auditor", "api_platform_admin", SUPER_ADMIN):
        c = world["login"](world["root"] if role == SUPER_ADMIN else holder(world, role))
        for path in ("/admin", "/admin/api-platform"):
            r = c.get(path)
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), (role, path, r.status_code)
    plain = world["login"](world["plain"])
    assert plain.get("/admin").status_code == 404 and plain.get("/admin").json()["error"] == "not_found"
    assert TestClient(world["api"]).get("/admin").status_code == 404


def test_a_denied_attempt_is_audited_once_per_window(world):
    auditor = holder(world, "auditor")
    c = world["login"](auditor)
    path = f"/api/admin/users/{world['plain']['id']}/status"
    for _ in range(3):
        r = post(c, path, {"reason": REASON, "status": "suspended"})
        assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied"
    assert c.get(CONSOLE_GET).status_code == 403
    rows = world["store"].all("SELECT * FROM audit_events WHERE action='admin.access.denied' AND actor_id=? ORDER BY seq",
                              (auditor["id"],))
    assert [r["object_id"] for r in rows] == ["platform.users.manage", "api_platform.manage"]
    assert all(r["result"] == "denied" for r in rows)
    assert json.loads(rows[0]["meta"])["path"] == path


# ---------------------------------------------------------------- granting
def test_a_super_administrator_grants_and_revokes_through_the_routes(world):
    store = world["store"]
    root = world["login"](world["root"])
    target = world["plain"]
    path = f"/api/admin/users/{target['id']}/platform-grants"
    assert post(root, path, {"role": "support", "reason": REASON}).json()["error"] == "reauth_required"
    reauth(root)
    assert post(root, path, {"role": "support", "reason": "短"}).json()["error"] == "missing_reason"
    assert post(root, path, {"role": "operator", "reason": REASON}).json()["error"] == "bad_platform_role"
    r = post(root, path, {"role": "support", "reason": REASON})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["before"] == [] and body["after"] == ["support"]
    assert post(root, path, {"role": "support", "reason": REASON}).json()["error"] == "already_granted"
    # the audit record is part of the grant: before/after, reason, request id
    ev = store.one("SELECT * FROM audit_events WHERE action='admin.platform.grant' AND object_id=?", (target["id"],))
    meta = json.loads(ev["meta"])
    assert ev["actor_id"] == world["root"]["id"] and ev["request_id"] and meta["reason"] == REASON
    assert meta["before"] == {"roles": []} and meta["after"] == {"roles": ["support"]} and meta["changed"] == ["roles"] \
        and meta["role"] == "support"
    explained = root.get(f"/api/admin/users/{target['id']}/platform-access").json()
    assert explained["roles"] == ["support"] and explained["grants"][0]["granted_by_email"] == "root@example.test"
    assert {p["permission"] for p in explained["permissions"]} == ROLES["support"]
    assert all(p["sources"][0]["role"] == "support" for p in explained["permissions"])
    assert SUPER_ADMIN not in explained["assignable_roles"] and "support" not in explained["assignable_roles"]
    assert explained["can_offer_super_admin"]
    r = post(root, f"/api/admin/platform-grants/{body['grant']['id']}/revoke", {"reason": REASON})
    assert r.status_code == 200 and r.json()["after"] == []
    ev = store.one("SELECT meta FROM audit_events WHERE action='admin.platform.revoke' AND object_id=?", (target["id"],))
    assert json.loads(ev["meta"])["before"] == {"roles": ["support"]}
    assert post(root, f"/api/admin/platform-grants/{body['grant']['id']}/revoke", {"reason": REASON}).json()["error"] == "grant_revoked"


def test_grant_preconditions_close_d2(world):
    from synwevo_core.apps import DOCS_APP_ID
    from synwevo_core.messaging_bridge import ensure_app_account
    store, ident = world["store"], world["ident"]
    root = world["login"](world["root"])
    reauth(root)
    pending = ident.create_user("pending@example.test", PW)
    suspended = verified(store, ident, "suspended@example.test")
    ident.set_status(suspended["id"], "suspended")
    unverified = ident.create_user("unverified@example.test", PW, status="active")
    no_factor = verified(store, ident, "no-factor@example.test", password=None)
    app_identity = ensure_app_account(store, ident, DOCS_APP_ID)
    for user_id, code in ((pending["id"], "target_pending"), (suspended["id"], "target_unavailable"),
                          (unverified["id"], "target_unverified"), (no_factor["id"], "target_cannot_step_up"),
                          (app_identity, "application_identity"), ("u_fixture_missing", "no_user")):
        r = post(root, f"/api/admin/users/{user_id}/platform-grants", {"role": "auditor", "reason": REASON})
        assert r.json()["error"] == code, (code, r.text)
        assert r.status_code == (404 if code == "no_user" else 409)
    assert not store.one("SELECT 1 AS x FROM platform_role_grants WHERE user_id<>?", (world["root"]["id"],))
    # The compatibility promotion no longer grants the highest role without recipient proof.
    r = post(root, f"/api/admin/users/{pending['id']}/platform-role", {"role": "admin", "reason": REASON})
    assert r.json()["error"] == "super_admin_acceptance_required"
    assert post(root, f"/api/admin/users/{world['plain']['id']}/platform-role",
                {"role": "operator", "reason": REASON}).json()["error"] == "operator_retired"


def test_holding_is_not_granting_and_nobody_changes_their_own_authority_to_assign(world):
    store = world["store"]
    root = world["login"](world["root"])
    reauth(root)
    me = world["root"]["id"]
    # self-escalation and self-demotion of a role that can assign roles are refused
    assert post(root, f"/api/admin/users/{me}/platform-grants", {"role": SUPER_ADMIN, "reason": REASON}).json()["error"] == "self_assignment"
    own = grant_of(world, world["root"], SUPER_ADMIN)
    r = post(root, f"/api/admin/platform-grants/{own['id']}/revoke", {"reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "self_assignment"
    r = post(root, f"/api/admin/users/{me}/platform-role", {"role": "none", "reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "self_assignment"
    # a role without assigning permissions may be added to oneself (it adds nothing to a super admin)
    assert post(root, f"/api/admin/users/{me}/platform-grants", {"role": "auditor", "reason": REASON}).status_code == 200
    # the explanation names the refusal the service would give, grant by grant, so the page can say it
    mine = root.get(f"/api/admin/users/{me}/platform-access").json()
    assert {g["role"]: g["revoke_refusal"] for g in mine["grants"]} == {SUPER_ADMIN: "self_assignment", "auditor": None}
    assert mine["assign_refusal"] is None and SUPER_ADMIN not in mine["assignable_roles"]
    # an access administrator holds users.manage but cannot grant anything
    access_admin = world["login"](holder(world, "access_admin"))
    reauth(access_admin)
    r = post(access_admin, f"/api/admin/users/{world['plain']['id']}/platform-grants", {"role": "support", "reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied"
    seen = access_admin.get(f"/api/admin/users/{me}/platform-access").json()
    assert seen["assignable_roles"] == [] and seen["assign_refusal"] == "platform_permission_denied"
    assert {g["revoke_refusal"] for g in seen["grants"]} == {"platform_permission_denied"}
    # the service refuses a role wider than the actor's own permissions, and super_admin without assign_super
    access = PlatformAccess(store)
    with pytest.raises(ServiceError) as e:
        access.grant(world["plain"]["id"], world["plain"]["id"], "auditor", REASON)
    assert e.value.code == "platform_permission_denied"
    assert PlatformAccess(store).is_usable_super_admin(me)


# ---------------------------------------------------------------- the historical data
def _legacy_workspace(store) -> None:
    """The pre-identity data as the v1 migration leaves it (fixture): no members, pending_claim."""
    ts = now()
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES ('default','历史数据（待认领）','legacy','pending_claim',?,?)", (ts, ts))


def _assigned_legacy_workspace(w) -> dict:
    """Fixture set-up: the pre-identity data assigned by the super administrator, through the real
    claim route, to an ordinary account (the only kind that may receive it); returns that account."""
    from synwevo_core.apps import bootstrap_docs_app
    bootstrap_docs_app(w["store"], w["ident"])
    _legacy_workspace(w["store"])
    owner = verified(w["store"], w["ident"], "legacy-owner@example.test")
    root = w["login"](w["root"])
    reauth(root)
    r = post(root, f"/api/admin/workspaces/{LEGACY_WID}/claim", {"user_id": owner["id"], "reason": REASON})
    assert r.status_code == 200, r.text
    return owner


def test_the_historical_data_goes_only_from_a_super_administrator_to_an_ordinary_account(world):
    """A platform role is never the way to a document: an account and access administrator may
    freeze workspaces but not claim the historical data (least of all for themselves), and a
    super administrator may hand it only to an account that holds no platform grant."""
    from synwevo_core.apps import bootstrap_docs_app
    store, ident = world["store"], world["ident"]
    bootstrap_docs_app(store, ident)
    _legacy_workspace(store)
    access_admin = holder(world, "access_admin")
    aa = world["login"](access_admin)
    reauth(aa)
    r = post(aa, "/api/admin/workspaces/default/claim", {"user_id": access_admin["id"], "reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", r.text
    assert r.json()["detail"]["permission"] == "platform.workspaces.claim"
    root = world["login"](world["root"])
    reauth(root)
    r = post(root, "/api/admin/workspaces/default/claim", {"user_id": world["root"]["id"], "reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "self_claim", r.text
    for other in (access_admin, holder(world, "support")):
        r = post(root, "/api/admin/workspaces/default/claim", {"user_id": other["id"], "reason": REASON})
        assert r.status_code == 409 and r.json()["error"] == "target_is_platform_admin", r.text
    assert ident.get_workspace("default")["status"] == "pending_claim"
    assert not store.one("SELECT 1 AS x FROM memberships WHERE workspace_id='default'")
    r = post(root, "/api/admin/workspaces/default/claim", {"user_id": world["plain"]["id"], "reason": REASON})
    assert r.status_code == 200, r.text
    assert ident.membership("default", world["plain"]["id"])["role"] == "owner"
    assert ident.membership("default", world["root"]["id"]) is None


# ---------------------------------------------------------------- a lower administrator and a higher one
def _failed_reset_mail(store, user) -> str:
    """A reset mail the transport refused (fixture row): retryable by `platform.mail.manage`."""
    mid = "mail_fixture_reset"
    store.execute("INSERT INTO mail_outbox (id,to_addr,subject,body,kind,ref_id,status,attempts,last_error,created_at) "
                  "VALUES (?,?,'fixture','fixture','reset',?,'failed',3,'smtp_authentication_failed',?)",
                  (mid, user["email"], user["id"], now() - 7200))
    return mid


def test_a_lower_administrator_does_not_act_on_a_higher_one(world):
    store = world["store"]
    root_client = world["login"](world["root"])
    support_user, access_user = holder(world, "support"), holder(world, "access_admin")
    support = world["login"](support_user)
    reauth(support)
    for path in (f"/api/admin/users/{world['root']['id']}/revoke-sessions", f"/api/admin/users/{world['root']['id']}/recovery",
                 f"/api/admin/users/{access_user['id']}/revoke-sessions"):
        r = post(support, path, {"reason": REASON})
        assert r.status_code == 403 and r.json()["error"] == "target_outranks_actor", (path, r.text)
        assert "platform.users.manage" in r.json()["detail"]["permissions"]
    # nothing happened to the super administrator: still signed in, no reset mail queued
    assert root_client.get("/api/admin/overview").status_code == 200
    assert not store.one("SELECT 1 AS x FROM mail_outbox WHERE ref_id=?", (world["root"]["id"],))
    # an account at or below the actor's permissions is theirs to support
    for target in (world["plain"], holder(world, "support", "support-2@example.test")):
        r = post(support, f"/api/admin/users/{target['id']}/revoke-sessions", {"reason": REASON})
        assert r.status_code == 200, r.text
    # retrying a super administrator's reset mail is acting on them too
    mid = _failed_reset_mail(store, world["root"])
    access = world["login"](access_user)
    reauth(access)
    r = post(access, f"/api/admin/mail/{mid}/retry", {"reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "target_outranks_actor", r.text
    assert store.one("SELECT last_error FROM mail_outbox WHERE id=?", (mid,))["last_error"] == "smtp_authentication_failed"
    # audited, once per actor and account per window
    rows = store.all("SELECT * FROM audit_events WHERE action='admin.access.denied' AND object_kind='user' ORDER BY seq")
    assert [(r["actor_id"], r["object_id"]) for r in rows] == [
        (support_user["id"], world["root"]["id"]), (support_user["id"], access_user["id"]),
        (access_user["id"], world["root"]["id"])]
    assert all(json.loads(r["meta"])["code"] == "target_outranks_actor" for r in rows)
    # the listing marks the rows the page should not offer
    listed = {u["email"]: u["outranks_caller"] for u in support.get("/api/admin/users?limit=200").json()["items"]}
    assert listed["root@example.test"] and listed["access_admin@example.test"]
    assert not listed["plain@example.test"] and not listed["support@example.test"]


def test_restoring_is_for_suspended_accounts_and_by_an_administrator_who_outranks_them(world):
    from synwevo_core import journal
    store, ident = world["store"], world["ident"]
    second = make_platform_admin(store, verified(store, ident, "second-super@example.test"))
    # the second super administrator asks to delete their account: pending_delete, grants not counted
    c2 = world["login"](second)
    assert post(c2, "/api/auth/deletion", {"password": PW}).status_code == 200
    assert ident.get_user(second["id"])["status"] == "pending_delete"
    assert PlatformAccess(store).effective_permissions(second["id"]) == frozenset()
    access = world["login"](holder(world, "access_admin"))
    reauth(access)
    root = world["login"](world["root"])
    reauth(root)
    pending = ident.create_user("pending-restore@example.test", PW)
    for client in (access, root):
        for target in (second, pending, world["plain"]):
            r = post(client, f"/api/admin/users/{target['id']}/status", {"status": "active", "reason": REASON})
            assert r.status_code == 409 and r.json()["error"] == "not_suspended", r.text
    assert ident.get_user(second["id"])["status"] == "pending_delete", "恢复不能让正在删除的最高管理员复活"
    assert ident.get_user(pending["id"])["status"] == "pending", "恢复不能跳过邮箱验证"
    # A suspended account that still holds grants (a backup restored and the journal replayed
    # over it): restoring it brings them back, so only an administrator who outranks it may.
    third = make_platform_admin(store, verified(store, ident, "third-super@example.test"))
    journal.apply(store, [{"kind": "user_suspended", "user_id": third["id"], "at": now()}])
    assert ident.get_user(third["id"])["status"] == "suspended"
    r = post(access, f"/api/admin/users/{third['id']}/status", {"status": "active", "reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "target_outranks_actor", r.text
    assert ident.get_user(third["id"])["status"] == "suspended"
    preview = post(root, f"/api/admin/users/{third['id']}/status-preview", {"status": "active"})
    assert preview.status_code == 200, preview.text
    r = post(root, f"/api/admin/users/{third['id']}/status",
             {"status": "active", "reason": REASON, "preview_token": preview.json()["preview_token"],
              "idempotency_key": "fixture-restore-third-super"})
    assert r.status_code == 200 and ident.get_user(third["id"])["status"] == "active"


# ---------------------------------------------------------------- the last usable super administrator
def test_last_super_admin_under_revocation_suspension_and_self_deletion(world):
    store, ident = world["store"], world["ident"]
    access = PlatformAccess(store)
    root_grant = grant_of(world, world["root"], SUPER_ADMIN)
    # a super administrator who cannot complete the step-up does not count as usable ...
    no_factor = make_platform_admin(store, verified(store, ident, "no-factor-super@example.test", password=None))
    assert not access.is_usable_super_admin(no_factor["id"]) and len(ident.platform_admins()) == 1
    # ... so it cannot remove the last one who can
    with pytest.raises(ServiceError) as e:
        access.revoke(no_factor["id"], root_grant["id"], REASON)
    assert e.value.code == "last_super_admin"
    # suspension: an access administrator may suspend accounts, never the last usable super admin
    access_admin = world["login"](holder(world, "access_admin"))
    reauth(access_admin)
    r = post(access_admin, f"/api/admin/users/{world['root']['id']}/status", {"status": "suspended", "reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "last_super_admin"
    support = holder(world, "support")
    r = post(access_admin, f"/api/admin/users/{support['id']}/status", {"status": "suspended", "reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "demote_first"
    assert ident.get_user(world["root"]["id"])["status"] == "active" and ident.get_user(support["id"])["status"] == "active"
    # self-deletion
    root = world["login"](world["root"])
    r = post(root, "/api/auth/deletion", {"password": PW})
    assert r.status_code == 409 and r.json()["error"] == "last_super_admin"
    assert len(ident.platform_admins()) == 1


def test_two_super_admins_revoking_each_other_at_once_leave_exactly_one(world, monkeypatch):
    store, ident = world["store"], world["ident"]
    second = make_platform_admin(store, verified(store, ident, "second-super@example.test"))
    a, b = world["login"](world["root"]), world["login"](second)
    reauth(a)
    reauth(b)
    grant_a, grant_b = grant_of(world, world["root"], SUPER_ADMIN), grant_of(world, second, SUPER_ADMIN)
    # Both requests pass their first permission check before either enters the write
    # transaction. The second check and service execute under the writer lock; putting the
    # barrier inside that lock would deadlock the fixture instead of exercising the race.
    from synwevo_identity import admin_routes
    admitted, results = threading.Barrier(2, timeout=10), {}
    original = admin_routes.require_platform

    def after_admission(request, *args, **kwargs):
        actor = original(request, *args, **kwargs)
        if not getattr(request.state, "fixture_admission_synchronized", False):
            request.state.fixture_admission_synchronized = True
            admitted.wait()
        return actor

    monkeypatch.setattr(admin_routes, "require_platform", after_admission)

    def revoke(name, client, gid):
        results[name] = post(client, f"/api/admin/platform-grants/{gid}/revoke", {"reason": REASON})

    threads = [threading.Thread(target=revoke, args=("a", a, grant_b["id"])),
               threading.Thread(target=revoke, args=("b", b, grant_a["id"]))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    codes = sorted(r.status_code for r in results.values())
    # The writer-lock recheck sees the losing caller's only grant has ended. As for every
    # caller with no platform permission, that is the same masked 404 as an absent route.
    assert codes == [200, 404], {k: v.text for k, v in results.items()}
    loser = next(r for r in results.values() if r.status_code != 200)
    assert loser.json()["error"] == "not_found"
    assert len(ident.platform_admins()) == 1
    assert store.one("SELECT COUNT(*) AS n FROM platform_role_grants WHERE role='super_admin' AND revoked_at IS NULL")["n"] == 1
    assert store.one("SELECT COUNT(*) AS n FROM users WHERE platform_role='admin'")["n"] == 1, "回滚镜像同步"


def _passkey(store, user, rp_id: str) -> None:
    """A registered, user-verifying passkey row (fixture: public material only, never used to sign)."""
    store.execute("INSERT INTO passkeys (id,user_id,credential_id,public_key,rp_id,uv_capable,name,created_at) "
                  "VALUES (?,?,?,?,?,1,'fixture key',?)",
                  (f"pk_{user['id']}_{rp_id}", user["id"], f"cred-{user['id']}-{rp_id}", "fixture-public-key", rp_id, now()))


def test_existing_host_granted_passwordless_admin_with_current_uv_is_usable(world):
    """Existing/host-granted capability fixture. Actual UV succession is independently
    exercised in test_admin_handover; this metadata does not prove acceptance."""
    store, ident = world["store"], world["ident"]
    target = verified(store, ident, "passkey-only@example.test", password=None)
    from synwevo_identity.passkeys import rp_id
    _passkey(store, target, rp_id())
    step = PlatformAccess(store).step_up(target["id"])
    assert step["capable"] and step["reauth"]
    root = world["login"](world["root"])
    reauth(root)
    result = post(root, f"/api/admin/users/{target['id']}/platform-grants", {"role": SUPER_ADMIN, "reason": REASON})
    assert result.status_code == 409 and result.json()["error"] == "super_admin_acceptance_required"
    make_platform_admin(store, target)  # Explicit host fixture, never acceptance evidence.
    assert {u["id"] for u in ident.platform_admins()} == {world["root"]["id"], target["id"]}
    PlatformAccess(store).revoke(target["id"], grant_of(world, world["root"], SUPER_ADMIN)["id"], REASON)
    assert [u["id"] for u in ident.platform_admins()] == [target["id"]]
    assert not ident.bootstrap_needed()


def test_a_passkey_bound_to_an_earlier_domain_is_not_a_way_to_confirm_identity(world):
    store, ident = world["store"], world["ident"]
    stale = verified(store, ident, "stale-passkey@example.test", password=None)
    _passkey(store, stale, "old-accounts.example.test")
    assert PlatformAccess(store).step_up(stale["id"])["capable"] is False
    root = world["login"](world["root"])
    reauth(root)
    r = post(root, f"/api/admin/users/{stale['id']}/platform-grants", {"role": "auditor", "reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "target_cannot_step_up", r.text
    from synwevo_identity.passkeys import rp_id
    assert platform_access.current_rp_id() == rp_id(), "the core reads the RP ID by the account service's rule"


# ---------------------------------------------------------------- sessions: tightening and revocation
def test_a_grant_tightens_the_grantees_sessions_and_a_revocation_applies_on_the_next_request(world):
    store = world["store"]
    target = world["plain"]
    remembered = world["login"](target, remember=True)
    row = store.one("SELECT * FROM sessions WHERE user_id=? AND revoked_at IS NULL", (target["id"],))
    assert row["persistent"] == 1 and row["idle_expires_at"] - row["last_seen_at"] > ADMIN_IDLE_SECONDS
    assert remembered.get("/api/admin/overview").status_code == 404
    root = world["login"](world["root"])
    reauth(root)
    granted = post(root, f"/api/admin/users/{target['id']}/platform-grants", {"role": "support", "reason": REASON})
    assert granted.status_code == 200 and granted.json()["sessions_tightened"] == 1
    row = store.one("SELECT * FROM sessions WHERE id=?", (row["id"],))
    assert row["persistent"] == 0 and row["idle_expires_at"] <= row["last_seen_at"] + ADMIN_IDLE_SECONDS
    # the same browser session now has the permission, from the next request on
    assert remembered.get("/api/admin/overview").status_code == 200
    assert remembered.get("/api/admin/users").status_code == 200
    r = post(root, f"/api/admin/platform-grants/{granted.json()['grant']['id']}/revoke", {"reason": REASON})
    assert r.status_code == 200
    assert remembered.get("/api/admin/overview").status_code == 404, "撤权在下一个请求生效，不等重新登录"
    assert remembered.get("/api/auth/me").json()["platform_permissions"] == []


def test_expired_grants_are_ignored_and_can_be_granted_again(world, monkeypatch):
    target = world["plain"]
    root = world["login"](world["root"])
    reauth(root)
    assert post(root, f"/api/admin/users/{target['id']}/platform-grants",
                {"role": SUPER_ADMIN, "reason": REASON, "expires_at": now() + 3600}).json()["error"] == "super_admin_no_expiry"
    assert post(root, f"/api/admin/users/{target['id']}/platform-grants",
                {"role": "auditor", "reason": REASON, "expires_at": now() + 10}).json()["error"] == "bad_expiry"
    r = post(root, f"/api/admin/users/{target['id']}/platform-grants",
             {"role": "auditor", "reason": REASON, "expires_at": now() + 600})
    assert r.status_code == 200
    first_grant = r
    c = world["login"](target)
    assert c.get("/api/admin/audit").status_code == 200
    later = now() + 700
    monkeypatch.setattr(platform_access, "_now", lambda: later)     # the clock, not the data, moves
    assert c.get("/api/admin/audit").status_code == 404
    assert c.get("/api/auth/me").json()["platform_roles"] == []
    assert target["id"] not in {row["user"]["id"] for row in root.get("/api/admin/platform/roles").json()["roster"]}
    first = first_grant.json()["grant"]["id"]
    r = post(root, f"/api/admin/users/{target['id']}/platform-grants", {"role": "auditor", "reason": REASON})
    assert r.status_code == 200 and r.json()["after"] == ["auditor"]
    assert c.get("/api/admin/audit").status_code == 200
    # the expired row the new grant replaced is named in the grant's own record
    ev = world["store"].one("SELECT meta FROM audit_events WHERE action='admin.platform.grant' AND object_id=? "
                            "ORDER BY seq DESC LIMIT 1", (target["id"],))
    assert json.loads(ev["meta"])["replaced_expired_grant"] == first
    assert world["store"].one("SELECT revoked_by FROM platform_role_grants WHERE id=?", (first,))["revoked_by"] == "system:expired"


# ---------------------------------------------------------------- every change is audited
def test_the_audit_names_what_a_change_did_even_on_an_account_whose_grants_do_not_count(world):
    """before/after are the live grants, not the counted ones: ending a grant of a pending-deletion
    account is a change, and the record says so."""
    store, ident = world["store"], world["ident"]
    root = world["login"](world["root"])
    reauth(root)
    target = verified(store, ident, "leaving@example.test")
    granted = post(root, f"/api/admin/users/{target['id']}/platform-grants", {"role": "support", "reason": REASON}).json()
    assert post(world["login"](target), "/api/auth/deletion", {"password": PW}).status_code == 200
    assert PlatformAccess(store).roles(target["id"]) == []
    r = post(root, f"/api/admin/platform-grants/{granted['grant']['id']}/revoke", {"reason": REASON})
    assert r.status_code == 200 and r.json()["before"] == ["support"] and r.json()["after"] == []
    meta = json.loads(store.one("SELECT meta FROM audit_events WHERE action='admin.platform.revoke' AND object_id=?",
                                (target["id"],))["meta"])
    assert meta["before"] == {"roles": ["support"]} and meta["after"] == {"roles": []} and meta["changed"] == ["roles"]


def test_a_finalized_deletion_records_each_grant_it_ends(world):
    from synwevo_identity.profile import finalize_deletions
    store, ident = world["store"], world["ident"]
    leaving = holder(world, "auditor", "leaving-auditor@example.test")
    PlatformAccess(store).host_grant(leaving["id"], "support", source="fixture:tests", reason="test fixture: second role")
    store.execute("UPDATE users SET status='pending_delete', deletion_due_at=? WHERE id=?", (now() - 1, leaving["id"]))
    assert finalize_deletions(store) == 1
    assert not PlatformAccess(store).live_grants(leaving["id"])
    rows = store.all("SELECT * FROM audit_events WHERE action='admin.platform.revoke' AND object_id=? ORDER BY seq",
                     (leaving["id"],))
    metas = [json.loads(r["meta"]) for r in rows]
    assert sorted(m["role"] for m in metas) == ["auditor", "support"]
    assert all(m["source"] == "system:account_deleted" and m["reason"] == "账户已删除" for m in metas)
    assert metas[-1]["after"] == {"roles": []} and all(r["actor_kind"] == "system" for r in rows)


def test_a_replayed_deletion_ends_the_grants_with_the_account(world, tmp_path):
    """journal.apply of `user_deleted` writes platform_role='none'; the grants end in the same
    transaction, recorded as a deletion — not left for the next start to "reconcile" as if the
    previous release had demoted the account during a rollback."""
    from synwevo_core import journal
    store, ident = world["store"], world["ident"]
    second = make_platform_admin(store, verified(store, ident, "replayed-super@example.test"))
    journal.apply(store, [{"kind": "user_deleted", "user_id": second["id"], "at": now()}])
    assert store.one("SELECT platform_role FROM users WHERE id=?", (second["id"],))["platform_role"] == "none"
    assert not PlatformAccess(store).live_grants(second["id"]), "镜像与授予在同一事务里一致"
    ended = store.one("SELECT * FROM platform_role_grants WHERE user_id=?", (second["id"],))
    assert ended["revoked_by"] == "system:journal_replay"
    Store(store.root)                                   # the next start reconciles nothing
    rows = store.all("SELECT meta FROM audit_events WHERE action='admin.platform.revoke' AND object_id=?", (second["id"],))
    assert [json.loads(r["meta"])["source"] for r in rows] == ["system:journal_replay"]


# ---------------------------------------------------------------- explanation, roster, session
def test_session_roster_and_explanation(world):
    store, ident = world["store"], world["ident"]
    root = world["login"](world["root"])
    s = root.get("/api/admin/session").json()
    assert s["roles"] == [SUPER_ADMIN] and s["step_up"]["required"] is False
    assert {p["permission"] for p in s["permissions"]} == set(PERMISSIONS)
    assert s["reauth"]["seconds_left"] == 0 and s["environment"]["instance"] == store.instance_kind()
    reauth(root)
    assert 0 < root.get("/api/admin/session").json()["reauth"]["seconds_left"] <= s["reauth"]["window_s"]
    holder(world, "auditor")
    roster = root.get("/api/admin/platform/roles").json()
    assert [r["id"] for r in roster["roles"]] == list(ROLE_ORDER) and roster["super_admins"] == 1
    by_email = {row["user"]["email"]: row for row in roster["roster"]}
    assert by_email["root@example.test"]["roles"] == [SUPER_ADMIN]
    assert by_email["root@example.test"]["step_up"] == {"capable": True, "password": True, "passkey_uv": False,
                                                         "totp": False, "reauth": True, "second_factor": False}
    assert by_email["root@example.test"]["last_sign_in_at"]
    assert by_email["auditor@example.test"]["roles"] == ["auditor"]
    assert "plain@example.test" not in by_email
    # Static permission descriptions may say "non-secret"; the dynamic account/grant payload
    # still contains neither secret fields nor password material.
    assert "secret" not in json.dumps(roster["roster"]) and "phc" not in json.dumps(roster["roster"])
    serialized = json.dumps(roster)
    for password in store.all("SELECT phc FROM user_passwords"):
        assert password["phc"] not in serialized


# ---------------------------------------------------------------- migration and the rollback mirror
def _as_v26(store: Store, rows: dict[str, str]) -> None:
    """Put a v27 database back into the shape v26 wrote: no grants table, no migration mark,
    schema 26, and `platform_role` holding the role itself."""
    with store.tx() as t:
        t.execute("DROP TABLE platform_role_grants")
        t.execute("DELETE FROM meta WHERE key='platform_grants.migrated'")
        t.execute("UPDATE meta SET value='26' WHERE key='schema_version'")
        for uid, role in rows.items():
            t.execute("UPDATE users SET platform_role=? WHERE id=?", (role, uid))


def test_migration_26_to_27_makes_admins_super_admins_and_clears_operators(tmp_path):
    store = Store(tmp_path / "d")
    ident = Identity(store)
    # the production shape: an administrator created by bootstrap, its address never verified
    admin = ident.create_user("owner@example.test", PW, status="active")
    operator = ident.create_user("operator@example.test", PW, status="active")
    plain = ident.create_user("member@example.test", PW, status="active")
    _as_v26(store, {admin["id"]: "admin", operator["id"]: "operator"})
    migrated = Store(tmp_path / "d")
    assert migrated.schema_version() == 27
    grants = migrated.all("SELECT * FROM platform_role_grants")
    assert [(g["user_id"], g["role"], g["granted_by"]) for g in grants] == [(admin["id"], SUPER_ADMIN, "migration:v27")]
    access = PlatformAccess(migrated)
    assert access.is_usable_super_admin(admin["id"]), "迁移授予的最高管理员不因邮箱未验证而被锁在外面"
    assert Identity(migrated).platform_admins()[0]["id"] == admin["id"] and not Identity(migrated).bootstrap_needed()
    users = {u["id"]: u["platform_role"] for u in migrated.all("SELECT id, platform_role FROM users")}
    assert users == {admin["id"]: "admin", operator["id"]: "none", plain["id"]: "none"}
    assert access.effective_permissions(operator["id"]) == frozenset()
    audit = {r["action"]: r for r in migrated.all("SELECT * FROM audit_events WHERE actor_kind='system'")}
    assert json.loads(audit["admin.platform.operator_cleared"]["meta"])["before"] == {"platform_role": "operator"}
    assert json.loads(audit["admin.platform.grant"]["meta"])["source"] == "migration:v27"
    # opening again changes nothing
    Store(tmp_path / "d")
    assert migrated.one("SELECT COUNT(*) AS n FROM platform_role_grants")["n"] == 1
    assert migrated.one("SELECT COUNT(*) AS n FROM audit_events WHERE actor_kind='system'")["n"] == 2


def test_the_rollback_mirror_follows_every_grant_and_carries_a_rollbacks_changes_forward(tmp_path):
    store = Store(tmp_path / "d")
    ident = Identity(store)
    a = make_platform_admin(store, verified(store, ident, "a@example.test"))
    b = make_platform_admin(store, verified(store, ident, "b@example.test"))
    delegated = make_platform_admin(store, verified(store, ident, "d@example.test"), role="access_admin")
    role_of = lambda u: ident.get_user(u["id"])["platform_role"]  # noqa: E731
    assert (role_of(a), role_of(b), role_of(delegated)) == ("admin", "admin", "none"), \
        "委派管理员的镜像是 none：回到上一版本时它失去权限，而不是变宽"
    PlatformAccess(store).revoke(a["id"], grant_of({"store": store}, b, SUPER_ADMIN)["id"], REASON)
    assert role_of(b) == "none"
    # the previous release, during a rollback, demoted `a` and promoted `delegated` in the column
    make_platform_admin(store, b)  # Host fixture for mirror behavior, not an ordinary handover.
    legacy_platform_role_row(store, a, "none")
    legacy_platform_role_row(store, delegated, "admin")
    reopened = Store(tmp_path / "d")
    access = PlatformAccess(reopened)
    assert SUPER_ADMIN not in access.roles(a["id"]), "回滚期间的降级在升级后仍然有效"
    assert access.roles(delegated["id"]) == [SUPER_ADMIN, "access_admin"]
    carried = grant_of({"store": reopened}, delegated, SUPER_ADMIN)
    assert carried["granted_by"] == "reconcile:mirror"
    assert (role_of(a), role_of(b), role_of(delegated)) == ("none", "admin", "admin")


def _enable_totp(c):
    """The real enrolment path (test_mfa): recent re-authentication, begin, confirm; returns the secret."""
    import pyotp
    assert post(c, "/api/auth/mfa/reauth", {"password": PW}).status_code == 200
    secret = post(c, "/api/auth/mfa/totp/begin").json()["secret"]
    r = post(c, "/api/auth/mfa/totp/confirm", {"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return secret


def _login_with_totp(api, email, secret):
    import pyotp, time
    c = TestClient(api)
    step = post(c, "/api/auth/login", {"email": email, "password": PW}).json()
    assert step.get("mfa_required") and step.get("ticket"), step
    r = post(c, "/api/auth/login/mfa", {"ticket": step["ticket"], "code": pyotp.TOTP(secret).at(int(time.time()) + 30)})
    assert r.status_code == 200 and r.json()["session"]["amr"] == "password+totp", r.text
    return c


def test_the_admin_second_factor_policy_locks_password_only_administrators_until_they_prove_one(world):
    """A11: requiring a second factor is refused while nobody could meet it; once on, a password-only
    administrator session is locked out of administration (not out of the account) with the
    reason and whether they have enrolled; a TOTP sign-in or a user-verified passkey meets it."""
    w, store = world, world["store"]
    root = w["login"](w["root"])
    policy = root.get("/api/admin/security/second-factor").json()
    assert policy["mode"] == "off" and policy["source"] == "default" and policy["enrolled_super_admins"] == 0
    assert policy["usable_super_admins"] == 1 and policy["caller_second_factor"] is False
    reauth(root)
    refused = post(root, "/api/admin/security/second-factor", {"mode": "required", "expected_mode": "off", "reason": REASON})
    assert refused.status_code == 409 and refused.json()["error"] == "no_second_factor_admin"
    assert root.get("/api/admin/overview").status_code == 200, "nothing changed"
    secret = _enable_totp(root)
    reauth(root)
    enabled = post(root, "/api/admin/security/second-factor", {"mode": "required", "expected_mode": "off", "reason": REASON})
    assert enabled.status_code == 200 and enabled.json()["mode"] == "required" and enabled.json()["enrolled_super_admins"] == 1
    # This very session proved the password only: administration waits; the account does not.
    denied = root.get("/api/admin/overview")
    assert denied.status_code == 403 and denied.json()["error"] == "step_up_required"
    assert denied.json()["detail"] == {"scope": "admin", "policy": "admin_second_factor", "second_factor_enrolled": True}
    session = root.get("/api/admin/session").json()["step_up"]
    assert session["required"] is True and session["reason"] == "second_factor" and session["policy"] == "required"
    me = root.get("/api/auth/me").json()
    assert me["admin_locked"] is True and me["admin_lock"] == {"reason": "second_factor", "second_factor_enrolled": True}
    assert me["permissions"] == [] and me["platform_roles"] == ["super_admin"]
    reauth(root)  # the password again is the same factor
    assert root.get("/api/admin/overview").status_code == 403
    strong = _login_with_totp(w["api"], w["root"]["email"], secret)
    assert strong.get("/api/admin/overview").status_code == 200
    assert strong.get("/api/auth/me").json()["admin_locked"] is False
    # An administrator with no second factor at all: locked, told to enrol, still signed in.
    other = w["login"](holder(w, "access_admin"))
    denied = other.get("/api/admin/users")
    assert denied.status_code == 403 and denied.json()["detail"]["second_factor_enrolled"] is False
    assert other.get("/api/auth/me").json()["admin_lock"] == {"reason": "second_factor", "second_factor_enrolled": False}
    assert other.get("/api/admin/session").status_code == 200, "a locked administrator still learns what is missing"
    # A grace window (CR-102): the requirement stays on, the lock waits; the account page warns instead.
    reauth(strong)
    assert post(strong, "/api/admin/security/second-factor", {"mode": "required", "expected_mode": "required", "grace_days": 40, "reason": REASON}).json()["error"] == "bad_grace_days"
    graced = post(strong, "/api/admin/security/second-factor", {"mode": "required", "expected_mode": "required", "grace_days": 7, "reason": REASON})
    assert graced.status_code == 200 and graced.json()["mode"] == "required", graced.text
    assert 6 * 86400 < graced.json()["grace_until"] - time.time() <= 7 * 86400 and graced.json()["grace_max_days"] == 30
    assert root.get("/api/admin/overview").status_code == 200 and other.get("/api/admin/users").status_code == 200, "warned, not locked"
    warned = other.get("/api/auth/me").json()
    assert warned["admin_locked"] is False and warned["admin_lock"] is None
    assert warned["admin_grace"] == {"until": graced.json()["grace_until"], "second_factor_enrolled": False}
    assert strong.get("/api/auth/me").json()["admin_grace"] is None, "a strong session has nothing to be warned about"
    step = other.get("/api/admin/session").json()["step_up"]
    assert step["required"] is False and step["grace_until"] == graced.json()["grace_until"] and step["policy"] == "required"
    store.execute("UPDATE meta SET value=? WHERE key='security.admin_second_factor_grace_until'", (str(time.time() - 1),))
    assert other.get("/api/admin/users").status_code == 403 and other.get("/api/auth/me").json()["admin_grace"] is None, "the window ended: locked"
    # Off again restores the standing rule for everyone.
    reauth(strong)
    assert post(strong, "/api/admin/security/second-factor", {"mode": "off", "expected_mode": "required", "reason": REASON}).status_code == 200
    assert store.one("SELECT value FROM meta WHERE key='security.admin_second_factor_grace_until'") is None
    assert other.get("/api/admin/users").status_code == 200 and root.get("/api/admin/overview").status_code == 200
    stale = post(strong, "/api/admin/security/second-factor", {"mode": "required", "expected_mode": "required", "reason": REASON})
    assert stale.status_code == 409 and stale.json()["error"] == "policy_changed"
    assert [json.loads(r["meta"])["mode"] for r in store.all("SELECT meta FROM audit_events WHERE action='admin.security.second_factor' ORDER BY seq")] == ["required", "required", "off"]


#: The six digits in a sign-in code mail, in any of the three languages (test_email_code_login).
EMAIL_CODE = re.compile(r"(?:登录验证码|登入驗證碼|Sign-in code)[:：]\s*(\d{6})")


def _login_with_email_code(api, store, email):
    """A sign-in by mailed code: the code is read from the file mail sink's outbox row, as
    test_email_code_login does. Returns the client and the verify answer — a session, or the
    MFA step (`mfa_required`, `ticket`) when the account has TOTP."""
    from synwevo_identity.email_login import START_MIN_GAP_S
    c = TestClient(api)
    # A second request within the minimum gap is acknowledged without a new code; the test
    # stands in for the wait by ageing the account's earlier requests.
    store.execute("UPDATE login_codes SET created_at=created_at-? WHERE user_id=(SELECT id FROM users WHERE email=?)",
                  (START_MIN_GAP_S + 1, email))
    assert c.post("/api/auth/login/email/start", json={"email": email}).status_code == 202
    row = store.one("SELECT body FROM mail_outbox WHERE kind='login_code' AND to_addr=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (email,))
    assert row, "no sign-in code mail was queued"
    code = EMAIL_CODE.search(row["body"])
    assert code, row["body"]
    r = c.post("/api/auth/login/email/verify", json={"email": email, "code": code.group(1)})
    assert r.status_code == 200, r.text
    return c, r.json()


@pytest.mark.parametrize("amr, strong", [
    ("password+totp", True), ("email+totp", True), ("idp:google+totp", True), ("passkey_uv", True),
    ("email+passkey_uv", True), ("password+recovery", True), ("email+recovery", True),
    ("password", False), ("email", False), ("idp:google", False), ("passkey", False), ("", False),
    # two different one-factor proofs: admin-capable under the standing rule, not the A11 second factor
    ("email+password", False), ("idp:google+password", False), ("password+email", False), ("passkey+password", False),
])
def test_a_strong_session_proved_totp_or_a_user_verified_passkey_not_any_two_factors(amr, strong):
    from synwevo_core.sessions import admin_capable
    assert platform_access.session_strong(amr) is strong
    if strong:
        assert admin_capable(amr), "every strong session is also admin-capable under the standing rule"


def test_the_admin_second_factor_policy_is_not_met_by_a_mailed_code_plus_the_password(world):
    """A11 (review C1): an administrator with neither TOTP nor a passkey signs in by mailed code
    and re-enters the password — `email+password` is two one-factor proofs, admin-capable under
    the standing rule but not the second factor the policy requires — and stays locked with the
    reason and "not enrolled". The same sign-in finished with a TOTP code (`email+totp`) is let in."""
    w, store = world, world["store"]
    root = w["login"](w["root"])
    _enable_totp(root)
    reauth(root)
    enabled = post(root, "/api/admin/security/second-factor", {"mode": "required", "expected_mode": "off", "reason": REASON})
    assert enabled.status_code == 200 and enabled.json()["mode"] == "required", enabled.text
    admin = holder(w, "access_admin")
    assert PlatformAccess(store).step_up(admin["id"])["second_factor"] is False
    c, issued = _login_with_email_code(w["api"], store, admin["email"])
    assert issued["session"]["amr"] == "email"
    one = c.get("/api/admin/users")
    assert one.status_code == 403 and one.json()["error"] == "step_up_required" and one.json()["detail"] == {"scope": "admin"}
    stepped = post(c, "/api/auth/reauth", {"password": PW, "scope": "admin"})
    assert stepped.status_code == 200 and stepped.json()["amr"] == "email+password", stepped.text
    denied = c.get("/api/admin/users")
    assert denied.status_code == 403 and denied.json()["error"] == "step_up_required", denied.text
    assert denied.json()["detail"] == {"scope": "admin", "policy": "admin_second_factor", "second_factor_enrolled": False}
    me = c.get("/api/auth/me").json()
    assert me["admin_locked"] is True and me["admin_lock"] == {"reason": "second_factor", "second_factor_enrolled": False}
    assert me["permissions"] == [] and me["platform_roles"] == ["access_admin"]
    step = c.get("/api/admin/session").json()["step_up"]
    assert step["required"] is True and step["reason"] == "second_factor" and step["amr"] == "email+password"
    assert post(c, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    assert c.get("/api/admin/users").status_code == 403, "the password again is still the same kind of proof"
    # The account page stays open to them: TOTP enrolled there, the next mailed-code sign-in asks
    # for the code, and `email+totp` is the second factor the policy means.
    import pyotp
    secret = _enable_totp(c)
    again, mfa = _login_with_email_code(w["api"], store, admin["email"])
    assert mfa.get("mfa_required") and mfa.get("ticket"), mfa
    r = post(again, "/api/auth/login/mfa", {"ticket": mfa["ticket"], "code": pyotp.TOTP(secret).at(int(time.time()) + 30)})
    assert r.status_code == 200 and r.json()["session"]["amr"] == "email+totp", r.text
    assert again.get("/api/admin/users").status_code == 200
    assert again.get("/api/auth/me").json()["admin_locked"] is False
    assert again.get("/api/admin/session").json()["step_up"]["required"] is False
    assert c.get("/api/admin/users").status_code == 403, "enrolling does not strengthen the session that only re-entered the password"


def test_platform_access_simulation_computes_the_change_without_making_it(world):
    """A5, simulation: what an account's effective permissions would be after one hypothetical
    grant or revocation, from the catalogue and the counting rules, plus whether the caller could
    really make that change — and nothing in the store moves."""
    w, store = world, world["store"]
    root = w["login"](w["root"])
    plain = w["plain"]["id"]
    path = f"/api/admin/users/{plain}/platform-access/simulate"
    assert root.get(path).json()["error"] == "bad_simulation"
    assert root.get(path + "?change=grant&role=owner").status_code == 400
    before_rows = store.all("SELECT id FROM platform_role_grants ORDER BY id")
    sim = root.get(path + "?change=grant&role=auditor").json()
    assert sim["before"] == {"roles": [], "permissions": []} and sim["after"]["roles"] == ["auditor"]
    assert sim["gained"] == sorted_permissions(ROLES["auditor"]) and sim["lost"] == [] and sim["feasible"] is True and sim["refusal"] is None
    assert sim["counts"] is True
    # The root cannot hand itself the assigning role again, nor drop the last usable super administrator.
    own = f"/api/admin/users/{w['root']['id']}/platform-access/simulate"
    assert root.get(own + "?change=grant&role=super_admin").json()["refusal"] == "self_assignment", "the self rule answers first, as the grant route would"
    drop = root.get(own + "?change=revoke&role=super_admin").json()
    assert drop["refusal"] == "self_assignment" and drop["feasible"] is False, "one's own assigning role: the self rule, as the revoke route answers"
    assert drop["after"]["permissions"] == [] and set(drop["lost"]) == set(PERMISSIONS), "the arithmetic is still shown"
    assert root.get(path + "?change=revoke&role=auditor").json()["refusal"] == "not_held"
    # An unverified address: a person's grant would not count yet, and the simulation says so.
    store.execute("UPDATE users SET email_verified_at=NULL WHERE id=?", (plain,))
    pending = root.get(path + "?change=grant&role=support").json()
    assert pending["counts"] is False and pending["gained"] == [] and pending["after"]["roles"] == []
    store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (clock(), plain))
    # An auditor reads the simulation but could not make the change; the refusal names why.
    aud = holder(w, "auditor")
    auditor = w["login"](aud)
    assert root.get(f"/api/admin/users/{aud['id']}/platform-access/simulate?change=grant&role=auditor").json()["refusal"] == "already_held"
    seen = auditor.get(path + "?change=grant&role=support").json()
    assert seen["gained"] == sorted_permissions(ROLES["support"]) and seen["feasible"] is False and seen["refusal"] == "platform_permission_denied"
    assert store.all("SELECT id FROM platform_role_grants ORDER BY id") == before_rows or len(store.all("SELECT id FROM platform_role_grants")) == len(before_rows) + 1, "only the auditor's own fixture grant was added; the simulation wrote nothing"
    assert PlatformAccess(store).roles(w["ident"].get_user(plain)) == []
