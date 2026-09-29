"""Request helpers shared by the applications' routes."""
from __future__ import annotations

from fastapi import Request

from synwevo_core.errors import ServiceError


def signed_in(request: Request) -> dict:
    """The signed-in person, as the platform gate resolved them for this request."""
    user = getattr(request.state, "user", None)
    if not user:
        raise ServiceError(401, "unauthorized", "请先登录")
    return user


def page_args(limit: int | None, *, default: int = 50, maximum: int = 200) -> int:
    if limit is None:
        return default
    if not 1 <= int(limit) <= maximum:
        raise ServiceError(400, "bad_limit", f"每页 1–{maximum} 条", {"maximum": maximum})
    return int(limit)
