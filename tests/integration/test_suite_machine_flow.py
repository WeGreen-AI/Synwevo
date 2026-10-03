"""The business applications' machine API through the real sign-in, consent and token endpoints.

`apps/suite/tests/test_machine_api.py` covers what the API allows, with tokens minted directly.
What only the composed deployment can show is here: a person signs in on the account service, is
asked for exactly the scopes the client wants, the token endpoint issues a token for this API's
audience, that token is useless on the two other machine APIs, the SDK keeps the grant alive
through refresh rotation, and withdrawing consent on the account page ends it everywhere.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from synwevo_core import oidc
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store
from services.identity.tests.oauth_consent_helper import through_consent

PW = "a-sufficiently-long-passphrase"
REDIRECT = "https://crm.example.test/callback"
SUITE = "urn:synwevo:platform:suite"
BASE = "http://testserver"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://accounts.localhost:8770")
    monkeypatch.setenv("SYNWEVO_SUITE_APPS", "all")
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    alice = ident.create_user("alice@example.com", PW, status="active")
    ws = ident.workspaces_for(alice["id"])[0]["id"]
    oidc.Clients(store).register(
        client_id="crm-sync", app_id="platform", name="Fixture CRM sync", redirect_uris=[REDIRECT],
        scopes="openid offline_access tables.read tables.write forms.read")
    return {"api": api, "store": store, "ident": ident, "alice": alice, "ws": ws}


def authorize(env, scope: str) -> tuple[TestClient, dict, list[str] | None]:
    """Sign in, read what the consent page shows (None when the stored consent already covers the
    request and no page is shown), approve, exchange the code."""
    c = TestClient(env["api"])
    assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    verifier = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": "crm-sync", "redirect_uri": REDIRECT, "response_type": "code", "scope": scope,
         "state": "s", "code_challenge": challenge, "code_challenge_method": "S256", "workspace_id": env["ws"]}
    parked = c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False)
    assert parked.status_code == 303, parked.text
    shown = None
    if parked.headers["location"].startswith("/oauth/consent"):
        rid = urllib.parse.parse_qs(urllib.parse.urlparse(parked.headers["location"]).query)["request"][0]
        shown = c.get(f"/api/auth/oauth-consent/{rid}").json()["scopes"]
    back = through_consent(c, parked)
    code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(back.headers["location"]).query))["code"]
    issued = c.post("/oidc/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                         "client_id": "crm-sync", "code_verifier": verifier, "resource": SUITE})
    assert issued.status_code == 200, issued.text
    return c, issued.json(), shown


def test_consent_token_audience_sdk_rotation_and_withdrawal(env):
    from synwevo_docs_sdk import SuiteClient, UserToken
    from synwevo_docs_sdk.errors import AuthenticationError

    browser, tokens, shown = authorize(env, "openid offline_access tables.read tables.write")
    # the person was asked for exactly what the client wanted, by name
    assert set(shown) >= {"tables.read", "tables.write", "offline_access"} and "forms.read" not in shown
    assert set(tokens["scope"].split()) == {"openid", "offline_access", "tables.read", "tables.write"}
    row = env["store"].one("SELECT audience, workspace_id, user_id FROM oauth_tokens ORDER BY issued_at DESC LIMIT 1")
    assert row == {"audience": SUITE, "workspace_id": env["ws"], "user_id": env["alice"]["id"]}

    machine = TestClient(env["api"], base_url=BASE)                      # no cookie anywhere in it
    auth = {"Authorization": "Bearer " + tokens["access_token"]}
    who = machine.get("/api/suite/v1/whoami", headers=auth).json()
    assert (who["user_id"], who["workspace_id"], who["scopes"]) == (env["alice"]["id"], env["ws"],
                                                                     ["tables.read", "tables.write"])
    # this API's token opens neither of the others
    for other in ("/api/v1/whoami", "/api/workspace/v1/whoami"):
        refused = machine.get(other, headers=auth)
        assert refused.status_code in (401, 403), (other, refused.text)
        assert refused.json()["error"]["code"] in ("invalid_audience", "wrong_audience", "wrong_credential"), other
    # and a scope the client is registered for but the person was not asked for is not held
    listed = machine.post("/api/suite/v1/ops/forms.form.list", json={"input": {}}, headers=auth)
    assert listed.status_code == 403 and listed.json()["error"]["code"] == "insufficient_scope"

    # the SDK, starting from the refresh token alone: one exchange, the successor handed over
    stored: list[dict] = []
    credentials = UserToken(refresh_token=tokens["refresh_token"], token_url=BASE + "/oidc/token",
                            client_id="crm-sync", resource=SUITE, on_rotate=stored.append)
    with SuiteClient(BASE, credentials, http=machine, sleep=lambda _: None) as suite:
        table = suite.invoke("tables.table.create", {"name": "Synced accounts"}, idempotency_key="accounts-table")
        again = suite.invoke("tables.table.create", {"name": "Synced accounts"}, idempotency_key="accounts-table")
        assert (table.replayed, again.replayed, again["output"]["id"]) == (False, True, table["output"]["id"])
        assert len(stored) == 1 and stored[0]["refresh_token"] != tokens["refresh_token"]
        assert set(stored[0]["scope"].split()) >= {"tables.read", "tables.write"}
        assert env["store"].one("SELECT COUNT(*) AS n FROM tb_tables")["n"] == 1

        # the spent refresh token is presented again (a leaked copy): the whole grant ends
        replay = machine.post("/oidc/token", data={"grant_type": "refresh_token", "client_id": "crm-sync",
                                                   "refresh_token": tokens["refresh_token"], "resource": SUITE})
        assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
        with pytest.raises(AuthenticationError):
            suite.invoke("tables.table.list")
    assert machine.get("/api/suite/v1/whoami", headers=auth).status_code == 401

    # a new, narrower grant: what she already consented to covers it, so she is not asked again.
    # Then she withdraws the client under "Connected apps".
    browser, tokens, asked = authorize(env, "openid offline_access tables.read")
    assert asked is None and set(tokens["scope"].split()) == {"openid", "offline_access", "tables.read"}
    auth = {"Authorization": "Bearer " + tokens["access_token"]}
    assert machine.post("/api/suite/v1/ops/tables.table.list", json={"input": {}}, headers=auth).status_code == 200
    apps = browser.get("/api/auth/connected-apps").json()["items"]
    mine = next(a for a in apps if a["client"]["id"] == "crm-sync")
    assert "tables.read" in mine["scopes"] and mine["workspace"]["id"] == env["ws"]
    gone = browser.post(f"/api/auth/connected-apps/{mine['id']}/revoke",
                        headers={CSRF_HEADER: browser.cookies.get(CSRF_COOKIE) or ""})
    assert gone.status_code == 200, gone.text
    refused = machine.post("/api/suite/v1/ops/tables.table.list", json={"input": {}}, headers=auth)
    assert refused.status_code == 401 and refused.json()["error"]["code"] == "unauthorized"
    # and the next request is asked again, from nothing
    _, _, asked = authorize(env, "openid tables.read")
    assert asked is not None and "tables.read" in asked
