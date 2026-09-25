"""In-app notifications, shared by every role.

Why here: the account service raises security and membership events, Synwevo Docs raises file and
job events, the portal shows the inbox — three processes, one inbox. The inbox is a table; what
each process contributes is a row, and what each shell reads is the same list rendered in the
reader's language. Nothing about delivery is inferred from a network call succeeding: an e-mail
is a *delivery row* that the account service's dispatcher turns into an outbox message later,
and the inbox row exists whether or not that mail is ever sent.

Model (docs/platform/notifications.md):

* ``notifications`` — one row per recipient per event. ``kind`` names the event (``invitation.received``),
  the catalogue below fixes its category, severity and three-language wording; ``params`` are the
  plain strings rendered into that wording (workspace names, people's names — never translated,
  never markup). ``target`` is a server-chosen path on a named origin, so a click never follows a
  URL the client assembled. ``dedupe_key`` collapses repeats (the same invitation resent).
* ``notification_deliveries`` — the e-mail channel's outbox: ``pending`` until the dispatcher
  renders and queues a mail, ``queued`` with the mail id afterwards, ``skipped`` when the
  deployment has no mail or the reader turned the channel off, ``failed`` after the retries.
  Digest and quiet-hour preferences only move ``not_before``.
* ``notification_preferences`` — per reader, per category: in-app on/off and e-mail
  ``immediate`` / ``digest`` / ``off``; quiet hours in the reader's time zone; preview detail.
  ``critical`` notifications (account security) ignore the off switches: the mail that tells
  someone an attacker changed their password must not be something the attacker can silence.

Reading a notification never changes business state: an approval is done in its own flow and
reports back through ``action_state``.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import env
from .errors import ServiceError
from .store import Store, new_id, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT UNIQUE NOT NULL,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('security','organization','members','approval','conversation','docs','automation','platform')),
  severity TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','action','critical')),
  organization_id TEXT, workspace_id TEXT, object_kind TEXT, object_id TEXT,
  dedupe_key TEXT,
  -- plain strings rendered into the catalogue's wording; never markup, never translated
  params TEXT,
  -- {"origin": "accounts"|"portal"|"docs", "path": "/..."}: the server picks both halves
  target TEXT,
  action_state TEXT CHECK (action_state IN ('pending','done','void')),
  created_at REAL NOT NULL, read_at REAL, archived_at REAL
);
CREATE INDEX IF NOT EXISTS ix_notif_user_seq ON notifications(user_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS ux_notif_dedupe ON notifications(user_id, dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS notification_deliveries (
  notification_id TEXT NOT NULL REFERENCES notifications(id),
  channel TEXT NOT NULL CHECK (channel IN ('email')),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','queued','skipped','failed')),
  not_before REAL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, mail_id TEXT,
  updated_at REAL NOT NULL,
  PRIMARY KEY (notification_id, channel)
);
CREATE INDEX IF NOT EXISTS ix_notif_delivery_due ON notification_deliveries(status, not_before);
CREATE TABLE IF NOT EXISTS notification_preferences (
  user_id TEXT PRIMARY KEY,
  categories TEXT NOT NULL,
  quiet_start TEXT, quiet_end TEXT, timezone TEXT,
  preview TEXT NOT NULL DEFAULT 'full' CHECK (preview IN ('full','minimal')),
  updated_at REAL NOT NULL
);
"""

CATEGORIES = ("security", "organization", "members", "approval", "conversation", "docs", "automation", "platform")
LOCALES = ("zh-Hans", "zh-Hant", "en")
EMAIL_MODES = ("immediate", "digest", "off")
DIGEST_WINDOW_S = 30 * 60
RETRY_DELAYS_S = (300, 900, 3600, 4 * 3600)
PAGE_MAX = 100

