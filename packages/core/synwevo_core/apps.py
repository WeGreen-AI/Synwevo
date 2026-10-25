"""Application registry and access eligibility.

Signing in proves who someone is. Whether they may *use an application* is a separate question
with its own answer, and keeping the two apart is the point of this module: a second application
added later must not become reachable just because someone is a workspace administrator for the
first one.

Three things are checked, in order, and all three must hold:

1. the application exists and is enabled;
2. the workspace has turned that application on;
3. the person has an entitlement in that workspace, or the application's policy is `members`
   and they are a member.
"""
from __future__ import annotations

import sqlite3

from . import env
from .errors import ServiceError
from .config import DOCS_NAME, MESSAGES_NAME
from .identity import Identity
from .store import Store, now

#: 应用 ID。**值保持 "docweave" 不变**：它是 `apps` / `workspace_apps` / `app_entitlements`
#: 里的主键，也写在已经签发出去的令牌里。改这个字符串等于把现有的使用资格和机器凭据一起作废，
#: 换来的只是一次搜索结果清零。对外显示的名字是 DOCS_NAME。
DOCS_APP_ID = "docweave"

#: The messaging application (Synwevo Message), on its own origin since CI-66. A row in `apps`
#: like Synwevo Docs — it has a launch URL that the portal card, the platform bar's 消息 link and
#: every `conversation.*` inbox item resolve through — but its eligibility is **not** the workspace
#: model this module describes: conversations belong to an organization, not to a workspace, and
#: who may read one is decided by `synwevo_core.messaging` (organization membership, conversation
#: membership, `joined_seq`). So `check_access` treats it like the platform id: any live account
#: may open the application; what they see inside it is the service layer's answer.
MESSAGES_APP_ID = "messages"

# The portal itself. Deliberately *not* a row in `apps`: the registry lists business
# applications someone can be granted or denied, and the portal is the place you go to find out
# which of those you have. Putting it in the registry would make it appear on its own home page
# as an application you can open, and would make "no entitlement to the portal" a state in which
# a person cannot reach their own account settings.
PLATFORM_APP_ID = "platform"

#: The business applications served on the workplace origin by `synwevo_suite`
#: (docs/agent-work/DECISIONS.md D1). Workspace-scoped like Synwevo Docs, except the personal Agent.
from .identity import SUITE_WORKSPACE_APPS  # noqa: E402
SUITE_APP_IDS = SUITE_WORKSPACE_APPS + ("agent",)

#: Applications that belong to a person rather than to a workspace. Like the portal, any live
#: account may open them; what they can reach inside is authorized per call, in the workspace
#: each call names (the capability layer, capabilities.py). Being able to open the Agent grants nothing.
PERSONAL_APP_IDS = frozenset({"agent"})

#: Applications switched per organization (B3): their data belongs to an organization, not to a
#: workspace, so the platform turns them on or off for one organization (`organization_apps`,
#: `Apps.disable_for_organization`). Today only Synwevo Message; the platform-wide switch
#: (`apps.status`, `Apps.set_status`) outranks it.
ORGANIZATION_APP_IDS = frozenset({MESSAGES_APP_ID})


def app_disabled(store: Store, app_id: str) -> bool:
    """Whether the application is switched off platform-wide (`apps.status = 'disabled'`). An
    application that never registered is not "disabled": a deployment without it has nothing to
    switch off. One primary-key read, uncached — the switch holds from the next request on."""
    row = store.one("SELECT status FROM apps WHERE id=?", (app_id,))
    return bool(row) and row["status"] == "disabled"


def organization_app_row(store: Store, organization_id: str, app_id: str) -> dict | None:
    """The organization's switch row for the application (`status`, `updated_at`,
    `updated_by`), or None when it was never switched (on). Tolerant of a database opened without
    the table (`Store(..., migrate=False)` on an older copy): None."""
    try:
        return store.one("SELECT organization_id, app_id, status, updated_at, updated_by FROM organization_apps "
                         "WHERE organization_id=? AND app_id=?", (organization_id, app_id))
    except sqlite3.OperationalError:
        return None


