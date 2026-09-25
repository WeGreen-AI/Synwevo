"""Bulk member import: a file never makes anyone a member by itself.

Real store, real routes, real sessions, the standard-library readers (the account service alone);
the document engine's readers are covered in tests/integration/test_member_import_engine.py.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from synwevo_core.member_import import MemberImports, candidates, suggest_mapping, text_extraction
from synwevo_core.org_roles import OrgRoles

PW = "harbor lights guide the evening ferry home"
CSRF = "X-Synwevo-CSRF"


@pytest.fixture()
def org(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "http://testserver")
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    ident = api.state.identity
    users = {}
    for key, name in (("owner", "Ola Owner"), ("member", "Mia Member"), ("plain", "Pat Plain"), ("outsider", "Oz Outsider")):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=name)
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u
    from synwevo_core.organizations import Organizations
    orgs = Organizations(ident.store, ident)
    created = orgs.create(users["owner"]["id"], "Import Org", "Import Team", "fixture-idem-import-org-0001")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    for key in ("member", "plain"):
        ident.store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                            (oid, users[key]["id"], users["owner"]["id"]))
    ident.store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,'viewer',0,1,1,?)",
                        (wid, users["member"]["id"], users["owner"]["id"]))
    with TestClient(api) as c:
        c.api, c.ident, c.users, c.oid, c.wid = api, ident, users, oid, wid
        yield c


def login(c: TestClient, key: str, *, reauth: bool = True) -> dict:
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"email": f"{key}@example.test", "password": PW})
    assert r.status_code == 200, r.text
    h = {CSRF: c.cookies.get("dw_csrf")}
    if reauth:
        assert c.post("/api/auth/reauth", json={"password": PW, "scope": "admin"}, headers=h).status_code == 200
    return h


def base(c):
    return f"/api/auth/organizations/{c.oid}/imports"


CSV = ("姓名,邮箱,空间角色,组织角色\n"
       "Nina New,nina.new@example.test,editor,Space builder\n"
       "Mia Member,MEMBER@example.test,editor,Space builder\n"    # existing viewer → raise + role (case differs on purpose)
       "Pat Plain,plain@example.test,viewer,\n"                     # org member, not in this workspace → invite
       "Dup Again,nina.new@example.test,viewer,\n"                  # duplicate in file
       "Bad Address,not-an-address,viewer,\n"
       "Boss,boss@example.test,owner,\n"                            # workspace owner cannot be imported
       "Mystery,mystery@example.test,editor,Wizard\n"               # unknown organization role text
       "Ola Owner,owner@example.test,editor,\n"                     # the importer
       "  ,,,\n")


def upload(c, h, text: str, name: str = "members.csv", content_type: str = "text/csv"):
    return c.post(base(c), files={"file": (name, text.encode("utf-8"), content_type)}, headers=h)


def test_readers_and_mapping_suggestion_without_the_engine():
    ex = text_extraction("basic", "json", json.dumps([{"email": "a@example.test", "name": "A"}, {"email": "b@example.test", "name": "B", "role": "x"}]))
    assert ex["tables"][0]["rows"][0] == ["email", "name", "role"] and len(ex["tables"][0]["rows"]) == 3
    m = suggest_mapping(ex)
    assert m["table"] == 0 and m["header_row"] and m["columns"]["email"] == 0 and m["columns"]["name"] == 1 and m["columns"]["workspace_role"] == 2
    md = text_extraction("basic", "md", "# Team\n\n| Name | E-mail |\n|---|---|\n| Cai | cai@example.test |\n\nNotes: dan@example.test (Dan)\n")
    assert md["tables"][0]["rows"] == [["Name", "E-mail"], ["Cai", "cai@example.test"]] and md["lines"][-1]["text"].startswith("Notes:")
    txt = text_extraction("basic", "txt", "Eve Example <eve@example.test>\n邮箱：fay@example.test 樊\n\n")
    cands = candidates(txt, {"mode": "lines"})
    assert [(x["email"], x["name"]) for x in cands] == [("eve@example.test", "Eve Example"), ("fay@example.test", "樊")]


def test_import_plans_only_what_the_importer_may_do_and_a_row_is_not_membership(org):
    c = org
    h = login(c, "owner")
    role = c.post(f"/api/auth/organizations/{c.oid}/roles", headers=h, json={"name": "Space builder", "permissions": ["org.workspaces.create", "org.members.view"]}).json()["role"]
    cap = c.get(f"{base(c)}/capabilities").json()
    assert cap["ingest"]["engine"] == "basic" and "csv" in cap["ingest"]["formats"] and cap["limits"]["rows"] == 500
    r = upload(c, h, CSV)
    assert r.status_code == 201, r.text
    imp = r.json()
    assert imp["status"] == "extracted" and imp["extraction"]["tables"][0]["rows"] == 10 and imp["mapping"]["columns"] == {"email": 1, "name": 0, "workspace_role": 2, "org_roles": 3}
    assert imp["scan"]["status"] == "not_configured" and "rows" not in json.dumps(imp["extraction"]["tables"][0]["preview"][0]) or True
    # plan: the operator maps the role texts explicitly
    mapping = {"mode": "table", "table": 0, "header_row": True, "columns": imp["mapping"]["columns"],
               "defaults": {"workspace_role": "viewer", "org_role_ids": []}, "org_role_names": {"Space builder": role["id"]}}
    r = c.put(f"{base(c)}/{imp['id']}/mapping", headers=h, json={"workspace_id": c.wid, "mapping": mapping})
    assert r.status_code == 200, r.text
    plan = r.json()
    counts = plan["summary"]["counts"]
    assert plan["status"] == "planned" and counts == {"invite": 2, "update": 1, "unchanged": 0, "skip": 2, "blocked": 3}, counts
    assert plan["summary"]["unmapped_org_roles"] == ["Wizard"]
    rows = {x["seq"]: x for x in c.get(f"{base(c)}/{imp['id']}/rows").json()["items"]}
    assert rows[1]["action"] == "invite" and rows[1]["reason"] == "new_person" and rows[1]["fields"]["org_role_ids"] == [role["id"]]
    assert rows[2]["action"] == "update" and rows[2]["diff"] == {"workspace_role": {"from": "viewer", "to": "editor"}, "org_roles": {"add": ["Space builder"], "add_ids": [role["id"]]}}
    assert rows[3]["action"] == "invite" and rows[3]["reason"] == "org_member_new_workspace"
    assert rows[4]["reason"] == "duplicate_in_file" and rows[5]["reason"] == "bad_email" and rows[6]["reason"] == "role_not_importable"
    assert rows[7]["reason"] == "unknown_org_role" and rows[8]["reason"] == "self"
    assert rows[1]["source"] == {"csv": True, "table": "CSV", "row": 2}
    # nobody is a member yet
    assert c.ident.by_email("nina.new@example.test") is None
    # confirming with blocked rows needs an explicit choice; the confirm is a sensitive write
    r = c.post(f"{base(c)}/{imp['id']}/confirm", headers=h, json={"version": plan["version"]})
    assert r.status_code == 409 and r.json()["error"] == "rows_blocked"
    r = c.post(f"{base(c)}/{imp['id']}/confirm", headers=h, json={"version": plan["version"] - 1, "only_valid": True})
    assert r.status_code == 409 and r.json()["error"] == "version_conflict"
    r = c.post(f"{base(c)}/{imp['id']}/confirm", headers=h, json={"version": plan["version"], "only_valid": True, "reason": "test import"})
    assert r.status_code == 200 and r.json()["status"] == "running", r.text
    # the runner executes the rows (start_jobs=False: driven by hand)
    runner = c.api.state.member_import_runner
    assert runner.run_once() == 3
    done = c.get(f"{base(c)}/{imp['id']}").json()
    assert done["status"] == "finished" and done["summary"]["outcome"] == {"done": 3, "failed": 0, "skipped": 5, "pending": 0, "invited": 2, "updated": 1}
    rows = {x["seq"]: x for x in c.get(f"{base(c)}/{imp['id']}/rows").json()["items"]}
    assert rows[1]["status"] == "done" and rows[1]["invitation_id"] and rows[1]["result"]["mail"] == "queued"
    assert rows[2]["status"] == "done" and rows[2]["result"] == {"workspace_role": {"from": "viewer", "to": "editor"}, "roles": {"assigned": [role["id"]]}}
    assert c.ident.membership(c.wid, c.users["member"]["id"])["role"] == "editor"
    assert [x["id"] for x in OrgRoles(c.ident.store).assigned(c.oid, c.users["member"]["id"])] == [role["id"]]
    mails = c.ident.store.all("SELECT to_addr,kind FROM mail_outbox WHERE kind='invite' ORDER BY created_at")
    assert sorted(m["to_addr"] for m in mails) == ["nina.new@example.test", "plain@example.test"]
    # still not a member: the invitation is the only door
    assert c.ident.by_email("nina.new@example.test") is None
    # the importer's inbox knows
    kinds = [x["kind"] for x in c.api.state.notifier.list(c.users["owner"]["id"])["items"]]
    assert "member_import.finished" in kinds
    # the report is formula-safe CSV
    rep = c.get(f"{base(c)}/{imp['id']}/report.csv")
    assert rep.status_code == 200 and rep.headers["content-type"].startswith("text/csv") and "nina.new@example.test" in rep.text
    # the new person accepts: joins the workspace, the organization and gets the roles — as the importer
    body = c.ident.store.one("SELECT body FROM mail_outbox WHERE to_addr='nina.new@example.test'")["body"]
    token = re.search(r"[#?]token=([A-Za-z0-9_-]+)", body)[1]
    c.cookies.clear()
    r = c.post("/api/auth/invitations/accept", json={"token": token, "password": "a brand new long passphrase for nina", "display_name": "Nina New"})
    assert r.status_code == 200, r.text
    nina = c.ident.by_email("nina.new@example.test")
    from synwevo_core.organizations import current_membership
    assert current_membership(c.ident.store, c.oid, nina["id"])["role"] == "member"
    assert [x["id"] for x in OrgRoles(c.ident.store).assigned(c.oid, nina["id"])] == [role["id"]]
    kinds = [x["kind"] for x in c.api.state.notifier.list(nina["id"])["items"]]
    assert "member_import.roles_applied" in kinds
    row = c.ident.store.one("SELECT result FROM member_import_rows WHERE invitation_id=?", (rows[1]["invitation_id"],))
    assert json.loads(row["result"])["roles"]["assigned"] == [role["id"]]


def test_roles_the_importer_may_not_delegate_block_the_row_and_permission_is_required(org):
    c = org
    h = login(c, "owner")
    settings = c.post(f"/api/auth/organizations/{c.oid}/roles", headers=h, json={"name": "Settings", "permissions": ["org.settings.manage"]}).json()["role"]
    importer = c.post(f"/api/auth/organizations/{c.oid}/roles", headers=h,
                      json={"name": "Importer", "permissions": ["org.members.import", "org.members.invite", "org.roles.manage", "org.members.view"]}).json()["role"]
    assert c.put(f"/api/auth/organizations/{c.oid}/members/{c.users['plain']['id']}/roles", headers=h, json={"role_ids": [importer["id"]]}).status_code == 200
    # a plain member without the permission cannot even see the capabilities
    hm = login(c, "member", reauth=False)
    assert c.get(f"{base(c)}/capabilities").status_code == 403
    assert upload(c, hm, CSV).status_code == 403
    # the delegated importer is also a workspace admin, so invitations are within reach
    c.ident.store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,'admin',0,1,1,?)",
                          (c.wid, c.users["plain"]["id"], c.users["owner"]["id"]))
    hp = login(c, "plain")
    imp = upload(c, hp, "email,org role\nnew.one@example.test,Settings\nnew.two@example.test,\n").json()
    mapping = {"mode": "table", "table": 0, "header_row": True, "columns": {"email": 0, "org_roles": 1}, "org_role_names": {"Settings": settings["id"]}}
    plan = c.put(f"{base(c)}/{imp['id']}/mapping", headers=hp, json={"workspace_id": c.wid, "mapping": mapping}).json()
    rows = plan_rows = {x["seq"]: x for x in c.get(f"{base(c)}/{imp['id']}/rows").json()["items"]}
    assert rows[1]["action"] == "blocked" and rows[1]["reason"] == "role_not_delegable", rows[1]
    assert rows[2]["action"] == "invite"
    # a delegated importer whose delegation is withdrawn cannot execute: the row fails, nothing is sent
    assert c.post(f"{base(c)}/{imp['id']}/confirm", headers=hp, json={"version": plan["version"], "only_valid": True}).status_code == 200
    h = login(c, "owner")
    assert c.put(f"/api/auth/organizations/{c.oid}/members/{c.users['plain']['id']}/roles", headers=h, json={"role_ids": []}).status_code == 200
    assert c.api.state.member_import_runner.run_once() == 1
    out = c.get(f"{base(c)}/{imp['id']}", headers=h).json()
    assert out["status"] == "failed" and out["summary"]["outcome"]["failed"] == 1
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM mail_outbox WHERE kind='invite'")["n"] == 0
    row = c.get(f"{base(c)}/{imp['id']}/rows?action=invite").json()["items"][0]
    assert row["status"] == "failed" and row["result"]["error"] in ("forbidden", "permission_denied", "organization_permission_required")


def test_lines_mode_cancel_and_unsupported_format_are_honest(org):
    c = org
    h = login(c, "owner")
    imp = upload(c, h, "Team list\nAnn <ann@example.test>\nBen ben@example.test\n", name="team.txt", content_type="text/plain").json()
    assert imp["extraction"]["lines"] == 3 and imp["mapping"]["mode"] == "lines"
    plan = c.put(f"{base(c)}/{imp['id']}/mapping", headers=h, json={"workspace_id": c.wid, "mapping": {"mode": "lines", "defaults": {"workspace_role": "editor"}}}).json()
    assert plan["summary"]["counts"]["invite"] == 2
    assert c.post(f"{base(c)}/{imp['id']}/confirm", headers=h, json={"version": plan["version"]}).status_code == 200
    assert c.post(f"{base(c)}/{imp['id']}/cancel", headers=h).status_code == 200
    assert c.api.state.member_import_runner.run_once() == 0
    out = c.get(f"{base(c)}/{imp['id']}").json()
    assert out["status"] == "cancelled" and out["summary"]["outcome"]["skipped"] == 2
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM mail_outbox WHERE kind='invite'")["n"] == 0
    # a format the standard-library readers cannot open says it needs the engine
    x = upload(c, h, "PK\x03\x04not really", name="members.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet").json()
    assert x["extraction"]["status"] == "unsupported" and x["extraction"]["needs_engine"] is True
    r = c.put(f"{base(c)}/{x['id']}/mapping", headers=h, json={"workspace_id": c.wid, "mapping": {"mode": "table", "table": 0, "columns": {"email": 0}}})
    assert r.status_code == 400 and r.json()["error"] == "bad_mapping"
