"""Request-scoped tenant.

Every resource (project, template, job) belongs to a tenant.  The web UI works as the
tenant in ``SYNWEVO_UI_TENANT`` (default ``default``); a machine API request works as the
tenant of its API key.

That last part used to read "service methods check the current tenant on every lookup, so an
endpoint cannot forget the check".  It was not true: the check was hand-written at four call
sites and eleven UI endpoints reached across tenants.  Object-level checks now live in
``authz.py`` and are applied at the accessors every handler already goes through — see
docs/security/permissions.md §5.1 for the list that was measured, not assumed.
"""
from __future__ import annotations

import os

from . import env
from contextlib import contextmanager
from contextvars import ContextVar

UI_TENANT = env.get("UI_TENANT", "default")
_tenant: ContextVar[str] = ContextVar("synwevo_tenant", default=UI_TENANT)


def tenant() -> str:
    return _tenant.get()


def set_tenant(name: str):
    return _tenant.set(name or UI_TENANT)


@contextmanager
def as_tenant(name: str):
    token = _tenant.set(name or UI_TENANT)
    try:
        yield name
    finally:
        _tenant.reset(token)


# Who is acting in this request: the signed-in person's id, the delegated person behind a machine
# token, or None (a service account, a probe, a background job). Read by services that record
# attribution — never by authorization, which stays with the gate and the object checks.
_actor: ContextVar[str | None] = ContextVar("synwevo_actor", default=None)


def actor() -> str | None:
    return _actor.get()


def set_actor(user_id: str | None):
    return _actor.set(user_id)


def reset_actor(token) -> None:
    try:
        _actor.reset(token)
    except (ValueError, LookupError):
        pass
