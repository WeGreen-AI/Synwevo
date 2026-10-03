"""Opaque tokens the platform hands out and takes back: sealed, bound to their holder, not stored.

A position in a log, for one. Its holder can neither read it nor make one: AES-256-GCM under a
key derived from the server root secret (`Auth.secret()`), one key per label. ``bound`` — who the
token is for — is authenticated but not carried, so a token presented by anyone else, or changed
anywhere, does not open at all.

Unlike `sealed` (secrets at rest, which are re-keyed with the root secret), nothing here is
stored. After the root secret changes, tokens issued before simply stop opening and their holders
start again; callers answer that as they answer any token that does not open.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .store import Store

_ALPHABET = re.compile(r"[A-Za-z0-9_-]+")


def _key(store: Store, label: bytes) -> bytes:
    from .auth import Auth
    return hmac.new(Auth(store).secret(), label, hashlib.sha256).digest()


def seal(store: Store, label: bytes, body: bytes, bound: bytes) -> str:
    nonce = secrets.token_bytes(12)
    sealed = nonce + AESGCM(_key(store, label)).encrypt(nonce, body, bound)
    return base64.urlsafe_b64encode(sealed).decode("ascii").rstrip("=")


def unseal(store: Store, label: bytes, token: str, bound: bytes, *, max_length: int = 1024) -> bytes | None:
    """The body, or None for anything that is not a token sealed here for `bound`. The length and
    alphabet are checked before the key is touched."""
    if not isinstance(token, str) or not 0 < len(token) <= max_length or not _ALPHABET.fullmatch(token):
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        return AESGCM(_key(store, label)).decrypt(raw[:12], raw[12:], bound)
    except (InvalidTag, ValueError):
        return None
