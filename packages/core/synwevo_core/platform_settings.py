"""Versioned, non-secret platform settings in the existing Store.

This is a data service, not an authorization surface. Governance callers authenticate and
check permission, obtain Store.tx(), then append together with their operation result and
audit. Consumers read the current snapshot each time they make a decision. A stored revision
reports configuration only: it is never evidence that any worker has applied it.

Revision 0 is the built-in defaults, without a database row. Every real edit, reset or rollback
appends a complete overrides snapshot; an earlier value never makes an earlier revision valid
again. There is no environment override, cache, independent database or notification side effect.
"""
from __future__ import annotations

import json
import re

from .errors import ServiceError
from .store import Store, now

INTERVAL_KEY = "notifications.conversation_digest_interval_s"
DAILY_CAP_KEY = "notifications.conversation_digest_daily_cap"
_DEFINITIONS = (
    {"key": INTERVAL_KEY, "type": "integer", "unit": "seconds", "default": 1800,
     "min": 60, "max": 86400, "scope": "global", "default_source": "builtin_default",
     "description": "同一收件人的会话未读摘要邮件最小间隔；不影响安全通知或其他类别。"},
    {"key": DAILY_CAP_KEY, "type": "integer", "unit": "mails_per_day", "default": 8,
     "min": 1, "max": 100, "scope": "global", "default_source": "builtin_default",
     "description": "同一收件人在滚动最近 24 小时内的会话未读摘要邮件上限；按邮件计数，不按消息条数计数。"},
)
_BY_KEY = {item["key"]: item for item in _DEFINITIONS}
_MAX_REVISION = (1 << 63) - 1


def _revision(value) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_REVISION:
        raise ServiceError(400, "settings_bad_revision", "配置版本无效，请重新读取当前配置")
    return value


def _values(overrides: dict) -> dict:
    return {"overrides": dict(overrides),
            "values": {key: overrides.get(key, item["default"]) for key, item in _BY_KEY.items()},
            "sources": {key: "platform_override" if key in overrides else "builtin_default" for key in _BY_KEY}}


def _metadata(value, name: str) -> str:
    # These are server-generated identifiers, never public caller-selected authority.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ServiceError(400, "settings_bad_metadata", "配置操作信息无效，请重新开始操作", {"field": name})
    return value


