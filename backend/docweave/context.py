"""Request-scoped tenant.

Every resource (project, template, job) belongs to a tenant.  The web UI works as the
tenant in ``DOCWEAVE_UI_TENANT`` (default ``default``); a machine API request works as the
tenant of its API key.  Service methods check the current tenant on every lookup, so an
endpoint cannot forget the check.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar

UI_TENANT = os.environ.get("DOCWEAVE_UI_TENANT", "default")
_tenant: ContextVar[str] = ContextVar("docweave_tenant", default=UI_TENANT)


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
