"""Test fixture: platform grants for test accounts, labelled as a fixture.

In the product a platform role is granted by an administrator through `PlatformAccess.grant`
(with every rule it enforces) or under host authority (bootstrap token, CLI). A test that only
needs "this account is an administrator" uses this helper instead: the grant goes through the
same service (`PlatformAccess.host_grant`, which keeps the `users.platform_role` mirror in step)
with the source `fixture:tests`, and the address is marked verified, as a delegated
administrator's is. Nothing here writes `users.platform_role` itself.
"""
from __future__ import annotations

from synwevo_core.platform_access import PlatformAccess, SUPER_ADMIN
from synwevo_core.store import now

FIXTURE_SOURCE = "fixture:tests"


def _uid(user_or_id) -> str:
    return user_or_id["id"] if isinstance(user_or_id, dict) else user_or_id


def make_platform_admin(store, user_or_id, role: str = SUPER_ADMIN) -> dict:
    """Give a test account a built-in platform role; returns the refreshed users row."""
    uid = _uid(user_or_id)
    store.execute("UPDATE users SET email_verified_at=COALESCE(email_verified_at, ?) WHERE id=?", (now(), uid))
    PlatformAccess(store).host_grant(uid, role, source=FIXTURE_SOURCE, reason="test fixture: platform role")
    return store.one("SELECT * FROM users WHERE id=?", (uid,))


def end_platform_grants(store, user_or_id) -> int:
    """A synthetic demotion (every live grant ends) for tests about what happens next; the
    rules of a real revocation are tested through the admin routes themselves."""
    return PlatformAccess(store).system_revoke_all(_uid(user_or_id), source=FIXTURE_SOURCE,
                                                   reason="test fixture: synthetic demotion")


def legacy_platform_role_row(store, user_or_id, value: str) -> None:
    """Write `users.platform_role` the way the previous release (v26) did, for tests about legacy
    rows and the rollback mirror. The only direct write of the column in the test tree."""
    store.execute("UPDATE users SET platform_role=? WHERE id=?", (value, _uid(user_or_id)))
