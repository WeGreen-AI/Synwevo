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
    from synwevo_core import source_manifest, source_offer
    root = tmp_path / "installed"
    (root / "apps" / "docs").mkdir(parents=True)
    (root / "apps" / "docs" / "x.py").write_text("# code\n", encoding="utf-8")
    (root / "LICENSE").write_text("licence text\n", encoding="utf-8")
    source_manifest.write(root, commit="0123456789abcdef0123456789abcdef01234567", commit_time=1767225600)
    monkeypatch.setattr(source_offer, "ROOT", root)
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


def _standalone(factory: str, data_dir, monkeypatch, *, mode: str) -> TestClient:
    """A module's own factory, as it runs without the composer: the account service with no
    document engine mounted, the portal with Synwevo Docs stopped, Synwevo Message alone."""
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", mode)
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)            # each factory's own default role
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    module = importlib.import_module(factory)
    return TestClient(module.create_app(str(data_dir), start_jobs=False))


STANDALONE = ["synwevo_identity.app", "synwevo_portal.app", "synwevo_messages.app"]


@pytest.mark.parametrize("mode", ["session", "basic"])
@pytest.mark.parametrize("factory", STANDALONE)
def test_a_module_run_on_its_own_offers_the_source_too(tmp_path, monkeypatch, small_tree, factory, mode):
    """The account frame and the portal footer link to /api/source-code on their own origin. Only
    the composer (synwevo_server) and Synwevo Docs mounted it, so the account service, the portal
    and Synwevo Message run from their own factories answered that link with 404 — the offer §13
    owes their users pointed at nothing. Each serves it now, anonymously, from synwevo_core
    (none of them may import Synwevo Docs)."""
    c = _standalone(factory, tmp_path / f"{factory}-{mode}", monkeypatch, mode=mode)
    about = c.get("/api/about")
    assert about.status_code == 200, (factory, mode, about.text)
    assert about.json()["license"] == "AGPL-3.0-or-later" and about.json()["source_offer"] is True
    archive = c.get("/api/source-code")
    assert archive.status_code == 200, (factory, mode, archive.text)
    assert archive.headers["content-type"] == "application/gzip"
    names = tarfile.open(fileobj=io.BytesIO(archive.content), mode="r:gz").getnames()
    assert any(n.endswith("/apps/docs/x.py") for n in names) and any(n.endswith("/SOURCE_OFFER.txt") for n in names)
    for path in ("/api/about/", "/api/source-code/x"):
        assert c.get(path).status_code in (401, 404), (factory, path)


def test_every_process_mounts_the_offer_exactly_once(tmp_path, monkeypatch, small_tree):
    """The composer mounts the router on the roles without Synwevo Docs and Docs' install on the
    roles with it; the modules mount it in their standalone factories, never in the install()
    the composer calls — so no composed process carries two routers for one path."""
    def paths(routes, prefix=""):
        # This FastAPI keeps an included router as one `_IncludedRouter` entry without a path
        # (services/identity/tests/test_admin.py), and the schema's path table would fold two
        # routers for one path into one entry: walk into the included routers instead.
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None:
                yield from paths(inner.routes, prefix + r.include_context.prefix)
            elif getattr(r, "path", None) is not None:
                yield prefix + r.path

    apps = {role: _client(tmp_path / f"once-{role}", monkeypatch, mode="session", role=role).app for role in ROLES}
    for factory in STANDALONE:
        apps[factory] = _standalone(factory, tmp_path / f"once-{factory}", monkeypatch, mode="session").app
    for name, app in apps.items():
        mounted = list(paths(app.router.routes))
        assert "/api/auth/me" in mounted, f"{name}: 路由表读取方式失效了"
        offered = sorted(p for p in mounted if p in ("/api/about", "/api/source-code"))
        assert offered == ["/api/about", "/api/source-code"], (name, offered)


def test_every_role_and_every_module_says_the_same_about_the_licence(tmp_path, monkeypatch, small_tree):
    """One program, one statement: the Docs role reads the engine it has selected, every other
    role and module the selection the same settings make (synwevo_core.source_offer), and the
    answers are the same document."""
    bodies = {role: _client(tmp_path / f"same-{role}", monkeypatch, mode="session", role=role).get("/api/about").json()
              for role in ROLES}
    for factory in STANDALONE:
        bodies[factory] = _standalone(factory, tmp_path / f"same-{factory}", monkeypatch, mode="session").get("/api/about").json()
    first = bodies["all"]
    assert {name for name, body in bodies.items() if body != first} == set(), bodies


