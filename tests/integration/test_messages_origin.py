"""Synwevo Message on its own origin (CI-66), across the roles that have to agree about it.

Two composed ASGI apps — the account service and the messages application — with a host-scoped
cookie jar, the way `test_sso_chain.py` drives the account service and Docs. What is checked here
is cross-module by nature: the account origin handing its former `/messages` pages over, the
legacy API being served only while the application is absent, every origin's `/api/auth/state`
naming the messages address, a direct message's inbox item pointing at that origin, and the
platform-wide sign-out started from it ending the central session.
"""
from __future__ import annotations

import importlib
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core import oidc
from synwevo_core.apps import Apps, MESSAGES_APP_ID, bootstrap_messages_app, messages_launch_url
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"
ACCOUNTS = "http://accounts.localhost:8770"
MESSAGES = "http://message.localhost:8768"
PORTAL = "http://portal.localhost:8769"
DOCS = "http://docweave.localhost:8765"
CSRF = "X-Synwevo-CSRF"


class Jar:
    """Host-scoped on purpose: different ports on one host do not isolate cookies."""

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
    def __init__(self, apps: dict[str, Svc]):
        self.apps = apps
        self.jar = Jar()
        self.trail: list[str] = []

    def _client(self, url: str) -> Svc:
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
    """One database, the account service and the messages application, two origins. The
    application is **not** registered yet: tests register it when they want the deployed state."""
    data = str(tmp_path / "d")
    for k, v in {"SYNWEVO_DATA_DIR": data, "SYNWEVO_UI_AUTH": "session", "SYNWEVO_COOKIE_SECURE": "0",
                 "SYNWEVO_ISSUER": ACCOUNTS, "SYNWEVO_APP_URL": MESSAGES, "SYNWEVO_PORTAL_URL": PORTAL,
                 "SYNWEVO_OIDC_CLIENT_ID": "messages-web", "SYNWEVO_OIDC_APP_ID": "messages"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    import synwevo_core.oidc as oidc_mod
    later: dict = {}

    def _deliver(url, token, timeout=3.0):
        r = later["messages"].post(url[len(MESSAGES):], data={"logout_token": token})
        return (200 <= r.status_code < 300, None if r.status_code < 300 else f"HTTP {r.status_code}")

    monkeypatch.setattr(oidc_mod, "deliver_logout", _deliver)
    accounts = Svc(app_mod.create_app(data, role="accounts", start_jobs=False), ACCOUNTS)
    import synwevo_auth_client.rp as rp_mod
    monkeypatch.setattr(rp_mod.Rp, "http", property(lambda self: accounts.c))
    # Built as the messages role but registered by the test, not at start-up: the account role
    # above must first be seen *without* the application (its legacy fallback), then with it.
    messages = Svc(app_mod.create_app(data, role="messages", start_jobs=False), MESSAGES)
    later["messages"] = messages

    store = Store(tmp_path / "d")
    ident = Identity(store)
    apps = Apps(store, ident)
    store.execute("DELETE FROM apps WHERE id=?", (MESSAGES_APP_ID,))      # the state before the first start
    alice = ident.create_user("alice@example.com", PW, display_name="Alice", status="active")
    bob = ident.create_user("bob@example.com", PW, display_name="Bob", status="active")
    from synwevo_core.organizations import Organizations
    org = Organizations(store, ident).create(alice["id"], "Chat Org", "Chat Team", "fixture-idem-chat-org-0001")["organization"]["id"]
    store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                  (org, bob["id"], alice["id"]))
    oidc.Clients(store).register(
        client_id="messages-web", app_id="messages", name="Synwevo Message",
        redirect_uris=[MESSAGES + "/auth/callback"],
        post_logout_redirect_uris=[MESSAGES + "/signed-out"],
        backchannel_logout_uri=MESSAGES + "/auth/backchannel-logout")
    b = Browser({ACCOUNTS: accounts, MESSAGES: messages})
    return {"b": b, "store": store, "ident": ident, "apps": apps, "alice": alice, "bob": bob, "org": org,
            "accounts": accounts, "messages": messages, "data": data, "app_mod": app_mod}


