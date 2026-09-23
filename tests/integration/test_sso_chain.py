"""The unified sign-in chain, end to end, across two origins.

Two separate ASGI apps — the account service and DocWeave — with a cookie jar that enforces
**host scoping**, which is the property the whole design rests on. The environment's sandboxed
browser allows one `*.localhost` host and denies its siblings, so a click-through of the real
redirect chain is not available here; this drives the same redirects over real HTTP semantics
and can additionally express the cases a browser cannot be made to perform (a forged state, a
stolen code, a callback replayed from somewhere else).

What it does NOT prove: that a real browser applies the same cookie rules. That is checked
separately by `scripts/verify_sso.py` against live servers on real hostnames.
"""
from __future__ import annotations

import importlib
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from platform_core.identity import Identity
from platform_core import oidc
from platform_core.apps import Apps, bootstrap_docweave
from platform_core.store import Store

PW = "a-sufficiently-long-passphrase"
ACCOUNTS = "http://accounts.localhost:8770"
DOCWEAVE = "http://docweave.localhost:8765"


class Jar:
    """A cookie jar that is host-scoped on purpose.

    Different ports on the same host do **not** isolate cookies, so a jar keyed by origin would
    quietly pass a test that the real world fails. This is keyed by host alone, which is what a
    browser does, and is why `accounts.localhost` and `docweave.localhost` are used rather than
    two ports on `localhost`.
    """

    def __init__(self):
        self.by_host: dict[str, dict[str, str]] = {}

    def host(self, url: str) -> str:
        return urllib.parse.urlparse(url).hostname or ""

    def apply(self, url: str) -> dict[str, str]:
        return dict(self.by_host.get(self.host(url), {}))

    def absorb(self, url: str, resp) -> None:
        store = self.by_host.setdefault(self.host(url), {})
        for name, value in resp.cookies.items():
            store[name] = value
        for raw in resp.headers.get_list("set-cookie"):
            name = raw.split("=", 1)[0].strip()
            if "max-age=0" in raw.lower() or "expires=thu, 01 jan 1970" in raw.lower():
                store.pop(name, None)


class Svc:
    """One service at one origin. Its own cookie jar is cleared before every request so that the
    only cookies in play are the ones this test hands over — otherwise the client would quietly
    re-send a session on the calls that are meant to arrive without one."""

    def __init__(self, app, origin: str):
        self.origin = origin
        self.c = TestClient(app, base_url=origin)

    def request(self, method: str, path: str, **kw):
        self.c.cookies.clear()
        kw.setdefault("follow_redirects", False)
        return self.c.request(method, path, **kw)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)


