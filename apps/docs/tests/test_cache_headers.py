"""Caching policy, per response class.

Three classes with three different correct answers, and getting any of them wrong is a real
problem rather than a tuning issue:

* **entry HTML** — names asset hashes. A stale shell points at files that a deploy deleted, so
  it is a blank page that a reload does not fix.
* **content-hashed assets** — the name changes when the bytes change, so the bytes under a name
  never change. Anything short of `immutable` costs a request per bundle per reload.
* **anything carrying someone's data** — must be revalidated, because "the browser already has
  it" is the one case where no request reaches the authorization check.
"""
from __future__ import annotations

import glob
import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


def _client(tmp_path, monkeypatch, *, role: str = "all", mode: str = "none") -> TestClient:
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DOCWEAVE_UI_AUTH", mode)
    monkeypatch.setenv("DOCWEAVE_COOKIE_SECURE", "0")
    monkeypatch.setenv("DOCWEAVE_ROLE", role)
    import docweave.api.app as app_mod
    importlib.reload(app_mod)
    return TestClient(app_mod.create_app(str(tmp_path), start_jobs=False))


pytestmark = pytest.mark.skipif(not DIST.exists(), reason="前端未构建")


# ---------------------------------------------------------------- entry HTML
@pytest.mark.parametrize("path", ["/", "/projects/p_x", "/index.html",
                                  "/portal.html", "/accounts.html"])
def test_every_entry_document_must_revalidate(tmp_path, monkeypatch, path):
    """Asking for a shell **by filename** used to fall through to the generic-file branch and
    be served with no Cache-Control at all — measured before the fix."""
    r = _client(tmp_path, monkeypatch).get(path)
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "no-cache" in cc, f"{path} 的 Cache-Control 是 {cc!r}"
    assert "max-age=0" in cc or "max-age" not in cc, f"{path} 不应当被缓存：{cc!r}"


# ---------------------------------------------------------------- hashed assets
def _asset_names():
    return [os.path.basename(p) for p in glob.glob(str(DIST / "assets" / "*"))]


def test_content_hashed_assets_are_immutable(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    names = [n for n in _asset_names() if "-" in n]
    assert names, "构建产物里没有带内容 hash 的文件，这个测试就失去意义了"
    for name in names[:6]:
        cc = c.get(f"/assets/{name}").headers.get("cache-control", "")
        assert "immutable" in cc and "max-age=31536000" in cc, f"{name}: {cc!r}"


def test_an_asset_without_a_hash_is_not_marked_immutable(tmp_path, monkeypatch):
    """A file whose name carries no hash can change under that name. Telling a browser to keep
    it for a year is how a deploy silently fails to reach people."""
    (DIST / "assets").mkdir(parents=True, exist_ok=True)
    plain = DIST / "assets" / "__unhashed_probe.js"
    plain.write_text("// test fixture\n")
    try:
        cc = _client(tmp_path, monkeypatch).get("/assets/__unhashed_probe.js").headers.get("cache-control", "")
        assert "immutable" not in cc and "no-cache" in cc, cc
    finally:
        plain.unlink(missing_ok=True)


def test_assets_are_still_behind_the_auth_gate(tmp_path, monkeypatch):
    """Adding a header must not move the mount outside the gate."""
    c = _client(tmp_path, monkeypatch, mode="basic")
    names = _asset_names()
    assert c.get(f"/assets/{names[0]}").status_code == 401


# ---------------------------------------------------------------- someone's data
def test_a_document_preview_is_revalidated_not_cached(api, fixtures):
    """This one is a security property, not a performance one.

    The route used to answer `private, max-age=600`, so for ten minutes the browser rendered a
    page of someone's source document **without contacting the server** — including after they
    signed out, were removed from the workspace, or had the session revoked. Every other path
    re-checks on the next request; this one had no next request.

    The bytes may still be kept; only the permission to see them must be re-checked. So: an
    ETag, a 304 on revalidation, and no `max-age` anywhere.
    """
    pid = api.project("缓存策略验证")
    api.upload(pid, fixtures / "测试_发货单模板.pdf")
    sid = api.ok(api.c.get(f"/api/projects/{pid}"))["sources"][0]["id"]

    r = api.c.get(f"/api/sources/{sid}/page/0.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    cc = r.headers.get("cache-control", "")
    assert "no-cache" in cc and "max-age" not in cc, f"预览响应会绕过下一次授权检查：{cc!r}"

    etag = r.headers.get("etag")
    assert etag, "没有 ETag 的话，no-cache 会让每一次缩放都重新光栅化一遍"

    again = api.c.get(f"/api/sources/{sid}/page/0.png", headers={"If-None-Match": etag})
    assert again.status_code == 304 and not again.content, "重新校验应当返回 304 且不带正文"
    assert again.headers.get("etag") == etag

    # The validator must actually track the content, not be a constant.
    other = api.c.get(f"/api/sources/{sid}/page/0.png?zoom=3.0")
    assert other.headers.get("etag") not in (None, etag), "换了缩放比例应当是另一个 ETag"


def test_the_etag_needs_the_same_authorization_as_the_render():
    """A validator obtainable without authorization would leak whether a document changed."""
    import inspect
    from docweave.views import Views
    src = inspect.getsource(Views.source_page_etag)
    assert "self.app.get_source(" in src, "ETag 必须走与渲染相同的对象查找"
    assert "render_page_png" not in src, "算 ETag 不应当先把页面渲染出来"


def test_an_unauthorized_caller_cannot_obtain_the_validator(api, fixtures):
    """Same refusal as the render itself — 404, not a 304 that would confirm existence."""
    r = api.c.get("/api/sources/s_does_not_exist/page/0.png",
                  headers={"If-None-Match": '"anything"'})
    assert r.status_code == 404


# ---------------------------------------------------------------- editor body
def test_an_oversized_editor_document_is_refused_before_it_is_parsed(api):
    """Uploaded files are capped by `_read_limited`; this route takes JSON, which Starlette
    reads into memory and parses in full before any handler runs — and it had no limit at all.

    Images arrive inline as data URIs, so an 11 MB photo becomes ~16 MB inside the body. On a
    container limited to 3 GB that is an out-of-memory kill, not an error page.
    """
    from docweave.engine.limits import LIMITS
    oversized = "x" * (LIMITS.max_editor_body_bytes + 1024)
    r = api.c.post("/api/templates/builtin",
                   json={"name": "太大了", "kind": "builtin_doc",
                         "editor": {"body": {"blob": oversized}}})
    assert r.status_code == 413
    body = r.json()
    assert body["error"] == "editor_body_too_large"
    assert "图片" in body["message"], "错误信息要说清楚通常是图片造成的"
    assert "没有丢失" in body["message"], "要告诉用户编辑器里的内容还在"


def test_a_normal_editor_document_still_saves(api):
    r = api.c.post("/api/templates/builtin",
                   json={"name": "正常大小", "kind": "builtin_doc",
                         "editor": {"page": {}, "body": {"type": "doc", "content": []},
                                    "header": {"type": "doc", "content": []},
                                    "footer": {"type": "doc", "content": []}}})
    assert r.status_code == 200, r.text