#: What each event is: category, severity, whether it carries a to-do, default e-mail mode, and
#: its wording. Titles and bodies are templates over `params`; `{p}` placeholders are str.format
#: fields. Keep params to names and counts — the reader's shell never interprets markup here.
CATALOGUE: dict[str, dict] = {
    # ---- account security: always in-app, always mailed (severity critical)
    "security.change": {"category": "security", "severity": "critical", "email": "immediate", "title": {
        "zh-Hans": "账户安全变更：{what}", "zh-Hant": "帳戶安全變更：{what}", "en": "Account security change: {what}"}, "body": {
        "zh-Hans": "如果这是你本人的操作，不需要做任何事。如果不是，请立即修改口令并结束其他设备的登录。",
        "zh-Hant": "若為你本人操作，無需處理。若不是，請立即變更密碼並結束其他裝置的登入。",
        "en": "If this was you, nothing to do. If not, change your password now and sign out other devices."}},
    # ---- organization & workspace membership
    "invitation.received": {"category": "organization", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "{inviter} 邀请你加入「{workspace}」", "zh-Hant": "{inviter} 邀請你加入「{workspace}」", "en": "{inviter} invited you to “{workspace}”"}, "body": {
        "zh-Hans": "角色：{role}。打开邀请，核对信息后确认加入。", "zh-Hant": "角色：{role}。開啟邀請，核對資訊後確認加入。", "en": "Role: {role}. Open the invitation, check the details and confirm."}},
    "invitation.accepted": {"category": "organization", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "{member} 已加入「{workspace}」", "zh-Hant": "{member} 已加入「{workspace}」", "en": "{member} joined “{workspace}”"}, "body": {
        "zh-Hans": "你发出的邀请已被接受，角色：{role}。", "zh-Hant": "你發出的邀請已被接受，角色：{role}。", "en": "Your invitation was accepted. Role: {role}."}},
    "invitation.declined": {"category": "organization", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "{member} 拒绝了加入「{workspace}」的邀请", "zh-Hant": "{member} 拒絕了加入「{workspace}」的邀請", "en": "{member} declined the invitation to “{workspace}”"}, "body": {
        "zh-Hans": "如有需要可以重新发送邀请。", "zh-Hant": "如有需要可以重新發送邀請。", "en": "You can send a new invitation if needed."}},
    "invitation.revoked": {"category": "organization", "severity": "info", "email": "off", "title": {
        "zh-Hans": "加入「{workspace}」的邀请已撤回", "zh-Hant": "加入「{workspace}」的邀請已撤回", "en": "The invitation to “{workspace}” was withdrawn"}, "body": {
        "zh-Hans": "邀请人撤回了这份邀请，链接已失效。", "zh-Hant": "邀請人撤回了這份邀請，連結已失效。", "en": "The inviter withdrew it; the link no longer works."}},
    "member.role_changed": {"category": "organization", "severity": "info", "email": "immediate", "title": {
        "zh-Hans": "你在「{workspace}」的角色变为 {role}", "zh-Hant": "你在「{workspace}」的角色變為 {role}", "en": "Your role in “{workspace}” is now {role}"}, "body": {
        "zh-Hans": "由 {actor} 更改。", "zh-Hant": "由 {actor} 變更。", "en": "Changed by {actor}."}},
    "member.removed": {"category": "organization", "severity": "info", "email": "immediate", "title": {
        "zh-Hans": "你已被移出「{workspace}」", "zh-Hant": "你已被移出「{workspace}」", "en": "You were removed from “{workspace}”"}, "body": {
        "zh-Hans": "由 {actor} 操作。该空间的项目与文件不再可见。", "zh-Hant": "由 {actor} 操作。該空間的專案與檔案不再可見。", "en": "Done by {actor}. Its projects and files are no longer visible to you."}},
    "member.left": {"category": "organization", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "{member} 退出了「{workspace}」", "zh-Hant": "{member} 退出了「{workspace}」", "en": "{member} left “{workspace}”"}, "body": {
        "zh-Hans": "", "zh-Hant": "", "en": ""}},
    "organization.role_changed": {"category": "organization", "severity": "info", "email": "immediate", "title": {
        "zh-Hans": "你在组织「{organization}」的角色变为 {role}", "zh-Hant": "你在組織「{organization}」的角色變為 {role}", "en": "Your role in the organization “{organization}” is now {role}"}, "body": {
        "zh-Hans": "由 {actor} 更改。", "zh-Hant": "由 {actor} 變更。", "en": "Changed by {actor}."}},
    "organization.roles_assigned": {"category": "organization", "severity": "info", "email": "immediate", "title": {
        "zh-Hans": "你在组织「{organization}」的角色已更新", "zh-Hant": "你在組織「{organization}」的角色已更新", "en": "Your roles in the organization “{organization}” changed"}, "body": {
        "zh-Hans": "当前自定义角色：{roles}。由 {actor} 更改。", "zh-Hant": "目前自訂角色：{roles}。由 {actor} 變更。", "en": "Current custom roles: {roles}. Changed by {actor}."}},
    "member_import.finished": {"category": "organization", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "成员导入「{filename}」{outcome}", "zh-Hant": "成員匯入「{filename}」{outcome}", "en": "Member import “{filename}” {outcome}"}, "body": {
        "zh-Hans": "已发出邀请 {invited}，更新成员 {updated}，跳过 {skipped}，失败 {failed}。", "zh-Hant": "已發出邀請 {invited}，更新成員 {updated}，略過 {skipped}，失敗 {failed}。",
        "en": "{invited} invited, {updated} updated, {skipped} skipped, {failed} failed."}},
    "member_import.roles_applied": {"category": "organization", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "你在组织「{organization}」获得了导入时指定的角色", "zh-Hant": "你在組織「{organization}」獲得了匯入時指定的角色", "en": "Roles from a member import were applied in “{organization}”"}, "body": {
        "zh-Hans": "自定义角色：{roles}。由 {actor} 在导入时指定。", "zh-Hant": "自訂角色：{roles}。由 {actor} 在匯入時指定。", "en": "Custom roles: {roles}. Specified by {actor} in the import."}},
    # ---- Synwevo Docs
    "docs.file_rejected": {"category": "docs", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "文件「{filename}」未通过安全检查", "zh-Hant": "檔案「{filename}」未通過安全檢查", "en": "“{filename}” failed the security check"}, "body": {
        "zh-Hans": "项目「{project}」中的这个文件被判定为恶意内容，已拒绝并隔离。", "zh-Hant": "專案「{project}」中的這個檔案被判定為惡意內容，已拒絕並隔離。", "en": "The file in project “{project}” was identified as malicious and has been rejected and quarantined."}},
    "docs.job_finished": {"category": "docs", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "项目「{project}」的生成任务{outcome}", "zh-Hant": "專案「{project}」的產生任務{outcome}", "en": "Generation for “{project}” {outcome}"}, "body": {
        "zh-Hans": "{done} 份成功，{failed} 份失败。", "zh-Hant": "{done} 份成功，{failed} 份失敗。", "en": "{done} succeeded, {failed} failed."}},
}