class PlatformSettings:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def catalogue() -> list[dict]:
        return [dict(item) for item in _DEFINITIONS]

    @staticmethod
    def validate(changes) -> dict:
        """Normalize a nonempty changes object; null means remove the explicit override.

        An explicit value equal to its default remains an override: choosing that source and
        restoring the built-in default are distinct configuration states, even if values match.
        """
        if not isinstance(changes, dict) or not changes:
            raise ServiceError(400, "settings_bad_changes", "请提供至少一项配置变更")
        if any(key not in _BY_KEY for key in changes):
            raise ServiceError(400, "settings_unknown_key", "包含不支持修改的配置项")
        clean = {}
        for key in sorted(changes):
            value, item = changes[key], _BY_KEY[key]
            if value is not None and (type(value) is not int or not item["min"] <= value <= item["max"]):
                raise ServiceError(400, "settings_invalid_value", "配置值必须是允许范围内的整数",
                                   {"key": key, "min": item["min"], "max": item["max"]})
            clean[key] = value
        return clean

    @staticmethod
    def _entry(row: dict) -> dict:
        # Corrupt or future snapshots must not silently fall back to permissive defaults.
        try:
            overrides = json.loads(row["overrides"])
            if not isinstance(overrides, dict) or any(value is None for value in overrides.values()):
                raise ValueError()
            if overrides:
                PlatformSettings.validate(overrides)
        except (TypeError, ValueError, ServiceError):
            raise ServiceError(503, "settings_unavailable", "配置记录暂不可用，请联系管理员检查") from None
        return {**row, **_values(overrides)}

    def read(self) -> dict:
        """One current snapshot, without writing or caching anything."""
        row = self.store.one("SELECT * FROM platform_settings_versions ORDER BY revision DESC LIMIT 1")
        if not row:
            return {"revision": 0, **_values({})}
        entry = self._entry(row)
        return {key: entry[key] for key in ("revision", "overrides", "values", "sources")}

    def revision(self, revision: int) -> dict:
        revision = _revision(revision)
        if revision == 0:
            return {"revision": 0, **_values({}), "actor_id": None, "reason": None,
                    "operation_id": None, "created_at": None, "rollback_revision": None}
        row = self.store.one("SELECT * FROM platform_settings_versions WHERE revision=?", (revision,))
        if not row:
            raise ServiceError(404, "settings_revision_not_found", "找不到该配置版本，请重新选择")
        return self._entry(row)

    def preview(self, changes=None, *, rollback_revision: int | None = None) -> dict:
        """Read-only plan; append recomputes it under the caller's writer transaction.

        after intentionally has no revision: a preview has not committed a new version.
        """
        if (changes is None) == (rollback_revision is None):
            raise ServiceError(400, "settings_bad_changes", "请提供配置变更或选择一个历史版本")
        before = self.read()
        if rollback_revision is not None:
            target = self.revision(rollback_revision)["overrides"]
        else:
            normalized = self.validate(changes)
            target = dict(before["overrides"])
            for key, value in normalized.items():
                if value is None:
                    target.pop(key, None)
                else:
                    target[key] = value
        delta = {key: target.get(key) for key in sorted(set(before["overrides"]) | set(target))
                 if before["overrides"].get(key) != target.get(key)}
        if not delta:
            raise ServiceError(409, "settings_no_change", "配置未发生变化，无需提交")
        return {"expected_revision": before["revision"], "changes": delta, "before": before,
                "after": _values(target), "rollback_revision": rollback_revision}

    def append(self, changes=None, *, expected_revision: int, actor_id: str, reason: str,
               operation_id: str, rollback_revision: int | None = None) -> dict:
        """Append under an already-held Store.tx(); never commits its caller's transaction.

        actor_id and operation_id come from the authenticated governance service. This method
        does not grant permission or provide HTTP idempotency: the caller records/replays its
        authorized operation result in the same transaction. No worker acknowledgement is made.
        """
        with self.store.conn() as connection:
            # in_transaction alone also accepts a manually opened deferred/read transaction;
            # Store.tx is the owner of BEGIN IMMEDIATE and records its active nesting depth.
            if not connection.in_transaction or not getattr(self.store._local, "depth", 0):
                raise RuntimeError("platform settings append requires an existing Store.tx()")
            expected_revision = _revision(expected_revision)
            actor_id = _metadata(actor_id, "actor_id")
            operation_id = _metadata(operation_id, "operation_id")
            if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            current = self.read()
            if current["revision"] != expected_revision:
                raise ServiceError(409, "settings_revision_conflict", "配置已变化，请重新预览",
                                   {"expected_revision": expected_revision, "current_revision": current["revision"]})
            plan = self.preview(changes, rollback_revision=rollback_revision)
            if self.store.one("SELECT 1 FROM platform_settings_versions WHERE operation_id=?", (operation_id,)):
                raise ServiceError(409, "settings_operation_conflict", "此配置操作编号已使用，请重新开始操作")
            inserted = connection.execute(
                "INSERT INTO platform_settings_versions (overrides,actor_id,reason,operation_id,created_at,rollback_revision) "
                "VALUES (?,?,?,?,?,?)",
                (json.dumps(plan["after"]["overrides"], sort_keys=True, separators=(",", ":")), actor_id,
                 reason.strip(), operation_id, now(), rollback_revision))
            return self.revision(inserted.lastrowid)

    def history(self, *, before_revision: int | None = None, limit: int = 50) -> dict:
        """Newest first; revision 0 is virtual and never pretends to be an authored edit."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ServiceError(400, "settings_bad_page", "配置历史分页参数无效")
        if before_revision is not None:
            before_revision = _revision(before_revision)
        where, args = ("WHERE revision<?", (before_revision,)) if before_revision is not None else ("", ())
        rows = self.store.all(f"SELECT * FROM platform_settings_versions {where} ORDER BY revision DESC LIMIT ?",
                              (*args, limit + 1))
        items = [self._entry(row) for row in rows[:limit]]
        return {"items": items, "next_before_revision": items[-1]["revision"] if len(rows) > limit else None}