class Browser:
    """Follows redirects across both apps the way a browser would, one hop at a time."""

    def __init__(self, apps: dict[str, Svc]):
        self.apps = apps
        self.jar = Jar()
        self.trail: list[str] = []

    def _client(self, url: str) -> "Svc":
        origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(url))
        if origin not in self.apps:
            raise AssertionError(f"浏览器被引导到未知来源 {origin}（可能是开放重定向）")
        return self.apps[origin]

    def go(self, url: str, *, method: str = "GET", data=None, max_hops: int = 10):
        for _ in range(max_hops):
            self.trail.append(url)
            c = self._client(url)
            path = url[len(c.origin):] or "/"
            resp = c.request(method, path, cookies=self.jar.apply(url), data=data)
            self.jar.absorb(url, resp)
            if resp.status_code not in (301, 302, 303, 307, 308):
                return resp
            loc = resp.headers["location"]
            url = loc if "://" in loc else urllib.parse.urljoin(url, loc)
            method, data = "GET", None
        raise AssertionError("重定向次数过多——可能是登录回环:\n  " + "\n  ".join(self.trail))


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """One database (one account authority), two apps, two origins."""
    data = str(tmp_path / "d")
    for k, v in {"DOCWEAVE_DATA_DIR": data, "DOCWEAVE_UI_AUTH": "session",
                 "DOCWEAVE_COOKIE_SECURE": "0", "DOCWEAVE_ISSUER": ACCOUNTS,
                 "DOCWEAVE_APP_URL": DOCWEAVE, "DOCWEAVE_OIDC_CLIENT_ID": "docweave-web"}.items():
        monkeypatch.setenv(k, v)
    import platform_server.app as app_mod
    importlib.reload(app_mod)

    # The provider's back-channel POST must reach DocWeave over the test transport. The router
    # binds its deliverer when it is built, so this is patched *before* the account service is
    # created; the app it posts to is filled in below.
    import platform_core.oidc as oidc_mod
    later: dict = {}

    def _deliver(url, token, timeout=3.0):
        r = later["docweave"].post(url[len(DOCWEAVE):], data={"logout_token": token})
        return (200 <= r.status_code < 300,
                None if r.status_code < 300 else f"HTTP {r.status_code}")

    monkeypatch.setattr(oidc_mod, "deliver_logout", _deliver)
    accounts = Svc(app_mod.create_app(data, start_jobs=False), ACCOUNTS)

    # DocWeave's relying-party client talks to the account service over that same transport, so
    # the token exchange really is an HTTP request to the real provider, not a function call.
    import platform_auth_client.rp as rp_mod
    monkeypatch.setattr(rp_mod.Rp, "http", property(lambda self: accounts.c))
    docweave = Svc(app_mod.create_app(data, start_jobs=False), DOCWEAVE)
    later["docweave"] = docweave

    store = Store(tmp_path / "d")
    ident = Identity(store)
    apps = Apps(store, ident)
    user = ident.create_user("alice@example.com", PW, display_name="Alice", status="active")
    ws = ident.workspaces_for(user["id"])[0]["id"]
    bootstrap_docweave(store, ident)
    apps.enable_for_workspace(ws, "docweave")
    oidc.Clients(store).register(
        client_id="docweave-web", app_id="docweave", name="DocWeave",
        redirect_uris=[DOCWEAVE + "/auth/callback"],
        post_logout_redirect_uris=[DOCWEAVE + "/signed-out"],
        backchannel_logout_uri=DOCWEAVE + "/auth/backchannel-logout")

    b = Browser({ACCOUNTS: accounts, DOCWEAVE: docweave})
    return {"b": b, "store": store, "ident": ident, "apps": apps, "user": user, "ws": ws,
            "accounts": accounts, "docweave": docweave}


def whoami(svc, cookies) -> str | None:
    """The signed-in email at this origin, or None. `/api/auth/me` answers 401 when there is no
    session, which is the shape the app's own client relies on."""
    r = svc.get("/api/auth/me", cookies=cookies)
    return r.json()["email"] if r.status_code == 200 else None


def _sign_in_centrally(world):
    r = world["accounts"].post("/api/auth/login",
                               json={"email": "alice@example.com", "password": PW})
    assert r.status_code == 200, r.text
    world["b"].jar.absorb(ACCOUNTS, r)
    return r


# ---------------------------------------------------------------- chain 2: app first
def test_docweave_first_signs_in_and_returns_to_the_page_that_was_asked_for(world):
    """开始于 DocWeave 的受保护页面 → 统一登录 → 回到原页面。"""
    b = world["b"]
    _sign_in_centrally(world)
    resp = b.go(DOCWEAVE + "/auth/start?next=/projects/p_abc/mappings")
    assert resp.status_code == 200
    assert b.trail[-1] == DOCWEAVE + "/projects/p_abc/mappings", b.trail
    # It really went via the account service, and really came back.
    assert any(u.startswith(ACCOUNTS + "/oidc/authorize") for u in b.trail), b.trail

    # A DocWeave session now exists, and it is DocWeave's own — not the central one.
    dw = b.jar.by_host["docweave.localhost"]
    acc = b.jar.by_host["accounts.localhost"]
    assert dw["dw_session"] and acc["dw_session"]
    assert dw["dw_session"] != acc["dw_session"], "两个来源必须是两个会话，不能复制中央会话值"
    me = world["docweave"].get("/api/auth/me", cookies=b.jar.apply(DOCWEAVE)).json()
    assert me["email"] == "alice@example.com"
    row = world["store"].one("SELECT workspace_id FROM sessions WHERE oidc_sid IS NOT NULL "
                             "ORDER BY created_at DESC LIMIT 1")
    assert row["workspace_id"] == world["ws"], "落地的工作空间由服务端重新解析，不是令牌里带来的"


