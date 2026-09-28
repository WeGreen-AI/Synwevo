"""Whose "today" a document is generated for (CI-95).

Spreadsheet formulas such as TODAY() and NOW() mean the local date and time of the person who
calculates the workbook. The server's clock is UTC, so a person in UTC+8 generating before 08:00
got yesterday's date, and a job retried after midnight gave a different result than the first run.
A generation job therefore fixes one instant when it is submitted — the submission time in the
submitter's time zone — and every run of that job computes with it; a preview uses the viewer's
time zone and the current time. The time zone comes from the request (the browser's own, which is
what the person sees), else the person's profile, else UTC.
"""
from __future__ import annotations

import datetime as _dt
import time as _time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from synwevo_core.errors import ServiceError


def current_instant() -> float:
    """Now, as epoch seconds: the one place TODAY()/NOW() take the time from (tests replace it)."""
    return _time.time()


def _zone(name: str) -> ZoneInfo | None:
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or name.startswith("/") or ".." in name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def resolve_timezone(requested, user_id: str | None, store) -> str:
    """An explicit value must be a real IANA name — a wrong one is refused, not quietly made UTC;
    without one, the person's profile time zone; without that, UTC."""
    if requested is not None and requested != "":
        if _zone(requested) is None:
            raise ServiceError(400, "invalid_field", "timezone 必须是 IANA 时区名（如 Asia/Shanghai）", {"field": "timezone"})
        return requested
    if user_id:
        try:
            row = store.one("SELECT timezone FROM users WHERE id=?", (user_id,))
        except Exception:  # noqa: BLE001 - a deployment without the account tables
            row = None
        if row and row.get("timezone") and _zone(row["timezone"]) is not None:
            return row["timezone"]
    return "UTC"


def moment(at: float, timezone: str) -> _dt.datetime:
    """The instant `at` (epoch seconds) as a wall-clock time in `timezone`."""
    return _dt.datetime.fromtimestamp(at, _zone(timezone) or ZoneInfo("UTC"))


def moment_of(snapshot: dict | None) -> _dt.datetime | None:
    """The instant a job fixed at submission; None for jobs submitted before CI-95."""
    as_of = (snapshot or {}).get("as_of")
    if not isinstance(as_of, dict) or not isinstance(as_of.get("at"), (int, float)):
        return None
    return moment(float(as_of["at"]), str(as_of.get("timezone") or "UTC"))
