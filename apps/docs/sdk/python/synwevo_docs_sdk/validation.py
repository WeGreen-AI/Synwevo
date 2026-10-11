"""Client-side checks of the values the server documents as a closed set.

They exist so that a typo (``error_policy="Skip"``, ``layout="fitt"``) fails before a request
is sent, with the accepted values listed, instead of after a round trip. The server remains the
authority: it validates everything again, and a `Client(validate=False)` sends values unchecked
(for a server newer than this SDK that accepts more).
"""
from __future__ import annotations

import re
from typing import Iterable

from .errors import ValidationError

ERROR_POLICIES = ("block", "skip")
LAYOUTS = ("off", "warn", "fit")
ITEM_STATUSES = ("pending", "running", "success", "failed", "skipped", "cancelled")
JOB_TERMINAL = ("completed", "completed_with_errors", "failed", "cancelled")
JOB_STATUSES = ("queued", "running", "cancelling") + JOB_TERMINAL
ISSUE_LEVELS = ("error", "warning")
TEMPLATE_CONTENT = ("original", "working")
UPLOAD_PURPOSES = ("source", "template")
DELIVERY_STATUSES = ("pending", "delivered", "failed", "dropped")
# The server's catalogue (synwevo_docs.webhooks.EVENT_TYPES); apps/docs/tests/test_file_events.py keeps them equal.
EVENT_TYPES = ("job.created", "job.started", "job.planned", "job.cancel_requested", "job.retry_requested", "job.recovered",
               "job.completed", "job.completed_with_errors", "job.failed", "job.cancelled",
               "file.ready", "file.needs_ocr", "file.rejected", "file.failed",
               "copy.requested", "copy.approved", "copy.rejected", "copy.withdrawn", "copy.expired",
               "copy.done", "copy.failed", "copy.stale",
               "member.added", "member.changed", "member.removed",
               "invitation.created", "invitation.accepted", "invitation.revoked",
               "template.created", "template.updated", "template.deleted",
               "template.published", "template.released", "template.deprecated")
EVENT_FILTERS = ("*", "job.*", "file.*", "copy.*", "member.*", "invitation.*", "template.*") + EVENT_TYPES
_FORMAT = re.compile(r"^[a-z0-9]{1,10}$")


def one_of(value, allowed: Iterable[str], field: str):
    allowed = tuple(allowed)
    if value is None:
        return None
    if value not in allowed:
        hint = ""
        if isinstance(value, str) and value.strip().lower() in allowed:
            hint = f"（取值区分大小写，应为 {value.strip().lower()!r}）"
        raise ValidationError(f"{field} 只能是 {' / '.join(allowed)}，收到 {value!r}{hint}", field, allowed)
    return value


def formats(value, field: str = "formats") -> list[str] | None:
    """A list of output format names as the server names them (lower-case, e.g. docx, pdf)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value, (list, tuple)) or not value:
        raise ValidationError(f"{field} 必须是非空的格式名列表，例如 ['docx', 'pdf']", field)
    out = []
    for f in value:
        if not isinstance(f, str) or not _FORMAT.match(f):
            hint = "；格式名是小写的，例如 docx" if isinstance(f, str) and _FORMAT.match(f.lower()) else ""
            raise ValidationError(f"{field} 中的 {f!r} 不是格式名（1–10 个小写字母或数字）{hint}", field)
        out.append(f)
    return out


def format_name(value, field: str = "format") -> str | None:
    if value is None or value == "":
        return None
    return formats([value], field)[0]


def events(value) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value, (list, tuple)) or not value:
        raise ValidationError("events 必须是事件类型的非空列表（或 ['*'] 表示全部）", "events", EVENT_FILTERS)
    for v in value:
        one_of(v, EVENT_FILTERS, "events")
    return list(value)


def positive_int(value, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{field} 必须是正整数", field)
    return value


def simulate(value):
    if value in (None, False):
        return False
    if value is True:
        return True
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    raise ValidationError("simulate 只能是 true / false，或正整数（只模拟前 N 个样本）", "simulate")