#: Wording for the `what` of a security change, keyed so the mail and the inbox agree.
SECURITY_WHAT = {
    "password_changed": {"zh-Hans": "口令已更改", "zh-Hant": "密碼已變更", "en": "Password changed"},
    "password_reset": {"zh-Hans": "口令已重置", "zh-Hant": "密碼已重設", "en": "Password reset"},
    "email_changed": {"zh-Hans": "登录邮箱已更改", "zh-Hant": "登入電子郵件地址已變更", "en": "Sign-in email changed"},
    "passkey_added": {"zh-Hans": "新增了一个安全密钥（Passkey）", "zh-Hant": "已新增一組通行密鑰（Passkey）", "en": "A passkey was added"},
    "passkey_removed": {"zh-Hans": "删除了一个安全密钥（Passkey）", "zh-Hant": "已移除一組通行密鑰（Passkey）", "en": "A passkey was removed"},
    "totp_enabled": {"zh-Hans": "启用了动态验证码（两步验证）", "zh-Hant": "已啟用驗證器動態驗證碼（兩步驟驗證）", "en": "Authenticator verification enabled"},
    "totp_disabled": {"zh-Hans": "关闭了动态验证码（两步验证）", "zh-Hant": "已停用驗證器動態驗證碼（兩步驟驗證）", "en": "Authenticator verification disabled"},
    "recovery_regenerated": {"zh-Hans": "重新生成了恢复码，旧的恢复码已全部失效", "zh-Hant": "已重新產生復原碼；所有舊復原碼已失效", "en": "Recovery codes regenerated; all previous codes are invalid"},
    "external_linked": {"zh-Hans": "关联了外部登录方式（{provider}）", "zh-Hant": "已連結外部登入方式（{provider}）", "en": "External sign-in linked ({provider})"},
    "external_unlinked": {"zh-Hans": "解除了外部登录方式（{provider}）", "zh-Hant": "已解除外部登入方式（{provider}）", "en": "External sign-in unlinked ({provider})"},
    "sessions_revoked": {"zh-Hans": "结束了其他设备的登录", "zh-Hant": "已結束其他裝置的登入", "en": "Other devices were signed out"},
}
#: Legacy callers pass the zh-Hans sentence; map it back to a key so every reader gets their language.
_WHAT_BY_ZH = {v["zh-Hans"]: k for k, v in SECURITY_WHAT.items()}

