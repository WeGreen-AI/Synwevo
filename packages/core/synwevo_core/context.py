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


# The authority descriptor of whoever acts in this request (authority.describe_request): the
# session's person, a delegated client with its person, a service account or an API key. Set by
# the gate beside `_actor`; read by `auth.audit_event` so a service records who did a write
# without every call site threading the request through. Background runners and command lines
# set it themselves. Never an authorization input.
_principal: ContextVar[dict | None] = ContextVar("synwevo_principal", default=None)


def principal() -> dict | None:
    return _principal.get()


def set_principal(desc: dict | None):
    return _principal.set(desc)


def reset_principal(token) -> None:
    try:
        _principal.reset(token)
    except (ValueError, LookupError):
        pass


# The request's trace id (the gate's validated X-Request-ID), for records that outlive the request:
# a job remembers the request that submitted it, and its first event says so, so an integrator can
# follow one id from the call log to the job and its webhooks. Never an authorization input.
_request_id: ContextVar[str | None] = ContextVar("synwevo_request_id", default=None)


def request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    try:
        _request_id.reset(token)
    except (ValueError, LookupError):
        pass