def organization_app_disabled(store: Store, organization_id: str, app_id: str) -> bool:
    """Whether the platform switched the application off for this organization (its own switch
    only — `app_disabled` is the platform-wide one, which outranks it)."""
    row = organization_app_row(store, organization_id, app_id)
    return bool(row) and row["status"] == "disabled"


def disabled_organizations(store: Store, app_id: str) -> set[str]:
    """The organizations the platform switched the application off for. The table holds only the
    organizations ever switched, so this is one small read (empty on almost every deployment)."""
    try:
        rows = store.all("SELECT organization_id FROM organization_apps WHERE app_id=? AND status='disabled'", (app_id,))
    except sqlite3.OperationalError:
        return set()
    return {r["organization_id"] for r in rows}


def entitlement_state(store: Store, workspace_id: str, app_id: str, user_id: str) -> str:
    """``granted``, ``revoked`` or ``none``: this person's entitlement row, as it applies to
    their **current** membership of the workspace.

    A revocation older than the membership it would apply to is stale and reads as ``none``.
    Removing someone from a workspace writes explicit revocations (so the removal holds under
    the ``members`` policy, where membership alone would otherwise let them in), and nothing on
    the way back in clears them — `Identity.add_member` does not, and neither did invitation
    acceptance at first. Honoured as-is, those rows locked every re-added or re-invited member
    out of the application for good: every member of an upgraded database (adoption gave them
    all rows) and every owner of a new one. Removing the membership deletes its row, so a re-add
    starts a fresh ``created_at``; a revocation written after that is in force, one written
    before it belonged to the membership that ended.

    Equal timestamps count as in force: on a coarse clock the tie goes to the withdrawal.

    One indexed read. Shared with `synwevo_core.authority`, which asks the same question for
    deferred work and must give the same answer as the browser gate.
    """
    row = store.one(
        "SELECT e.revoked_at, m.created_at AS member_since FROM app_entitlements e "
        "LEFT JOIN memberships m ON m.workspace_id=e.workspace_id AND m.user_id=e.user_id "
        "WHERE e.workspace_id=? AND e.app_id=? AND e.user_id=?", (workspace_id, app_id, user_id))
    if not row:
        return "none"
    if row["revoked_at"] is None:
        return "granted"
    if row["member_since"] is not None and row["revoked_at"] < row["member_since"]:
        return "none"
    return "revoked"