ROLE_WORDS = {
    "owner": {"zh-Hans": "所有者", "zh-Hant": "擁有者", "en": "owner"},
    "admin": {"zh-Hans": "管理员", "zh-Hant": "管理員", "en": "admin"},
    "editor": {"zh-Hans": "编辑者", "zh-Hant": "編輯者", "en": "editor"},
    "viewer": {"zh-Hans": "查看者", "zh-Hant": "檢視者", "en": "viewer"},
    "member": {"zh-Hans": "成员", "zh-Hant": "成員", "en": "member"},
}
OUTCOME_WORDS = {
    "completed": {"zh-Hans": "已完成", "zh-Hant": "已完成", "en": "finished"},
    "completed_with_errors": {"zh-Hans": "已完成，部分失败", "zh-Hant": "已完成，部分失敗", "en": "finished with errors"},
    "failed": {"zh-Hans": "失败", "zh-Hant": "失敗", "en": "failed"},
    "cancelled": {"zh-Hans": "已取消", "zh-Hant": "已取消", "en": "was cancelled"},
}


def default_preferences() -> dict:
    cats = {c: {"in_app": True, "email": "immediate"} for c in CATEGORIES}
    cats["docs"]["email"] = "digest"
    cats["conversation"]["email"] = "digest"
    return {"categories": cats, "quiet_start": None, "quiet_end": None, "timezone": None, "preview": "full"}


def _locale(value: str | None) -> str:
    return value if value in LOCALES else "zh-Hans"


