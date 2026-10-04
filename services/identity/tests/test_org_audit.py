"""CI-101: the organization's governance log behind `org.audit.read` — a permission that was in the
catalogue with nothing to read. Real store, routes and sessions.

Held: only holders of the permission read it (owner, admin by default, a member through a custom
role), frozen organizations included; it lists the organization's own governance events and its
workspaces' member and invitation events, newest first and paged, with names resolved and a
bounded set of details; it never lists business events, another organization's events, IP
addresses or request ids.
"""
from __future__ import annotations

import json

from synwevo_core.store import new_id, now

from .test_org_roles import PW, base, login, org  # noqa: F401  (the same organization fixture)

WHY = "fixture: reason recorded with a role change"   # role writes need 8+ characters (D12)


def audit(c, h, **params):
    r = c.get(f"{base(c, c.oid)}/audit", params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_log_lists_governance_with_names_and_nothing_else(org):
    c = org
    h = login(c, "owner")
    role = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "审计员", "permissions": ["org.audit.read"], "reason": WHY}).json()["role"]
    assert c.put(f"{base(c, c.oid)}/members/{c.users['member']['id']}/roles", headers=h, json={"role_ids": [role["id"]], "reason": WHY}).status_code == 200
    assert c.patch(f"{base(c, c.oid)}/members/{c.users['delegate']['id']}", headers=h,
                   json={"role": "admin", "reason": "负责日常的组织管理工作"}).status_code == 200
    store = c.ident.store
    # TEST FIXTURES: a business event in the organization's workspace, and another organization's governance event
    store.insert("audit_events", {"id": new_id("au"), "at": now(), "actor_kind": "user", "actor_id": c.users["owner"]["id"],
                                  "workspace_id": c.wid, "action": "docs.project.create", "object_kind": "project", "object_id": "p_fixture",
                                  "result": "ok", "ip": "203.0.113.9", "meta": json.dumps({"name": "机密项目"})})
    store.insert("audit_events", {"id": new_id("au"), "at": now(), "actor_kind": "user", "actor_id": c.users["other"]["id"],
                                  "action": "organization.role.create", "object_kind": "organization_role", "object_id": "r_other",
                                  "result": "ok", "meta": json.dumps({"organization_id": "o_somewhere_else"})})
    out = audit(c, h)
    actions = [x["action"] for x in out["items"]]
    assert "organization.role.create" in actions and "organization.role.assign" in actions and "organization.member.role" in actions
    assert "organization.create" in actions
    assert "docs.project.create" not in actions
    assert all(x["object"].get("name") != "r_other" for x in out["items"])
    assert len([a for a in actions if a == "organization.role.create"]) == 1, "another organization's role is not ours"
    text = json.dumps(out, ensure_ascii=False)
    assert "203.0.113.9" not in text and "机密项目" not in text and "request_id" not in text and "@example.test" not in text
    created = next(x for x in out["items"] if x["action"] == "organization.role.create")
    assert created["actor"] == {"kind": "member", "id": c.users["owner"]["id"], "name": "Ola Owner"}
    assert created["object"] == {"kind": "organization_role", "name": "审计员"} and created["detail"]["permissions"] == 1
    assigned = next(x for x in out["items"] if x["action"] == "organization.role.assign")
    assert assigned["object"]["person"]["name"] == "Mia Member" and assigned["detail"]["added"] == ["审计员"]
    changed = next(x for x in out["items"] if x["action"] == "organization.member.role")
    assert changed["detail"]["reason"] == "负责日常的组织管理工作"
    seqs = [x["seq"] for x in out["items"]]
    assert seqs == sorted(seqs, reverse=True), "newest first"


def test_who_may_read_it(org):
    c = org
    h = login(c, "owner")
    role = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "审计员", "permissions": ["org.audit.read"], "reason": WHY}).json()["role"]
    # a plain member may not; with a custom role holding the permission, they may
    hm = login(c, "member")
    r = c.get(f"{base(c, c.oid)}/audit", headers=hm)
    assert r.status_code == 403
    h = login(c, "owner")
    assert c.put(f"{base(c, c.oid)}/members/{c.users['member']['id']}/roles", headers=h, json={"role_ids": [role["id"]], "reason": WHY}).status_code == 200
    hm = login(c, "member")
    assert c.get(f"{base(c, c.oid)}/audit", headers=hm).status_code == 200
    # someone outside the organization learns nothing
    c.ident.store.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (c.oid, c.users["other"]["id"]))
    ho = login(c, "other")
    assert c.get(f"{base(c, c.oid)}/audit", headers=ho).status_code in (403, 404)
    # a frozen organization's log is still readable by those who may
    c.ident.store.execute("UPDATE organizations SET status='frozen' WHERE id=?", (c.oid,))
    h = login(c, "owner")
    assert c.get(f"{base(c, c.oid)}/audit", headers=h).status_code == 200


