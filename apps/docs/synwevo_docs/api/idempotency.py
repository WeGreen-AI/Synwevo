"""Compatibility import for the shared machine API idempotency implementation.

Docs and the workspace directory use the same transaction and replay authorization rules.
Keep the original import path for clients, job services and existing validation suites.
"""
from synwevo_core.idempotency import (
    HEADER,
    IN_FLIGHT_STALE_S,
    MAX_KEY_LEN,
    REPLAY_HEADER,
    SECRET_ROUTES,
    TTL_S,
    Idempotency,
    fingerprint,
    key_of,
    owner_of,
    purge_expired,
    scope_of,
    validate_key,
)

__all__ = [
    "HEADER", "IN_FLIGHT_STALE_S", "MAX_KEY_LEN", "REPLAY_HEADER", "SECRET_ROUTES", "TTL_S",
    "Idempotency", "fingerprint", "key_of", "owner_of", "purge_expired", "scope_of", "validate_key",
]