class Apps:
    def __init__(self, store: Store, ident: Identity):
        self.store = store
        self.ident = ident

    # ------------------------------------------------------------------ registry
    def get(self, app_id: str) -> dict | None:
        return self.store.one("SELECT * FROM apps WHERE id=?", (app_id,))

    def register(self, *, app_id: str, name: str, launch_url: str | None, icon: str | None = None,
                 access_policy: str | None = None, keep_launch_url: bool = False) -> dict:
        """Register or update an application.

        `keep_launch_url` leaves an existing launch URL alone. It matters because every process
        runs this at startup, and `SYNWEVO_APP_URL` means *this service's own address*: the
        portal process registering Synwevo Docs would otherwise overwrite Synwevo Docs' launch URL with
        the portal's, producing a card on the home page that loops straight back to the home
        page. Only the process that actually is the application sets its address.

        `access_policy=None` keeps whatever the registry already says (``members`` for a new
        row). Startup registration used to pass ``members`` unconditionally, so an operator who
        had switched Synwevo Docs to per-user ``explicit`` grants had that decision reverted —
        and access re-opened to every member — on the next restart of any service.
        """
        ts = now()
        existing = self.get(app_id)
        if (keep_launch_url or launch_url is None) and existing:
            launch_url = existing["launch_url"]
        if launch_url is None:
            launch_url = "http://docweave.localhost:8765/"
        if access_policy is None:
            access_policy = existing["access_policy"] if existing else "members"
        if access_policy not in ("members", "explicit"):
            raise ServiceError(400, "bad_policy", f"未知的访问策略 {access_policy!r}（members / explicit）")
        self.store.execute(
            "INSERT INTO apps (id,name,icon,launch_url,status,access_policy,created_at,updated_at) "
            "VALUES (?,?,?,?,'active',?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "icon=excluded.icon, launch_url=excluded.launch_url, access_policy=excluded.access_policy, "
            "updated_at=excluded.updated_at",
            (app_id, name, icon, launch_url, access_policy, ts, ts))
        return self.get(app_id)

    def enable_for_workspace(self, workspace_id: str, app_id: str, actor_id: str | None = None) -> None:
        self.store.execute(
            "INSERT INTO workspace_apps (workspace_id,app_id,enabled_at,enabled_by) VALUES (?,?,?,?) "
            "ON CONFLICT(workspace_id,app_id) DO NOTHING", (workspace_id, app_id, now(), actor_id))

    def grant(self, workspace_id: str, app_id: str, user_id: str, actor_id: str | None = None) -> None:
        self.store.execute(
            "INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by) "
            "VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,app_id,user_id) DO UPDATE SET "
            "revoked_at=NULL, granted_at=excluded.granted_at, granted_by=excluded.granted_by",
            (workspace_id, app_id, user_id, now(), actor_id))

    def revoke(self, workspace_id: str, app_id: str, user_id: str) -> None:
        """Withdraw one person's right to use one application in one workspace.

        Written as an upsert, not an UPDATE. Under the ``members`` policy most people have no
        entitlement row at all — membership is what lets them in — so an UPDATE matched nothing
        and `app revoke` printed "已撤销" while changing nothing. The revocation has to exist as a
        row for the eligibility check to see it.

        Journalled in the same transaction as the database write (joining the caller's, if any),
        ahead of its COMMIT and with a receipt that commits with it (`journal._intent`), so a
        restore re-applies it to any backup that does not hold it (`synwevo_core.journal`). For a
        ``members``-policy person the backup usually has no row at all, so replay has to insert
        one, not only update — see `journal.apply`. A journal failure raises: a withdrawal that
        cannot be made durable must not be reported as done. The revocation itself stands — the
        failure is caught inside the transaction, which commits it with its receipt, and raised
        after — unless a caller's transaction rolls back on it (the attempt is then voided).

        The row's ``revoked_at`` is always set to now, even over an older revocation: an older
        one may predate the current membership and be stale (:func:`entitlement_state`).
        """
        from . import journal
        failure: OSError | None = None
        with self.store.tx() as t:
            ts = now()
            t.execute(
                "INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by,revoked_at) "
                "VALUES (?,?,?,?,NULL,?) ON CONFLICT(workspace_id,app_id,user_id) DO UPDATE SET "
                "revoked_at=excluded.revoked_at",
                (workspace_id, app_id, user_id, ts, ts))
            try:
                journal._intent(self.store, "entitlement_revoked", workspace_id=workspace_id, app_id=app_id,
                                user_id=user_id)
            except OSError as e:
                failure = e
        if failure is not None:
            raise failure

    def disable_for_workspace(self, workspace_id: str, app_id: str) -> bool:
        """Turn an application off for one workspace. Takes effect on the next request of every
        open session, because the platform gate re-checks eligibility on each one.

        Journalled like every other withdrawal, so restoring a backup taken while the
        application was still on does not quietly turn it back on. Only when something was
        actually turned off. The entry is written ahead, inside the transaction that deletes the
        row, with a receipt that commits with it (`journal._intent`): a journal failure raises and
        rolls the deletion back (and voids the attempt), so running the command again after
        fixing the data directory still finds the row and journals it; a backup taken between
        the entry and the COMMIT is turned off on restore.
        """
        from . import journal
        with self.store.tx() as t:
            changed = t.execute("DELETE FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                                (workspace_id, app_id)).rowcount > 0
            if changed:
                journal._intent(self.store, "workspace_app_disabled", workspace_id=workspace_id, app_id=app_id)
        return changed

    def _organization_switch(self, organization_id: str, app_id: str) -> None:
        """What both organization switches refuse: an application that is not switched per
        organization (400 `bad_change`), and an organization that does not exist (404
        `no_organization`)."""
        if app_id not in ORGANIZATION_APP_IDS:
            raise ServiceError(400, "bad_change", "该应用不按组织开关", {"app_id": app_id, "field": "organization_id"})
        if not isinstance(organization_id, str) or not organization_id.strip():
            raise ServiceError(400, "bad_change", "请指定组织", {"field": "organization_id"})
        if not self.store.one("SELECT 1 FROM organizations WHERE id=?", (organization_id,)):
            raise ServiceError(404, "no_organization", "找不到该组织", {"organization_id": organization_id})

    def disable_for_organization(self, organization_id: str, app_id: str, actor_id: str | None = None) -> bool:
        """Turn an organization-scoped application (`ORGANIZATION_APP_IDS`) off for one
        organization (B3). Returns whether anything changed. For Synwevo Message it takes effect on
        the next call of every open session: every conversation of the organization is refused
        `403 messages_disabled` (`synwevo_core.messaging`), nothing is deleted.

        A withdrawal, journalled like `disable_for_workspace`: the upsert to `disabled` and the
        line (`organization_app_disabled`, written ahead with its receipt, `journal._intent`) share
        one transaction, so a journal failure raises and rolls the switch back (and voids the
        attempt), and a restore from a backup taken while it was on — or between the line and the
        COMMIT — turns it off again. Nothing is journalled when it was already off."""
        from . import journal
        self._organization_switch(organization_id, app_id)
        with self.store.tx() as t:
            changed = t.execute(
                "INSERT INTO organization_apps (organization_id,app_id,status,updated_at,updated_by) VALUES (?,?,'disabled',?,?) "
                "ON CONFLICT(organization_id,app_id) DO UPDATE SET status='disabled', updated_at=excluded.updated_at, "
                "updated_by=excluded.updated_by WHERE organization_apps.status<>'disabled'",
                (organization_id, app_id, now(), actor_id)).rowcount > 0
            if changed:
                journal._intent(self.store, "organization_app_disabled", organization_id=organization_id, app_id=app_id)
        return changed

    def enable_for_organization(self, organization_id: str, app_id: str, actor_id: str | None = None) -> bool:
        """Turn it back on for one organization. Returns whether anything changed (an organization
        never switched off is on already). Not journalled, the same as turning an application on
        platform-wide. The row is updated to `active`, never deleted: its `updated_at` is what a
        restore reads to tell a switch-off that never committed from the backup's own race
        (`journal._touched_since`)."""
        self._organization_switch(organization_id, app_id)
        with self.store.tx() as t:
            return t.execute("UPDATE organization_apps SET status='active', updated_at=?, updated_by=? "
                             "WHERE organization_id=? AND app_id=? AND status<>'active'",
                             (now(), actor_id, organization_id, app_id)).rowcount > 0

    def set_status(self, app_id: str, status: str) -> bool:
        """Turn an application on or off platform-wide. Returns whether anything changed.

        Turning it off is a withdrawal like any other and is journalled ahead, inside the same
        transaction, with a receipt that commits with it (`journal._intent`; a journal failure
        rolls it back and voids the attempt), so a restore from a backup taken while it was on —
        or between the entry and the COMMIT — keeps it off. Turning it on is not journalled, the
        same as enabling it for a workspace.

        Turning it off also deletes the deployment's "held back" marker of a business application
        (`SUITE_UNRELEASED_PREFIX`): the switch-off is the operator's (command line or admin
        console) from then on, and a later release of the application must not turn it back on
        — `bootstrap_suite_app` re-activates only what it switched off itself."""
        from . import journal
        if status not in ("active", "disabled"):
            raise ValueError(status)
        with self.store.tx() as t:
            changed = t.execute("UPDATE apps SET status=?, updated_at=? WHERE id=? AND status<>?",
                                (status, now(), app_id, status)).rowcount > 0
            if changed and status == "disabled":
                t.execute("DELETE FROM meta WHERE key=?", (SUITE_UNRELEASED_PREFIX + app_id,))
                journal._intent(self.store, "app_disabled", app_id=app_id)
        return changed

    def set_access_policy(self, app_id: str, policy: str) -> bool:
        """Change who may use an application: ``members`` (membership of a workspace that turned
        it on is enough, unless revoked) or ``explicit`` (only a granted entitlement). The policy
        is the application's, platform-wide. Returns whether anything changed.

        Tightening to ``explicit`` is a withdrawal — every member without a grant loses the
        application — and is journalled inside the same transaction as the UPDATE, with a receipt
        in `meta` that commits with it (`journal.record_app_policy_tightened`): a journal failure
        rolls it back, and a restore into a database without the receipt — one from before the
        tightening, or one taken before it committed — comes back ``explicit``, while a database
        that holds the receipt keeps whatever it says, a later loosening included. Loosening is not
        journalled, the same as turning an application on (`set_status`). Neither touches an
        entitlement row: grants stay granted and revocations stay revoked either way.
        """
        from . import journal
        if policy not in ("members", "explicit"):
            raise ServiceError(400, "bad_policy", f"未知的访问策略 {policy!r}（members / explicit）")
        with self.store.tx() as t:
            changed = t.execute("UPDATE apps SET access_policy=?, updated_at=? WHERE id=? AND access_policy<>?",
                                (policy, now(), app_id, policy)).rowcount > 0
            if changed and policy == "explicit":
                journal.record_app_policy_tightened(self.store, app_id=app_id)
        return changed

    # ------------------------------------------------------------------ eligibility
    def check_access(self, user_id: str, app_id: str,
                     workspace_id: str | None = None) -> tuple[bool, str | None, str]:
        """(allowed, workspace_id, reason).

        `workspace_id` is returned rather than taken on trust: it is the workspace this person
        will actually be acting in, resolved here from live membership. The reason string is
        shown to the person, so it says what to do, not just that something failed.
        """
        if app_id == PLATFORM_APP_ID:
            return self._check_platform(user_id, workspace_id)
        app = self.get(app_id)
        if not app:
            return False, None, "该应用不存在"
        if app["status"] != "active":
            return False, None, "该应用已停用"
        if app_id in PERSONAL_APP_IDS:
            # Opening the application needs a live account; an operation that names a workspace (a
            # workspace's own policy for it) needs membership of that workspace as well.
            return self._check_platform(user_id, workspace_id)
        if app_id == MESSAGES_APP_ID:
            # Organization-scoped, not workspace-scoped (see MESSAGES_APP_ID): the application is
            # open to every live account, and someone with no workspace at all still has their
            # organizations' conversations. Requiring `workspace_apps` or an entitlement here would
            # lock people out of conversations they are members of for a reason that has nothing
            # to do with messaging — while an operator's `app disable messages` still holds above.
            return self._check_platform(user_id, workspace_id)

        spaces = self.ident.workspaces_for(user_id)
        if not spaces:
            return False, None, "你还不属于任何工作空间，请联系管理员邀请你加入"

        candidates = [w for w in spaces if not workspace_id or w["id"] == workspace_id]
        if workspace_id and not candidates:
            return False, None, "你不是该工作空间的成员"

        revoked = False
        from .organizations import workspace_access_failure
        for w in candidates:
            if w["status"] != "active":
                continue
            if workspace_access_failure(self.store, w["id"], user_id):
                continue
            if not self.store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                                  (w["id"], app_id)):
                continue
            state = entitlement_state(self.store, w["id"], app_id, user_id)
            if state == "revoked":
                # An explicit revocation outranks the policy. `members` means "membership is
                # enough when nobody has said otherwise"; it used to mean "membership is enough,
                # full stop", which made `app revoke` a no-op for the only policy in use. Only a
                # revocation of the *current* membership counts — see entitlement_state().
                revoked = True
                continue
            if state == "granted" or app["access_policy"] == "members":
                return True, w["id"], "ok"

        if workspace_id and revoked:
            return False, None, f"你在该工作空间使用「{app['name']}」的资格已被撤销，请联系工作空间管理员"
        return False, None, f"你所在的工作空间尚未启用「{app['name']}」，或你没有使用它的资格"

    def _check_platform(self, user_id: str, workspace_id: str | None) -> tuple[bool, str | None, str]:
        """Anyone with a live account may open the portal.

        Including someone who belongs to no workspace at all. They see a real empty state and
        their own account settings — not a locked door, and emphatically not the legacy
        workspace. The returned workspace is their current one *if they have one*; `None` is a
        valid answer and the portal renders it honestly.
        """
        user = self.ident.get_user(user_id)
        if not user or user["status"] != "active":
            return False, None, "账户不可用"
        spaces = [w for w in self.ident.workspaces_for(user_id) if w["status"] == "active"]
        if workspace_id:
            match = [w for w in spaces if w["id"] == workspace_id]
            if not match:
                return False, None, "你不是该工作空间的成员"
            return True, workspace_id, "ok"
        return True, (spaces[0]["id"] if spaces else None), "ok"

    def check_access_for_workspace(self, workspace_id: str, app_id: str) -> tuple[bool, str | None, str]:
        """Is this application usable *in this workspace*, with nobody in particular asking?

        The service-account question. There is no user, so there is no membership to resolve —
        only whether the application is live and the workspace has turned it on. Deliberately a
        separate method rather than a `user_id=None` branch of `check_access`: a null user
        silently skipping the entitlement check is the shape of a bug nobody notices.
        """
        app = self.get(app_id)
        if not app:
            return False, None, "该应用不存在"
        if app["status"] != "active":
            return False, None, "该应用已停用"
        ws = self.ident.get_workspace(workspace_id)
        if not ws or ws["status"] != "active":
            return False, None, "该工作空间不可用"
        from .organizations import workspace_access_failure
        if workspace_access_failure(self.store, workspace_id):
            return False, None, "该组织当前不可用"
        if not self.store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                              (workspace_id, app_id)):
            return False, None, f"该工作空间尚未启用「{app['name']}」"
        return True, workspace_id, "ok"

    def apps_for(self, user_id: str, workspace_id: str | None = None) -> list[dict]:
        """What the portal shows. Only applications this person can actually open.

        With `workspace_id`, only what is usable *in that workspace* — the portal passes the
        workspace its session is in, so switching workspace changes the cards rather than being
        decorative. Without it, the first workspace where each application is usable.

        Returns nothing about the contents of any workspace — the portal must not become a place
        where project names leak to someone who cannot read them.
        """
        out = []
        for app in self.store.all("SELECT * FROM apps WHERE status='active' ORDER BY name"):
            ok, wid, reason = self.check_access(user_id, app["id"], workspace_id)
            if ok:
                ws = self.ident.get_workspace(wid)
                from urllib.parse import urlsplit, urlunsplit, urlencode
                launch = urlsplit(app["launch_url"])
                # The destination reauthorizes this untrusted hint during SSO and callback.
                # Persist it in the sign-in request, never in an unscoped browser preference.
                # An account with no workspace (opening the messages application) sends no hint
                # at all rather than the string "None".
                hint = {"workspace_id": wid} if wid else {}
                launch_url = urlunsplit((launch.scheme, launch.netloc, "/auth/start",
                                        urlencode({**hint, "next": launch.path or "/"}), ""))
                out.append({"id": app["id"], "name": app["name"], "icon": app["icon"],
                            "launch_url": launch_url, "workspace_id": wid,
                            "workspace_name": (ws or {}).get("name")})
        return out

    # ------------------------------------------------------------------ migration
    def adopt_existing_members(self, app_id: str = DOCS_APP_ID, actor_id: str | None = None) -> dict:
        """Give every current member of every workspace an entitlement to this application.

        The one-time migration for when the app registry was introduced. Without it, adding an
        entitlement table would lock out everyone who already had access — the table starts
        empty, and an empty table means "nobody" under a default-deny rule. Services call
        :meth:`adopt_once`, which runs this at most once per database; calling it directly is for
        the CLI and tests.

        It never clears a revocation: an existing entitlement row, revoked or not, is left
        exactly as it is. It used to go through `grant()`, whose upsert sets `revoked_at=NULL`,
        and it ran on every start of the account service — so every `app revoke` was undone by
        the next restart.

        Deliberately scoped to one application and to workspaces that already exist. It grants
        nothing else, and it does not touch workspace `default`, which stays memberless and
        unclaimed.
        """
        enabled = claimed = 0
        ts = now()
        rows = self.store.all("SELECT DISTINCT workspace_id FROM memberships")
        for row in rows:
            wid = row["workspace_id"]
            if not self.store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                                  (wid, app_id)):
                self.enable_for_workspace(wid, app_id, actor_id)
                enabled += 1
        for m in self.store.all("SELECT workspace_id, user_id FROM memberships"):
            self.store.execute(
                "INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by) "
                "VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,app_id,user_id) DO NOTHING",
                (m["workspace_id"], app_id, m["user_id"], ts, actor_id))
            claimed += 1
        return {"workspaces_enabled": enabled, "entitlements": claimed}

    def adopt_once(self, app_id: str = DOCS_APP_ID) -> dict | None:
        """Run :meth:`adopt_existing_members` once per database, recorded in `meta`.

        Returns the adoption's counts when it ran now, None when it had already happened.

        A database that predates the marker but already has registry rows for this application
        was adopted by the earlier every-start code; running the adoption again would re-enable
        the application in every workspace an operator has since turned it off in, so it is only
        marked done. Only a database with no registry rows at all — one from before the registry
        existed — is actually adopted.
        """
        key = f"apps_adopted:{app_id}"
        if self.store.one("SELECT 1 FROM meta WHERE key=?", (key,)):
            return None
        had_rows = bool(
            self.store.one("SELECT 1 FROM workspace_apps WHERE app_id=? LIMIT 1", (app_id,))
            or self.store.one("SELECT 1 FROM app_entitlements WHERE app_id=? LIMIT 1", (app_id,)))
        out = None if had_rows else self.adopt_existing_members(app_id)
        self.store.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                           (key, str(now())))
        return out