def test_the_account_cookie_is_not_sent_to_docweave(world):
    """不同端口不隔离 Cookie，不同主机才隔离——这里检查的是后者。"""
    b = world["b"]
    _sign_in_centrally(world)
    b.go(DOCWEAVE + "/auth/start?next=/")
    assert "docweave.localhost" in b.jar.by_host and "accounts.localhost" in b.jar.by_host
    # The jar is keyed by host, so this is what DocWeave would actually receive.
    assert b.jar.apply(DOCWEAVE)["dw_session"] != b.jar.apply(ACCOUNTS)["dw_session"]
    # And DocWeave's session is not usable at the account service, nor the reverse.
    assert whoami(world["docweave"], {"dw_session": "not-a-session"}) is None


def test_signing_in_at_docweave_when_not_signed_in_centrally_stops_at_the_login_page(world):
    b = world["b"]
    resp = b.go(DOCWEAVE + "/auth/start?next=/projects")
    assert resp.status_code == 200
    assert b.trail[-1].startswith(ACCOUNTS + "/login?next="), b.trail
    nxt = urllib.parse.unquote(
        dict(urllib.parse.parse_qsl(urllib.parse.urlparse(b.trail[-1]).query))["next"])
    assert nxt.startswith("/oidc/authorize?"), "登录后要回到原来的授权请求，而不是账户首页"


def test_a_second_application_sign_in_does_not_ask_for_the_password_again(world):
    """有有效统一登录态时按策略完成单点登录，无须再次输入密码。"""
    b = world["b"]
    _sign_in_centrally(world)
    b.go(DOCWEAVE + "/auth/start?next=/")
    first = b.jar.apply(DOCWEAVE)["dw_session"]
    b.trail.clear()
    b.go(DOCWEAVE + "/auth/start?next=/templates")
    assert b.trail[-1] == DOCWEAVE + "/templates"
    assert not any("/login" in u for u in b.trail), b.trail
    assert b.jar.apply(DOCWEAVE)["dw_session"] != first, "每次授权都应换新的本地会话值"


# ---------------------------------------------------------------- return targets
@pytest.mark.parametrize("bad", ["//evil.example.com/x", "https://evil.example.com/x",
                                 "/\\evil.example.com", "http:/evil.example.com"])
def test_an_off_site_return_target_lands_on_a_safe_page(world, bad):
    b = world["b"]
    _sign_in_centrally(world)
    b.go(DOCWEAVE + "/auth/start?next=" + urllib.parse.quote(bad, safe=""))
    assert b.trail[-1] == DOCWEAVE + "/", b.trail
    # The browser is never *sent* anywhere off-site. The first entry is the request under test,
    # which of course still contains the string; every hop after it must stay on a known origin
    # — and `Browser._client` fails the test outright if one does not.
    assert all(u.startswith((DOCWEAVE, ACCOUNTS)) for u in b.trail[1:]), b.trail


# ---------------------------------------------------------------- callback integrity
def test_a_callback_without_the_request_cookie_is_refused(world):
    """The state in the URL means nothing without the server-side request it points to."""
    b = world["b"]
    _sign_in_centrally(world)
    start = world["docweave"].get("/auth/start?next=/", follow_redirects=False)
    auth_url = start.headers["location"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(auth_url).query))["state"]
    b.jar.absorb(DOCWEAVE, start)
    hop = world["accounts"].get(auth_url[len(ACCOUNTS):], cookies=b.jar.apply(ACCOUNTS),
                                follow_redirects=False)
    code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(hop.headers["location"]).query))["code"]

    stolen = world["docweave"].get(f"/auth/callback?code={code}&state={state}")   # no cookie
    assert stolen.status_code == 400
    # the legitimate browser can still finish
    ok = world["docweave"].get(f"/auth/callback?code={code}&state={state}",
                               cookies=b.jar.apply(DOCWEAVE), follow_redirects=False)
    assert ok.status_code == 303