def test_paging_and_categories(org):
    c = org
    h = login(c, "owner")
    for i in range(5):
        assert c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": f"角色{i}", "permissions": ["org.audit.read"], "reason": WHY}).status_code in (200, 201)
    first = audit(c, h, limit=3)
    assert len(first["items"]) == 3 and first["next_before"] == first["items"][-1]["seq"]
    rest = audit(c, h, limit=100, before=first["next_before"])
    assert not {x["seq"] for x in rest["items"]} & {x["seq"] for x in first["items"]}
    roles_only = audit(c, h, category="roles")
    assert roles_only["items"] and all(x["action"].startswith("organization.role") for x in roles_only["items"])
    assert c.get(f"{base(c, c.oid)}/audit", params={"category": "docs"}, headers=h).status_code == 422


def test_review_findings_revoke_filing_prior_history_outsiders_and_bounds(org):
    """Review of CI-101: a revoke is filed under the invitation's own workspace, whatever the
    caller's current one; a workspace brought into the organization brings no earlier history;
    someone outside the organization is never named from their address; an oversized page
    cursor is a 422, not a 500."""
    c = org
    store = c.ident.store
    h = login(c, "owner")
    # a team workspace from before organizations existed, owned by the owner (TEST FIXTURE)
    wid = "w_legacyteam01"
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at,created_by) VALUES (?,?,'org','active',1,1,?)",
                  (wid, "Old Team", c.users["owner"]["id"]))
    store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,'owner',1,1,1,?)",
                  (wid, c.users["owner"]["id"], c.users["owner"]["id"]))
    store.insert("audit_events", {"id": new_id("au"), "at": now() - 3600, "actor_kind": "user", "actor_id": c.users["owner"]["id"],
                                  "workspace_id": wid, "action": "member.remove", "object_kind": "membership", "object_id": c.users["other"]["id"],
                                  "result": "ok", "meta": json.dumps({"reason": "private matter from before the organization"})})
    assert c.post(f"{base(c, c.oid)}/workspaces/{wid}/adopt", headers=h, json={"reason": "并入组织统一管理的旧团队"}).status_code == 200
    out = audit(c, h)
    assert "private matter" not in json.dumps(out, ensure_ascii=False)
    adopt = next(x for x in out["items"] if x["action"] == "organization.workspace.adopt")
    assert adopt["workspace"]["name"] == "Old Team"
    # an invitation in the adopted workspace, revoked while the caller's current workspace is another one
    r = c.post(f"/api/auth/workspaces/{wid}/invitations/batch", headers=h, json={"invitations": [{"email": "noname@example.test", "role": "viewer"}]})
    assert r.status_code in (200, 201), r.text
    iid = store.one("SELECT id FROM invitations WHERE workspace_id=?", (wid,))["id"]
    assert c.delete(f"/api/auth/invitations/{iid}", headers={**h, "X-Synwevo-Workspace": c.wid}).status_code == 200
    revoke = store.one("SELECT workspace_id FROM audit_events WHERE action='invite.revoke' ORDER BY seq DESC LIMIT 1")
    assert revoke["workspace_id"] == wid
    # an outsider with no display name who acted in the workspace is not named from their address (TEST FIXTURE event)
    outsider = c.ident.create_user("secret.localpart@example.test", PW, status="active")
    store.execute("UPDATE users SET display_name=NULL WHERE id=?", (outsider["id"],))
    store.insert("audit_events", {"id": new_id("au"), "at": now(), "actor_kind": "user", "actor_id": outsider["id"],
                                  "workspace_id": wid, "action": "invite.decline", "object_kind": "invitation", "object_id": iid,
                                  "result": "ok", "meta": "{}"})
    declined = next(x for x in audit(c, h)["items"] if x["action"] == "invite.decline")
    assert declined["actor"] == {"kind": "outside"} and "secret.localpart" not in json.dumps(audit(c, h))
    assert c.get(f"{base(c, c.oid)}/audit", params={"before": 2 ** 64}, headers=h).status_code == 422
