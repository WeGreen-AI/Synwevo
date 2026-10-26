"""The licence and the Corresponding Source offer reach every remote user (AGPL-3.0 §13).

Measured before this: with `SYNWEVO_UI_AUTH=session` (production) both `/api/about` and
`/api/source-code` answered 401 「请先登录」, and only the Docs role served them at all — so a
person who used only the sign-in page, the public site, the workspace or the conversations of the
same program was never offered its source. Every role now serves both paths, without a session,
in every authentication mode; nothing else becomes anonymous with them.
"""
from __future__ import annotations

import importlib
import io
import tarfile

import pytest
from fastapi.testclient import TestClient

ROLES = ["all", "accounts", "portal", "app", "messages"]


def _client(data_dir, monkeypatch, *, mode: str, role: str) -> TestClient:
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", mode)
    monkeypatch.setenv("SYNWEVO_ROLE", role)
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    return TestClient(app_mod.create_app(str(data_dir), start_jobs=False))


@pytest.fixture()
def small_tree(tmp_path, monkeypatch):
    """TEST FIXTURE: a two-file installation as an image build leaves it — the files, then the
    release manifest the build wrote from them — so each role's archive is built in milliseconds."""
    from synwevo_docs import licensing, source_manifest
    root = tmp_path / "installed"
    (root / "apps" / "docs").mkdir(parents=True)
    (root / "apps" / "docs" / "x.py").write_text("# code\n", encoding="utf-8")
    (root / "LICENSE").write_text("licence text\n", encoding="utf-8")
    source_manifest.write(root, commit="0123456789abcdef0123456789abcdef01234567", commit_time=1767225600)
    monkeypatch.setattr(licensing, "ROOT", root)
    return root


@pytest.mark.parametrize("mode", ["session", "basic"])
@pytest.mark.parametrize("role", ROLES)
def test_every_role_offers_the_source_without_a_sign_in(tmp_path, monkeypatch, small_tree, mode, role):
    c = _client(tmp_path / f"{mode}-{role}", monkeypatch, mode=mode, role=role)
    about = c.get("/api/about")
    assert about.status_code == 200, (role, mode, about.text)
    body = about.json()
    assert body["license"] == "AGPL-3.0-or-later" and body["source_offer"] is True
    assert "AGPL-3.0-or-later" in body["license_notice"]
    archive = c.get("/api/source-code")
    assert archive.status_code == 200, (role, mode, archive.text)
    assert archive.headers["content-type"] == "application/gzip"
    names = tarfile.open(fileobj=io.BytesIO(archive.content), mode="r:gz").getnames()
    assert any(n.endswith("/apps/docs/x.py") for n in names) and any(n.endswith("/SOURCE_OFFER.txt") for n in names)


@pytest.mark.parametrize("role", ROLES)
def test_the_offer_opens_no_other_path(tmp_path, monkeypatch, small_tree, role):
    """Exact paths: a neighbour of the offer, and the business API beside it, still need a session."""
    c = _client(tmp_path / role, monkeypatch, mode="session", role=role)
    for path in ("/api/about/", "/api/aboutx", "/api/source-code/x", "/api/projects"):
        assert c.get(path).status_code in (401, 404), path


def test_the_archive_is_rate_limited_per_address(tmp_path, monkeypatch, small_tree):
    from synwevo_docs import licensing
    monkeypatch.setattr(licensing, "DOWNLOADS_PER_MIN", 2)
    c = _client(tmp_path / "limited", monkeypatch, mode="session", role="accounts")
    assert [c.get("/api/source-code").status_code for _ in range(3)] == [200, 200, 429]
    refused = c.get("/api/source-code").json()
    assert refused["error"] == "too_many_attempts" and refused["detail"]["retry_after"] >= 1


@pytest.mark.parametrize("role", ["all", "accounts", "app"])
@pytest.mark.parametrize("target", ["/api/about%3F/probe", "/api/about%3f/probe", "/api/source-code%23/probe",
                                    "/api/source-code%3F/probe"])