def whoami(svc, cookies) -> str | None:
    r = svc.get("/api/auth/me", cookies=cookies)
    return r.json()["email"] if r.status_code == 200 else None


def _sign_in_centrally(world, email="alice@example.com"):
    r = world["accounts"].post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    world["b"].jar.absorb(ACCOUNTS, r)
    return r


def _register(world):
    bootstrap_messages_app(world["store"], world["ident"], launch_url=MESSAGES + "/")
    assert messages_launch_url(world["store"]) == MESSAGES


# ---------------------------------------------------------------- the account origin's former pages
def test_the_account_origin_serves_the_legacy_api_only_until_the_application_registers_itself(world):
    """Before the messages process ever started (no registry row) the account origin still
    answers its former conversation routes; the moment the application is registered they
    answer 410 naming the new address and the pages hand over — no restart, no second write
    source."""
    b = world["b"]
    _sign_in_centrally(world)
    ck = b.jar.apply(ACCOUNTS)
    r = world["accounts"].get("/api/auth/messaging/conversations", cookies=ck)
    assert r.status_code == 200 and r.json()["items"] == [], r.text[:200]
    page = world["accounts"].get("/messages/cv_abc", cookies=ck)
    assert page.status_code != 303, "without the application there is nothing to hand over to"
    assert world["accounts"].get("/api/auth/state").json()["messages_url"] is None
    # …and the account origin serves the messages shell itself under its former paths (and the
    # thread address the shell uses), so a deployment without the application — or the window
    # before its host exists — keeps its conversations rather than a "page not found".
    from synwevo_messages.app import web_dist as messages_dist
    if messages_dist().joinpath("index.html").is_file():
        for path in ("/messages", "/messages/cv_abc", "/c/cv_abc"):
            page = world["accounts"].get(path, cookies=ck)
            assert page.status_code == 200 and "messages-assets/" in page.text and "Synwevo Message" in page.text, (path, page.status_code)
        assert "messages-assets/" not in world["accounts"].get("/account", cookies=ck).text, "the account pages are still the account shell"

    _register(world)
    r = world["accounts"].get("/api/auth/messaging/conversations", cookies=ck)
    assert r.status_code == 410 and r.json()["error"] == "moved" and r.json()["detail"]["messages_url"] == MESSAGES, r.text[:200]
    assert MESSAGES in r.json()["message"]
    # the shared badge count is not a conversation route and keeps answering here
    assert world["accounts"].get("/api/auth/messaging/unread-count", cookies=ck).status_code == 200
    # the moderation half stays: a member gets 403 (permission), not 410
    assert world["accounts"].get(f"/api/auth/messaging/reports?organization_id={world['org']}", cookies=ck).status_code in (200, 403)
    for path, nxt in (("/messages/cv_abc?lang=en", "/c/cv_abc"), ("/messages", "/"), ("/c/cv_abc", "/c/cv_abc")):
        page = world["accounts"].get(path, cookies=ck)
        assert page.status_code == 303, (path, page.status_code)
        target = urllib.parse.urlsplit(page.headers["location"])
        assert f"{target.scheme}://{target.netloc}{target.path}" == MESSAGES + "/auth/start", page.headers["location"]
        q = dict(urllib.parse.parse_qsl(target.query))
        assert q["next"] == nxt and (q.get("lang") == "en") == path.endswith("lang=en")
    # nothing from the path can steer the hand-over: an id that is not a conversation id lands on the list
    page = world["accounts"].get("/messages/not-a-conversation-id", cookies=ck)
    assert page.status_code == 303
    assert dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(page.headers["location"]).query))["next"] == "/"
    # `/api/messaging/…` on the account origin is refused by role, naming the messages origin
    r = world["accounts"].get("/api/messaging/conversations", cookies=ck)
    assert r.status_code == 404 and r.json()["error"] == "not_in_this_role" and MESSAGES in r.json()["message"]


