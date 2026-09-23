"""Second factors: TOTP and recovery codes.

**Where the encryption key lives decides how much this is worth.** A TOTP seed is a shared
secret — anyone holding it can generate valid codes forever — so it is stored encrypted. But if
the key sits in the same SQLite file as the ciphertext, a copy of that file yields both, and MFA
stops helping against exactly the scenario encryption was meant to cover.

So the key is derived from ``DOCWEAVE_SECRET`` when it is supplied by the environment, which is
the configuration where key and data genuinely live apart, and falls back to the database-stored
server secret otherwise. The fallback is not silent: :func:`key_is_external` reports which one is
in force and ``manage.py info`` prints it. MFA still does its main job in either case — password
compromise is what it mostly defends against, and a stolen database is not a stolen password —
but the difference is real and is stated rather than papered over.

Recovery codes are the other half. They are what stands between "lost my phone" and "lost my
account", so they are high-entropy, stored only as hashes, single-use, and regenerating them
invalidates every old one.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
import urllib.parse

import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .services import ServiceError
from .store import Store, new_id, now

# A code is accepted one step either side of now, so a clock a few seconds out still works.
# Wider than that starts extending the useful life of a phished code for no real gain.
TOTP_WINDOW = 1
TOTP_PERIOD = 30
TOTP_DIGITS = 6
RECOVERY_CODE_COUNT = 10
# 10 groups of 5 base32 chars ≈ 25 bits each; easy to read off paper, far beyond guessing.
RECOVERY_GROUPS = 3
RECOVERY_GROUP_LEN = 5
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no I/O/0/1 — these get written down


def key_is_external() -> bool:
    """True when the encryption key comes from the environment rather than the database.

    Reported by `manage.py info` so an operator can see which configuration they are in without
    reading code.
    """
    return bool(os.environ.get("DOCWEAVE_SECRET"))


def _key(store: Store) -> bytes:
    """32 bytes for AES-GCM, domain-separated from every other use of the server secret.

    Separation matters: the same root secret also signs download links, and a key reused across
    unrelated purposes turns any weakness in one into a weakness in the other.
    """
    from .auth import Auth
    root = Auth(store).secret()
    return hmac.new(root, b"docweave/mfa-seed/v1", hashlib.sha256).digest()


def encrypt_seed(store: Store, seed: str) -> str:
    """AES-256-GCM, nonce prefixed. Authenticated, so a tampered ciphertext fails to decrypt
    rather than yielding a different seed."""
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_key(store)).encrypt(nonce, seed.encode("utf-8"), b"totp-seed")
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_seed(store: Store, blob: str) -> str:
    raw = base64.b64decode(blob)
    return AESGCM(_key(store)).decrypt(raw[:12], raw[12:], b"totp-seed").decode("utf-8")


class Mfa:
    def __init__(self, store: Store):
        self.store = store

    # ================================================================ TOTP
    def begin_totp(self, user_id: str, account_label: str, issuer: str = "DocWeave") -> dict:
        """Create an unconfirmed secret and return the provisioning URI.

        Unconfirmed on purpose: the row exists so the code can be checked, but
        :meth:`enabled_for` ignores it until one real code has been verified. Enabling MFA on the
        strength of a secret nobody has proved they can read is how people lock themselves out.
        """
        secret = pyotp.random_base32()
        ts = now()
        self.store.execute(
            "INSERT INTO totp_secrets (user_id,secret_enc,confirmed_at,last_step,created_at) "
            "VALUES (?,?,NULL,NULL,?) ON CONFLICT(user_id) DO UPDATE SET "
            "secret_enc=excluded.secret_enc, confirmed_at=NULL, last_step=NULL, created_at=excluded.created_at",
            (user_id, encrypt_seed(self.store, secret), ts))
        uri = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_PERIOD).provisioning_uri(
            name=account_label, issuer_name=issuer)
        # The secret is returned once, for manual entry when a camera is not an option. It is
        # never readable again afterwards — there is no endpoint that reveals a confirmed seed.
        return {"secret": secret, "uri": uri, "issuer": issuer, "account": account_label,
                "digits": TOTP_DIGITS, "period": TOTP_PERIOD,
                "qr_query": urllib.parse.quote(uri, safe="")}

    def confirm_totp(self, user_id: str, code: str) -> None:
        row = self.store.one("SELECT * FROM totp_secrets WHERE user_id=?", (user_id,))
        if not row:
            raise ServiceError(409, "no_totp_setup", "请先开始设置动态验证码")
        if row.get("confirmed_at"):
            raise ServiceError(409, "totp_already_on", "动态验证码已经启用")
        step = self._match(row, code)
        if step is None:
            raise ServiceError(400, "bad_code", "验证码不正确，请检查手机上的时间是否准确", {"field": "code"})
        self.store.execute("UPDATE totp_secrets SET confirmed_at=?, last_step=? WHERE user_id=?",
                           (now(), step, user_id))

    def verify_totp(self, user_id: str, code: str) -> bool:
        """Check a code at sign-in. Replay is refused: a step is accepted at most once.

        Without that, a code shoulder-surfed or captured by a phishing page stays usable for its
        full window, which is long enough to matter.
        """
        row = self.store.one("SELECT * FROM totp_secrets WHERE user_id=? AND confirmed_at IS NOT NULL",
                             (user_id,))
        if not row:
            return False
        step = self._match(row, code)
        if step is None:
            return False
        with self.store.tx() as t:
            n = t.execute("UPDATE totp_secrets SET last_step=? WHERE user_id=? "
                          "AND (last_step IS NULL OR last_step < ?)", (step, user_id, step)).rowcount
        return bool(n)

    def _match(self, row: dict, code: str) -> int | None:
        """Which time step this code belongs to, or None. Compared in constant time."""
        code = (code or "").strip().replace(" ", "")
        if not code.isdigit() or len(code) != TOTP_DIGITS:
            return None
        try:
            secret = decrypt_seed(self.store, row["secret_enc"])
        except Exception:  # noqa: BLE001 - an unreadable seed is a failed check, never a crash
            return None
        totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_PERIOD)
        nowt = int(time.time())
        for drift in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
            at = nowt + drift * TOTP_PERIOD
            if hmac.compare_digest(totp.at(at), code):
                return at // TOTP_PERIOD
        return None

    def disable_totp(self, user_id: str) -> None:
        self.store.execute("DELETE FROM totp_secrets WHERE user_id=?", (user_id,))

    def totp_enabled(self, user_id: str) -> bool:
        return bool(self.store.one(
            "SELECT 1 FROM totp_secrets WHERE user_id=? AND confirmed_at IS NOT NULL", (user_id,)))

    # ================================================================ recovery codes
    def generate_recovery_codes(self, user_id: str) -> list[str]:
        """Fresh codes, returned once. Any previously issued code stops working immediately.

        Regeneration invalidating the old set is the point: someone who regenerates because they
        think the old list leaked must actually be rid of it.
        """
        codes = ["-".join("".join(secrets.choice(_ALPHABET) for _ in range(RECOVERY_GROUP_LEN))
                          for _ in range(RECOVERY_GROUPS)) for _ in range(RECOVERY_CODE_COUNT)]
        ts = now()
        with self.store.tx() as t:
            t.execute("DELETE FROM recovery_codes WHERE user_id=?", (user_id,))
            for c in codes:
                t.execute("INSERT INTO recovery_codes (id,user_id,code_hash,created_at) VALUES (?,?,?,?)",
                          (new_id("rc"), user_id, self._hash(c), ts))
        return codes

    def use_recovery_code(self, user_id: str, code: str) -> bool:
        """Spend one code. Single-use, enforced by the UPDATE's own WHERE clause."""
        digest = self._hash(code)
        with self.store.tx() as t:
            n = t.execute("UPDATE recovery_codes SET used_at=? WHERE user_id=? AND code_hash=? "
                          "AND used_at IS NULL", (now(), user_id, digest)).rowcount
        return bool(n)

    def recovery_remaining(self, user_id: str) -> int:
        return self.store.one("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id=? AND used_at IS NULL",
                              (user_id,))["n"]

    @staticmethod
    def _hash(code: str) -> str:
        """Normalised so the dashes and case a person copies off paper do not matter."""
        norm = (code or "").strip().upper().replace("-", "").replace(" ", "")
        return hashlib.sha256(("docweave/recovery/v1:" + norm).encode("utf-8")).hexdigest()

    # ================================================================ overall state
    def factors(self, user_id: str) -> dict:
        passkeys = self.store.one("SELECT COUNT(*) AS n FROM webauthn_credentials WHERE user_id=?",
                                  (user_id,))["n"]
        return {"totp": self.totp_enabled(user_id), "passkeys": passkeys,
                "recovery_remaining": self.recovery_remaining(user_id),
                "key_external": key_is_external()}

    def enabled_for(self, user_id: str) -> bool:
        """Is a second factor required to sign this account in?"""
        f = self.factors(user_id)
        return bool(f["totp"] or f["passkeys"])


