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

from .errors import ServiceError
from .identity import Identity
from .store import Store, now

DOCWEAVE_APP_ID = "docweave"

# The portal itself. Deliberately *not* a row in `apps`: the registry lists business
# applications someone can be granted or denied, and the portal is the place you go to find out
# which of those you have. Putting it in the registry would make it appear on its own home page
# as an application you can open, and would make "no entitlement to the portal" a state in which
# a person cannot reach their own account settings.
PLATFORM_APP_ID = "platform"


class Apps:
    def __init__(self, store: Store, ident: Identity):
        self.store = store
        self.ident = ident

    # ------------------------------------------------------------------ registry
    def get(self, app_id: str) -> dict | None:
        return self.store.one("SELECT * FROM apps WHERE id=?", (app_id,))

    def register(self, *, app_id: str, name: str, launch_url: str | None, icon: str | None = None,
                 access_policy: str = "members", keep_launch_url: bool = False) -> dict:
        """Register or update an application.

        `keep_launch_url` leaves an existing launch URL alone. It matters because every process
        runs this at startup, and `DOCWEAVE_APP_URL` means *this service's own address*: the
        portal process registering DocWeave would otherwise overwrite DocWeave's launch URL with
        the portal's, producing a card on the home page that loops straight back to the home
        page. Only the process that actually is the application sets its address.
        """
        ts = now()
        existing = self.get(app_id)
        if (keep_launch_url or launch_url is None) and existing:
            launch_url = existing["launch_url"]
        if launch_url is None:
            launch_url = "http://docweave.localhost:8765/"
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
        self.store.execute(
            "UPDATE app_entitlements SET revoked_at=? WHERE workspace_id=? AND app_id=? AND user_id=?",
            (now(), workspace_id, app_id, user_id))

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

        spaces = self.ident.workspaces_for(user_id)
        if not spaces:
            return False, None, "你还不属于任何工作空间，请联系管理员邀请你加入"

        candidates = [w for w in spaces if not workspace_id or w["id"] == workspace_id]
        if workspace_id and not candidates:
            return False, None, "你不是该工作空间的成员"

        for w in candidates:
            if w["status"] != "active":
                continue
            if not self.store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                                  (w["id"], app_id)):
                continue
            ent = self.store.one(
                "SELECT 1 FROM app_entitlements WHERE workspace_id=? AND app_id=? AND user_id=? "
                "AND revoked_at IS NULL", (w["id"], app_id, user_id))
            if ent or app["access_policy"] == "members":
                return True, w["id"], "ok"

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
        if not self.store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                              (workspace_id, app_id)):
            return False, None, f"该工作空间尚未启用「{app['name']}」"
        return True, workspace_id, "ok"

    def apps_for(self, user_id: str) -> list[dict]:
        """What the portal shows. Only applications this person can actually open.

        Returns nothing about the contents of any workspace — the portal must not become a place
        where project names leak to someone who cannot read them.
        """
        out = []
        for app in self.store.all("SELECT * FROM apps WHERE status='active' ORDER BY name"):
            ok, wid, reason = self.check_access(user_id, app["id"])
            if ok:
                ws = self.ident.get_workspace(wid)
                out.append({"id": app["id"], "name": app["name"], "icon": app["icon"],
                            "launch_url": app["launch_url"], "workspace_id": wid,
                            "workspace_name": (ws or {}).get("name")})
        return out

    # ------------------------------------------------------------------ migration
    def adopt_existing_members(self, app_id: str = DOCWEAVE_APP_ID, actor_id: str | None = None) -> dict:
        """Give every current member of every workspace an entitlement to this application.

        Run once when the app registry is introduced. Without it, adding an entitlement table
        would lock out everyone who already had access — the table starts empty, and an empty
        table means "nobody" under a default-deny rule.

        Deliberately scoped to one application and to workspaces that already exist. It grants
        nothing else, and it does not touch workspace `default`, which stays memberless and
        unclaimed.
        """
        enabled = claimed = 0
        rows = self.store.all("SELECT DISTINCT workspace_id FROM memberships")
        for row in rows:
            wid = row["workspace_id"]
            if not self.store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                                  (wid, app_id)):
                self.enable_for_workspace(wid, app_id, actor_id)
                enabled += 1
        for m in self.store.all("SELECT workspace_id, user_id FROM memberships"):
            self.grant(m["workspace_id"], app_id, m["user_id"], actor_id)
            claimed += 1
        return {"workspaces_enabled": enabled, "entitlements": claimed}


def bootstrap_docweave(store: Store, ident: Identity, *, launch_url: str | None = None) -> dict:
    """Register DocWeave itself. Idempotent; safe to run on every start, in any role.

    The launch URL comes from `DOCWEAVE_APP_URL` **only in the process that serves DocWeave**.
    In the portal or the account service that variable holds their own address, and writing it
    here points the DocWeave card at the wrong service — which is exactly what happened: the
    account service starts first, `DOCWEAVE_OIDC_APP_ID` is unset there so `app_id()` returned
    its default of "docweave", and the card led back to the login page.

    So the test is the *role*, which every process sets and which cannot default to the wrong
    answer: only `app` (DocWeave's own process) and `all` (the single-process deployment) serve
    DocWeave.
    """
    import os
    apps = Apps(store, ident)
    role = (os.environ.get("DOCWEAVE_ROLE") or "all").strip().lower()
    mine = role in ("app", "all", "")
    return apps.register(
        app_id=DOCWEAVE_APP_ID, name="DocWeave", icon="doc",
        launch_url=launch_url or (os.environ.get("DOCWEAVE_APP_URL") if mine else None),
        access_policy="members", keep_launch_url=not mine and not launch_url)