# ---------------------------------------------------------------- every origin knows the address
def test_every_origin_exposes_the_messages_address_in_its_auth_state(world, tmp_path, monkeypatch):
    """`/api/auth/state` is what every shell's 消息 link reads: null before the application
    exists, the registry's launch URL after, on the account service, the portal, Docs and the
    application itself."""
    app_mod = world["app_mod"]
    assert world["accounts"].get("/api/auth/state").json()["messages_url"] is None
    assert world["messages"].get("/api/auth/state").json()["messages_url"] is None
    _register(world)
    portal = Svc(app_mod.create_app(world["data"], role="portal", start_jobs=False), PORTAL)
    docs = Svc(app_mod.create_app(world["data"], role="app", start_jobs=False), DOCS)
    for svc in (world["accounts"], world["messages"], portal, docs):
        st = svc.get("/api/auth/state").json()
        assert st["messages_url"] == MESSAGES, (svc.origin, st)
    st = world["messages"].get("/api/auth/state").json()
    assert st["role"] == "messages" and st["password_login"] is False and st["app_id"] == "messages"
    assert st["account_url"] == ACCOUNTS + "/account" and st["portal_url"] == PORTAL


# ---------------------------------------------------------------- the sign-in chain and a DM's inbox item
def test_signing_in_at_the_messages_origin_lands_on_the_thread_and_a_dm_points_there(world):
    """The deep link survives the round trip through the account service, a session of this
    origin (not the central one) is minted, and the message a person then sends produces an
    inbox item whose target names origin `messages` and resolves to this origin's thread."""
    _register(world)
    b = world["b"]
    _sign_in_centrally(world)
    resp = b.go(MESSAGES + "/auth/start?next=/c/cv_deep")
    assert b.trail[-1] == MESSAGES + "/c/cv_deep", b.trail
    assert any(u.startswith(ACCOUNTS + "/oidc/authorize") for u in b.trail), b.trail
    ms, acc = b.jar.by_host["message.localhost"], b.jar.by_host["accounts.localhost"]
    assert ms["dw_session"] and acc["dw_session"] and ms["dw_session"] != acc["dw_session"], "两个来源必须是两个会话"
    assert whoami(world["messages"], b.jar.apply(MESSAGES)) == "alice@example.com"
    # someone with no workspace at all still gets in: the application is organization-scoped
    ck = b.jar.apply(MESSAGES)
    h = {CSRF: ck["dw_csrf"]}
    dm = world["messages"].post("/api/messaging/dm", cookies=ck, headers=h,
                                json={"organization_id": world["org"], "user_id": world["bob"]["id"]})
    assert dm.status_code == 201, dm.text[:200]
    cid = dm.json()["id"]
    sent = world["messages"].post(f"/api/messaging/conversations/{cid}/messages", cookies=ck, headers=h,
                                  json={"body": "hello bob", "client_id": "c1"})
    assert sent.status_code == 201, sent.text[:200]
    from synwevo_core.notifications import Notifier
    import json
    item = Notifier(world["store"]).list(world["bob"]["id"])["items"][0]
    assert item["kind"] == "conversation.dm" and item["href"] == f"{MESSAGES}/c/{cid}", item
    raw = world["store"].one("SELECT target FROM notifications WHERE id=?", (item["id"],))["target"]
    assert json.loads(raw) == {"origin": "messages", "path": f"/c/{cid}"}
    # the account origin refuses this application's API and Docs' data alike
    assert world["messages"].get("/api/projects", cookies=ck).status_code == 404
    assert world["messages"].get("/api/auth/passkeys", cookies=ck).status_code == 404


# ---------------------------------------------------------------- platform-wide sign-out
def test_signing_out_everywhere_from_the_messages_origin_ends_the_central_session(world):
    """"退出登录" on the messages origin means leaving Synwevo: the application ends its own
    session, hands back the account service's logout URL, and following it ends the central
    session and returns to this origin's /signed-out (CI-60, on the new origin)."""
    _register(world)
    b = world["b"]
    _sign_in_centrally(world)
    b.go(MESSAGES + "/auth/start?next=/")
    ck = b.jar.apply(MESSAGES)
    assert whoami(world["messages"], ck) == "alice@example.com"
    out = world["messages"].post("/api/auth/sign-out?everywhere=true", cookies=ck, headers={CSRF: ck["dw_csrf"]}).json()
    assert out["scope"] == "platform" and out["redirect"].startswith(ACCOUNTS + "/oidc/logout?"), out
    b.trail.clear()
    b.go(out["redirect"])
    assert b.trail[-1] == MESSAGES + "/signed-out", b.trail
    assert whoami(world["accounts"], b.jar.apply(ACCOUNTS)) is None
    assert whoami(world["messages"], ck) is None
    cs = world["store"].one("SELECT logged_out_at, logout_error FROM client_sessions WHERE client_id='messages-web' ORDER BY created_at DESC LIMIT 1")
    assert cs["logged_out_at"] and not cs["logout_error"], "后通道通知仍然要送达"