@pytest.mark.parametrize("role", ROLES)
def test_the_offer_opens_no_other_path(tmp_path, monkeypatch, small_tree, role):
    """Exact paths: a neighbour of the offer, and the business API beside it, still need a session."""
    c = _client(tmp_path / role, monkeypatch, mode="session", role=role)
    for path in ("/api/about/", "/api/aboutx", "/api/source-code/x", "/api/projects"):
        assert c.get(path).status_code in (401, 404), path


def test_the_archive_is_rate_limited_per_address(tmp_path, monkeypatch, small_tree):
    from synwevo_core import source_offer
    monkeypatch.setattr(source_offer, "DOWNLOADS_PER_MIN", 2)
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
    from synwevo_core import source_offer
    monkeypatch.setattr(source_offer, "DOWNLOADS_PER_MIN", 1)
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
    from synwevo_core import source_offer
    monkeypatch.setattr(source_offer, "DOWNLOADS_PER_MIN", 2)
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
    from synwevo_core import source_offer
    monkeypatch.setattr(source_offer, "DOWNLOADS_PER_MIN", 2)
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "1")
    c = _client(tmp_path / "v6-wide", monkeypatch, mode="session", role="accounts")

    def get(address):
        return c.get("/api/source-code", headers={"X-Forwarded-For": address})

    per_56 = 2 * source_offer.WIDER_PREFIX_CLIENTS[56]
    per_48 = 2 * source_offer.WIDER_PREFIX_CLIENTS[48]
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


def test_an_ipv6_address_that_carries_an_ipv4_address_is_counted_as_it():
    """Behind a NAT64 or SIIT front (RFC 6052's well-known prefix 64:ff9b::/96) every IPv4 client
    arrives as 64:ff9b::<its address>, all in one /64, so the whole IPv4 Internet shared one bucket.
    A Teredo address's /64 is its Teredo server, shared by every client of that server (RFC 4380),
    and a 6to4 /48 is the one IPv4 address it is built from (RFC 3056). Each is counted as the IPv4
    address it carries, as an IPv4-mapped address already was."""
    from synwevo_core.http import address_buckets
    assert address_buckets("64:ff9b::198.51.100.7") == ("198.51.100.7",)
    assert address_buckets("64:ff9b::cb00:7109") == ("203.0.113.9",)
    # RFC 4380's example: server 65.54.227.120, client 192.0.2.45 (stored inverted), port 40000.
    assert address_buckets("2001:0000:4136:e378:8000:63bf:3fff:fdd2") == ("192.0.2.45",)
    assert address_buckets("2002:c633:6407:1::1") == ("198.51.100.7",)
    # The local-use translation prefix (RFC 8215) has no fixed place for the IPv4 address: it is
    # counted per prefix, like any other IPv6 address.
    assert address_buckets("64:ff9b:1::c633:6407") == ("64:ff9b:1::/64", "64:ff9b:1::/56", "64:ff9b:1::/48")
    assert address_buckets("2001:db8:1:2::1") == ("2001:db8:1:2::/64", "2001:db8:1::/56", "2001:db8:1::/48")


def test_clients_behind_a_translator_each_get_their_own_downloads(tmp_path, monkeypatch, small_tree):
    from synwevo_core import source_offer
    monkeypatch.setattr(source_offer, "DOWNLOADS_PER_MIN", 2)
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "1")
    c = _client(tmp_path / "nat64", monkeypatch, mode="session", role="accounts")

    def get(address):
        return c.get("/api/source-code", headers={"X-Forwarded-For": address}).status_code

    assert [get("64:ff9b::198.51.100.7") for _ in range(3)] == [200, 200, 429]
    assert get("198.51.100.7") == 429, "经翻译器来的就是这个 IPv4 客户端"
    assert [get("64:ff9b::198.51.100.8"), get("64:ff9b::203.0.113.9")] == [200, 200], "翻译器后面的其他客户端不受影响"


def test_without_a_release_list_the_archive_is_refused_not_walked(tmp_path, monkeypatch):
    """An installation with neither the image's manifest nor a Git checkout cannot tell its source
    from whatever else sits in the directory, so it serves nothing rather than everything."""
    from synwevo_core import source_offer
    root = tmp_path / "bare"
    (root / "apps" / "docs").mkdir(parents=True)
    (root / "apps" / "docs" / "x.py").write_text("# code\n", encoding="utf-8")
    (root / "apps" / "docs" / "runtime.env.bak").write_text("SECRET=fixture\n", encoding="utf-8")
    monkeypatch.setattr(source_offer, "ROOT", root)
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