def bootstrap_docs_app(store: Store, ident: Identity, *, launch_url: str | None = None) -> dict:
    """Register Synwevo Docs itself. Idempotent; safe to run on every start, in any role.

    The launch URL comes from `SYNWEVO_APP_URL` **only in the process that serves Synwevo Docs**.
    In the portal or the account service that variable holds their own address, and writing it
    here points the Synwevo Docs card at the wrong service — which is exactly what happened: the
    account service starts first, `SYNWEVO_OIDC_APP_ID` is unset there so `app_id()` returned
    its default of "docweave", and the card led back to the login page.

    So the test is the *role*, which every process sets and which cannot default to the wrong
    answer: only `app` (Synwevo Docs' own process) and `all` (the single-process deployment) serve
    Synwevo Docs.
    """
    import os
    apps = Apps(store, ident)
    role = (env.get("ROLE") or "all").strip().lower()
    mine = role in ("app", "all", "")
    # access_policy=None: a new registration starts as `members`, an existing one keeps the
    # policy an operator chose.
    return apps.register(
        app_id=DOCS_APP_ID, name=DOCS_NAME, icon="doc",
        launch_url=launch_url or (env.get("APP_URL") if mine else None),
        access_policy=None, keep_launch_url=not mine and not launch_url)


def bootstrap_messages_app(store: Store, ident: Identity, *, launch_url: str | None = None,
                           role: str | None = None) -> dict:
    """Register Synwevo Message. Idempotent; safe to run on every start of the process that serves it.

    The same rule as :func:`bootstrap_docs_app`: `SYNWEVO_APP_URL` is *this service's own
    address*, so only the `messages` role (its own origin) and `all` (one origin for everything)
    write it. On `all` the application lives under `/messages` of the shared origin, so the launch
    URL carries that path; on its own origin it is served at `/`. Any other role writing it would
    point the 消息 link and every conversation notification at the wrong service. `role` is the
    role the caller was composed as (a composer may be told it outright, see `http.app_role`);
    without it the environment's answer is used.

    Nothing else registers it: the account service, the portal and Synwevo Docs learn about the
    application from the row this writes (:func:`messages_launch_url`), never from their own
    environment.
    """
    apps = Apps(store, ident)
    role = (role or env.get("ROLE") or "all").strip().lower()
    mine = role in ("messages", "all", "")
    if launch_url is None and mine and env.get("APP_URL"):
        base = env.get("APP_URL").rstrip("/")
        launch_url = base + ("/messages" if role in ("all", "") else "/")
    return apps.register(
        app_id=MESSAGES_APP_ID, name=MESSAGES_NAME, icon="chat",
        launch_url=launch_url, access_policy=None, keep_launch_url=not mine and not launch_url)