# ---------------------------------------------------------------- the registry entry
def test_the_registered_application_is_open_to_every_live_account_and_listed_for_the_portal(world):
    """Organization-scoped: no workspace_apps row and no entitlement is needed; the portal's
    card and the account page's return targets get it with a sign-in-start URL and no "None"
    workspace hint for someone who has no workspace."""
    _register(world)
    apps, ident = world["apps"], world["ident"]
    loner = ident.create_user("loner@example.com", PW, status="active")
    world["store"].execute("DELETE FROM memberships WHERE user_id=?", (loner["id"],))   # an account with no workspace at all
    ok, wid, reason = apps.check_access(loner["id"], MESSAGES_APP_ID)
    assert (ok, wid, reason) == (True, None, "ok")
    listed = {a["id"]: a for a in apps.apps_for(loner["id"])}
    assert MESSAGES_APP_ID in listed
    url = urllib.parse.urlsplit(listed[MESSAGES_APP_ID]["launch_url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == MESSAGES + "/auth/start"
    assert "workspace_id" not in dict(urllib.parse.parse_qsl(url.query))
    ws = ident.workspaces_for(world["alice"]["id"])[0]["id"]
    assert apps.check_access(world["alice"]["id"], MESSAGES_APP_ID, ws) == (True, ws, "ok")
    assert apps.check_access(world["alice"]["id"], MESSAGES_APP_ID, "w_nope")[0] is False
    # an operator can still turn the whole application off
    world["store"].execute("UPDATE apps SET status='disabled' WHERE id=?", (MESSAGES_APP_ID,))
    assert apps.check_access(loner["id"], MESSAGES_APP_ID)[0] is False
    assert messages_launch_url(world["store"]) is None and world["accounts"].get("/api/auth/state").json()["messages_url"] is None


def test_disabling_the_messages_app_does_not_hand_conversations_back_to_the_account_origin(world):
    """Admin defect D7: the account origin's legacy fallback was closed only while the registry
    said Synwevo Message was *active*. `app disable messages` reopened it — a second writer with
    its own fan-out, serving conversations the disable was meant to stop."""
    _register(world)
    b = world["b"]
    _sign_in_centrally(world)
    b.go(MESSAGES + "/auth/start?next=/")
    mck, ack = b.jar.apply(MESSAGES), b.jar.apply(ACCOUNTS)
    dm = world["messages"].post("/api/messaging/dm", cookies=mck, headers={CSRF: mck["dw_csrf"]},
                                json={"organization_id": world["org"], "user_id": world["bob"]["id"]})
    assert dm.status_code == 201, dm.text[:200]
    cid = dm.json()["id"]
    world["store"].execute("UPDATE apps SET status='disabled' WHERE id='messages'")   # what `app disable messages` writes
    send = world["accounts"].post(f"/api/auth/messaging/conversations/{cid}/messages", cookies=ack,
                                  headers={CSRF: ack["dw_csrf"]}, json={"body": "after disable", "client_id": "a1"})
    assert send.status_code == 410 and send.json()["error"] == "moved"
    assert world["accounts"].get("/api/auth/messaging/conversations", cookies=ack).status_code == 410
    page = world["accounts"].get(f"/messages/{cid}", cookies=ack, follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"].startswith(MESSAGES + "/auth/start")
    assert world["store"].one("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?", (cid,))["n"] == 0
    # pointing people at the application still follows its status
    assert world["accounts"].get("/api/auth/state").json()["messages_url"] is None