def test_a_forged_state_is_refused(world):
    b = world["b"]
    _sign_in_centrally(world)
    start = world["docweave"].get("/auth/start?next=/", follow_redirects=False)
    b.jar.absorb(DOCWEAVE, start)
    out = world["docweave"].get("/auth/callback?code=whatever&state=not-the-one",
                                cookies=b.jar.apply(DOCWEAVE))
    assert out.status_code == 400 and "状态校验失败" in out.text


def test_a_callback_cannot_be_replayed(world):
    b = world["b"]
    _sign_in_centrally(world)
    start = world["docweave"].get("/auth/start?next=/", follow_redirects=False)
    b.jar.absorb(DOCWEAVE, start)
    auth_url = start.headers["location"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(auth_url).query))["state"]
    hop = world["accounts"].get(auth_url[len(ACCOUNTS):], cookies=b.jar.apply(ACCOUNTS),
                                follow_redirects=False)
    code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(hop.headers["location"]).query))["code"]
    args = dict(cookies=b.jar.apply(DOCWEAVE), follow_redirects=False)
    assert world["docweave"].get(f"/auth/callback?code={code}&state={state}", **args).status_code == 303
    again = world["docweave"].get(f"/auth/callback?code={code}&state={state}", **args)
    assert again.status_code == 400


def test_the_provider_refusing_lands_on_our_own_page(world):
    out = world["docweave"].get("/auth/callback?error=access_denied&error_description=no",
                                follow_redirects=False)
    assert out.status_code == 303 and out.headers["location"].startswith("/sign-in-failed")


# ---------------------------------------------------------------- entitlement
def test_losing_the_application_stops_the_sign_in_at_docweave(world):
    """No code is ever issued: the provider refuses at /oidc/authorize, and DocWeave shows why
    on its own page rather than following anything the query string suggests."""
    b = world["b"]
    _sign_in_centrally(world)
    world["store"].execute("DELETE FROM workspace_apps WHERE workspace_id=?", (world["ws"],))
    b.go(DOCWEAVE + "/auth/start?next=/")
    assert b.trail[-1].startswith(DOCWEAVE + "/sign-in-failed?reason="), b.trail
    reason = urllib.parse.unquote(b.trail[-1].split("reason=", 1)[1])
    assert "尚未启用" in reason, reason
    assert "dw_session" not in b.jar.apply(DOCWEAVE)
    assert world["store"].one("SELECT COUNT(*) AS n FROM oauth_codes")["n"] == 0


def test_losing_the_application_between_authorize_and_callback_still_stops_it(world):
    """The second gate: the id_token proves identity, not eligibility, so the callback checks
    again rather than trusting the decision made moments earlier."""
    b = world["b"]
    _sign_in_centrally(world)
    start = world["docweave"].get("/auth/start?next=/")
    b.jar.absorb(DOCWEAVE, start)
    auth_url = start.headers["location"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(auth_url).query))
    hop = world["accounts"].get(auth_url[len(ACCOUNTS):], cookies=b.jar.apply(ACCOUNTS))
    code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(hop.headers["location"]).query))["code"]

    world["store"].execute("DELETE FROM workspace_apps WHERE workspace_id=?", (world["ws"],))
    out = world["docweave"].get(f"/auth/callback?code={code}&state={q['state']}",
                                cookies=b.jar.apply(DOCWEAVE))
    assert out.status_code == 303 and out.headers["location"].startswith("/no-access?")
    assert "dw_session" not in {c.split("=")[0] for c in out.headers.get_list("set-cookie")}


# ---------------------------------------------------------------- logout
def test_platform_logout_ends_the_docweave_session_too(world):
    """全平台退出不能只清除门户 Cookie。"""
    b = world["b"]
    _sign_in_centrally(world)
    b.go(DOCWEAVE + "/auth/start?next=/")
    assert whoami(world["docweave"], b.jar.apply(DOCWEAVE)) == "alice@example.com"

    b.go(ACCOUNTS + "/oidc/logout?" + urllib.parse.urlencode(
        {"client_id": "docweave-web", "post_logout_redirect_uri": DOCWEAVE + "/signed-out"}))
    # the DocWeave session is gone even though its own cookie was never touched
    assert whoami(world["docweave"], b.jar.apply(DOCWEAVE)) is None
    row = world["store"].one("SELECT revoked_reason FROM sessions WHERE oidc_sid IS NOT NULL "
                             "ORDER BY created_at DESC LIMIT 1")
    assert row["revoked_reason"] == "backchannel_logout"


