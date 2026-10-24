"""B1/B2: the application catalogue as platform administration governs it — an application on or
off platform-wide, its access policy, and on or off for one workspace — each previewed with its
impact and confirmed through the protocol of `admin_organizations.OrganizationLifecycle`.

Until now only the operator command line (`synwevo_identity.cli app`) switched applications, and
nothing at all wrote `apps.access_policy` after registration. The console calls the same service
methods the command line calls: `Apps.set_status` (disable / enable), `Apps.set_access_policy`
(policy_explicit / policy_members) and, with a `workspace_id`, `Apps.disable_for_workspace` /
`Apps.enable_for_workspace`. Per-person grants and revocations are not here: they stay with the
workspace's own administrators (members page) and the command line.

**Who may change what (decision D12).** A change for one workspace (enable / disable with a
`workspace_id`) needs `platform.apps.manage` (super_admin, access_admin). A platform-scope change —
disable, enable, policy_explicit, policy_members without a `workspace_id` — needs
`platform.config.manage` as well, which only the super administrator holds: the registry is
platform configuration, and loosening the policy opens every never-granted member of every
workspace to that application's content. Both are checked at the preview and again at the
confirmation (each a 403 `platform_permission_denied` naming the missing permission in
`detail.permission`, `platform.apps.manage` first).

Reads (`platform.apps.view`; counts and workspace metadata, never a person or any content):

* `GET /api/admin/apps` — every registered application: status, policy, launch URL, the
  workspaces that turned it on, `members_with_access` (the people who can open it now, in at least
  one workspace), the grants and revocations that apply to a current membership, and — Synwevo
  Docs only — its queued, running and cancelling jobs (`running_jobs`, null for every other
  application). Additive: `scope` (`workspace`, `organization` for messages, `personal` for the
  Agent) and `changes`, the platform-wide changes that would be accepted now.
* `GET /api/admin/apps/{app_id}/workspaces?q=&limit=&offset=` — the workspaces (not deleted) with,
  for this application, whether it is on, since when, the members and the members who can open it
  there. `q` matches a substring of the name or a whole id.

**Who can open it, and where.** `Apps.check_access(person, app, workspace)` — the rule the session
gate runs on every request, per workspace — restated as one statement (`_access_sql`) giving the
(person, workspace) pairs in which a live person account (active, not deleted, not an application
identity) can open the application; test_admin_apps pins it pair by pair. check_access leaves the
account's own state to the session gate, so a suspended member passes it but cannot sign in, and is
not counted. For `messages` (organization-scoped) and the personal `agent`, check_access asks only
for a live account: they are counted per person, and their policy and per-workspace switch do not
exist for the gate.

Changes (`platform.apps.manage`, plus `platform.config.manage` platform-wide):

* `POST /api/admin/apps/{app_id}/preview` `{"change", "workspace_id"?}` — not dangerous, changes
  nothing, read in one deferred read snapshot (no writer lock). `impact`, from the pairs before and
  after the change: `memberships_losing_access` / `memberships_gaining_access` (pairs; null for
  messages/agent), `people_losing_access` / `people_gaining_access` (the distinct people who lose or
  gain it in at least one workspace — every account keeps Synwevo Docs in its own personal
  workspace, so "anywhere" would hide a whole team losing a shared one), `workspaces_affected` (the
  workspaces with it on, or 1; null for messages/agent), `running_jobs` (below),
  `credentials_stopping` (Synwevo Docs only, else null: the active API-platform approvals whose
  approving owner loses Synwevo Docs in the approval's workspace — each governs one API key or
  service account, bound or still to be created, which then stops; 0 when nothing is taken away),
  `sessions_in_app` (live sessions — unrevoked, unexpired — whose person and workspace are a pair
  losing access; null for messages/agent). A signed five-minute plan binds the actor, the catalogued action
  (application, change, scope), the application, the workspace, the origin and a digest of the
  version: the application's status and policy, the workspace's state and switch, the pairs lost
  and gained (identities: a member joining the workspace makes the preview stale; for
  messages/agent, the number of live accounts) and the approvals that stop (a new one whose
  approver loses access makes it stale too).
* `POST /api/admin/apps/{app_id}/{change}` `{"reason", "preview_token", "idempotency_key",
  "workspace_id"?}` — dangerous: strict re-authentication before the body is read. In one write
  transaction: re-admission, the stored result for a repeated key, the refusals again, the plan,
  its expiry, the version; then the result as the change will leave things (computed first), the
  platform's audit row (`admin.app.<change>` or `admin.app.workspace_<change>`, with the workspace
  in its column and meta), the service method, and the receipt in `admin_operations`. A
  withdrawal's security-journal entry is written by the service method and is followed only by the
  receipt's INSERT and the COMMIT. The administrator gains no membership, entitlement or token.

`running_jobs` counts the Synwevo Docs jobs (queued, running, cancelling) that the runner allows now
(`JobService._authorized`: a live project, then `authority.check`) and refuses once the change is
made, read from the same after-state as the impact: switching off (platform-wide, or the job's
workspace) refuses every principal except pre-identity operator work; tightening refuses a person
(browser or delegation) whose (person, workspace) pair is lost, and an API key or service account
bound to an API-platform approval whose approving owner loses access (`credentials_stopping`). Keys
and service accounts are not entitlement-checked themselves — but an approved one answers through its
approver: `Approvals.active` → `require_owner` asks `check_access` of the approving owner, so an owner
promoted without a grant takes every credential they approved down with them under `explicit`
(`approver_unavailable`, for queued work and live calls alike). A job already refused (a deleted
project, a frozen workspace, a suspended submitter) is not counted. 0 when nothing is taken away;
null for other applications.

**Per organization (B3).** An application whose data belongs to an organization rather than to a
workspace (`apps.ORGANIZATION_APP_IDS`: Synwevo Message) is switched for one organization with an
`organization_id` in place of the `workspace_id` — `platform.apps.manage` alone, the same as a
workspace's switch (a per-tenant change under D12). `GET /api/admin/apps/{app_id}/organizations?q=&
limit=&offset=` lists every organization (name, lifecycle state, whether the application is on
for it and since when, live members, conversations), `platform.apps.view`; for an application that is
not organization-scoped it is 400 `bad_change`. The preview's impact for an organization is what the
switch-off closes: `members_affected` (live members), `members_with_conversations`,
`members_left_without_messages` (no other organization where it is on), `conversations`
`{groups, direct, object}` (not archived), `open_reports`, `pending_notifications` `{email, push}`
(mail and push about its conversations and reports not yet sent), `pending_cards` (undelivered
outbox rows for it, under the attempt limit), `signed_in_sessions` (live sessions of its members —
on any origin; the account service cannot tell a Message tab from another) and `open_streams`
(null: the streams live in the Message process's memory). Enabling is allowed for a frozen
organization and for one deleted inside its recovery window (the lifecycle gates are separate and
pause its conversations anyway); one deleted past the window, or with no window, cannot be enabled
(409 `organization_unavailable`). The change writes the platform's audit row
(`admin.app.organization_disable` / `_enable`, the organization in its meta), the organization's
own governance-log row (`organization.app.disable` / `.enable`, object the organization, meta
`{by: platform, app_id, enabled, reason}`), then `Apps.disable_for_organization` (journalled,
`organization_app_disabled`) or `enable_for_organization`, then the receipt. What a switch-off
does is the Message service's rule (`synwevo_core.messaging._closed`): every conversation of the
organization answers `403 messages_disabled` to its members from the next request on, open
streams are told within a heartbeat, mail and push about it are skipped, cards and queued Agent
sends are refused, moderation and the account export go on, nothing is deleted, and re-enabling
gives everything back as it was (notes below). The platform-wide switch outranks it; since it
holds for signed-in sessions too (G1), the platform-wide Message preview carries the same notes.

Refusals (preview and confirmation alike): 400 `bad_change` (not one of the four; a policy change
for a workspace; a policy or workspace change of an application that is not workspace-scoped —
`detail.scope`; a `workspace_id` that is not a non-empty id; an `organization_id` together with a
`workspace_id`, a blank one, a policy change for an organization, or an organization switch of an
application that is not organization-scoped); 404 `no_organization`; 409 `portal_required` for
`platform`, the portal itself: deliberately not a registry row (`apps.PLATFORM_APP_ID`) and
answered by `_check_platform` before the registry is read, so no switch could stop or narrow it;
404 `no_app`; 404 `no_workspace`; 409 `legacy_workspace` (the unclaimed historical workspace: the
claim flow owns its applications); 409 `workspace_unavailable` (enabling in a workspace that is
not active); 409 `no_change`; 409 `app_not_released` (enabling a business application the
deployment holds back — `apps.suite_unreleased`: its operations do not exist and its next start
would switch it off again). A platform-wide switch-off through `Apps.set_status` also deletes that
marker, so a later release does not turn back on what an operator switched off.

Notes (codes; the console words each by the direction of the change):

* `data_kept` — every change: the four methods write `apps`, `workspace_apps` and `meta` only.
* `links_stop` — only for the applications whose links are re-authorized when used (`LINK_GATED`):
  Synwevo Docs (a signed download link asks `authority.check` at download → 403 `link_revoked`) and
  Synwevo Forms (a fill link asks `Apps.check_access_for_workspace` → 410 `link_unavailable`), on
  top of the session gate every application has. Taken away, they end at their next use; given
  back, an unexpired link works again. The other applications issue no links of their own.
* `jobs_continue_or_stop` — Synwevo Docs only. Its runner asks `authority.check` again before a
  queued job starts, between plan batches, between samples and before and while it publishes a
  sample's files (`JobService._authorized`); a refusal cancels the job with `halt_reason`
  `authorization_revoked:<why>` and keeps what was finished (see `running_jobs`). Enabling or
  loosening resumes nothing: a halted job stays cancelled until someone retries it.
* `webhooks_disabled` — Synwevo Docs, on a switch-off (platform-wide or for a workspace) and on
  tightening: a delivery attempted while the subscription's creator is refused disables the
  subscription for good (webhooks.py, `authorization_revoked:<why>`); switching back does not
  resume it.
* `delegations_end_at_refresh` — on the same changes, for an application with a registered OAuth
  client: a refresh attempted while its holder is refused revokes the whole grant family
  (provider_routes, `entitlement_revoked`); switching back does not restore it.
* `approved_credentials_stop` — Synwevo Docs, on a change that takes access away, when at least one
  active API-platform approval's approving owner loses access in its workspace
  (`credentials_stopping` > 0): the API keys and service accounts those approvals govern are refused
  — their queued work and every live call (`approver_unavailable`) — until the owner can open
  Synwevo Docs there again or another owner approves anew.
* `integrations_need_reauthorization` — on enable, workspace enable and loosening, for Synwevo
  Docs or an application with a registered OAuth client: what the two notes above ended while
  access was off stays ended — subscriptions need resuming and integrations a new authorization.
* `explicit_grants_kept` — enabling and both policy changes (workspace-scoped applications): no
  entitlement row is written, so every grant stays and still counts under `explicit`.
* `revocations_kept_on_loosen` — both policy changes: an explicit revocation outranks `members`.
* Synwevo Message, for an organization and platform-wide (G1) — on a switch-off:
  `conversations_paused` (every conversation of the organization — or of every organization —
  answers `messages_disabled` to its members from their next request; membership, history, read
  positions and preferences are kept), `streams_end_within_heartbeat` (open tabs learn of the
  change at the next heartbeat and then receive nothing about it; the streams themselves are
  not cut, and cannot be counted here), `notifications_skipped` (mail and push about its
  conversations and reports are skipped, not deferred; the inbox item stays; nothing skipped is
  sent later), `cards_skipped` (Docs cards into its discussions are skipped, never held, so other
  organizations' cards are not starved; not replayed), `agent_sends_refused` (a queued Agent or
  Suite send into it fails with `messages_disabled`), `moderation_continues` (its moderators can
  still review reports; A44), `export_unaffected` (a person's account export still holds their
  own messages there), and `platform_switch_outranks` (for an organization while the platform-wide
  switch is off: enabling the organization changes nothing until the platform's switch is back).
  On an enable: `history_unchanged_on_enable` (everything comes back exactly as it was) and
  `skipped_not_replayed` (mail, push, cards and sends skipped meanwhile are not sent now).

Beyond the notes: a workspace created while a business application of the workplace origin was off
platform-wide does not get it on when it is enabled again (`Identity._create_workspace` enables
only the active ones). Enabling and enabling for a workspace are not journalled, so restoring a
backup from before a switch-off applies the switch-off again; loosening is not journalled either,
but a tightening's entry is replayed only into a database that lacks its receipt (journal.py
`app_policy_tightened`), so a loosening a restored backup already holds stands. A replayed
switch-off also ends the deployment's hold (the `suite_unreleased:` marker), and a replay into a
database from before the application was registered writes a placeholder row carrying the
restriction, which the next start-up registration keeps (journal.py `_app_row`). The residual
restore window of `app_disabled` / `workspace_app_disabled` (time-cut entries): a backup whose
`snapshot_at` falls between the entry's append and the transaction's COMMIT holds neither the
change nor a replayable entry — the window is the receipt's INSERT and the COMMIT.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from contextlib import contextmanager

from fastapi import APIRouter, Body, Query, Request

from synwevo_core import authority
from synwevo_core.api_platform import Approvals
from synwevo_core.apps import (DOCS_APP_ID, MESSAGES_APP_ID, ORGANIZATION_APP_IDS, PERSONAL_APP_IDS, PLATFORM_APP_ID,
                               Apps, app_disabled, organization_app_row, suite_unreleased)
from synwevo_core.audit_change import change as change_meta
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.oidc import docs_resource
from synwevo_core.platform_access import require_platform
from synwevo_core.store import Store, new_id, now
from synwevo_core.workspace_availability import LOST, lifecycle_state

from .admin_operations import PREVIEW_SECONDS, SignedPreview, canonical
from .admin_organizations import IDEMPOTENCY_KEY, RUNNING_JOB_STATUSES
from .invites import like_escape

VIEW = "platform.apps.view"
MANAGE = "platform.apps.manage"
#: Platform-scope changes need this as well (decision D12, module docstring).
PLATFORM_SCOPE = "platform.config.manage"
CHANGES = ("disable", "enable", "policy_explicit", "policy_members")
WORKSPACE_CHANGES = ("disable", "enable")
ORGANIZATION_CHANGES = ("disable", "enable")
#: Undelivered outbox rows still to be attempted (messaging_bridge.MAX_ATTEMPTS, read as data).
CARD_ATTEMPTS = 10
#: The changes that take access away: confirmed as a danger, journalled, and the only ones that
#: stop running work.
TAKES_AWAY = frozenset({"disable", "policy_explicit"})
#: Applications whose issued links are re-authorized when used (the `links_stop` note).
FORMS_APP_ID = "forms"
LINK_GATED = frozenset({DOCS_APP_ID, FORMS_APP_ID})
#: (scope, change) → the catalogued audit action (admin_actions.py).
ACTIONS = {
    ("platform", "disable"): "admin.app.disable",
    ("platform", "enable"): "admin.app.enable",
    ("platform", "policy_explicit"): "admin.app.policy_explicit",
    ("platform", "policy_members"): "admin.app.policy_members",
    ("workspace", "disable"): "admin.app.workspace_disable",
    ("workspace", "enable"): "admin.app.workspace_enable",
    ("organization", "disable"): "admin.app.organization_disable",
    ("organization", "enable"): "admin.app.organization_enable",
}
#: The Message notes (module docstring), by the direction of the change.
MESSAGES_AWAY_NOTES = ("conversations_paused", "streams_end_within_heartbeat", "notifications_skipped", "cards_skipped",
                       "agent_sends_refused", "moderation_continues", "export_unaffected")
MESSAGES_BACK_NOTES = ("history_unchanged_on_enable", "skipped_not_replayed")
REASON_MIN, REASON_MAX = 8, 500

#: A person who could sign in now: the account check_access leaves to the session gate.
LIVE_PERSON = ("u.status='active' AND u.deleted_at IS NULL "
               "AND NOT EXISTS (SELECT 1 FROM app_accounts a WHERE a.user_id=u.id)")
#: `Identity.workspaces_for`: a workspace of an organization counts only with its membership.
ORG_MEMBER = ("(w.organization_id IS NULL OR EXISTS (SELECT 1 FROM organization_memberships om "
              "WHERE om.organization_id=w.organization_id AND om.user_id=m.user_id))")
#: `organizations.workspace_access_failure(workspace, person)` is None (with LIVE_PERSON and
#: ORG_MEMBER): no organization, or an active one the person is a member of.
ORG_GATE = ("(w.organization_id IS NULL OR EXISTS (SELECT 1 FROM organizations o JOIN organization_memberships om "
            "ON om.organization_id=o.id WHERE o.id=w.organization_id AND o.status='active' AND om.user_id=m.user_id))")
#: `entitlement_state` is `granted`, or `none` (no row, or a revocation older than the current
#: membership) under the `members` policy — one parameter, the policy.
ENTITLED = ("((e.user_id IS NOT NULL AND e.revoked_at IS NULL) "
            "OR (?='members' AND (e.user_id IS NULL OR e.revoked_at<m.created_at)))")


def app_scope(app_id: str) -> str:
    """How check_access decides eligibility: the portal, a personal application, the
    organization-scoped messages, or the workspace model (every other id, as check_access treats
    an id it does not know)."""
    if app_id == PLATFORM_APP_ID:
        return "platform"
    if app_id in PERSONAL_APP_IDS:
        return "personal"
    if app_id == MESSAGES_APP_ID:
        return "organization"
    return "workspace"


class AppGovernance(SignedPreview):
    def __init__(self, store: Store, ident: Identity, audit):
        self.store, self.ident, self.audit = store, ident, audit
        self.apps = Apps(store, ident)

    def _actor(self, request, permission: str = MANAGE, *, execute: bool = False) -> dict:
        return require_platform(request, permission, store=self.store, audit=self.audit, dangerous=execute)

    def _admit(self, request, wid, oid=None, *, execute: bool = False) -> dict:
        """`platform.apps.manage`, and for a platform-scope change (neither a `workspace_id` nor
        an `organization_id`) `platform.config.manage` too (decision D12) — both permissions before
        the strict re-authentication, as `require_platform` orders a single one. A change for one
        organization (B3) is a per-tenant change like a workspace's: the catalogue's own permission."""
        actor = self._actor(request, MANAGE)
        if wid is None and oid is None:
            actor = self._actor(request, PLATFORM_SCOPE)
        if execute:
            actor = self._actor(request, MANAGE, execute=True)
        return actor

    def _table(self, name: str) -> bool:
        return bool(self.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))

    @contextmanager
    def snapshot(self):
        """One committed state for every statement of a read, without the writer lock (the
        deferred read transaction of admin_invitations._one_snapshot). Read-only by construction:
        a write inside would fail rather than escalate."""
        with self.store.conn() as c:
            if c.in_transaction:
                yield
                return
            c.execute("BEGIN DEFERRED")
            try:
                yield
            finally:
                if c.in_transaction:
                    c.execute("ROLLBACK")

    # ------------------------------------------------------------------ who can open it, where
    def _access_sql(self, app: dict, *, policy: str | None = None, without: str | None = None,
                    plus: str | None = None, within: list[str] | None = None) -> tuple[str, list]:
        """`SELECT DISTINCT user_id, workspace_id`: the live person may open the application in
        that workspace by check_access's rule, its status aside. `policy` replaces the
        application's, `without` takes one workspace's switch off and `plus` puts one on (the
        after-state of a change); `within` restricts to some workspaces. For messages and the
        Agent, the pairs `_check_platform` accepts with a workspace named: membership of an
        active workspace."""
        aid = app["id"]
        if app_scope(aid) != "workspace":
            sql = ("SELECT DISTINCT m.user_id, m.workspace_id FROM memberships m JOIN users u ON u.id=m.user_id "
                   f"JOIN workspaces w ON w.id=m.workspace_id WHERE w.deleted_at IS NULL AND w.status='active' "
                   f"AND {LIVE_PERSON} AND {ORG_MEMBER}")
            args: list = []
        else:
            enabled = "EXISTS (SELECT 1 FROM workspace_apps x WHERE x.workspace_id=m.workspace_id AND x.app_id=?)"
            switch: list = [aid]
            if without is not None:
                enabled = f"({enabled} AND m.workspace_id<>?)"
                switch.append(without)
            if plus is not None:
                enabled = f"({enabled} OR m.workspace_id=?)"
                switch.append(plus)
            sql = ("SELECT DISTINCT m.user_id, m.workspace_id FROM memberships m JOIN users u ON u.id=m.user_id "
                   "JOIN workspaces w ON w.id=m.workspace_id LEFT JOIN app_entitlements e "
                   "ON e.workspace_id=m.workspace_id AND e.app_id=? AND e.user_id=m.user_id "
                   f"WHERE w.deleted_at IS NULL AND w.status='active' AND {LIVE_PERSON} AND {ORG_MEMBER} "
                   f"AND {ORG_GATE} AND {enabled} AND {ENTITLED}")
            args = [aid, *switch, policy or app["access_policy"]]
        if within is not None:
            sql += f" AND m.workspace_id IN ({','.join('?' * len(within))})"
            args += within
        return sql, args

    def _pairs(self, app: dict, *, status: str | None = None, **after) -> set[tuple[str, str]]:
        """The (person, workspace) pairs with access — now, or in an after-state (`status`, and
        `_access_sql`'s `policy` / `without` / `plus`)."""
        if (status or app["status"]) != "active":
            return set()
        sql, args = self._access_sql(app, **after)
        return {(row["user_id"], row["workspace_id"]) for row in self.store.all(sql, tuple(args))}

    def _live_people(self) -> int:
        return self.store.one(f"SELECT COUNT(*) AS n FROM users u WHERE {LIVE_PERSON}")["n"]

    def _people_count(self, app: dict) -> int:
        """People who can open the application now (in at least one workspace), counted in SQL."""
        if app["status"] != "active":
            return 0
        if app_scope(app["id"]) != "workspace":
            return self._live_people()
        sql, args = self._access_sql(app)
        return self.store.one(f"SELECT COUNT(DISTINCT user_id) AS n FROM ({sql})", tuple(args))["n"]

    # ------------------------------------------------------------------ views
    def _running_jobs(self, app_id: str) -> int | None:
        """Synwevo Docs' queued, running and cancelling jobs (apps/docs jobs.ACTIVE, read as data:
        the account service may not import the application). Null for every other application."""
        if app_id != DOCS_APP_ID:
            return None
        if not self._table("jobs"):
            return 0
        marks = ",".join("?" * len(RUNNING_JOB_STATUSES))
        return self.store.one(f"SELECT COUNT(*) AS n FROM jobs WHERE status IN ({marks})", RUNNING_JOB_STATUSES)["n"]

    def _enabled_count(self, app_id: str) -> int:
        return self.store.one("SELECT COUNT(*) AS n FROM workspace_apps x JOIN workspaces w ON w.id=x.workspace_id "
                              "WHERE x.app_id=? AND w.deleted_at IS NULL", (app_id,))["n"]

    def _available(self, app: dict, *, unreleased: bool | None = None) -> list[str]:
        out = []
        if app["status"] == "active":
            out.append("disable")
        elif not (suite_unreleased(self.store, app["id"]) if unreleased is None else unreleased):
            out.append("enable")
        if app_scope(app["id"]) == "workspace":
            out.append("policy_explicit" if app["access_policy"] == "members" else "policy_members")
        return out

    def _item(self, app: dict, *, people: int | None = None, enabled: int | None = None,
              unreleased: bool | None = None) -> dict:
        aid = app["id"]
        # Grants and revocations that apply to a current membership (entitlement_state `granted` /
        # `revoked`); those of people since removed, or older than the membership, are history.
        ent = self.store.one(
            "SELECT COALESCE(SUM(CASE WHEN e.revoked_at IS NULL THEN 1 ELSE 0 END),0) AS grants, "
            "COALESCE(SUM(CASE WHEN e.revoked_at IS NOT NULL AND e.revoked_at>=m.created_at THEN 1 ELSE 0 END),0) AS revocations "
            "FROM app_entitlements e JOIN memberships m ON m.workspace_id=e.workspace_id AND m.user_id=e.user_id "
            "JOIN workspaces w ON w.id=e.workspace_id WHERE e.app_id=? AND w.deleted_at IS NULL", (aid,))
        return {"id": aid, "name": app["name"], "status": app["status"], "access_policy": app["access_policy"],
                "launch_url": app["launch_url"], "scope": app_scope(aid),
                "workspaces_enabled": self._enabled_count(aid) if enabled is None else enabled,
                "organizations_disabled": self._organizations_disabled(aid) if aid in ORGANIZATION_APP_IDS else None,
                "members_with_access": self._people_count(app) if people is None else people,
                "explicit_grants": ent["grants"], "explicit_revocations": ent["revocations"],
                "running_jobs": self._running_jobs(aid), "changes": self._available(app, unreleased=unreleased)}

    def _workspace_items(self, app: dict, rows: list[dict]) -> list[dict]:
        wids = [row["id"] for row in rows]
        if not wids:
            return []
        marks = ",".join("?" * len(wids))
        members = {row["workspace_id"]: row["n"] for row in self.store.all(
            f"SELECT workspace_id, COUNT(*) AS n FROM memberships WHERE workspace_id IN ({marks}) GROUP BY workspace_id",
            tuple(wids))}
        switched = {row["workspace_id"]: row["enabled_at"] for row in self.store.all(
            f"SELECT workspace_id, enabled_at FROM workspace_apps WHERE app_id=? AND workspace_id IN ({marks})",
            (app["id"], *wids))}
        access = Counter(w for _, w in self._pairs(app, within=wids))
        return [{"id": row["id"], "name": row["name"], "kind": row["kind"], "status": row["status"],
                 "organization_id": row["organization_id"], "enabled": row["id"] in switched,
                 "enabled_at": switched.get(row["id"]), "members": members.get(row["id"], 0),
                 "members_with_access": access.get(row["id"], 0)} for row in rows]

    def _workspace_row(self, wid: str) -> dict:
        return self.store.one("SELECT id, name, kind, status, organization_id FROM workspaces WHERE id=?", (wid,))

    # ------------------------------------------------------------------ per organization (B3)
    def _organizations_disabled(self, app_id: str) -> int:
        """How many organizations the platform switched the application off for (B3)."""
        if not self._table("organization_apps"):
            return 0
        return self.store.one("SELECT COUNT(*) AS n FROM organization_apps a JOIN organizations o ON o.id=a.organization_id "
                              "WHERE a.app_id=? AND a.status='disabled'", (app_id,))["n"]

    def _organization_row(self, oid: str) -> dict | None:
        return self.store.one("SELECT id, name, status, deleted_at, recover_until, version FROM organizations WHERE id=?", (oid,))

    def _organization_items(self, app: dict, rows: list[dict]) -> list[dict]:
        """One item per organization: its lifecycle state, the application's switch there (on by
        default: no row means on; `updated_at` is the switch's time, either way), its live members
        and its conversations that are not archived. Counts and metadata, never a person."""
        oids = [row["id"] for row in rows]
        if not oids:
            return []
        marks = ",".join("?" * len(oids))
        members = {r["organization_id"]: r["n"] for r in self.store.all(
            f"SELECT m.organization_id, COUNT(*) AS n FROM organization_memberships m JOIN users u ON u.id=m.user_id "
            f"WHERE m.organization_id IN ({marks}) AND {LIVE_PERSON} GROUP BY m.organization_id", tuple(oids))}
        switches = {r["organization_id"]: r for r in self.store.all(
            f"SELECT organization_id, status, updated_at FROM organization_apps WHERE app_id=? AND organization_id IN ({marks})",
            (app["id"], *oids))} if self._table("organization_apps") else {}
        conversations = {r["organization_id"]: r["n"] for r in self.store.all(
            f"SELECT organization_id, COUNT(*) AS n FROM conversations WHERE organization_id IN ({marks}) AND archived_at IS NULL "
            "GROUP BY organization_id", tuple(oids))} if self._table("conversations") else {}
        return [{"id": row["id"], "name": row["name"], "status": row["status"], "deleted_at": row["deleted_at"],
                 "recover_until": row["recover_until"],
                 "enabled": (switches.get(row["id"]) or {}).get("status") != "disabled",
                 "updated_at": (switches.get(row["id"]) or {}).get("updated_at"),
                 "members": members.get(row["id"], 0), "conversations": conversations.get(row["id"], 0)} for row in rows]

    def _organization_impact(self, oid: str) -> dict:
        """What switching Synwevo Message off for the organization closes (module docstring)."""
        t = now()
        one = lambda sql, args=(): self.store.one(sql, args)["n"]  # noqa: E731
        live_members = (f"SELECT m.user_id FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                        f"WHERE m.organization_id=? AND {LIVE_PERSON}")
        affected = one(f"SELECT COUNT(*) AS n FROM ({live_members})", (oid,))
        tables = {name: self._table(name) for name in ("conversations", "message_reports", "notifications", "messaging_outbox",
                                                        "notification_deliveries", "push_queue", "organization_apps")}
        with_conversations = one(f"SELECT COUNT(*) AS n FROM ({live_members}) p WHERE EXISTS (SELECT 1 FROM conversation_members cm "
                                 "JOIN conversations c ON c.id=cm.conversation_id WHERE cm.user_id=p.user_id AND cm.left_at IS NULL "
                                 "AND c.organization_id=?)", (oid, oid)) if tables["conversations"] else 0
        # another organization of theirs where Message is on: active, not switched off (the platform
        # switch closes every organization alike, so it is not asked here)
        elsewhere = ("EXISTS (SELECT 1 FROM organization_memberships o2 JOIN organizations x ON x.id=o2.organization_id "
                     "WHERE o2.user_id=p.user_id AND o2.organization_id<>? AND x.status='active'"
                     + (" AND NOT EXISTS (SELECT 1 FROM organization_apps s WHERE s.organization_id=x.id AND s.app_id=? AND s.status='disabled')"
                        if tables["organization_apps"] else "") + ")")
        left_without = one(f"SELECT COUNT(*) AS n FROM ({live_members}) p WHERE NOT {elsewhere}",
                           (oid, oid, MESSAGES_APP_ID) if tables["organization_apps"] else (oid, oid))
        kinds = {"groups": 0, "direct": 0, "object": 0}
        if tables["conversations"]:
            for r in self.store.all("SELECT kind, purpose, COUNT(*) AS n FROM conversations WHERE organization_id=? AND archived_at IS NULL "
                                    "GROUP BY kind, purpose", (oid,)):
                kinds["direct" if r["kind"] == "dm" else "object" if r["purpose"] == "object" else "groups"] += r["n"]
        reports = one("SELECT COUNT(*) AS n FROM message_reports WHERE organization_id=? AND status='open'", (oid,)) if tables["message_reports"] else 0
        # items about its conversations (the item's object) or about reports of messages in them
        about = ("SELECT n.id FROM notifications n WHERE n.category='conversation' AND ("
                 "(n.object_kind='conversation' AND EXISTS (SELECT 1 FROM conversations c WHERE c.id=n.object_id AND c.organization_id=?))"
                 + (" OR (n.object_kind='message_report' AND EXISTS (SELECT 1 FROM message_reports r WHERE r.id=n.object_id AND r.organization_id=?))"
                    if tables["message_reports"] else "") + ")")
        about_args = (oid, oid) if tables["message_reports"] else (oid,)
        email = one(f"SELECT COUNT(*) AS n FROM notification_deliveries d WHERE d.status='pending' AND d.notification_id IN ({about})",
                    about_args) if tables["notifications"] and tables["notification_deliveries"] and tables["conversations"] else 0
        push = one(f"SELECT COUNT(*) AS n FROM push_queue q WHERE q.status IN ('pending','claimed') AND q.notification_id IN ({about})",
                   about_args) if tables["notifications"] and tables["push_queue"] and tables["conversations"] else 0
        cards = one("SELECT COUNT(*) AS n FROM messaging_outbox o LEFT JOIN object_conversations oc ON oc.app=o.app AND oc.kind=o.object_kind "
                    "AND oc.object_id=o.object_id LEFT JOIN conversations c ON c.id=oc.conversation_id "
                    "WHERE o.delivered_at IS NULL AND o.attempts<? AND COALESCE(o.organization_id, c.organization_id)=?",
                    (CARD_ATTEMPTS, oid)) if tables["messaging_outbox"] and tables["conversations"] else 0
        sessions = one(f"SELECT COUNT(*) AS n FROM sessions s WHERE s.revoked_at IS NULL AND s.idle_expires_at>? AND s.absolute_expires_at>? "
                       f"AND s.user_id IN ({live_members})", (t, t, oid))
        return {"members_affected": affected, "members_with_conversations": with_conversations,
                "members_left_without_messages": left_without, "conversations": kinds, "open_reports": reports,
                "pending_notifications": {"email": email, "push": push}, "pending_cards": cards,
                "signed_in_sessions": sessions, "open_streams": None}

    # ------------------------------------------------------------------ the change
    @staticmethod
    def _bad(message: str, **detail) -> ServiceError:
        return ServiceError(400, "bad_change", message, {"field": "change", **detail})

    def _target(self, app_id: str, change, wid, oid=None) -> dict:
        """The change admitted against the rows as they are, or its refusal (module docstring)."""
        if change not in CHANGES:
            raise self._bad("请选择有效的应用操作（disable / enable / policy_explicit / policy_members）")
        if app_id == PLATFORM_APP_ID:
            raise ServiceError(409, "portal_required", "门户是平台本身，不是可以启停的应用：任何有效账户都可以打开它")
        app = self.apps.get(app_id)
        if not app:
            raise ServiceError(404, "no_app", "找不到该应用")
        scope = app_scope(app_id)
        if oid is not None:
            if wid is not None:
                raise ServiceError(400, "bad_change", "不能同时指定工作空间和组织", {"field": "organization_id"})
            if not isinstance(oid, str) or not oid.strip() or len(oid) > 128:
                raise ServiceError(400, "bad_change", "组织编号无效", {"field": "organization_id"})
            if change not in ORGANIZATION_CHANGES:
                raise self._bad("按组织只能启用或停用应用；访问策略对全平台生效")
            if app_id not in ORGANIZATION_APP_IDS:
                raise self._bad(f"「{app['name']}」不按组织开关：没有按组织的启用或停用", scope=scope)
            org = self._organization_row(oid)
            if not org:
                raise ServiceError(404, "no_organization", "找不到该组织")
            if change == "enable" and lifecycle_state(org) == LOST:
                raise ServiceError(409, "organization_unavailable", "该组织已删除且不能再恢复，不能为它启用应用",
                                   {"organization_status": org["status"]})
            row = organization_app_row(self.store, oid, app_id)
            if (change == "disable") == bool(row and row["status"] == "disabled"):
                raise ServiceError(409, "no_change", "该组织的这个应用已经处于这个状态，不需要更改")
            return {"app": app, "change": change, "scope": "organization", "workspace": None, "organization": org,
                    "action": ACTIONS[("organization", change)]}
        if wid is not None:
            if not isinstance(wid, str) or not wid.strip() or len(wid) > 128:
                raise ServiceError(400, "bad_change", "工作空间编号无效", {"field": "workspace_id"})
            if change not in WORKSPACE_CHANGES:
                raise self._bad("按工作空间只能启用或停用应用；访问策略对全平台生效")
            if scope != "workspace":
                raise self._bad(f"「{app['name']}」不按工作空间授权：只能在全平台启用或停用", scope=scope)
            ws = self.store.one("SELECT * FROM workspaces WHERE id=? AND deleted_at IS NULL", (wid,))
            if not ws:
                raise ServiceError(404, "no_workspace", "找不到这个工作空间")
            if ws["kind"] == "legacy" and ws["status"] == "pending_claim":
                raise ServiceError(409, "legacy_workspace", "这是待认领的历史数据空间，由认领流程管理，不能在这里启用或停用应用")
            switched = self.store.one("SELECT enabled_at FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                                      (wid, app_id))
            if change == "enable" and ws["status"] != "active":
                raise ServiceError(409, "workspace_unavailable", "该工作空间当前不可用，不能为它启用应用",
                                   {"workspace_status": ws["status"]})
            if (change == "enable") == bool(switched):
                raise ServiceError(409, "no_change", "该工作空间的这个应用已经处于这个状态，不需要更改")
            return {"app": app, "change": change, "scope": "workspace", "workspace": ws, "organization": None,
                    "action": ACTIONS[("workspace", change)]}
        if change.startswith("policy_") and scope != "workspace":
            raise self._bad(f"「{app['name']}」不按工作空间授权：访问策略对它不起作用", scope=scope)
        column, value = {"disable": ("status", "disabled"), "enable": ("status", "active"),
                         "policy_explicit": ("access_policy", "explicit"),
                         "policy_members": ("access_policy", "members")}[change]
        if app[column] == value:
            raise ServiceError(409, "no_change", "应用已经处于这个状态，不需要更改")
        if change == "enable" and suite_unreleased(self.store, app_id):
            raise ServiceError(409, "app_not_released", "当前部署没有发布这个应用：它的功能在服务里不存在，"
                                                        "启用后下次启动也会被重新停用")
        return {"app": app, "change": change, "scope": "platform", "workspace": None, "organization": None,
                "action": ACTIONS[("platform", change)], "column": column, "value": value}

    def _approvals_stopping(self, target: dict, lost: set) -> list[list[str]]:
        """The API-platform approvals for Synwevo Docs that `Approvals.active` accepts now and
        refuses once the change is made: its approving owner must still be able to open Synwevo
        Docs in the approval's workspace (`Approvals.require_owner` → check_access →
        `approver_unavailable`), so an approval whose (approver, workspace) pair is lost stops the
        API key or service account it governs — its queued work and every live call. Each as
        `[approval id, approver, workspace]`. Empty for a change that takes nothing away."""
        if target["app"]["id"] != DOCS_APP_ID or target["change"] not in TAKES_AWAY or not lost \
                or not self._table("api_platform_approvals"):
            return []
        approvals, out = Approvals(self.store), []
        for row in self.store.all("SELECT id, workspace_id, approved_by FROM api_platform_approvals "
                                  "WHERE status='approved' AND revoked_at IS NULL AND expires_at>? ORDER BY id", (now(),)):
            if (row["approved_by"], row["workspace_id"]) not in lost:
                continue
            try:
                active = approvals.active(row["id"])
            except ServiceError:
                continue                       # already refused: this change stops nothing there
            if active["resource"] == docs_resource():
                out.append([row["id"], row["approved_by"], row["workspace_id"]])
        return out

    def _stopping_jobs(self, target: dict, lost: set, stopping_approvals: list) -> int | None:
        """The Docs jobs (queued, running, cancelling) that `JobService._authorized` allows now and
        refuses once the change is made: its project must exist and not be deleted, then
        `authority.check` of the current run's descriptor (else the job's) — module docstring,
        `running_jobs`. The after-state is read from the same pairs as the impact: a person's job
        stops when its (person, workspace) pair is lost, a key's or service account's when the
        approval it is bound to stops (`_approvals_stopping`); switching off stops every principal
        but pre-identity operator work."""
        app, change, ws = target["app"], target["change"], target["workspace"]
        if app["id"] != DOCS_APP_ID:
            return None
        if change not in TAKES_AWAY or not self._table("jobs"):
            return 0
        marks = ",".join("?" * len(RUNNING_JOB_STATUSES))
        runs, projects = self._table("job_runs"), self._table("projects")
        sql = ("SELECT j.tenant, j.principal AS job_principal, "
               + ("r.principal AS run_principal, " if runs else "NULL AS run_principal, ")
               + ("(p.id IS NOT NULL AND COALESCE(p.deleted, 0)=0) AS live_project " if projects else "1 AS live_project ")
               + "FROM jobs j "
               + ("LEFT JOIN job_runs r ON r.job_id=j.id AND r.run_no=COALESCE(j.run_no, 1) " if runs else "")
               + ("LEFT JOIN projects p ON p.id=j.project_id " if projects else "")
               + f"WHERE j.status IN ({marks})")
        args: list = list(RUNNING_JOB_STATUSES)
        if ws is not None:
            sql += " AND COALESCE(j.tenant, 'default')=?"
            args.append(ws["id"])
        stopping = {approval_id for approval_id, _, _ in stopping_approvals}
        bindings = Approvals(self.store)
        n = 0
        for row in self.store.all(sql, tuple(args)):
            if not row["live_project"]:
                continue                       # refused already: project_deleted
            raw = row["run_principal"]
            desc = authority.loads(row["job_principal"] if raw is None or raw == "" else raw)
            tenant = row["tenant"] or "default"
            kind = "operator" if desc is None else desc.get("kind")
            if kind == "operator":
                continue                       # pre-identity work: allowed before and after
            if not authority.check(self.store, desc, need="write", tenant=tenant)[0]:
                continue                       # already refused: this change stops nothing there
            if change == "disable":
                n += 1                         # app_disabled / app_not_enabled_for_workspace
            elif kind in ("user", "delegated"):
                n += (desc.get("user_id"), tenant) in lost              # no_entitlement
            elif kind in ("api_key", "service_account") and stopping:
                # api_platform._binding_id: a key's lineage, a service account's client.
                credential = (desc.get("lineage") or desc.get("key_id")) if kind == "api_key" else desc.get("client_id")
                binding = bindings.binding(kind, credential) if credential else None
                n += bool(binding and binding["approval_id"] in stopping)  # approver_unavailable
        return n

    def _assess(self, target: dict) -> dict:
        """Who can open the application where, before and after the change, what else it
        reaches, and the digest of the version the plan binds."""
        app, change, ws = target["app"], target["change"], target["workspace"]
        version = {"app": {"id": app["id"], "status": app["status"], "access_policy": app["access_policy"]}}
        org = target.get("organization")
        if org is not None:
            # One organization (B3): the plan binds the organization's lifecycle row, its switch,
            # its live members and the platform switch; a member joining makes the preview stale.
            row = organization_app_row(self.store, org["id"], app["id"])
            impact = self._organization_impact(org["id"])
            version["organization"] = {"id": org["id"], "status": org["status"], "deleted_at": org["deleted_at"],
                                       "recover_until": org["recover_until"], "version": org["version"],
                                       "switch": (row or {}).get("status"), "members": impact["members_affected"],
                                       "platform_off": app_disabled(self.store, app["id"])}
            return {"after_people": None, "after_in_workspace": None, "approvals_stopping": [], "impact": impact,
                    "digest": hashlib.sha256(canonical(version).encode()).hexdigest()}
        if app_scope(app["id"]) != "workspace":
            # Messages and the Agent: platform-wide disable / enable only, per live account.
            live = self._live_people()
            before = live if app["status"] == "active" else 0
            after = 0 if change == "disable" else live
            version["people"] = live
            return {"after_people": after, "after_in_workspace": None, "approvals_stopping": [],
                    "impact": {"people_losing_access": max(before - after, 0),
                               "people_gaining_access": max(after - before, 0),
                               "memberships_losing_access": None, "memberships_gaining_access": None,
                               "workspaces_affected": None, "running_jobs": None, "credentials_stopping": None,
                               "sessions_in_app": None},
                    "digest": hashlib.sha256(canonical(version).encode()).hexdigest()}
        before = self._pairs(app)
        if ws is not None:
            after = self._pairs(app, without=ws["id"]) if change == "disable" else self._pairs(app, plus=ws["id"])
            affected = 1
        else:
            if change == "disable":
                after = set()
            elif change == "enable":
                after = self._pairs(app, status="active")
            else:
                after = self._pairs(app, policy=target["value"])
            affected = self._enabled_count(app["id"])
        lost, gained = before - after, after - before
        sessions = 0
        if lost:
            t = now()
            live = "revoked_at IS NULL AND idle_expires_at>? AND absolute_expires_at>?"
            rows = (self.store.all(f"SELECT user_id, workspace_id FROM sessions WHERE {live} AND workspace_id=?",
                                   (t, t, ws["id"])) if ws is not None else
                    self.store.all(f"SELECT user_id, workspace_id FROM sessions WHERE {live} AND workspace_id IN "
                                   "(SELECT workspace_id FROM workspace_apps WHERE app_id=?)", (t, t, app["id"])))
            sessions = sum(1 for row in rows if (row["user_id"], row["workspace_id"]) in lost)
        if ws is not None:
            enabled = bool(self.store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                                          (ws["id"], app["id"])))
            version["workspace"] = {"id": ws["id"], "status": ws["status"], "kind": ws["kind"],
                                    "organization_id": ws["organization_id"], "enabled": enabled}
        approvals = self._approvals_stopping(target, lost)
        # The approvals that stop are bound too: a new one whose approver loses access makes the
        # preview stale like a new member does.
        version.update(lost=sorted(lost), gained=sorted(gained), workspaces_affected=affected, approvals=approvals)
        return {"before_people": len({u for u, _ in before}), "after_people": len({u for u, _ in after}),
                "after_in_workspace": None if ws is None else sum(1 for _, w in after if w == ws["id"]),
                "approvals_stopping": approvals,
                "impact": {"people_losing_access": len({u for u, _ in lost}),
                           "people_gaining_access": len({u for u, _ in gained}),
                           "memberships_losing_access": len(lost), "memberships_gaining_access": len(gained),
                           "workspaces_affected": affected,
                           "running_jobs": self._stopping_jobs(target, lost, approvals),
                           "credentials_stopping": len(approvals) if app["id"] == DOCS_APP_ID else None,
                           "sessions_in_app": sessions},
                "digest": hashlib.sha256(canonical(version).encode()).hexdigest()}

    def _notes(self, target: dict, assessed: dict) -> list[str]:
        """The note codes of the module docstring for this change."""
        app, change = target["app"], target["change"]
        aid = app["id"]
        clients = bool(self.store.one("SELECT 1 FROM oauth_clients WHERE app_id=? AND disabled_at IS NULL LIMIT 1", (aid,)))
        notes = ["data_kept"]
        if aid in LINK_GATED:
            notes.append("links_stop")
        if aid == DOCS_APP_ID:
            notes.append("jobs_continue_or_stop")
        if change in TAKES_AWAY:
            if aid == DOCS_APP_ID:
                notes.append("webhooks_disabled")
            if clients:
                notes.append("delegations_end_at_refresh")
            if assessed["approvals_stopping"]:
                notes.append("approved_credentials_stop")
        elif aid == DOCS_APP_ID or clients:
            notes.append("integrations_need_reauthorization")
        if app_scope(aid) == "workspace":
            if change != "disable":
                notes.append("explicit_grants_kept")
            if change.startswith("policy_"):
                notes.append("revocations_kept_on_loosen")
        if aid == MESSAGES_APP_ID:
            # What the switch does to conversations — for one organization and platform-wide alike,
            # since the platform-wide switch holds for signed-in sessions too (G1).
            notes.extend(MESSAGES_AWAY_NOTES if change == "disable" else MESSAGES_BACK_NOTES)
            if target.get("organization") is not None and app_disabled(self.store, aid):
                notes.append("platform_switch_outranks")
        return notes

    def preview(self, request, app_id: str, body: dict) -> dict:
        change, wid, oid = body.get("change"), body.get("workspace_id"), body.get("organization_id")
        actor = self._admit(request, wid, oid)
        try:
            # Read-only: one deferred snapshot, no writer lock; the confirmation checks it all
            # again under the lock.
            with self.snapshot():
                target = self._target(app_id, change, wid, oid)
                assessed = self._assess(target)
                view = self._item(target["app"], people=assessed.get("before_people"))
                if target["workspace"] is not None:
                    view["workspace"] = self._workspace_items(target["app"], [self._workspace_row(wid)])[0]
                if target["organization"] is not None:
                    view["organization"] = self._organization_items(target["app"], [target["organization"]])[0]
                notes = self._notes(target, assessed)
        except Exception as exc:
            self._failure(request, actor, app_id, change, wid, exc, phase="preview", oid=oid)
            raise
        expires = time.time() + PREVIEW_SECONDS
        plan = {"v": 1, "actor": actor["id"], "action": target["action"], "app": app_id,
                "change": target["change"], "workspace": wid, "organization": oid, "origin": self._environment(request),
                "version": assessed["digest"], "expires_at": expires, "nonce": new_id("plan")}
        return {"action": target["action"], "change": target["change"], "scope": target["scope"],
                "risk": "high" if target["change"] in TAKES_AWAY else "medium", "target": view,
                "impact": assessed["impact"], "notes": notes, "expires_at": expires,
                "preview_token": self._sign(plan)}

    def execute(self, request, app_id: str, change: str, body: dict) -> dict:
        wid, oid = body.get("workspace_id"), body.get("organization_id")
        actor = self._admit(request, wid, oid, execute=True)
        raw_reason = body.get("reason")
        reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
        key, token = body.get("idempotency_key"), body.get("preview_token")
        origin = self._environment(request)
        request_hash = hashlib.sha256(canonical({"action": "admin.app", "app": app_id, "change": change,
                                                "workspace": wid, "organization": oid, "reason": reason,
                                                "preview_token": token, "origin": origin}).encode()).hexdigest()
        try:
            if not REASON_MIN <= len(reason) <= REASON_MAX:
                raise ServiceError(400, "missing_reason", f"请写明理由（{REASON_MIN} 至 {REASON_MAX} 个字）")
            with self.store.tx() as tx:
                actor = self._admit(request, wid, oid, execute=True)
                if isinstance(key, str):
                    previous = self.store.one("SELECT action,request_hash,result_json FROM admin_operations "
                                              "WHERE actor_id=? AND idempotency_key=?", (actor["id"], key))
                    if previous:
                        if previous["action"] not in ACTIONS.values() or previous["request_hash"] != request_hash:
                            raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                        return json.loads(previous["result_json"])
                # The state refusals before the confirmation checks, as the organization lifecycle:
                # a change the rows do not allow is refused as such, token or no token.
                target = self._target(app_id, change, wid, oid)
                if not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key) or not isinstance(token, str):
                    raise ServiceError(400, "admin_confirmation_required", "请先预览操作影响，再确认执行")
                plan = self._verify(token)
                expected = {"actor": actor["id"], "action": target["action"], "app": app_id,
                            "change": target["change"], "workspace": wid, "organization": oid, "origin": origin}
                if any(plan.get(field) != value for field, value in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                if plan.get("expires_at", 0) <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                assessed = self._assess(target)
                if plan.get("version") != assessed["digest"]:
                    raise ServiceError(409, "admin_preview_stale", "应用、工作空间或可使用的人已变化，请重新预览")
                return self._apply(tx, request, actor, target, assessed, reason=reason, key=key,
                                   request_hash=request_hash, origin=origin)
        except Exception as exc:
            self._failure(request, actor, app_id, change, wid, exc, phase="execute", reason=reason, oid=oid)
            raise

    def _apply(self, tx, request, actor, target, assessed, *, reason, key, request_hash, origin) -> dict:
        app, change, ws, org = target["app"], target["change"], target["workspace"], target["organization"]
        app_id = app["id"]
        operation_id = new_id("ao")
        # The result as the change will leave things, computed before the service method: a
        # withdrawal's journal entry must be followed only by the receipt and the COMMIT (a backup
        # taken inside that window holds neither; see the module docstring).
        org_view = None
        if org is not None:
            before, after = {"enabled": change == "disable"}, {"enabled": change == "enable"}
            app_view = self._item(app)
            app_view["organizations_disabled"] = (app_view["organizations_disabled"] or 0) + (1 if change == "disable" else -1)
            org_view = {**self._organization_items(app, [org])[0], "enabled": change == "enable", "updated_at": None}
            ws_view = None
        elif ws is not None:
            before, after = {"enabled": change == "disable"}, {"enabled": change == "enable"}
            app_view = self._item(app, people=assessed["after_people"],
                                  enabled=self._enabled_count(app_id) + (1 if change == "enable" else -1))
            ws_view = {**self._workspace_items(app, [self._workspace_row(ws["id"])])[0],
                       "enabled": change == "enable", "enabled_at": None,
                       "members_with_access": assessed["after_in_workspace"]}
        else:
            column, value = target["column"], target["value"]
            before, after = {column: app[column]}, {column: value}
            # A platform switch-off removes the deployment's "held back" marker (Apps.set_status).
            app_view = self._item({**app, column: value}, people=assessed["after_people"],
                                  unreleased=False if change == "disable" else None)
            ws_view = None
        result = {"ok": True, "operation_id": operation_id, "state": "succeeded", "action": target["action"],
                  "change": change, "scope": target["scope"], "journaled": True if change in TAKES_AWAY else None,
                  "app": app_view}
        if ws_view is not None:
            result["workspace"] = ws_view
        if org_view is not None:
            result["organization"] = org_view
        meta = {**change_meta(before, after), "change": change, "scope": target["scope"],
                "impact": assessed["impact"], "reason": reason, "operation_id": operation_id, "environment": origin}
        if ws is not None:
            meta["workspace_id"] = ws["id"]
        if org is not None:
            meta["organization_id"] = org["id"]
        # The platform record first. A workspace's switch is filed under that workspace; a
        # platform-wide one and an organization's under none — not the administrator's own
        # session workspace.
        self.audit(request, target["action"], actor_id=actor["id"], object_kind="app", object_id=app_id,
                   workspace_id=ws["id"] if ws is not None else None, workspace_given=True, result="ok", meta=meta)
        if org is not None:
            # The organization's own governance-log row (`organization.app.<change>`, by: platform),
            # before the service method and its journal entry, as the lifecycle writes its own.
            self.audit(request, f"organization.app.{change}", actor_id=actor["id"], object_kind="organization",
                       object_id=org["id"], workspace_given=True, result="ok",
                       meta={"by": "platform", "app_id": app_id, "enabled": change == "enable", "reason": reason,
                             "operation_id": operation_id})
        try:
            if org is not None:
                if change == "disable":
                    changed = self.apps.disable_for_organization(org["id"], app_id, actor["id"])
                else:
                    changed = self.apps.enable_for_organization(org["id"], app_id, actor["id"])
                if changed:
                    # Read back under this lock: the switch's own time, the same row the list reads.
                    org_view["updated_at"] = organization_app_row(self.store, org["id"], app_id)["updated_at"]
            elif ws is not None:
                if change == "disable":
                    changed = self.apps.disable_for_workspace(ws["id"], app_id)
                else:
                    self.apps.enable_for_workspace(ws["id"], app_id, actor["id"])
                    changed = True
                    # Not a withdrawal: nothing was journalled, so reading the switch's time costs nothing.
                    ws_view["enabled_at"] = self.store.one(
                        "SELECT enabled_at FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                        (ws["id"], app_id))["enabled_at"]
            elif change in ("disable", "enable"):
                changed = self.apps.set_status(app_id, target["value"])
            else:
                changed = self.apps.set_access_policy(app_id, target["value"])
        except OSError:
            # The security journal could not append or fsync: nothing is committed.
            raise ServiceError(503, "journal_unavailable", "安全记录暂不可用，操作未提交，请稍后重试") from None
        if not changed:                         # re-checked under this lock above; never silently a no-op
            raise ServiceError(409, "no_change", "应用已经处于这个状态，不需要更改")
        tx.execute("INSERT INTO admin_operations (id,actor_id,action,target_id,idempotency_key,request_hash,"
                   "result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                   (operation_id, actor["id"], target["action"], app_id, key, request_hash, canonical(result), time.time()))
        return result

    def _failure(self, request, actor, app_id, change, wid, exc, *, phase, reason="", oid=None):
        """Outside the failed transaction, under the catalogued action the request named. Only a
        stable code, the phase, the change, an application, a workspace or an organization that
        exist and a valid reason are kept: invalid inputs and exception messages can hold
        arbitrary data. A change that names no catalogued act (a policy for a workspace, an
        unknown word) is not recorded."""
        scope = "organization" if oid is not None else "platform" if wid is None else "workspace"
        action = ACTIONS.get((scope, change)) if isinstance(change, str) else None
        if action is None:
            return
        meta = {"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed",
                "phase": phase, "change": change, "environment": self._environment(request)}
        if isinstance(wid, str) and len(wid) <= 128 and self.store.one("SELECT 1 FROM workspaces WHERE id=?", (wid,)):
            meta["workspace_id"] = wid
        if isinstance(oid, str) and len(oid) <= 128 and self.store.one("SELECT 1 FROM organizations WHERE id=?", (oid,)):
            meta["organization_id"] = oid
        if REASON_MIN <= len(reason) <= REASON_MAX:
            meta["reason"] = reason
        # The path's application only when it names one (a registered application, or the portal
        # refused as `portal_required`): an unknown id is the caller's text, not an object.
        known = app_id == PLATFORM_APP_ID or (len(app_id) <= 128 and self.apps.get(app_id) is not None)
        self.audit(request, action, actor_id=actor["id"], object_kind="app", object_id=app_id if known else None,
                   workspace_given=True,
                   result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error", meta=meta)


def build_router(store: Store, ident: Identity, audit) -> APIRouter:
    """Registered under the parent admin router, which supplies /api/admin. No parent route shares
    the `/apps` prefix (the run records are `/app-runs`). `/apps/{app_id}/preview` is declared
    before the generic `/apps/{app_id}/{change}`, so a preview is never read as a change."""
    r = APIRouter(tags=["admin"])
    governance = AppGovernance(store, ident, audit)

    @r.get("/apps")
    def list_apps(request: Request):
        """Every registered application with its state and counts (module docstring)."""
        governance._actor(request, VIEW)
        with governance.snapshot():
            items = [governance._item(app) for app in store.all("SELECT * FROM apps ORDER BY name, id")]
        return {"items": items}

    @r.get("/apps/{app_id}/workspaces")
    def app_workspaces(request: Request, app_id: str, q: str = Query("", max_length=100),
                       limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=2**63 - 1)):
        """The workspaces (not deleted) and this application in each: `{items: [{id, name, kind,
        status, organization_id, enabled, enabled_at, members, members_with_access}], total, limit,
        offset}`, the unclaimed historical workspace first, then by creation. 404 `no_app`."""
        governance._actor(request, VIEW)
        q = q.strip()
        with governance.snapshot():
            app = governance.apps.get(app_id)
            if not app:
                raise ServiceError(404, "no_app", "找不到该应用")
            where, args = "w.deleted_at IS NULL", []
            if q:
                where += " AND (w.name LIKE ? ESCAPE '\\' OR w.id=?)"
                args += [f"%{like_escape(q)}%", q]
            total = store.one(f"SELECT COUNT(*) AS n FROM workspaces w WHERE {where}", tuple(args))["n"]
            rows = store.all(f"SELECT w.id, w.name, w.kind, w.status, w.organization_id FROM workspaces w WHERE {where} "
                             "ORDER BY w.kind='legacy' DESC, w.created_at, w.id LIMIT ? OFFSET ?", (*args, limit, offset))
            items = governance._workspace_items(app, rows)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @r.get("/apps/{app_id}/organizations")
    def app_organizations(request: Request, app_id: str, q: str = Query("", max_length=100),
                          limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=2**63 - 1)):
        """B3: every organization and this application in each — `{items: [{id, name, status,
        deleted_at, recover_until, enabled, updated_at, members, conversations}], total, limit,
        offset}`, by creation. Only for an application switched per organization (400 `bad_change`
        otherwise); 404 `no_app`. `q` matches a substring of the name or a whole id."""
        governance._actor(request, VIEW)
        q = q.strip()
        with governance.snapshot():
            app = governance.apps.get(app_id)
            if not app:
                raise ServiceError(404, "no_app", "找不到该应用")
            if app_id not in ORGANIZATION_APP_IDS:
                raise ServiceError(400, "bad_change", f"「{app['name']}」不按组织开关：没有按组织的列表",
                                   {"field": "organization_id", "scope": app_scope(app_id)})
            where, args = "1=1", []
            if q:
                where += " AND (o.name LIKE ? ESCAPE '\\' OR o.id=?)"
                args += [f"%{like_escape(q)}%", q]
            total = store.one(f"SELECT COUNT(*) AS n FROM organizations o WHERE {where}", tuple(args))["n"]
            rows = store.all(f"SELECT o.id, o.name, o.status, o.deleted_at, o.recover_until, o.version FROM organizations o "
                             f"WHERE {where} ORDER BY o.created_at, o.id LIMIT ? OFFSET ?", (*args, limit, offset))
            items = governance._organization_items(app, rows)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @r.post("/apps/{app_id}/preview")
    def preview_app_change(request: Request, app_id: str, body: dict = Body(...)):
        """`{"change": "disable"|"enable"|"policy_explicit"|"policy_members", "workspace_id"?,
        "organization_id"?}` → the signed impact preview. Changes nothing."""
        return governance.preview(request, app_id, body if isinstance(body, dict) else {})

    @r.post("/apps/{app_id}/{change}")
    def change_app(request: Request, app_id: str, change: str, body: dict = Body(...)):
        """`{"reason", "preview_token", "idempotency_key", "workspace_id"?, "organization_id"?}` →
        the confirmed change, once: `{ok, operation_id, app, workspace?, organization?}`."""
        return governance.execute(request, app_id, change, body if isinstance(body, dict) else {})

    return r