# ---------------------------------------------------------------------- sign-in tickets
TICKET_TTL_S = 300
TICKET_MAX_ATTEMPTS = 5


class Tickets:
    """The half-authenticated state between password and second factor.

    A ticket says only "this person proved the password a moment ago". It is not a session and
    cannot become one by itself: redeeming it requires a second factor, and it expires in five
    minutes whether or not anyone comes back.
    """

    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _digest(raw: str) -> str:
        return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

    def issue(self, user_id: str, *, user_agent: str | None = None, ip: str | None = None) -> str:
        raw = secrets.token_urlsafe(32)
        self.store.execute(
            "INSERT INTO mfa_tickets (id,user_id,created_at,expires_at,user_agent,ip) VALUES (?,?,?,?,?,?)",
            (self._digest(raw), user_id, now(), now() + TICKET_TTL_S, (user_agent or "")[:300], (ip or "")[:64]))
        return raw

    def load(self, raw: str) -> dict | None:
        r = self.store.one("SELECT * FROM mfa_tickets WHERE id=?", (self._digest(raw),))
        if not r or r.get("used_at") or r["expires_at"] <= now() or r["attempts"] >= TICKET_MAX_ATTEMPTS:
            return None
        return r

    def count_failure(self, raw: str) -> int:
        """Returns attempts used. At the limit the ticket is dead and a new sign-in is required."""
        d = self._digest(raw)
        self.store.execute("UPDATE mfa_tickets SET attempts=attempts+1 WHERE id=?", (d,))
        r = self.store.one("SELECT attempts FROM mfa_tickets WHERE id=?", (d,))
        return int((r or {}).get("attempts") or 0)

    def redeem(self, raw: str) -> dict | None:
        """Spend the ticket. The WHERE clause is the check, so it cannot be spent twice."""
        d = self._digest(raw)
        with self.store.tx() as t:
            n = t.execute("UPDATE mfa_tickets SET used_at=? WHERE id=? AND used_at IS NULL AND expires_at>? "
                          "AND attempts<?", (now(), d, now(), TICKET_MAX_ATTEMPTS)).rowcount
            if not n:
                return None
            return t.execute("SELECT * FROM mfa_tickets WHERE id=?", (d,)).fetchone()

    def sweep(self, older_than_s: float = 3600) -> int:
        return self.store.execute("DELETE FROM mfa_tickets WHERE expires_at < ?", (now() - older_than_s,))
