"""An organization's governance log, read by people holding `org.audit.read` (CI-101).

The permission was in the catalogue (organization-roles.md) with nothing behind it; this is what it
reads. What the log is, and is not:

* **Governance only.** The organization's own events (settings, lifecycle, members, custom roles,
  member imports, copy approvals, ownership) and, for the organization's workspaces, the member and
  invitation events (who was invited, whose role changed, who was removed or left, ownership
  transfers). Never business events — nothing from Synwevo Docs or Synwevo Message — and never
  anyone's sign-in or security activity.
* **Metadata, bounded.** Time, who acted (a member's display name; a platform administrator or the
  system are named as such), what, on which workspace, member or role (names resolved now), the
  result, and a short allowlist of details (the reason given, before/after roles and states,
  counts). Never IP addresses, request ids, invitees' addresses or free-form metadata.
* **Only from when the workspace was the organization's.** A workspace brought into the
  organization later brings no earlier history with it (removals, reasons, names from before).
* **Newest first, paged by sequence** (`before`). Three index-served parts — the organization's
  own events, `organization.*` events by action prefix, each workspace's events by workspace —
  are merged, so a page never scans the whole table.
"""
from __future__ import annotations

import json

from .store import Store

#: Workspace-level actions that are governance (their `workspace_id` is one of the organization's).
WORKSPACE_ACTIONS = ("invite.create", "invite.resend", "invite.revoke", "invite.update", "invite.accept", "invite.decline",
                     "member.role", "member.remove", "member.leave", "member.entitlement",
                     "workspace.ownership.transfer")
#: Filters the page offers, by action prefix.
CATEGORIES = {
    "organization": ("organization.create", "organization.update", "organization.transfer", "organization.freeze",
                     "organization.unfreeze", "organization.delete", "organization.recover", "organization.avatar.",
                     "organization.workspace."),
    "members": ("organization.member.", "member.", "workspace.ownership.", "workspace.owner."),
    "invitations": ("invite.",),
    "roles": ("organization.role.", "organization.roles_assigned"),
    "imports": ("organization.member_import.",),
    "approvals": ("organization.copy.",),
}
#: Detail keys shown as they are (scalars, or the small before/after objects of a role change).
DETAIL_KEYS = ("reason", "role", "before", "after", "status", "only_valid", "invite", "update", "unchanged", "skip", "blocked",
               "reviewed", "name", "self_role", "result", "action", "self_approved", "invited", "updated", "app_id", "enabled")
#: Lists of custom-role ids, shown as the roles' current names.
ROLE_LISTS = ("added", "removed")
_SMALL = ("role", "status", "can_export", "name")
MAX_LIMIT = 100


def _category_sql(category: str | None) -> tuple[str, list]:
    if not category:
        return "", []
    prefixes = CATEGORIES.get(category)
    if prefixes is None:
        return " AND 0", []
    return " AND (" + " OR ".join("action LIKE ?" for _ in prefixes) + ")", [p + "%" if p.endswith(".") else p for p in prefixes]


def _meta(meta) -> dict:
    try:
        m = json.loads(meta) if isinstance(meta, str) else (meta or {})
    except ValueError:
        return {}
    return m if isinstance(m, dict) else {}


def _detail(meta) -> dict:
    try:
        m = json.loads(meta) if isinstance(meta, str) else (meta or {})
    except ValueError:
        return {}
    if not isinstance(m, dict):
        return {}
    out = {}
    for k in DETAIL_KEYS:
        v = m.get(k)
        if v is None:
            continue
        if isinstance(v, dict):
            v = {x: y for x, y in v.items() if x in _SMALL and isinstance(y, (str, int, float, bool))}
            if not v:
                continue
        elif isinstance(v, str):
            v = v[:300]
        elif not isinstance(v, (int, float, bool)):
            continue
        out[k] = v
    for k in ROLE_LISTS:
        if isinstance(m.get(k), list):
            out[k] = [str(x) for x in m[k][:50]]
    if isinstance(m.get("permissions"), list):
        out["permissions"] = len(m["permissions"])
    return out


