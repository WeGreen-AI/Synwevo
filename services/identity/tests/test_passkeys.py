"""Passkeys, driven by a software authenticator.

`SoftAuthenticator` below is **test equipment**, not a product capability: it is a WebAuthn
authenticator implemented in Python so the ceremonies can be exercised without a browser or a
hardware key. It signs with a real P-256 key over the real client data and authenticator data,
so the server's verification is doing genuine cryptography — but it proves the *server* side
only. That a real browser and a real authenticator interoperate with this is a separate claim
and is not made here.

The interesting tests are the ones where a valid-looking ceremony must still be refused: a
challenge replayed, a registration redeemed as a sign-in, an origin that is not ours.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import struct

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from platform_core.identity import Identity
from platform_identity.passkeys import Passkeys
from platform_core.sessions import CSRF_COOKIE, CSRF_HEADER
from platform_core.store import Store

PW = "a-sufficiently-long-passphrase"
RP_ID = "accounts.localhost"
ORIGIN = "http://accounts.localhost:8770"


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


class SoftAuthenticator:
    """A WebAuthn authenticator in Python. TEST EQUIPMENT — clearly not a real device.

    Implements exactly what the server verifies: a CTAP2 attestation object with `none`
    attestation for registration, and a signed assertion for authentication.
    """

    def __init__(self, rp_id: str = RP_ID, *, user_verified: bool = True):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.rp_id = rp_id
        self.user_verified = user_verified
        self.cred_id = b"soft-credential-" + hashlib.sha256(
            self.key.public_key().public_bytes_raw() if hasattr(self.key.public_key(), "public_bytes_raw")
            else b"x").digest()[:8]
        self.sign_count = 0

    # ---- COSE public key (EC2 / P-256 / ES256) -------------------------------
    def _cose_key(self) -> bytes:
        import cbor2
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1,
                            -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})

    def _auth_data(self, *, attested: bool) -> bytes:
        flags = 0x01                                   # user present
        if self.user_verified:
            flags |= 0x04                              # user verified
        if attested:
            flags |= 0x40                              # attested credential data included
        data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) \
            + struct.pack(">I", self.sign_count)
        if attested:
            cose = self._cose_key()
            data += b"\x00" * 16 + struct.pack(">H", len(self.cred_id)) + self.cred_id + cose
        return data

    def register(self, challenge: str, origin: str = ORIGIN) -> dict:
        import cbor2
        client_data = json.dumps({"type": "webauthn.create", "challenge": challenge,
                                  "origin": origin, "crossOrigin": False},
                                 separators=(",", ":")).encode()
        att = cbor2.dumps({"fmt": "none", "attStmt": {},
                           "authData": self._auth_data(attested=True)})
        return {"id": b64(self.cred_id), "rawId": b64(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64(client_data), "attestationObject": b64(att),
                             "transports": ["internal"]},
                "clientExtensionResults": {}}

    def authenticate(self, challenge: str, origin: str = ORIGIN) -> dict:
        self.sign_count += 1
        client_data = json.dumps({"type": "webauthn.get", "challenge": challenge,
                                  "origin": origin, "crossOrigin": False},
                                 separators=(",", ":")).encode()
        auth_data = self._auth_data(attested=False)
        sig = self.key.sign(auth_data + hashlib.sha256(client_data).digest(),
                            ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.cred_id), "rawId": b64(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64(client_data), "authenticatorData": b64(auth_data),
                             "signature": b64(sig), "userHandle": None},
                "clientExtensionResults": {}}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("DOCWEAVE_UI_AUTH", "session")
    monkeypatch.setenv("DOCWEAVE_COOKIE_SECURE", "0")
    monkeypatch.setenv("DOCWEAVE_ISSUER", ORIGIN)
    monkeypatch.setenv("DOCWEAVE_RP_ID", RP_ID)
    import platform_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    user = ident.create_user("alice@example.com", PW, display_name="Alice", status="active")
    c = TestClient(api)
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    return {"api": api, "c": c, "store": store, "ident": ident, "user": user,
            "pk": Passkeys(store)}


def post(c, path, body=None):
    return c.post(path, json=body if body is not None else {},
                  headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def reauth(c):
    assert post(c, "/api/auth/reauth", {"password": PW, "scope": "mfa"}).status_code == 200


def enrol(env, auth: SoftAuthenticator | None = None, name="我的手机"):
    auth = auth or SoftAuthenticator()
    c = env["c"]
    reauth(c)
    opts = post(c, "/api/auth/passkeys/register/begin").json()
    r = post(c, "/api/auth/passkeys/register/finish",
             {"credential": auth.register(opts["challenge"]), "name": name})
    assert r.status_code == 200, r.text
    return auth, r.json()


# ---------------------------------------------------------------- registration
def test_registering_a_passkey_and_signing_in_with_it(env):
    auth, made = enrol(env)
    assert made["name"] == "我的手机"

    fresh = TestClient(env["api"])
    opts = post(fresh, "/api/auth/passkeys/authenticate/begin").json()
    r = post(fresh, "/api/auth/login/passkey",
             {"credential": auth.authenticate(opts["challenge"])})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["session"]["amr"] == "passkey_uv", "认证器这次真的验证了本人，要记下来"
    assert fresh.get("/api/auth/me").status_code == 200


def test_possession_only_is_recorded_differently_from_verified(env):
    """"账户有 Passkey" 和 "这次登录验证了本人" 是两件事。"""
    auth, _ = enrol(env, SoftAuthenticator(user_verified=False))
    fresh = TestClient(env["api"])
    opts = post(fresh, "/api/auth/passkeys/authenticate/begin").json()
    body = post(fresh, "/api/auth/login/passkey",
                {"credential": auth.authenticate(opts["challenge"])}).json()
    assert body["session"]["amr"] == "passkey", "没有用户验证就不能记成 passkey_uv"


def test_registering_needs_recent_reauth(env):
    r = post(env["c"], "/api/auth/passkeys/register/begin")
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"


def test_registering_needs_a_session(env):
    assert post(TestClient(env["api"]), "/api/auth/passkeys/register/begin").status_code == 401


def test_the_same_authenticator_cannot_be_registered_twice(env):
    auth, _ = enrol(env)
    c = env["c"]
    reauth(c)
    opts = post(c, "/api/auth/passkeys/register/begin").json()
    r = post(c, "/api/auth/passkeys/register/finish",
             {"credential": auth.register(opts["challenge"])})
    assert r.status_code == 409 and r.json()["error"] == "already_registered"


# ---------------------------------------------------------------- challenge integrity
def test_a_challenge_is_single_use(env):
    auth, _ = enrol(env)
    fresh = TestClient(env["api"])
    opts = post(fresh, "/api/auth/passkeys/authenticate/begin").json()
    assert post(fresh, "/api/auth/login/passkey",
                {"credential": auth.authenticate(opts["challenge"])}).status_code == 200
    again = post(TestClient(env["api"]), "/api/auth/login/passkey",
                 {"credential": auth.authenticate(opts["challenge"])})
    assert again.status_code == 400, "重放同一个挑战必须失败"


def test_a_registration_challenge_cannot_be_redeemed_as_a_sign_in(env):
    """Two ceremonies, two purposes; one must never satisfy the other."""
    auth, _ = enrol(env)
    c = env["c"]
    reauth(c)
    reg_opts = post(c, "/api/auth/passkeys/register/begin").json()
    r = post(TestClient(env["api"]), "/api/auth/login/passkey",
             {"credential": auth.authenticate(reg_opts["challenge"])})
    assert r.status_code == 400 and "挑战" in r.text


def test_a_made_up_challenge_is_refused(env):
    auth, _ = enrol(env)
    r = post(TestClient(env["api"]), "/api/auth/login/passkey",
             {"credential": auth.authenticate(b64(b"not-a-challenge-we-issued"))})
    assert r.status_code == 400


def test_an_assertion_from_another_origin_is_refused(env):
    """The origin check is what makes a passkey phishing-resistant. Without it a copy of the
    login page on another domain could collect a usable assertion."""
    auth, _ = enrol(env)
    fresh = TestClient(env["api"])
    opts = post(fresh, "/api/auth/passkeys/authenticate/begin").json()
    r = post(fresh, "/api/auth/login/passkey",
             {"credential": auth.authenticate(opts["challenge"], origin="http://evil.example.com")})
    assert r.status_code == 401 and r.json()["error"] == "bad_assertion"


def test_a_credential_for_another_rp_id_is_refused(env):
    """A credential is bound to a domain; one minted for a different RP ID is not ours."""
    other = SoftAuthenticator(rp_id="evil.example.com")
    c = env["c"]
    reauth(c)
    opts = post(c, "/api/auth/passkeys/register/begin").json()
    r = post(c, "/api/auth/passkeys/register/finish", {"credential": other.register(opts["challenge"])})
    assert r.status_code == 400


def test_an_unknown_credential_is_refused(env):
    stranger = SoftAuthenticator()
    fresh = TestClient(env["api"])
    opts = post(fresh, "/api/auth/passkeys/authenticate/begin").json()
    r = post(fresh, "/api/auth/login/passkey",
             {"credential": stranger.authenticate(opts["challenge"])})
    assert r.status_code == 401 and r.json()["error"] == "unknown_passkey"


def test_a_forged_signature_is_refused(env):
    auth, _ = enrol(env)
    fresh = TestClient(env["api"])
    opts = post(fresh, "/api/auth/passkeys/authenticate/begin").json()
    cred = auth.authenticate(opts["challenge"])
    cred["response"]["signature"] = b64(b"\x30\x44" + b"\x00" * 68)
    assert post(fresh, "/api/auth/login/passkey", {"credential": cred}).status_code == 401


# ---------------------------------------------------------------- account state
def test_a_suspended_account_cannot_sign_in_with_a_passkey(env):
    auth, _ = enrol(env)
    env["ident"].set_status(env["user"]["id"], "suspended")
    fresh = TestClient(env["api"])
    opts = post(fresh, "/api/auth/passkeys/authenticate/begin").json()
    r = post(fresh, "/api/auth/login/passkey",
             {"credential": auth.authenticate(opts["challenge"])})
    assert r.status_code == 401
    assert "不正确" in r.text, "失败形状与其他登录失败一致，不泄露账户状态"


def test_a_removed_passkey_stops_working(env):
    auth, made = enrol(env)
    c = env["c"]
    reauth(c)
    assert c.request("DELETE", f"/api/auth/passkeys/{made['id']}",
                     headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""}).status_code == 200
    fresh = TestClient(env["api"])
    opts = post(fresh, "/api/auth/passkeys/authenticate/begin").json()
    r = post(fresh, "/api/auth/login/passkey", {"credential": auth.authenticate(opts["challenge"])})
    assert r.status_code == 401 and r.json()["error"] == "unknown_passkey"


def test_removing_the_only_way_in_is_refused(env):
    """Someone with a passkey and no password would lock themselves out permanently, and the
    dialog they clicked through would be the only record of how."""
    _, made = enrol(env)
    store, c = env["store"], env["c"]
    store.execute("DELETE FROM user_passwords WHERE user_id=?", (env["user"]["id"],))
    reauth_row = store.one("SELECT id FROM sessions WHERE user_id=?", (env["user"]["id"],))
    store.execute("UPDATE sessions SET reauth_at=?, reauth_scope='mfa' WHERE id=?",
                  (__import__("time").time(), reauth_row["id"]))
    r = c.request("DELETE", f"/api/auth/passkeys/{made['id']}",
                  headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})
    assert r.status_code == 409 and r.json()["error"] == "last_factor"


def test_listing_and_renaming(env):
    _, made = enrol(env)
    items = env["c"].get("/api/auth/passkeys").json()
    assert len(items["items"]) == 1 and items["items"][0]["name"] == "我的手机"
    assert items["rp_id"] == RP_ID and items["items"][0]["usable_here"] is True
    env["c"].patch(f"/api/auth/passkeys/{made['id']}", json={"name": "工作手机"},
                   headers={CSRF_HEADER: env["c"].cookies.get(CSRF_COOKIE) or ""})
    assert env["c"].get("/api/auth/passkeys").json()["items"][0]["name"] == "工作手机"


def test_a_credential_from_another_domain_is_shown_as_unusable(env):
    """A domain change does not migrate passkeys. Said plainly rather than left as credentials
    the browser silently stops offering."""
    enrol(env)
    env["store"].execute("UPDATE passkeys SET rp_id='old.example.com'")
    item = env["c"].get("/api/auth/passkeys").json()["items"][0]
    assert item["usable_here"] is False and item["rp_id"] == "old.example.com"


def test_no_private_key_material_is_ever_stored(env):
    enrol(env)
    row = env["store"].one("SELECT * FROM passkeys")
    blob = " ".join(str(v) for v in row.values())
    assert "PRIVATE" not in blob.upper() and "BEGIN" not in blob.upper()
    # what is stored is a COSE public key, which decodes to a public EC point and nothing else
    import cbor2
    from platform_identity.passkeys import _b64d
    cose = cbor2.loads(_b64d(row["public_key"]))
    assert cose[1] == 2 and cose[3] == -7 and set(cose) == {1, 3, -1, -2, -3}


