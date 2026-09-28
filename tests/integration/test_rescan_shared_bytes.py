"""CI-90 across applications: the same historical bytes stored as a Synwevo Docs source and as a
Synwevo Message attachment, on the composed server (`all`).

The blob store is shared and content-addressed, so a verdict on the bytes is a verdict for every
row that points at them — but each application marks only its own rows. What must hold:

* Docs' rescan moves the bytes to the quarantine; the attachment, not yet marked, is refused
  (410) from that moment through the quarantine's standing verdict — never a 500 on a missing
  file — and Docs' summary names the other application's command;
* Message's rescan then applies the standing verdict to its row without the bytes;
* a release restores each application's rows by its own command; the bytes return to the blob
  store with the first, and leave the quarantine only when no row is held any more.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synwevo_core import quarantine as Q

PW = "harbor lights guide the evening ferry home"
CSRF, WS = "X-Synwevo-CSRF", "X-Synwevo-Workspace"
M = "/api/messaging"
ORIGIN = "http://testserver"


def _double_module():
    """TEST DOUBLE: the clamd protocol server from the Synwevo Docs tests, loaded by path (test
    directories are not packages of one another)."""
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("_ci90_clamd_double", root / "apps" / "docs" / "tests" / "clamd_double.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def double():
    d = _double_module().ClamdDouble()
    yield d
    d.close()


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for k, v in {"UI_AUTH": "session", "COOKIE_SECURE": "0", "DATA_DIR": str(tmp_path / "d"), "PUBLIC_URL": ORIGIN,
                 "ISSUER": ORIGIN, "ROLE": "all", "APP_URL": ORIGIN}.items():
        monkeypatch.setenv("SYNWEVO_" + k, v)
    monkeypatch.delenv("SYNWEVO_CLAMD_ADDRESS", raising=False)          # stored before scanning was on
    from synwevo_server.app import create_app
    api = create_app(str(tmp_path / "d"), role="all", start_jobs=False)
    ident = api.state.identity
    users = {}
    for key in ("ann", "ben"):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=key.title(), with_personal_workspace=True)
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u
    from synwevo_core.organizations import Organizations
    created = Organizations(ident.store, ident).create(users["ann"]["id"], "Scan Org", "Scan Team", "fixture-idem-scan-org-0001")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    ident.store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                        (oid, users["ben"]["id"], users["ann"]["id"]))
    clients = {}
    for key in users:
        c = TestClient(api)
        assert c.post("/api/auth/login", json={"email": f"{key}@example.test", "password": PW}).status_code == 200
        c.headers.update({CSRF: c.cookies.get("dw_csrf")})
        if key == "ann":                       # ben is only in the organization, not the workspace
            c.headers[WS] = wid
        clients[key] = c
    try:
        yield {"api": api, "c": clients, "u": users, "oid": oid, "wid": wid, "store": ident.store, "data": str(tmp_path / "d")}
    finally:
        for c in clients.values():
            c.close()


def test_one_verdict_on_shared_bytes_reaches_both_applications(world, double, monkeypatch, capsys):
    ann, ben = world["c"]["ann"], world["c"]["ben"]
    store, data = world["store"], world["data"]
    eicar = _double_module().EICAR + b"\n"
    # the same bytes, stored twice before scanning: a Docs source and a message attachment
    pid = ann.post("/api/projects", json={"name": "共用字节"}).json()["id"]
    src = ann.post(f"/api/projects/{pid}/sources", files=[("files", ("a.csv", eicar))]).json()[0]
    assert src["scan"]["status"] == "not_configured"
    dm = ann.post(f"{M}/dm", json={"organization_id": world["oid"], "user_id": world["u"]["ben"]["id"]}).json()
    att = ann.post(f"{M}/conversations/{dm['id']}/attachments", files={"file": ("a.txt", eicar, "text/plain")}).json()
    ann.post(f"{M}/conversations/{dm['id']}/messages", json={"body": "", "attachment_id": att["id"]})
    sha = store.one("SELECT blob FROM source_files WHERE id=?", (src["id"],))["blob"]
    assert store.one("SELECT blob FROM message_attachments WHERE id=?", (att["id"],))["blob"] == sha
    assert ben.get(f"{M}/attachments/{att['id']}").status_code == 200

    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")
    from synwevo_docs.rescan import main_quarantine as docs_quarantine
    from synwevo_docs.rescan import main_rescan as docs_rescan
    from synwevo_core.messaging_rescan import main_quarantine as msg_quarantine
    from synwevo_core.messaging_rescan import main_rescan as msg_rescan
    scan_args = argparse.Namespace(apply=True, limit=None, workspace=None, only=None)

    capsys.readouterr()
    assert docs_rescan(scan_args, data) == 1
    out = capsys.readouterr().out
    assert "Synwevo Message 仍有 1 个附件引用这些字节" in out and "python -m synwevo_messages.cli rescan --apply" in out
    assert store.one("SELECT status FROM source_files WHERE id=?", (src["id"],))["status"] == "rejected"
    assert not store.has_blob(sha)
    # the attachment is not marked yet, and it is already refused — through the standing verdict
    assert store.one("SELECT status FROM message_attachments WHERE id=?", (att["id"],))["status"] == "accepted"
    r = ben.get(f"{M}/attachments/{att['id']}")
    assert r.status_code == 410 and r.json()["error"] == "rejected_malware"

    # Message's own rescan applies the verdict to its row, without the bytes
    assert msg_rescan(argparse.Namespace(apply=True, limit=None), data) == 1
    out = capsys.readouterr().out
    assert "已有隔离结论" in out
    assert store.one("SELECT status FROM message_attachments WHERE id=?", (att["id"],))["status"] == "rejected"
    entry = Q.load(store, sha)
    assert {x["kind"] for x in Q.pending(entry)} == {"source_files", "message_attachments"}

    # after review (a withdrawn signature) each application restores its own rows
    double.eicar_withdrawn = True
    rel = argparse.Namespace(action="release", sha256=sha, yes=False)
    assert docs_quarantine(rel, data) == 0
    out = capsys.readouterr().out
    assert "仍有 1 行待各自的应用恢复" in out
    # the verdict is withdrawn for everyone (the engine said clean); the attachment row stays
    # rejected in its own table until Message restores it, and the held copy stays until then
    assert store.has_blob(sha) and Q.bytes_path(store, sha).is_file() and Q.load(store, sha)["state"] == "released"
    assert ann.get(f"/api/sources/{src['id']}/download").status_code == 200
    assert ben.get(f"{M}/attachments/{att['id']}").status_code == 410            # its own row is still rejected
    assert msg_quarantine(rel, data) == 0
    assert ben.get(f"{M}/attachments/{att['id']}").status_code == 200
    assert Q.load(store, sha)["state"] == "released" and not Q.bytes_path(store, sha).exists()


def _docx(text: str) -> bytes:
    import io

    import docx
    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_organization_template_publications_are_rescanned_and_gate_new_copies(world, double, monkeypatch, capsys):
    """Review findings 5 and 7: a publication is a frozen copy of template bytes that outlives
    its source template and reaches other workspaces. The rescan looks at every publication (the
    source template deleted or not); a publication found infected cannot be used; and where the
    deployment scans, a copy made from a publication that carries no clean record is scanned at
    that moment instead of starting life unscanned."""
    ann, store, data = world["c"]["ann"], world["store"], world["data"]
    pubs = {}
    for key in ("bad", "good"):
        t = ann.post("/api/templates/upload", files={"file": (f"{key}.docx", _docx(f"尊敬的{key}客户：您好。"))}).json()
        assert t["scan"]["status"] == "not_configured"
        r = ann.post(f"/api/templates/{t['id']}/publish", json={})
        assert r.status_code == 201, r.text
        pubs[key] = (t, r.json())
        assert ann.delete(f"/api/templates/{t['id']}").status_code in (200, 204)      # the source template is gone
    bad_blob = store.one("SELECT working_blob FROM organization_templates WHERE id=?", (pubs["bad"][1]["id"],))["working_blob"]

    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")
    from synwevo_docs.rescan import main_rescan as docs_rescan
    # TEST SETUP: the server as it runs after a restart with scanning on (the policy it reads at
    # start). A copy of a publication that carries no clean record is scanned at use.
    from synwevo_core import malware as MW
    world["api"].state.app.scanner = MW.scanner_for(MW.policy())
    before = len(double.scans)
    r = ann.post(f"/api/organization-templates/{pubs['good'][1]['id']}/use", json={})
    assert r.status_code == 201, r.text
    assert len(double.scans) == before + 1
    assert store.one("SELECT scan FROM templates WHERE id=?", (r.json()["id"],))["scan"]["status"] == "clean"

    capsys.readouterr()
    assert docs_rescan(argparse.Namespace(apply=False, limit=None, workspace=None, only="templates"), data) == 0
    assert "组织共享模板：2 个" in capsys.readouterr().out
    double.infected[bad_blob] = "Doc.Test.Publication-1"
    assert docs_rescan(argparse.Namespace(apply=True, limit=None, workspace=None, only="templates"), data) == 1
    out = capsys.readouterr().out
    assert f"sha256={bad_blob}" in out and pubs["bad"][1]["id"] in out
    bad_info = store.one("SELECT info FROM organization_templates WHERE id=?", (pubs["bad"][1]["id"],))["info"]
    good_info = store.one("SELECT info FROM organization_templates WHERE id=?", (pubs["good"][1]["id"],))["info"]
    assert bad_info["scan"]["status"] == "infected" and good_info["scan"]["status"] == "clean"
    r = ann.post(f"/api/organization-templates/{pubs['bad'][1]['id']}/use", json={})
    assert r.status_code == 410 and r.json()["error"] == "rejected_malware"
    # the clean one is used from its carried record: no second scan
    before = len(double.scans)
    assert ann.post(f"/api/organization-templates/{pubs['good'][1]['id']}/use", json={}).status_code == 201
    assert len(double.scans) == before


def test_the_upload_gate_holds_bytes_it_shares_with_a_historical_attachment(world, double, monkeypatch, capsys):
    """Second review (pre-existing): a new upload judged malicious used to have its bytes unlinked
    — together with the only copy of a historical attachment on the same bytes. Now the upload gate
    holds them in the quarantine with a standing verdict, which refuses the attachment at once and
    which Message's rescan applies to its row."""
    ann, ben, store, data = world["c"]["ann"], world["c"]["ben"], world["store"], world["data"]
    eicar = _double_module().EICAR + b"\n"
    dm = ann.post(f"{M}/dm", json={"organization_id": world["oid"], "user_id": world["u"]["ben"]["id"]}).json()
    att = ann.post(f"{M}/conversations/{dm['id']}/attachments", files={"file": ("a.txt", eicar, "text/plain")}).json()
    ann.post(f"{M}/conversations/{dm['id']}/messages", json={"body": "", "attachment_id": att["id"]})
    assert ben.get(f"{M}/attachments/{att['id']}").status_code == 200
    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")
    from synwevo_core import malware as MW
    app = world["api"].state.app                            # TEST SETUP: the server restarted with scanning on
    app.scan_policy = MW.policy()
    app.scanner = MW.scanner_for(app.scan_policy)
    pid = ann.post("/api/projects", json={"name": "新上传"}).json()["id"]
    src = ann.post(f"/api/projects/{pid}/sources", files=[("files", ("new.csv", eicar))]).json()[0]
    import time
    sha = store.one("SELECT blob FROM source_files WHERE id=?", (src["id"],))["blob"]
    deadline = time.time() + 30                             # the gate marks the row first, then moves the bytes
    while time.time() < deadline and not (Q.load(store, sha) and not store.has_blob(sha)):
        time.sleep(0.1)
    assert store.one("SELECT status FROM source_files WHERE id=?", (src["id"],))["status"] == "rejected"
    assert not store.has_blob(sha) and Q.bytes_path(store, sha).is_file()          # held, not deleted
    entry = Q.load(store, sha)
    assert entry["state"] == "quarantined" and [(r["kind"], r["id"]) for r in entry["rows"]] == [("source_files", src["id"])]
    r = ben.get(f"{M}/attachments/{att['id']}")
    assert r.status_code == 410 and r.json()["error"] == "rejected_malware"
    from synwevo_core.messaging_rescan import main_rescan as msg_rescan
    capsys.readouterr()
    assert msg_rescan(argparse.Namespace(apply=True, limit=None), data) == 1
    assert "已有隔离结论" in capsys.readouterr().out
    assert store.one("SELECT status FROM message_attachments WHERE id=?", (att["id"],))["status"] == "rejected"
