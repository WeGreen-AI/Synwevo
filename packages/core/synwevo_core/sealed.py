"""Secrets the platform must be able to read back, sealed at rest.

Most credentials are *verified*, never read back, and are stored as digests: passwords, API
keys, client secrets, session ids, recovery codes. A few have to be used as themselves — a TOTP
seed to compute the expected code, a webhook signing secret to sign each delivery — and those
are sealed here with AES-256-GCM under a key derived from the server root secret
(`Auth.secret()`), one derived key per purpose.

What this does and does not protect against, stated plainly:

* With the root secret supplied from outside the data directory (``SYNWEVO_SECRET``), a copy of
  the database alone does not reveal these secrets.
* With the root secret generated into the database (``meta.server_secret``, the default), the key
  sits next to the ciphertext. That still keeps the plaintext out of every query result, export
  and log line, but it is **not** protection against someone who has the database file.
  `docs/security/permissions.md` says so; nothing here should be described as more than that.

Changing the root secret changes every derived key. `Auth.secret()` refuses to hand out a root
that does not match the one sealed data was written with, and `rekey()` is the way across.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .store import Store

# purpose -> (key-derivation label, associated data). The MFA label predates the rename and must
# **not** change: it is part of the key, and changing it makes every stored TOTP seed unreadable.
PURPOSES = {
    "mfa-seed": (b"docweave/mfa-seed/v1", b"totp-seed"),
    "webhook-secret": (b"synwevo/webhook-signing-secret/v1", b"webhook-secret"),
    # Synwevo Forms fill-in links: kept so an editor can copy a link again (the hash is what
    # opening a link is checked against).
    "forms-fill-link": (b"synwevo/forms-fill-link/v1", b"forms-fill-link"),
    # A person's own AI provider key for their Agent: used server-side only, never returned.
    "agent-provider-key": (b"synwevo/agent-provider-key/v1", b"agent-provider-key"),
}

# Every sealed column in the schema: (table, primary key, column, purpose). `rekey` walks exactly
# this list, so a new sealed column that is not added here would be left behind by a rekey —
# which is why the list lives next to the functions that use it rather than in each caller.
SEALED_COLUMNS = (
    ("totp_secrets", "user_id", "secret_enc", "mfa-seed"),
    ("webhook_subscriptions", "id", "secret_enc", "webhook-secret"),
    ("webhook_subscriptions", "id", "secret_prev_enc", "webhook-secret"),
    ("forms", "id", "public_token_sealed", "forms-fill-link"),
    ("form_requests", "id", "token_sealed", "forms-fill-link"),
    ("agent_settings", "user_id", "personal_key_sealed", "agent-provider-key"),
)


def derive(root: bytes, purpose: str) -> bytes:
    label, _ = PURPOSES[purpose]
    return hmac.new(root, label, hashlib.sha256).digest()


def seal_with(root: bytes, purpose: str, plaintext: str) -> str:
    _, aad = PURPOSES[purpose]
    nonce = secrets.token_bytes(12)
    ct = AESGCM(derive(root, purpose)).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return base64.b64encode(nonce + ct).decode("ascii")


def unseal_with(root: bytes, purpose: str, token: str) -> str:
    _, aad = PURPOSES[purpose]
    raw = base64.b64decode(token)
    return AESGCM(derive(root, purpose)).decrypt(raw[:12], raw[12:], aad).decode("utf-8")


def _root(store: Store) -> bytes:
    from .auth import Auth
    return Auth(store).secret()


def seal(store: Store, purpose: str, plaintext: str) -> str:
    return seal_with(_root(store), purpose, plaintext)


def unseal(store: Store, purpose: str, token: str) -> str:
    return unseal_with(_root(store), purpose, token)


def opens_with(store: Store, root: bytes) -> bool | None:
    """Does sealed data in this store open with `root`? None when there is nothing sealed yet."""
    for table, pk, col, purpose in SEALED_COLUMNS:
        try:
            row = store.one(f"SELECT {col} AS v FROM {table} WHERE {col} IS NOT NULL LIMIT 1")
        except Exception:  # noqa: BLE001 - table absent on a store opened without migration
            continue
        if row and row["v"]:
            try:
                unseal_with(root, purpose, row["v"])
                return True
            except (InvalidTag, ValueError):
                return False
    return None


def rekey(store: Store, old_root: bytes, new_root: bytes) -> dict:
    """Re-seal every sealed value from `old_root` to `new_root`, in one transaction.

    All or nothing: if any value does not open with `old_root` the transaction rolls back and
    nothing is changed, because a half-rekeyed database has two roots and no way to tell which
    row needs which.
    """
    counts: dict[str, int] = {}
    with store.tx() as c:
        for table, pk, col, purpose in SEALED_COLUMNS:
            rows = c.execute(f"SELECT {pk} AS k, {col} AS v FROM {table} WHERE {col} IS NOT NULL").fetchall()
            n = 0
            for r in rows:
                plain = unseal_with(old_root, purpose, r["v"])
                c.execute(f"UPDATE {table} SET {col}=? WHERE {pk}=?", (seal_with(new_root, purpose, plain), r["k"]))
                n += 1
            counts[f"{table}.{col}"] = n
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('root_kcv', ?)", (kcv(new_root),))
    return counts


def kcv(root: bytes) -> str:
    """Key check value: identifies a root secret without revealing it."""
    return hmac.new(root, b"synwevo/root-kcv/v1", hashlib.sha256).hexdigest()[:32]