def test_an_encoded_query_mark_does_not_borrow_the_exemption(tmp_path, monkeypatch, small_tree, role, target):
    """`/api/about%3F/x` is decoded to the path `/api/about?/x`; Starlette's `request.url` re-splits
    that at the `?` and reads `/api/about`. The gate used to decide on that shorter string, so the
    request passed both the role gate and the session gate as the exempt path while the router
    matched the longer one. A route shaped like any future `/api/{name}/…` route, placed first so
    nothing in front of it answers, must stay behind the gate."""
    c = _client(tmp_path / role, monkeypatch, mode="session", role=role)
    reached = []

    @c.app.get("/api/{name}/probe")
    def probe(name: str):          # TEST FIXTURE: a parameterised route the gate must protect
        reached.append(name)
        return {"name": name}

    c.app.router.routes.insert(0, c.app.router.routes.pop())
    r = c.get(target)
    assert not reached and r.status_code in (401, 404), (target, r.status_code, r.text)
    assert r.json()["error"] in ("unauthorized", "not_in_this_role")
    assert c.get("/api/about").status_code == 200, "精确路径本身仍然匿名可达"


def test_an_encoded_query_mark_does_not_borrow_the_probe_exemption(tmp_path, monkeypatch, small_tree):
    """The probes had the same shape: under `basic`, `/healthz%3F/x` skipped the password."""
    c = _client(tmp_path / "basic", monkeypatch, mode="basic", role="all")
    reached = []

    @c.app.get("/{name}/probe")
    def probe(name: str):          # TEST FIXTURE: a parameterised route the gate must protect
        reached.append(name)
        return {"name": name}

    c.app.router.routes.insert(0, c.app.router.routes.pop())
    for target in ("/healthz%3F/probe", "/readyz%23/probe"):
        r = c.get(target)
        assert r.status_code == 401 and not reached, (target, r.status_code)
    assert c.get("/healthz").status_code == 200


def test_repeats_are_answered_from_the_edge_and_the_etag(tmp_path, monkeypatch, small_tree):
    """The archive is the same bytes until the next deployment, so the edge may keep it: public,
    a bounded max-age and a strong ETag. A revalidation that still matches is a 304 with no body,
    and it does not spend the address's downloads."""
    from synwevo_docs import licensing
    monkeypatch.setattr(licensing, "DOWNLOADS_PER_MIN", 1)
    c = _client(tmp_path / "edge", monkeypatch, mode="session", role="accounts")
    first = c.get("/api/source-code")
    assert first.status_code == 200
    etag, cache = first.headers["etag"], first.headers["cache-control"]
    assert etag.startswith('"') and etag.endswith('"') and len(etag) > 20, etag      # strong, not W/
    assert "public" in cache and "max-age=" in cache and "no-store" not in cache, cache
    assert "set-cookie" not in first.headers, "带 Set-Cookie 的响应边缘不会缓存，也不该有"
    for sent in (etag, f'"other", {etag}', f"W/{etag}", "*"):
        again = c.get("/api/source-code", headers={"If-None-Match": sent})
        assert again.status_code == 304 and again.content == b"", sent
        assert again.headers["etag"] == etag and "public" in again.headers["cache-control"]
    stale = c.get("/api/source-code", headers={"If-None-Match": '"not-this-one"'})
    assert stale.status_code == 429, "不匹配的重新验证是一次真正的下载，照常计数"
    assert "no-store" in stale.headers["cache-control"], "拒绝不能被边缘缓存"


def test_the_limit_counts_an_ipv6_64_as_one_client(tmp_path, monkeypatch, small_tree):
    """An IPv6 subscriber is routinely given a whole /64 and picks addresses inside it freely, so a
    bucket per /128 limited nothing: 60 addresses of one /64 got 60 archives. IPv6 is counted per
    /64, IPv4 per address, and an IPv4-mapped IPv6 address as the IPv4 address it is."""
    from synwevo_docs import licensing
    monkeypatch.setattr(licensing, "DOWNLOADS_PER_MIN", 2)
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "1")
    c = _client(tmp_path / "v6", monkeypatch, mode="session", role="accounts")

    def get(address):
        return c.get("/api/source-code", headers={"X-Forwarded-For": address}).status_code

    assert [get(f"2001:db8:1:2::{i:x}") for i in range(1, 4)] == [200, 200, 429]
    assert get("2001:db8:1:2:ffff:ffff:ffff:ffff") == 429, "同一 /64 的另一个地址还是同一个客户端"
    assert get("2001:db8:1:3::1") == 200, "另一个 /64 是另一个客户端"
    assert [get("198.51.100.7"), get("::ffff:198.51.100.7"), get("198.51.100.7")] == [200, 200, 429]
    assert get("198.51.100.8") == 200


