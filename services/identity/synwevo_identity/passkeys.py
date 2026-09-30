"""Passkeys (WebAuthn), managed by the central account service.

The library (`webauthn` 3.0.0, BSD-3-Clause, from Duo) owns the cryptography and the response
parsing. What lives here is the part that is ours: which relying party we are, which origins we
trust, which challenge belongs to which ceremony, and what a successful ceremony is allowed to
prove.

Four things are deliberate.

**The RP ID is configured, never derived from the request.** It is the domain the credential is
bound to; deriving it from `Host` would let anyone who can reach the service register
credentials under a name of their choosing. It is also why a domain change does not migrate:
credentials registered under `accounts.localhost` simply do not exist under a production
hostname, and they have to be registered again. Storing `rp_id` per credential makes that
visible rather than a surprise at sign-in.

**Registration and authentication challenges never satisfy each other.** They are separate rows
with a `purpose`, so a registration ceremony cannot be replayed as a sign-in.

**Owning a passkey is not the same as having used one.** `verify()` reports whether user
verification actually happened in *this* ceremony. A policy that reads "the account has a
passkey" and concludes the sign-in was phishing-resistant is reading the wrong fact.

**A sign count that goes backwards is reported, not ignored.** It is the one cloning signal the
protocol offers. Many authenticators — including every synced passkey — always report zero, so
this cannot be a hard failure; it is surfaced and recorded instead of silently dropped.
"""
from __future__ import annotations

import json
import os
import secrets
import time

from synwevo_core import env
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store, new_id, now

CHALLENGE_TTL_S = 300
MAX_PASSKEYS = 10


def rp_id() -> str:
    """The domain credentials are bound to. Configured, never taken from the request."""
    configured = (env.get("RP_ID") or "").strip()
    if configured:
        return configured
    from urllib.parse import urlparse
    host = urlparse(_issuer()).hostname or "localhost"
    return host


def rp_name() -> str:
    from synwevo_core.config import PLATFORM_NAME
    return env.get("PLATFORM_NAME") or PLATFORM_NAME


def allowed_origins() -> list[str]:
    """Origins a ceremony may come from.

    An exact list. WebAuthn checks the origin the browser reports against what the server
    expects, and accepting any origin under the RP ID would give that check nothing to do.
    """
    extra = [o.strip().rstrip("/") for o in
             (env.get("RP_ORIGINS") or "").split(",") if o.strip()]
    return [_issuer()] + extra


def _issuer() -> str:
    return (env.get("ISSUER") or "http://accounts.localhost:8770").rstrip("/")