def messages_launch_url(store: Store) -> str | None:
    """The messages application's launch URL when it is registered and active, else None.

    Read from the registry row the messages process writes at start-up (:func:`bootstrap_messages_app`),
    so the account service, the portal and Docs point the 消息 link, the `/messages` redirects and
    every `conversation.*` inbox item at the origin that actually serves conversations — and say
    nothing (None) on a deployment that has not added the application. Never from the reader's own
    `SYNWEVO_APP_URL`: that is the reader's address. The trailing slash is dropped so callers can
    append a path.
    """
    row = store.one("SELECT launch_url, status FROM apps WHERE id=?", (MESSAGES_APP_ID,))
    if not row or row["status"] != "active":
        return None
    return _http_url(row["launch_url"])


def messages_registered_url(store: Store) -> str | None:
    """The messages application's launch URL once it has registered, whatever its status — None
    only on a deployment that never added it.

    What decides whether the account origin still serves conversations itself: once Synwevo
    Message exists it is the only writer, and disabling it must not hand conversations back to the
    account origin's legacy fallback (which would also bypass the disable). Pointing people at the
    origin is still `messages_launch_url`'s, active only (admin defect D7).
    """
    row = store.one("SELECT launch_url FROM apps WHERE id=?", (MESSAGES_APP_ID,))
    return _http_url(row["launch_url"]) if row else None


