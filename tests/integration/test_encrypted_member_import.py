"""Requirement §8.7 for the account service: a password-protected member file is not accepted.

A member import is read by the document engine that the composition root injects (XLSX, DOCX,
PDF…), so an encrypted workbook or PDF used to pass the scan as "clean" and reach the readers.
With scanning on, the shared acceptance gate refuses it with ``encrypted_file`` before anything
is stored — no import, no kept file. The clamd protocol double and the encrypted-file builders
are Synwevo Docs' test fixtures, loaded by path (as test_rescan_shared_bytes.py does).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PW = "harbor lights guide the evening ferry home"
CSRF = "X-Synwevo-CSRF"
SENTENCE = "文件设有密码或含有加密内容，安全检查无法查看其中的内容，因此未被接纳；请移除密码（或另存一份不加密的副本）后重新上传"
TESTS = Path(__file__).resolve().parents[2] / "apps" / "docs" / "tests"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_ci_{name}", TESTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


F = _load("encrypted_fixtures")


@pytest.fixture()
def double():
    d = _load("clamd_double").ClamdDouble()
    yield d
    d.close()


@pytest.fixture()
def platform(tmp_path, monkeypatch, double):
    for k, v in {"UI_AUTH": "session", "COOKIE_SECURE": "0", "DATA_DIR": str(tmp_path / "d"), "MAIL_MODE": "file",
                 "MAIL_DIR": str(tmp_path / "mail"), "MAIL_FROM": "synwevo@example.test", "PUBLIC_URL": "http://testserver",
                 "ROLE": "all", "REGISTRATION": "open", "CLAMD_ADDRESS": double.address, "CLAMD_TIMEOUT_S": "5"}.items():
        monkeypatch.setenv("SYNWEVO_" + k, v)
    from synwevo_server.app import create_app
    api = create_app(str(tmp_path / "d"), role="all", start_jobs=False)
    ident = api.state.identity
    owner = ident.create_user("owner@example.test", PW, status="active", display_name="Ola Owner")
    ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (owner["id"],))
    from synwevo_core.organizations import Organizations
    created = Organizations(ident.store, ident).create(owner["id"], "Scan Org", "Scan Team", "fixture-idem-encrypted-org-0001")
    with TestClient(api) as c:
        c.ident, c.oid, c.data = ident, created["organization"]["id"], tmp_path / "d"
        assert c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW}).status_code == 200
        c.h = {CSRF: c.cookies.get("dw_csrf")}
        assert c.post("/api/auth/reauth", json={"password": PW, "scope": "admin"}, headers=c.h).status_code == 200
        yield c


def test_a_password_protected_member_file_is_refused_before_it_is_read(platform):
    c = platform
    url = f"/api/auth/organizations/{c.oid}/imports"
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "TEST FIXTURE owner@example.test")
    locked_pdf = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    for name, data, kind in (("成员.xlsx", F.encrypted_ooxml(), "office"), ("成员.xls", F.xls(filepass=True), "office"),
                             ("成员.pdf", locked_pdf, "pdf")):
        r = c.post(url, files={"file": (name, data, "application/octet-stream")}, headers=c.h)
        assert r.status_code == 400, r.text
        body = r.json()
        assert body["error"] == "encrypted_file" and body["message"] == SENTENCE and body["detail"] == {"filename": name, "kind": kind}, body
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM member_imports")["n"] == 0
    kept = c.data / "member_imports"
    assert not kept.exists() or not any(kept.iterdir()), "nothing of a refused file is kept"
    # a readable file with only an owner password on it is imported as usual
    readable = pymupdf.open()
    readable.new_page().insert_text((72, 72), "Nina New nina.new@example.test")
    r = c.post(url, files={"file": ("成员.pdf", readable.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o"), "application/pdf")}, headers=c.h)
    assert r.status_code == 201 and r.json()["scan"]["status"] == "clean", r.text