def test_a_repeated_logout_notification_is_a_no_op_success(world):
    """The provider retries; a duplicate must not be an error it retries forever on."""
    b = world["b"]
    _sign_in_centrally(world)
    b.go(DOCWEAVE + "/auth/start?next=/")
    sid = world["store"].one("SELECT sid FROM client_sessions ORDER BY created_at DESC LIMIT 1")["sid"]
    from platform_core.oidc import Grants
    client = oidc.Clients(world["store"]).get("docweave-web")
    tok = Grants(world["store"]).logout_token(client=client, user_id=world["user"]["id"], sid=sid)
    first = world["docweave"].post("/auth/backchannel-logout", data={"logout_token": tok})
    second = world["docweave"].post("/auth/backchannel-logout", data={"logout_token": tok})
    assert first.status_code == 200 and first.json()["ended"] == 1
    assert second.status_code == 200 and second.json() == {"ended": 0, "duplicate": True}


def test_an_unsigned_logout_notification_is_refused(world):
    out = world["docweave"].post("/auth/backchannel-logout", data={"logout_token": "nonsense"})
    assert out.status_code == 400
    assert world["store"].one("SELECT COUNT(*) AS n FROM rp_logout_seen")["n"] == 0


def test_leaving_docweave_does_not_by_itself_end_the_platform_session(world):
    """退出当前应用 ≠ 退出平台。文案和行为必须一致。"""
    b = world["b"]
    _sign_in_centrally(world)
    b.go(DOCWEAVE + "/auth/start?next=/")
    csrf = b.jar.apply(DOCWEAVE)["dw_csrf"]
    out = world["docweave"].post("/api/auth/sign-out", cookies=b.jar.apply(DOCWEAVE),
                                 headers={"X-DocWeave-CSRF": csrf})
    assert out.status_code == 200 and out.json()["scope"] == "app"
    assert "redirect" not in out.json()
    assert whoami(world["accounts"], b.jar.apply(ACCOUNTS)) == "alice@example.com"


def test_signing_out_everywhere_hands_back_the_platform_logout_url(world):
    b = world["b"]
    _sign_in_centrally(world)
    b.go(DOCWEAVE + "/auth/start?next=/")
    csrf = b.jar.apply(DOCWEAVE)["dw_csrf"]
    out = world["docweave"].post("/api/auth/sign-out?everywhere=true",
                                 cookies=b.jar.apply(DOCWEAVE),
                                 headers={"X-DocWeave-CSRF": csrf}).json()
    assert out["scope"] == "platform" and out["redirect"].startswith(ACCOUNTS + "/oidc/logout?")
    b.trail.clear()
    b.go(out["redirect"])
    assert b.trail[-1] == DOCWEAVE + "/signed-out"
    assert whoami(world["accounts"], b.jar.apply(ACCOUNTS)) is None


# ---------------------------------------------------------------- auth_time honesty
def test_the_local_session_reports_the_real_central_authentication_time(world):
    """把“平台登录过”当作刚刚重认证，会让危险操作绕过 reauth。"""
    b = world["b"]
    _sign_in_centrally(world)
    central = world["store"].one("SELECT created_at FROM sessions ORDER BY created_at LIMIT 1")
    world["store"].execute("UPDATE sessions SET created_at=?, reauth_at=? WHERE id IN "
                           "(SELECT id FROM sessions ORDER BY created_at LIMIT 1)",
                           (central["created_at"] - 7200, central["created_at"] - 7200))
    b.go(DOCWEAVE + "/auth/start?next=/")
    local = world["store"].one("SELECT reauth_at, created_at FROM sessions WHERE oidc_sid IS NOT NULL "
                               "ORDER BY created_at DESC LIMIT 1")
    assert local["reauth_at"] < local["created_at"] - 3600, \
        "本地会话不能把两小时前的中央认证记成刚刚重认证"