class Passkeys:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------ listing
    def list_for(self, user_id: str) -> list[dict]:
        rows = self.store.all(
            "SELECT id,name,created_at,last_used_at,rp_id,backed_up,uv_capable,transports "
            "FROM passkeys WHERE user_id=? AND deleted_at IS NULL ORDER BY created_at",
            (user_id,))
        for r in rows:
            r["transports"] = json.loads(r["transports"] or "[]")
            r["backed_up"] = bool(r["backed_up"])
            r["uv_capable"] = bool(r["uv_capable"])
            # Whether this credential still matches the service's current domain. A mismatch is
            # not corruption: it is a credential registered before the domain changed, and it
            # will never be offered by the browser again.
            r["usable_here"] = r["rp_id"] == rp_id()
        return rows

    def count(self, user_id: str) -> int:
        return self.store.one("SELECT COUNT(*) AS n FROM passkeys WHERE user_id=? AND deleted_at IS NULL",
                              (user_id,))["n"]

    def rename(self, user_id: str, pid: str, name: str) -> None:
        n = self.store.execute("UPDATE passkeys SET name=? WHERE id=? AND user_id=? AND deleted_at IS NULL",
                               (name.strip()[:60] or "安全密钥", pid, user_id))
        if not n:
            raise ServiceError(404, "no_passkey", "找不到该安全密钥")

    def remove(self, user_id: str, pid: str) -> None:
        from synwevo_core.platform_access import PlatformAccess
        with self.store.tx():
            access = PlatformAccess(self.store)
            was_usable_super = access.is_usable_super_admin(user_id)
            n = self.store.execute("UPDATE passkeys SET deleted_at=? WHERE id=? AND user_id=? AND deleted_at IS NULL",
                                   (now(), pid, user_id))
            if not n:
                raise ServiceError(404, "no_passkey", "找不到该安全密钥")
            if was_usable_super and not access.usable_super_admins():
                raise ServiceError(409, "last_super_admin", "不能移除最后一名可用最高管理员的身份确认方式")
            password = self.store.one("SELECT 1 FROM user_passwords WHERE user_id=?", (user_id,))
            # A key on another RP cannot sign in here. A platform holder's passwordless
            # sign-in already requires UV; a possession-only spare cannot prevent lockout.
            uv = " AND uv_capable=1" if access.effective_permissions(user_id) else ""
            remaining = self.store.one("SELECT 1 FROM passkeys WHERE user_id=? AND deleted_at IS NULL "
                                       "AND rp_id=?" + uv, (user_id, rp_id()))
            if not password and not remaining:
                raise ServiceError(409, "last_factor",
                                   "这是你唯一的登录方式，删除后将无法登录。请先设置口令或再登记一个安全密钥。")

    # ------------------------------------------------------------------ registration
    def begin_registration(self, user: dict) -> dict:
        import webauthn
        from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                              PublicKeyCredentialDescriptor,
                                              ResidentKeyRequirement, UserVerificationRequirement)
        if self.count(user["id"]) >= MAX_PASSKEYS:
            raise ServiceError(409, "too_many", f"最多只能登记 {MAX_PASSKEYS} 个安全密钥", {"limit": MAX_PASSKEYS, "kind": "passkey"})

        existing = [PublicKeyCredentialDescriptor(id=_b64d(r["credential_id"]))
                    for r in self.store.all(
                        "SELECT credential_id FROM passkeys WHERE user_id=? AND deleted_at IS NULL",
                        (user["id"],))]
        opts = webauthn.generate_registration_options(
            rp_id=rp_id(), rp_name=rp_name(),
            user_id=user["id"].encode("utf-8"),
            user_name=user["email"],
            user_display_name=user.get("display_name") or user["email"],
            # Excluding what is already registered stops the same authenticator being enrolled
            # twice, which presents later as a key that mysteriously cannot be removed.
            exclude_credentials=existing,
            # REQUIRED, not PREFERRED: sign-in here is always usernameless (no credential list is
            # ever sent, so an address cannot be probed), and a non-discoverable credential can
            # never be offered in that ceremony. Preferring one registered keys that were listed
            # on the account page and then silently never usable. An authenticator that cannot
            # store a discoverable credential now fails at registration, where it can be seen.
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.PREFERRED),
        )
        self._store_challenge(user["id"], "register", opts.challenge)
        return json.loads(webauthn.options_to_json(opts))

    def finish_registration(self, user: dict, response: dict, *, name: str | None = None) -> dict:
        import webauthn
        row = self._spend_challenge_for(response, "register", user_id=user["id"])
        try:
            v = webauthn.verify_registration_response(
                credential=response, expected_challenge=_b64d(row["challenge"]),
                expected_rp_id=row["rp_id"], expected_origin=allowed_origins())
        except Exception as e:
            raise ServiceError(400, "bad_attestation", f"安全密钥登记失败：{type(e).__name__}") from e

        cred_id = _b64e(v.credential_id)
        if self.store.one("SELECT 1 FROM passkeys WHERE credential_id=? AND deleted_at IS NULL",
                          (cred_id,)):
            raise ServiceError(409, "already_registered", "这个安全密钥已经登记过了")
        pid = new_id("pk")
        self.store.execute(
            "INSERT INTO passkeys (id,user_id,credential_id,public_key,sign_count,transports,"
            "rp_id,backed_up,uv_capable,name,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, user["id"], cred_id, _b64e(v.credential_public_key), v.sign_count,
             json.dumps([t.value if hasattr(t, "value") else str(t)
                         for t in (response.get("response", {}).get("transports") or [])]),
             row["rp_id"], int(bool(getattr(v, "credential_backed_up", False))),
             int(bool(getattr(v, "user_verified", False))),
             (name or "").strip()[:60] or "安全密钥", now()))
        return {"id": pid, "name": (name or "").strip()[:60] or "安全密钥"}

    # ------------------------------------------------------------------ authentication
    def begin_authentication(self, user_id: str | None) -> dict:
        """Start a sign-in ceremony.

        With no user, this is a discoverable-credential ("usernameless") sign-in: the browser
        picks the credential and tells us who it belongs to. That is why no credential list is
        sent — sending one for a named account would also answer "does this address have an
        account", which the rest of the sign-in surface is careful not to.
        """
        import webauthn
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement
        allow = []
        if user_id:
            allow = [PublicKeyCredentialDescriptor(id=_b64d(r["credential_id"]))
                     for r in self.store.all(
                         "SELECT credential_id FROM passkeys WHERE user_id=? AND deleted_at IS NULL "
                         "AND rp_id=?", (user_id, rp_id()))]
        opts = webauthn.generate_authentication_options(
            rp_id=rp_id(), allow_credentials=allow,
            user_verification=UserVerificationRequirement.PREFERRED)
        # This endpoint is anonymous and writes a row per call, so the dead rows are reclaimed
        # here rather than by a sweep nothing ever scheduled.
        self.sweep(older_than_s=0)
        self._store_challenge(user_id, "authenticate", opts.challenge)
        return json.loads(webauthn.options_to_json(opts))

    def verify(self, response: dict) -> dict:
        """Finish a sign-in ceremony.

        Returns {user_id, passkey_id, user_verified, sign_count_warning}. `user_verified` is
        what happened *now*, not what the credential is capable of: a policy that treats owning
        a passkey as proof that this sign-in was phishing-resistant is reading the wrong fact,
        and this is the value that lets it read the right one.
        """
        import webauthn
        cred_id = response.get("id") or response.get("rawId")
        if not cred_id:
            raise ServiceError(400, "bad_response", "登录响应缺少凭据标识")
        pk = self.store.one("SELECT * FROM passkeys WHERE credential_id=? AND deleted_at IS NULL",
                            (cred_id,))
        if not pk:
            raise ServiceError(401, "unknown_passkey", "这个安全密钥没有登记过")
        row = self._spend_challenge_for(response, "authenticate", user_id=pk["user_id"],
                                        also_anonymous=True)

        try:
            v = webauthn.verify_authentication_response(
                credential=response, expected_challenge=_b64d(row["challenge"]),
                expected_rp_id=row["rp_id"], expected_origin=allowed_origins(),
                credential_public_key=_b64d(pk["public_key"]),
                credential_current_sign_count=pk["sign_count"])
        except Exception as e:
            raise ServiceError(401, "bad_assertion", f"安全密钥验证失败：{type(e).__name__}") from e

        # A counter that goes backwards is the protocol's only cloning signal, and many
        # authenticators — every synced passkey among them — always report zero. So it is
        # surfaced and recorded, not treated as a failure that would lock out ordinary users.
        warning = bool(pk["sign_count"] and v.new_sign_count and v.new_sign_count <= pk["sign_count"])
        self.store.execute("UPDATE passkeys SET sign_count=MAX(sign_count,?), last_used_at=? WHERE id=?",
                           (max(v.new_sign_count or 0, pk["sign_count"]), now(), pk["id"]))
        return {"user_id": pk["user_id"], "passkey_id": pk["id"],
                "user_verified": bool(getattr(v, "user_verified", False)),
                "sign_count_warning": warning}

    # ------------------------------------------------------------------ scoped re-authentication
    def current_reauth_session(self, admitted: dict | None) -> dict:
        from synwevo_core.sessions import Sessions
        session = Sessions(self.store).current(admitted)
        if not session or session.get("oidc_sid") or session.get("central_session_id"):
            raise ServiceError(401, "unauthorized", "请先登录")
        if not self.store.one("SELECT 1 FROM users WHERE id=? AND status='active' AND deleted_at IS NULL",
                              (session["user_id"],)):
            raise ServiceError(401, "unauthorized", "账户不可用，请重新登录")
        return session

    def reauth_keys(self, user_id: str) -> list[dict]:
        return self.store.all("SELECT * FROM passkeys WHERE user_id=? AND deleted_at IS NULL "
                              "AND rp_id=? AND uv_capable=1 ORDER BY id", (user_id, rp_id()))

    def begin_reauthentication(self, admitted: dict, scope: str, origin: str) -> dict:
        import webauthn
        from synwevo_core.sessions import REAUTH_SCOPES
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement
        if scope not in REAUTH_SCOPES or origin not in allowed_origins():
            raise ServiceError(400, "bad_scope", "请选择有效的身份确认用途")
        with self.store.tx():
            session = self.current_reauth_session(admitted)
            keys = self.reauth_keys(session["user_id"])
            if not keys:
                raise ServiceError(409, "no_usable_passkey", "当前账户没有可用于身份确认的安全密钥")
            opts = webauthn.generate_authentication_options(
                rp_id=rp_id(), allow_credentials=[PublicKeyCredentialDescriptor(id=_b64d(k["credential_id"])) for k in keys],
                user_verification=UserVerificationRequirement.REQUIRED, timeout=CHALLENGE_TTL_S * 1000)
            # Requests are limited at the route. Bound reclamation to keep this write small;
            # the periodic sweep handles the rest. Revoked sessions never authorize finish.
            ts = now()
            self.store.execute("DELETE FROM reauth_challenges WHERE id IN (SELECT r.id FROM reauth_challenges r "
                               "LEFT JOIN sessions s ON s.id=r.session_id WHERE r.expires_at<=? OR r.used_at IS NOT NULL "
                               "OR s.id IS NULL OR s.revoked_at IS NOT NULL LIMIT 200)", (ts,))
            self.store.execute("INSERT INTO reauth_challenges (id,user_id,session_id,purpose,scope,challenge,"
                               "rp_id,origin,created_at,expires_at) VALUES (?,?,?,'reauth',?,?,?,?,?,?)",
                               (new_id("rc"), session["user_id"], session["id"], scope, _b64e(opts.challenge),
                                rp_id(), origin, ts, ts + CHALLENGE_TTL_S))
            return json.loads(webauthn.options_to_json(opts))

    @staticmethod
    def _reauth_client_data(response: dict) -> dict:
        try:
            raw = response["response"]["clientDataJSON"]
            if not isinstance(raw, str) or len(raw) > 16384:
                raise ValueError()
            value = json.loads(_b64d(raw))
            if not isinstance(value, dict) or not isinstance(value.get("challenge"), str):
                raise ValueError()
            return value
        except (ValueError, TypeError, KeyError):
            raise ServiceError(400, "no_challenge", "身份确认已过期或已使用，请重新开始") from None

    def spend_reauthentication(self, admitted: dict, scope: str, response: dict, origin: str) -> dict:
        """Claim this proof once, in its own transaction, before doing cryptographic work.

        Failure afterwards cannot unspend a captured response. The following writer transaction
        still rechecks the session, account and credential before marking any confirmation.
        """
        value = self._reauth_client_data(response)["challenge"].rstrip("=")
        with self.store.tx() as tx:
            session = self.current_reauth_session(admitted)
            row = self.store.one("SELECT * FROM reauth_challenges WHERE challenge=? AND purpose='reauth' "
                                 "AND user_id=? AND session_id=? AND scope=? AND origin=? AND rp_id=? "
                                 "AND used_at IS NULL AND expires_at>?",
                                 (value, session["user_id"], session["id"], scope, origin, rp_id(), now()))
            if not row or not tx.execute("UPDATE reauth_challenges SET used_at=? WHERE id=? "
                                         "AND used_at IS NULL AND expires_at>?", (now(), row["id"], now())).rowcount:
                raise ServiceError(400, "no_challenge", "身份确认已过期或已使用，请重新开始")
            return row

    def verify_reauthentication(self, admitted: dict, scope: str, row: dict, response: dict) -> dict:
        """Verify current user, RP, origin, UP and UV while the caller holds Store.tx()."""
        import webauthn
        with self.store.conn() as connection:
            if not connection.in_transaction:
                raise RuntimeError("reauthentication verification requires a write transaction")
        session = self.current_reauth_session(admitted)
        if (row["user_id"] != session["user_id"] or row["session_id"] != session["id"] or row["scope"] != scope
                or row["purpose"] != "reauth" or row["rp_id"] != rp_id() or row["origin"] not in allowed_origins()
                or row["expires_at"] <= now()):
            raise ServiceError(400, "no_challenge", "身份确认已过期或已使用，请重新开始")
        try:
            cred_id = response.get("id") or response.get("rawId")
            if not isinstance(cred_id, str):
                raise ValueError()
            pk = self.store.one("SELECT * FROM passkeys WHERE credential_id=? AND user_id=? "
                                "AND deleted_at IS NULL AND rp_id=? AND uv_capable=1",
                                (cred_id, session["user_id"], row["rp_id"]))
            if not pk:
                raise ValueError()
            user_handle = response.get("response", {}).get("userHandle")
            if user_handle is not None and _b64d(user_handle) != session["user_id"].encode():
                raise ValueError()
            if self._reauth_client_data(response).get("crossOrigin", False) is not False:
                raise ValueError()
            verified = webauthn.verify_authentication_response(
                credential=response, expected_challenge=_b64d(row["challenge"]), expected_rp_id=row["rp_id"],
                expected_origin=row["origin"], credential_public_key=_b64d(pk["public_key"]),
                credential_current_sign_count=pk["sign_count"], require_user_verification=True)
        except Exception:
            # No response contents or parser/crypto exception details go to the caller or audit.
            raise ServiceError(401, "bad_assertion", "安全密钥验证失败，请重新开始身份确认") from None
        self.store.execute("UPDATE passkeys SET sign_count=?,last_used_at=? WHERE id=?",
                           (verified.new_sign_count, now(), pk["id"]))
        return {"session": session, "passkey_id": pk["id"]}

    # ------------------------------------------------------------------ challenges
    def _store_challenge(self, user_id: str | None, purpose: str, challenge: bytes) -> None:
        self.store.execute(
            "INSERT INTO webauthn_challenges (id,user_id,purpose,challenge,rp_id,origin,"
            "created_at,expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (new_id("wc"), user_id, purpose, _b64e(challenge), rp_id(), _issuer(),
             now(), now() + CHALLENGE_TTL_S))

    def _spend_challenge_for(self, response: dict, purpose: str, *, user_id: str | None,
                             also_anonymous: bool = False) -> dict:
        """Consume exactly the challenge this ceremony was answering, or refuse.

        The challenge is read from the client data the authenticator signed, and the row with
        that value is spent. Taking "the newest live challenge" instead meant an anonymous
        sign-in spent whichever challenge anyone had asked for last: two people signing in at
        once failed each other, and anyone looping on the anonymous begin endpoint could make
        every passkey sign-in fail. The signature check then still has to pass against this
        exact value, so reading it from the response grants the client nothing.
        """
        import base64 as _b64
        try:
            raw = (response.get("response") or {}).get("clientDataJSON") or ""
            client_data = json.loads(_b64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            value = str(client_data.get("challenge") or "").rstrip("=")
        except Exception:  # noqa: BLE001 - any malformed client data is simply "no challenge"
            value = ""
        if not value:
            raise ServiceError(400, "no_challenge", "挑战已过期或不存在，请重新开始")
        owner = "(user_id=? OR user_id IS NULL)" if also_anonymous else "user_id=?"
        row = self.store.one(
            "SELECT * FROM webauthn_challenges WHERE challenge=? AND purpose=? AND used_at IS NULL "
            f"AND expires_at > ? AND {owner}", (value, purpose, now(), user_id))
        if not row:
            raise ServiceError(400, "no_challenge", "挑战已过期或不存在，请重新开始")
        with self.store.tx() as t:
            if not t.execute("UPDATE webauthn_challenges SET used_at=? WHERE id=? AND used_at IS NULL",
                             (now(), row["id"])).rowcount:
                raise ServiceError(400, "no_challenge", "挑战已被使用")
        return row

    def sweep(self, older_than_s: float = 3600) -> int:
        with self.store.tx():
            old = self.store.execute("DELETE FROM webauthn_challenges WHERE expires_at < ?",
                                     (now() - older_than_s,))
            return old + self.store.execute("DELETE FROM reauth_challenges WHERE expires_at < ?",
                                            (now() - older_than_s,))


def _b64e(raw: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(raw: str | bytes) -> bytes:
    import base64
    if isinstance(raw, bytes):
        return raw
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