def _http_url(raw) -> str | None:
    url = (raw or "").strip()
    return url.rstrip("/") if url.startswith(("http://", "https://")) else None


def messages_origin(store: Store) -> str | None:
    """``scheme://host[:port]`` of :func:`messages_launch_url`, for paths on that origin."""
    url = messages_launch_url(store)
    if not url:
        return None
    from urllib.parse import urlsplit
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else None


#: `meta` key prefix (+ application id) that `bootstrap_suite_app` writes when it switches off an
#: application the deployment does not release (``SYNWEVO_SUITE_APPS``), and removes when it is
#: released again — which also turns the application back on.
SUITE_UNRELEASED_PREFIX = "suite_unreleased:"


def suite_unreleased(store: Store, app_id: str) -> bool:
    """Whether the deployment held this business application back at the serving process's last
    start: its operations do not exist there, and the next start switches it off again whatever
    the registry says. Read from the marker, the one signal that crosses processes — the
    account service cannot ask ``SYNWEVO_SUITE_APPS`` of the process that serves the application.
    An application an operator had switched off before the deployment stopped releasing it
    carries no marker (`bootstrap_suite_app` only marks what it switches off itself)."""
    return bool(store.one("SELECT 1 FROM meta WHERE key=?", (SUITE_UNRELEASED_PREFIX + app_id,)))