def read(store: Store, oid: str, *, before: int | None = None, limit: int = 50, category: str | None = None) -> dict:
    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    workspaces = {w["id"]: w["name"] for w in store.all("SELECT id, name FROM workspaces WHERE organization_id=?", (oid,))}
    ws_ids = list(workspaces)
    cols = "seq, at, actor_kind, actor_id, workspace_id, action, object_kind, object_id, result, meta"
    cat_sql, cat_args = _category_sql(category)
    page_sql, page_args = (" AND seq<?", [int(before)]) if before else ("", [])
    parts: list[tuple[str, list]] = [
        (f"SELECT {cols} FROM audit_events WHERE object_kind='organization' AND object_id=?", [oid]),
        # `organization.*` by prefix — an index range on `action` — then the organization it names
        (f"SELECT {cols} FROM audit_events WHERE action>='organization.' AND action<'organization/' "
         "AND object_kind<>'organization' AND json_extract(meta,'$.organization_id')=?", [oid]),
    ]
    # Each workspace from when it was the organization's: created in it, or brought into it.
    joined = {r["wid"]: r["at"] for r in store.all(
        "SELECT json_extract(meta,'$.workspace_id') AS wid, MIN(at) AS at FROM audit_events WHERE object_kind='organization' "
        "AND object_id=? AND action='organization.workspace.adopt' GROUP BY wid", (oid,))}
    created = {w["id"]: w["created_at"] for w in store.all("SELECT id, created_at FROM workspaces WHERE organization_id=?", (oid,))}
    marks = ",".join("?" * len(WORKSPACE_ACTIONS))
    for wid in ws_ids:
        since = joined.get(wid, created.get(wid) or 0)
        parts.append((f"SELECT {cols} FROM audit_events WHERE workspace_id=? AND at>=? AND action IN ({marks})",
                      [wid, since, *WORKSPACE_ACTIONS]))
    sql = " UNION ".join(f"SELECT * FROM ({q}{cat_sql}{page_sql} ORDER BY seq DESC LIMIT ?)" for q, _ in parts)
    args: list = []
    for _, a in parts:
        args += a + cat_args + page_args + [limit + 1]
    rows = store.all(f"SELECT * FROM ({sql}) ORDER BY seq DESC LIMIT ?", tuple(args + [limit + 1]))
    more = len(rows) > limit
    rows = rows[:limit]
    members = {r["user_id"] for r in store.all("SELECT user_id FROM organization_memberships WHERE organization_id=?", (oid,))}
    metas = [_meta(r["meta"]) for r in rows]
    people_ids = {r["actor_id"] for r in rows if r["actor_id"]} | {
        r["object_id"] for r in rows if r["object_kind"] in ("membership", "organization_membership", "user") and r["object_id"]} | {
        str(m["user_id"]) for m in metas if m.get("user_id")}
    people = {}
    if people_ids:
        marks = ",".join("?" * len(people_ids))
        for u in store.all(f"SELECT id, display_name, email, platform_role, status FROM users WHERE id IN ({marks})", tuple(people_ids)):
            people[u["id"]] = u
    details = [_detail(r["meta"]) for r in rows]
    role_ids = {r["object_id"] for r in rows if r["object_kind"] in ("org_role", "organization_role") and r["object_id"]}
    role_ids |= {x for d in details for k in ROLE_LISTS for x in d.get(k, [])}
    roles = {}
    if role_ids:
        try:
            marks = ",".join("?" * len(role_ids))
            roles = {x["id"]: x["name"] for x in store.all(f"SELECT id, name FROM organization_roles WHERE id IN ({marks})", tuple(role_ids))}
        except Exception:  # noqa: BLE001 - a deployment without custom roles names none
            roles = {}

    def person(uid: str | None, kind: str) -> dict:
        if kind in ("system", "machine") or not uid:
            return {"kind": "system"}
        u = people.get(uid)
        if not u or u.get("status") == "deleted":
            return {"kind": "outside"}
        if uid not in members:
            if u.get("platform_role") == "admin":
                return {"kind": "platform_admin"}
            # Not (or no longer) in the organization: the name they chose, never anything from
            # their address — an invitee who declined is not the organization's to identify.
            return {"kind": "outside", **({"name": u["display_name"]} if u.get("display_name") else {})}
        return {"kind": "member", "id": uid, "name": u.get("display_name") or (u.get("email") or "").split("@")[0] or "—"}

    items = []
    for r, detail, m in zip(rows, details, metas):
        for k in ROLE_LISTS:
            if k in detail:
                detail[k] = [roles.get(x) or "—" for x in detail[k]]
        obj: dict = {"kind": r["object_kind"]}
        if r["object_kind"] in ("membership", "organization_membership", "user") and r["object_id"]:
            obj["person"] = person(r["object_id"], "user")
        elif r["object_kind"] in ("org_role", "organization_role") and r["object_id"]:
            obj["name"] = roles.get(r["object_id"])
        elif r["object_kind"] == "workspace" and r["object_id"] in workspaces:
            obj["name"] = workspaces[r["object_id"]]
        # an organization event about one member or one workspace names it in its metadata
        if m.get("user_id") and "person" not in obj:
            obj["person"] = person(str(m["user_id"]), "user")

        # A workspace action carries its workspace; an organization event's `workspace_id` column is
        # only the actor's current workspace, so the one it is about comes from its metadata.
        wid = r["workspace_id"] if r["action"] in WORKSPACE_ACTIONS else m.get("workspace_id")
        items.append({"seq": r["seq"], "at": r["at"], "action": r["action"], "result": r["result"],
                      "actor": person(r["actor_id"], r["actor_kind"]),
                      "workspace": {"id": wid, "name": workspaces[wid]} if wid in workspaces else None,
                      "object": obj, "detail": detail})
    return {"items": items, "next_before": rows[-1]["seq"] if more and rows else None, "categories": list(CATEGORIES)}