class Notifier:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------ emitting
    def emit(self, user_id: str, kind: str, *, params: dict | None = None, organization_id: str | None = None,
             workspace_id: str | None = None, object_kind: str | None = None, object_id: str | None = None,
             dedupe_key: str | None = None, target: dict | None = None, actionable: bool = False) -> dict | None:
        """Record one notification for one reader; returns the row, or None when the reader's
        preferences drop it (never for `critical`). A repeated `dedupe_key` returns the existing
        row unchanged — a resent invitation is one item, not two."""
        spec = CATALOGUE[kind]
        prefs = self.preferences(user_id)
        cat = prefs["categories"].get(spec["category"], {"in_app": True, "email": "immediate"})
        critical = spec["severity"] == "critical"
        if not critical and not cat.get("in_app", True):
            return None
        if dedupe_key:
            existing = self.store.one("SELECT * FROM notifications WHERE user_id=? AND dedupe_key=?", (user_id, dedupe_key))
            if existing:
                return existing
        row = {"id": new_id("n"), "user_id": user_id, "kind": kind, "category": spec["category"], "severity": spec["severity"],
               "organization_id": organization_id, "workspace_id": workspace_id, "object_kind": object_kind, "object_id": object_id,
               "dedupe_key": dedupe_key, "params": json.dumps(_clean_params(params or {}), ensure_ascii=False),
               "target": json.dumps(target, ensure_ascii=False) if target else None,
               "action_state": "pending" if actionable else None, "created_at": now(), "read_at": None, "archived_at": None}
        email_mode = "immediate" if critical else (cat.get("email") if spec["email"] != "off" else "off")
        with self.store.tx() as t:
            t.execute("INSERT INTO notifications (id,user_id,kind,category,severity,organization_id,workspace_id,object_kind,object_id,"
                      "dedupe_key,params,target,action_state,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (row["id"], user_id, kind, row["category"], row["severity"], organization_id, workspace_id, object_kind, object_id,
                       dedupe_key, row["params"], row["target"], row["action_state"], row["created_at"]))
            if email_mode in ("immediate", "digest"):
                not_before = row["created_at"] + (DIGEST_WINDOW_S if email_mode == "digest" and not critical else 0)
                t.execute("INSERT INTO notification_deliveries (notification_id,channel,status,not_before,updated_at) VALUES (?,?,?,?,?)",
                          (row["id"], "email", "pending", not_before, now()))
            else:
                t.execute("INSERT INTO notification_deliveries (notification_id,channel,status,not_before,updated_at,last_error) "
                          "VALUES (?,?,?,?,?,?)", (row["id"], "email", "skipped", None, now(), "channel off"))
        return row

    def security_change(self, user_id: str, what: str) -> dict | None:
        """`what` is a SECURITY_WHAT key, or the zh-Hans sentence legacy callers still pass
        (the external-login sentences carry the provider label in brackets)."""
        params: dict = {}
        key = what if what in SECURITY_WHAT else _WHAT_BY_ZH.get(what)
        if key is None:
            for prefix, k in (("关联了外部登录方式", "external_linked"), ("解除了外部登录方式", "external_unlinked")):
                if what.startswith(prefix):
                    key = k
                    params["provider"] = what[len(prefix):].strip("（）() ")
        params["what"] = key or what
        return self.emit(user_id, "security.change", params=params, target={"origin": "accounts", "path": "/account#security"})

    # ------------------------------------------------------------------ reading
    def list(self, user_id: str, *, filter: str = "all", category: str | None = None, before_seq: int | None = None,
             limit: int = 30, locale: str = "zh-Hans") -> dict:
        limit = max(1, min(int(limit or 30), PAGE_MAX))
        where = ["user_id=?", "archived_at IS NULL"]
        args: list = [user_id]
        if filter == "unread":
            where.append("read_at IS NULL")
        elif filter == "pending":
            where.append("action_state='pending'")
        elif filter == "archived":
            where = ["user_id=?", "archived_at IS NOT NULL"]
        elif filter != "all":
            raise ServiceError(400, "invalid", "filter 必须是 all、unread、pending 或 archived", {"field": "filter"})
        if category:
            if category not in CATEGORIES:
                raise ServiceError(400, "invalid", "未知的通知类别", {"field": "category"})
            where.append("category=?"); args.append(category)
        if before_seq is not None:
            where.append("seq<?"); args.append(int(before_seq))
        rows = self.store.all(f"SELECT * FROM notifications WHERE {' AND '.join(where)} ORDER BY seq DESC LIMIT ?", (*args, limit + 1))
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": [self.render(r, locale) for r in rows], "next_before_seq": rows[-1]["seq"] if more else None,
                "unread_count": self.unread_count(user_id), "latest_seq": self.latest_seq(user_id)}

    def unread_count(self, user_id: str) -> int:
        return self.store.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read_at IS NULL AND archived_at IS NULL", (user_id,))["n"]

    def latest_seq(self, user_id: str) -> int:
        return self.store.one("SELECT COALESCE(MAX(seq),0) AS s FROM notifications WHERE user_id=?", (user_id,))["s"]

    def get(self, user_id: str, nid: str) -> dict:
        row = self.store.one("SELECT * FROM notifications WHERE id=? AND user_id=?", (nid, user_id))
        if not row:
            raise ServiceError(404, "not_found", "找不到该通知")
        return row

    def render(self, row: dict, locale: str) -> dict:
        loc = _locale(locale)
        spec = CATALOGUE.get(row["kind"]) or {"title": {}, "body": {}}
        params = _localised_params(_json(row.get("params")) or {}, loc)
        title = _fmt(spec.get("title", {}).get(loc, ""), params)
        body = _fmt(spec.get("body", {}).get(loc, ""), params)
        target = _json(row.get("target"))
        return {"id": row["id"], "seq": row["seq"], "kind": row["kind"], "category": row["category"], "severity": row["severity"],
                "title": title, "body": body, "organization_id": row.get("organization_id"), "workspace_id": row.get("workspace_id"),
                "object_kind": row.get("object_kind"), "object_id": row.get("object_id"),
                "href": resolve_target(target), "action_state": row.get("action_state"),
                "created_at": row["created_at"], "read_at": row.get("read_at"), "archived_at": row.get("archived_at")}

    # ------------------------------------------------------------------ state changes
    def mark(self, user_id: str, ids: list[str], *, read: bool | None = None, archived: bool | None = None) -> int:
        if not ids or len(ids) > PAGE_MAX:
            raise ServiceError(400, "invalid", "一次最多处理 100 条通知", {"field": "ids"})
        sets, args = [], []
        if read is not None:
            sets.append("read_at=?"); args.append(now() if read else None)
        if archived is not None:
            sets.append("archived_at=?"); args.append(now() if archived else None)
            if archived:
                sets.append("read_at=COALESCE(read_at, ?)"); args.append(now())
        if not sets:
            return 0
        marks = ",".join("?" for _ in ids)
        return int(self.store.execute(f"UPDATE notifications SET {', '.join(sets)} WHERE user_id=? AND id IN ({marks})", (*args, user_id, *ids)) or 0)

    def read_all(self, user_id: str, up_to_seq: int) -> int:
        """Marks what the reader has seen: everything up to the cursor the list was opened with,
        never the items that arrived after it."""
        return int(self.store.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL AND archived_at IS NULL AND seq<=?",
                                      (now(), user_id, int(up_to_seq))) or 0)

    def set_action_state(self, object_kind: str, object_id: str, state: str, *, user_id: str | None = None) -> int:
        """The business flow reports back (approved, declined, expired): the to-do leaves `pending`."""
        if state not in ("pending", "done", "void"):
            raise ValueError(state)
        sql = "UPDATE notifications SET action_state=? WHERE object_kind=? AND object_id=? AND action_state IS NOT NULL"
        args: list = [state, object_kind, object_id]
        if user_id:
            sql += " AND user_id=?"; args.append(user_id)
        return int(self.store.execute(sql, tuple(args)) or 0)

    # ------------------------------------------------------------------ preferences
    def preferences(self, user_id: str) -> dict:
        row = self.store.one("SELECT * FROM notification_preferences WHERE user_id=?", (user_id,))
        out = default_preferences()
        if row:
            saved = _json(row["categories"]) or {}
            for c in CATEGORIES:
                if c in saved and isinstance(saved[c], dict):
                    out["categories"][c] = {"in_app": bool(saved[c].get("in_app", True)),
                                            "email": saved[c].get("email") if saved[c].get("email") in EMAIL_MODES else "immediate"}
            out.update({"quiet_start": row["quiet_start"], "quiet_end": row["quiet_end"], "timezone": row["timezone"], "preview": row["preview"]})
        return out

    def set_preferences(self, user_id: str, data: dict) -> dict:
        if not isinstance(data, dict) or set(data) - {"categories", "quiet_start", "quiet_end", "timezone", "preview"}:
            raise ServiceError(400, "invalid_field", "通知偏好包含不支持的字段")
        current = self.preferences(user_id)
        cats = current["categories"]
        for c, v in (data.get("categories") or {}).items():
            if c not in CATEGORIES or not isinstance(v, dict) or set(v) - {"in_app", "email"}:
                raise ServiceError(400, "invalid_field", "未知的通知类别或字段", {"field": "categories"})
            if "in_app" in v:
                cats[c]["in_app"] = bool(v["in_app"])
            if "email" in v:
                if v["email"] not in EMAIL_MODES:
                    raise ServiceError(400, "invalid_field", "邮件方式必须是 immediate、digest 或 off", {"field": "categories"})
                cats[c]["email"] = v["email"]
        # Security stays on: the switches are stored (so the page can say "always on"), never honoured.
        cats["security"] = {"in_app": True, "email": "immediate"}
        qs, qe, tz = data.get("quiet_start", current["quiet_start"]), data.get("quiet_end", current["quiet_end"]), data.get("timezone", current["timezone"])
        for v, field in ((qs, "quiet_start"), (qe, "quiet_end")):
            if v is not None and not _valid_hhmm(v):
                raise ServiceError(400, "invalid_field", "时间必须是 HH:MM", {"field": field})
        if (qs is None) != (qe is None):
            raise ServiceError(400, "invalid_field", "免打扰需要同时给出开始与结束时间", {"field": "quiet_start"})
        if tz is not None:
            try:
                ZoneInfo(str(tz))
            except (ZoneInfoNotFoundError, ValueError):
                raise ServiceError(400, "invalid_field", "未知的时区", {"field": "timezone"}) from None
        if qs is not None and tz is None:
            raise ServiceError(400, "invalid_field", "免打扰需要时区", {"field": "timezone"})
        preview = data.get("preview", current["preview"])
        if preview not in ("full", "minimal"):
            raise ServiceError(400, "invalid_field", "preview 必须是 full 或 minimal", {"field": "preview"})
        self.store.execute("INSERT INTO notification_preferences (user_id,categories,quiet_start,quiet_end,timezone,preview,updated_at) "
                           "VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET categories=excluded.categories, quiet_start=excluded.quiet_start, "
                           "quiet_end=excluded.quiet_end, timezone=excluded.timezone, preview=excluded.preview, updated_at=excluded.updated_at",
                           (user_id, json.dumps(cats), qs, qe, tz, preview, now()))
        return self.preferences(user_id)

    # ------------------------------------------------------------------ the e-mail outbox side
    def due_deliveries(self, limit: int = 200) -> list[dict]:
        ts = now()
        return self.store.all("SELECT d.*, n.user_id, n.kind, n.category, n.severity, n.params, n.target, n.created_at AS notified_at "
                              "FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id "
                              "WHERE d.status='pending' AND (d.not_before IS NULL OR d.not_before<=?) ORDER BY d.not_before LIMIT ?", (ts, limit))

    def defer_delivery(self, notification_id: str, until: float) -> None:
        self.store.execute("UPDATE notification_deliveries SET not_before=?, updated_at=? WHERE notification_id=? AND channel='email'",
                           (until, now(), notification_id))

    def finish_delivery(self, notification_id: str, status: str, *, mail_id: str | None = None, error: str | None = None) -> None:
        if status not in ("queued", "skipped", "failed"):
            raise ValueError(status)
        self.store.execute("UPDATE notification_deliveries SET status=?, mail_id=?, last_error=?, updated_at=? WHERE notification_id=? AND channel='email'",
                           (status, mail_id, (error or None) and error[:200], now(), notification_id))

    def retry_delivery(self, notification_id: str, error: str) -> None:
        row = self.store.one("SELECT attempts FROM notification_deliveries WHERE notification_id=? AND channel='email'", (notification_id,))
        attempts = int((row or {}).get("attempts") or 0) + 1
        if attempts > len(RETRY_DELAYS_S):
            self.store.execute("UPDATE notification_deliveries SET status='failed', attempts=?, last_error=?, updated_at=? WHERE notification_id=? AND channel='email'",
                               (attempts, error[:200], now(), notification_id))
            return
        self.store.execute("UPDATE notification_deliveries SET attempts=?, not_before=?, last_error=?, updated_at=? WHERE notification_id=? AND channel='email'",
                           (attempts, now() + RETRY_DELAYS_S[attempts - 1], error[:200], now(), notification_id))

    def quiet_until(self, prefs: dict, at: float | None = None) -> float | None:
        """When the reader's quiet hours end, if `at` falls inside them; else None."""
        qs, qe, tz = prefs.get("quiet_start"), prefs.get("quiet_end"), prefs.get("timezone")
        if not (qs and qe and tz):
            return None
        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            return None
        moment = datetime.fromtimestamp(at or now(), tz=zone)
        sh, sm = (int(x) for x in qs.split(":")); eh, em = (int(x) for x in qe.split(":"))
        start = moment.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = moment.replace(hour=eh, minute=em, second=0, microsecond=0)
        if start <= end:                             # same-day window, e.g. 12:00–13:00
            return end.timestamp() if start <= moment < end else None
        # overnight window, e.g. 22:00–08:00
        if moment >= start:
            return (end + timedelta(days=1)).timestamp()
        if moment < end:
            return end.timestamp()
        return None