def bootstrap_suite_app(store: Store, ident: Identity, *, app_id: str, name: str, icon: str,
                        launch_url: str | None, released: bool) -> dict | None:
    """Register one business application of the workplace origin. Run by the process that serves
    them (portal or all), on every start; idempotent.

    ``released`` is the deployment's decision (``SYNWEVO_SUITE_APPS``, DECISIONS.md D4). A released
    application is registered, and adopted once into the workspaces that existed before it — the
    same one-time adoption Synwevo Docs had, so members can use it without an administrator
    switching it on. An application that is *not* released is marked disabled if a row exists,
    with a marker so that releasing it again re-activates it; an application an operator disabled
    by hand (no marker) stays disabled, because registration never touches ``status``.
    """
    if app_id not in SUITE_APP_IDS:
        raise ValueError(f"unknown suite application {app_id}")
    marker = SUITE_UNRELEASED_PREFIX + app_id
    apps = Apps(store, ident)
    if not released:
        with store.tx() as t:
            changed = t.execute("UPDATE apps SET status='disabled', updated_at=? WHERE id=? AND status='active'",
                                (now(), app_id)).rowcount
            if changed:
                t.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (marker, str(now())))
        return None
    row = apps.register(app_id=app_id, name=name, icon=icon, launch_url=launch_url, access_policy=None)
    if store.one("SELECT 1 FROM meta WHERE key=?", (marker,)):
        with store.tx() as t:
            t.execute("UPDATE apps SET status='active', updated_at=? WHERE id=?", (now(), app_id))
            t.execute("DELETE FROM meta WHERE key=?", (marker,))
    if app_id not in PERSONAL_APP_IDS:
        apps.adopt_once(app_id)
    return apps.get(app_id)