def test_the_limit_also_counts_the_wider_prefixes_a_site_is_given(tmp_path, monkeypatch, small_tree):
    """One /64 is one subnet, not one subscriber: an end site is routinely delegated a /56 (256
    /64s) and a tunnel broker hands out a /48 (65,536) for free, so counting only per /64 gave one
    holder 256× or 65,536× the allowance (review: 16 /64s of one /56, 6 requests each, 96 × 200).
    The /56 and the /48 are counted too, each with a few clients' allowance, and a refused request
    spends nothing at any level."""
    from synwevo_docs import licensing
    monkeypatch.setattr(licensing, "DOWNLOADS_PER_MIN", 2)
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "1")
    c = _client(tmp_path / "v6-wide", monkeypatch, mode="session", role="accounts")

    def get(address):
        return c.get("/api/source-code", headers={"X-Forwarded-For": address})

    per_56 = 2 * licensing.WIDER_PREFIX_CLIENTS[56]
    per_48 = 2 * licensing.WIDER_PREFIX_CLIENTS[48]
    assert per_56 < per_48 < 2 * 256, (per_56, per_48)
    # A different /64 of the same /56 for every request: the /56 runs out, not the /64s.
    one_56 = [get(f"2001:db8:77:{i:x}::1").status_code for i in range(per_56 + 3)]
    assert one_56 == [200] * per_56 + [429] * 3, one_56
    refused = get("2001:db8:77:ff::1")
    assert refused.status_code == 429 and refused.json()["detail"]["retry_after"] >= 1
    assert "no-store" in refused.headers["cache-control"]
    # The /48 has spent only what the /56 was given: the refusals above took nothing.
    other_56s = [get(f"2001:db8:77:{0x100 * (1 + i // per_56) + i % per_56:x}::1").status_code
                 for i in range(per_48 - per_56 + 2)]
    assert other_56s == [200] * (per_48 - per_56) + [429] * 2, other_56s
    assert get("2001:db8:78::1").status_code == 200, "另一个 /48 是另一个站点"
    assert get("198.51.100.9").status_code == 200, "IPv4 不受 IPv6 前缀计数影响"


def test_without_a_release_list_the_archive_is_refused_not_walked(tmp_path, monkeypatch):
    """An installation with neither the image's manifest nor a Git checkout cannot tell its source
    from whatever else sits in the directory, so it serves nothing rather than everything."""
    from synwevo_docs import licensing
    root = tmp_path / "bare"
    (root / "apps" / "docs").mkdir(parents=True)
    (root / "apps" / "docs" / "x.py").write_text("# code\n", encoding="utf-8")
    (root / "apps" / "docs" / "runtime.env.bak").write_text("SECRET=fixture\n", encoding="utf-8")
    monkeypatch.setattr(licensing, "ROOT", root)
    c = _client(tmp_path / "bare-data", monkeypatch, mode="session", role="accounts")
    r = c.get("/api/source-code")
    assert r.status_code == 503 and r.json()["error"] == "source_unavailable", r.text
    assert "git archive" in r.json()["message"]
    assert c.get("/api/about").json()["source_offer"] is True


def test_switching_the_offer_off_is_said_on_every_role(tmp_path, monkeypatch, small_tree):
    monkeypatch.setenv("SYNWEVO_DOCS_SOURCE_OFFER", "off")
    for role in ROLES:
        c = _client(tmp_path / f"off-{role}", monkeypatch, mode="session", role=role)
        r = c.get("/api/source-code")
        assert r.status_code == 404 and r.json()["error"] == "source_offer_off", role
        assert c.get("/api/about").json()["source_offer"] is False


def test_every_shell_links_to_the_offer():
    """§13 asks for a *prominent* offer. The archive is linked where people arrive: the footer of the
    public site and the workspace (one portal shell), the footer of every sign-in, sign-up and
    recovery page (the account frame), and the Docs page about licences, which also shows the
    licence itself."""
    from synwevo_core.http import repo_root
    root = repo_root()
    for rel in ("apps/portal/web/src/Portal.tsx", "services/identity/web/src/pages/org/AuthFlow.tsx",
                "apps/docs/web/src/pages/About.tsx"):
        text = (root / rel).read_text(encoding="utf-8")
        assert 'href="/api/source-code" download' in text, f"{rel} 没有链接到对应源代码"
    about = (root / "apps/docs/web/src/pages/About.tsx").read_text(encoding="utf-8")
    assert "a.license_notice" in about and "a.notice" in about, "关于页要显示本程序的许可与 AGPL 组件说明"