# ---------------------------------------------------------------------- helpers
def _json(value):
    """The Store decodes columns whose names it knows as JSON; ours may come back either way."""
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _clean_params(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        if v is None:
            continue
        s = str(v)
        out[str(k)] = s[:200]
    return out


def _localised_params(params: dict, loc: str) -> dict:
    out = dict(params)
    if "role" in out and out["role"] in ROLE_WORDS:
        out["role"] = ROLE_WORDS[out["role"]][loc]
    if "what" in out and out["what"] in SECURITY_WHAT:
        out["what"] = _fmt(SECURITY_WHAT[out["what"]][loc], out)
    if "outcome" in out and out["outcome"] in OUTCOME_WORDS:
        out["outcome"] = OUTCOME_WORDS[out["outcome"]][loc]
    return out


class _Safe(dict):
    def __missing__(self, key):
        return ""


def _fmt(template: str, params: dict) -> str:
    try:
        return template.format_map(_Safe(params))
    except (ValueError, IndexError):
        return template


def _valid_hhmm(v) -> bool:
    if not isinstance(v, str) or len(v) != 5 or v[2] != ":":
        return False
    try:
        h, m = int(v[:2]), int(v[3:])
    except ValueError:
        return False
    return 0 <= h <= 23 and 0 <= m <= 59


def resolve_target(target: dict | None) -> str | None:
    """The absolute link for a stored target, from this deployment's configured origins only.
    `docs` links resolve through SYNWEVO_APP_URL when this process knows it; otherwise the item
    has no link rather than a guessed one."""
    if not target or not isinstance(target, dict):
        return None
    path = target.get("path") or "/"
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return None
    origin = target.get("origin")
    base = None
    if origin == "accounts":
        base = env.get("ISSUER")
    elif origin == "portal":
        base = env.get("PORTAL_URL")
    elif origin == "docs":
        base = env.get("APP_URL")
    if not base:
        return None
    return base.rstrip("/") + path
