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
  ``defer_reason`` separates aggregation, quiet hours, retry and budget waits. Only a budget
  wait from an older ``policy_revision`` is reconsidered early after configuration changes.
* ``notification_preferences`` — per reader, per category: in-app on/off and e-mail
  ``immediate`` / ``digest`` / ``off``; quiet hours in the reader's time zone; ``preview`` says
  whether message text may leave the platform in mail and push (off by default).
  ``critical`` notifications (account security) ignore the off switches: the mail that tells
  someone an attacker changed their password must not be something the attacker can silence.
  The ``conversation`` category is the exception the other way: a private message is never
  mailed one by one. Its e-mail is off unless the reader turns the unread digest on, and the
  dispatcher re-checks at send time (``stale_reason``) that the item is still unread, the
  conversation not muted or left, and obeys the platform's current per-reader interval and
  rolling daily budget. Defaults are one per half hour, at most eight per 24 hours. A project
  discussion (or a report of a message in one), or a Docs, Forms or project-copy item, whose
  workspace is not available (frozen, deleted) sends nothing.

Reading a notification never changes business state: an approval is done in its own flow and
reports back through ``action_state``.
"""
from __future__ import annotations

import hashlib
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
  -- {"origin": "accounts"|"portal"|"docs"|"messages", "path": "/..."}: the server picks both halves
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
  enqueue_protocol TEXT CHECK (enqueue_protocol IS NULL OR enqueue_protocol='atomic-v1'),
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
#: A private message is never mailed one by one: the conversation category knows only the
#: unread digest (opt-in) and off (the default).
CONVERSATION_EMAIL_MODES = ("digest", "off")
DIGEST_WINDOW_S = 30 * 60
#: Historical default constants retained for consumers that describe the default policy.
#: Runtime decisions read PlatformSettings; one digest may carry many notification items.
CONVERSATION_DIGEST_INTERVAL_S = 30 * 60
CONVERSATION_DIGEST_DAILY_CAP = 8
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
    # ---- the person's own wallet (billing/reminders.py): one notice per crossing of their own threshold
    "wallet.low_balance": {"category": "platform", "severity": "info", "email": "immediate", "title": {
        "zh-Hans": "钱包可用余额低于你设的提醒线", "zh-Hant": "錢包可用餘額低於你設的提醒線", "en": "Your wallet balance is below your reminder"}, "body": {
        "zh-Hans": "可用余额 {available} {unit}，低于你设置的 {threshold} {unit}。充值后可继续使用付费的 Agent；余额回到提醒线以上后，下次低于时会再提醒一次。",
        "zh-Hant": "可用餘額 {available} {unit}，低於你設定的 {threshold} {unit}。儲值後可繼續使用付費的 Agent；餘額回到提醒線以上後，下次低於時會再提醒一次。",
        "en": "Available balance is {available} {unit}, below the {threshold} {unit} you set. Top up to keep using the paid Agent; once the balance is back above the line, you will be reminded again the next time it drops."}},
    # ---- paid model route closed automatically (billing/provider_settings.py, docs/deepseek-provider.md)
    "billing.model.route_missing": {"category": "platform", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "模型路线已停止：提供方不再列出 {model}", "zh-Hant": "模型路線已停止：供應商不再列出 {model}",
        "en": "Model route stopped: the provider no longer lists {model}"}, "body": {
        "zh-Hans": "定时核对发现所配置的模型标识已不在模型列表中（{environment}），付费 Agent 调用已停止，被拒绝的请求不扣费。请核实后发布新的路线配置。",
        "zh-Hant": "定時核對發現所設定的模型標識已不在模型清單中（{environment}），付費 Agent 呼叫已停止，被拒絕的請求不扣費。請核實後發佈新的路線設定。",
        "en": "A scheduled check found the configured model id missing from the model list ({environment}). Paid Agent calls are stopped and refused requests are not charged. Verify and publish a new route configuration."}},
    "billing.model.upstream_balance": {"category": "platform", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "模型提供方报告平台账户余额不足", "zh-Hant": "模型供應商回報平台帳戶餘額不足",
        "en": "The model provider reports the platform account balance is empty"}, "body": {
        "zh-Hans": "上游返回 HTTP 402（{environment}），付费 Agent 调用已停止，用户看到“服务暂时不可用，不扣费”。为上游账户充值后，在管理后台重新发布提供方配置即可恢复。",
        "zh-Hant": "上游回傳 HTTP 402（{environment}），付費 Agent 呼叫已停止，使用者看到「服務暫時無法使用，不扣費」。為上游帳戶儲值後，在管理後台重新發佈供應商設定即可恢復。",
        "en": "The upstream answered HTTP 402 ({environment}). Paid Agent calls are stopped and people see “service temporarily unavailable, not charged”. Top up the upstream account, then publish the provider configuration again in the admin console to reopen."}},
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
    # ---- Synwevo Forms (workplace origin): fill-in requests and what happens to submissions
    "forms.request.assigned": {"category": "automation", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "{requester} 请你填写「{form}」", "zh-Hant": "{requester} 請你填寫「{form}」", "en": "{requester} asks you to fill in “{form}”"}, "body": {
        "zh-Hans": "打开填写页面完成填写；可以先保存草稿，稍后继续。", "zh-Hant": "開啟填寫頁面完成填寫；可以先儲存草稿，稍後繼續。",
        "en": "Open the form to fill it in. You can save a draft and come back later."}},
    "forms.submission.received": {"category": "automation", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "「{form}」收到新的提交", "zh-Hant": "「{form}」收到新的提交", "en": "New submission for “{form}”"}, "body": {
        "zh-Hans": "提交人：{who}。", "zh-Hant": "提交人：{who}。", "en": "Submitted by {who}."}},
    "forms.submission.changes_requested": {"category": "automation", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "「{form}」需要补充或修改", "zh-Hant": "「{form}」需要補充或修改", "en": "“{form}” needs changes"}, "body": {
        "zh-Hans": "{reviewer} 请你补充或修改后重新提交。", "zh-Hant": "{reviewer} 請你補充或修改後重新提交。",
        "en": "{reviewer} asked you to add or correct something and submit again."}},
    "forms.submission.accepted": {"category": "automation", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "你提交的「{form}」已确认", "zh-Hant": "你提交的「{form}」已確認", "en": "Your submission for “{form}” was accepted"}, "body": {
        "zh-Hans": "由 {reviewer} 确认。", "zh-Hant": "由 {reviewer} 確認。", "en": "Accepted by {reviewer}."}},
    # ---- approvals: copying a personal Docs project into an organization workspace
    "copy.requested": {"category": "approval", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "{requester} 申请把项目「{project}」复制到「{workspace}」", "zh-Hant": "{requester} 申請把專案「{project}」複製到「{workspace}」", "en": "{requester} asks to copy “{project}” into “{workspace}”"}, "body": {
        "zh-Hans": "请查看清单后批准或拒绝。", "zh-Hant": "請查看清單後批准或拒絕。", "en": "Review the manifest, then approve or reject."}},
    "copy.approved": {"category": "approval", "severity": "info", "email": "immediate", "title": {
        "zh-Hans": "复制申请「{project}」已批准", "zh-Hant": "複製申請「{project}」已批准", "en": "Copy request for “{project}” approved"}, "body": {
        "zh-Hans": "由 {decider} 批准；副本正在创建。", "zh-Hant": "由 {decider} 批准；副本正在建立。", "en": "Approved by {decider}; the copy is being created."}},
    "copy.rejected": {"category": "approval", "severity": "info", "email": "immediate", "title": {
        "zh-Hans": "复制申请「{project}」被拒绝", "zh-Hant": "複製申請「{project}」被拒絕", "en": "Copy request for “{project}” rejected"}, "body": {
        "zh-Hans": "{decider}：{reason}", "zh-Hant": "{decider}：{reason}", "en": "{decider}: {reason}"}},
    "copy.done": {"category": "approval", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "项目「{project}」已复制到「{workspace}」", "zh-Hant": "專案「{project}」已複製到「{workspace}」", "en": "“{project}” was copied into “{workspace}”"}, "body": {
        "zh-Hans": "副本已可用；原项目保持不变。", "zh-Hant": "副本已可用；原專案保持不變。", "en": "The copy is ready; the original is unchanged."}},
    "copy.failed": {"category": "approval", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "项目「{project}」的复制{outcome}", "zh-Hant": "專案「{project}」的複製{outcome}", "en": "Copying “{project}” {outcome}"}, "body": {
        "zh-Hans": "{reason}", "zh-Hant": "{reason}", "en": "{reason}"}},
    # ---- conversations (the sender is never told about their own message; modes and mute apply)
    "conversation.dm": {"category": "conversation", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "{sender} 给你发了私信", "zh-Hant": "{sender} 傳了私訊給你", "en": "{sender} sent you a direct message"}, "body": {
        "zh-Hans": "{preview}", "zh-Hant": "{preview}", "en": "{preview}"}},
    "conversation.mention": {"category": "conversation", "severity": "action", "email": "digest", "title": {
        "zh-Hans": "{sender} 在「{conversation}」中提到了你", "zh-Hant": "{sender} 在「{conversation}」中提到了你", "en": "{sender} mentioned you in “{conversation}”"}, "body": {
        "zh-Hans": "{preview}", "zh-Hant": "{preview}", "en": "{preview}"}},
    "conversation.group": {"category": "conversation", "severity": "info", "email": "off", "title": {
        "zh-Hans": "「{conversation}」有新消息", "zh-Hant": "「{conversation}」有新訊息", "en": "New messages in “{conversation}”"}, "body": {
        "zh-Hans": "{sender}：{preview}", "zh-Hant": "{sender}：{preview}", "en": "{sender}: {preview}"}},
    "conversation.announcement": {"category": "conversation", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "{actor} 更新了群聊「{conversation}」的公告", "zh-Hant": "{actor} 更新了群組「{conversation}」的公告", "en": "{actor} updated the announcement in “{conversation}”"}, "body": {
        "zh-Hans": "{excerpt}", "zh-Hant": "{excerpt}", "en": "{excerpt}"}},
    "conversation.reported": {"category": "conversation", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "{who} 举报了「{conversation}」里的一条消息", "zh-Hant": "{who} 檢舉了「{conversation}」裡的一則訊息", "en": "{who} reported a message in “{conversation}”"}, "body": {
        "zh-Hans": "请在组织页查看被举报的内容并处理。", "zh-Hant": "請在組織頁檢視被檢舉的內容並處理。", "en": "Review the reported message on the organization page."}},
    "conversation.report_removed": {"category": "conversation", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "你的举报已处理：消息已删除", "zh-Hant": "你的檢舉已處理：訊息已刪除", "en": "Your report was reviewed: the message was removed"}, "body": {
        "zh-Hans": "{note}", "zh-Hant": "{note}", "en": "{note}"}},
    "conversation.report_dismissed": {"category": "conversation", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "你的举报已处理：消息保留", "zh-Hant": "你的檢舉已處理：訊息保留", "en": "Your report was reviewed: the message stays"}, "body": {
        "zh-Hans": "{note}", "zh-Hant": "{note}", "en": "{note}"}},
    "conversation.added": {"category": "conversation", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "{actor} 把你加入了群聊「{conversation}」", "zh-Hant": "{actor} 把你加入了群組「{conversation}」", "en": "{actor} added you to “{conversation}”"}, "body": {
        "zh-Hans": "你能看到加入之后的消息。", "zh-Hant": "你能看到加入之後的訊息。", "en": "You can read messages from the moment you joined."}},
    "conversation.removed": {"category": "conversation", "severity": "info", "email": "off", "title": {
        "zh-Hans": "你已被移出群聊「{conversation}」", "zh-Hant": "你已被移出群組「{conversation}」", "en": "You were removed from “{conversation}”"}, "body": {
        "zh-Hans": "由 {actor} 操作。", "zh-Hant": "由 {actor} 操作。", "en": "By {actor}."}},
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
    "docs.mention": {"category": "docs", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "{who} 在项目「{project}」的评论中提到了你", "zh-Hant": "{who} 在專案「{project}」的評論中提到了你", "en": "{who} mentioned you in a comment on “{project}”"}, "body": {
        "zh-Hans": "{excerpt}", "zh-Hant": "{excerpt}", "en": "{excerpt}"}},
    "docs.comment": {"category": "docs", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "{who} 评论了项目「{project}」", "zh-Hant": "{who} 評論了專案「{project}」", "en": "{who} commented on “{project}”"}, "body": {
        "zh-Hans": "{excerpt}", "zh-Hant": "{excerpt}", "en": "{excerpt}"}},
    "docs.job_finished": {"category": "docs", "severity": "info", "email": "digest", "title": {
        "zh-Hans": "项目「{project}」的生成任务{outcome}", "zh-Hant": "專案「{project}」的產生任務{outcome}", "en": "Generation for “{project}” {outcome}"}, "body": {
        "zh-Hans": "{done} 份成功，{failed} 份失败。", "zh-Hant": "{done} 份成功，{failed} 份失敗。", "en": "{done} succeeded, {failed} failed."}},
    # ---- paid model risk controls (billing/risk.py, docs/platform/model-risk.md): amounts and codes only
    "billing.risk.brake_triggered": {"category": "platform", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "付费调用自动刹车：{risk}的成本速率超过阈值", "zh-Hant": "付費呼叫自動煞車：{risk}的成本速率超過門檻", "en": "Paid-call brake: {risk} cost velocity over its threshold"}, "body": {
        "zh-Hans": "{window} 秒窗口内的成本超过了阈值，该{risk}的付费调用已暂停。核对后可在管理后台解除。", "zh-Hant": "{window} 秒視窗內的成本超過了門檻，該{risk}的付費呼叫已暫停。核對後可在管理後台解除。",
        "en": "Cost within the {window}-second window passed its threshold; paid calls for this {risk} are suspended. Lift it in the admin console after review."}},
    "billing.risk.paid_paused": {"category": "platform", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "你的付费 Agent 调用已暂停", "zh-Hant": "你的付費 Agent 呼叫已暫停", "en": "Your paid Agent calls are paused"}, "body": {
        "zh-Hans": "短时间内的用量超过了平台的安全阈值，付费调用已自动暂停；被拒绝的请求没有发送，也不会扣费。平台管理员核对后会解除暂停。",
        "zh-Hant": "短時間內的用量超過了平台的安全門檻，付費呼叫已自動暫停；被拒絕的請求沒有傳送，也不會扣費。平台管理員核對後會解除暫停。",
        "en": "Usage in a short period passed the platform's safety threshold, so paid calls were paused automatically. Refused requests were not sent and are not charged. A platform administrator will lift the pause after review."}},
    "billing.risk.breaker_opened": {"category": "platform", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "付费调用总闸已关闭", "zh-Hant": "付費呼叫總閘已關閉", "en": "Paid calls are switched off platform-wide"}, "body": {
        "zh-Hans": "原因：{risk}。在管理员核对并重新开启前，所有付费 Agent 调用都会被拒绝，且不会扣费。", "zh-Hant": "原因：{risk}。在管理員核對並重新開啟前，所有付費 Agent 呼叫都會被拒絕，且不會扣費。",
        "en": "Reason: {risk}. Until an administrator reviews and reopens it, every paid Agent call is refused and nothing is charged."}},
    "billing.risk.low_balance": {"category": "platform", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "模型提供方余额偏低", "zh-Hant": "模型供應商餘額偏低", "en": "Model provider balance is low"}, "body": {
        "zh-Hans": "上游账户余额为 {balance} {unit}，已低于预警阈值。请尽快充值；低于停止阈值时付费调用会自动停止。", "zh-Hant": "上游帳戶餘額為 {balance} {unit}，已低於預警門檻。請盡快儲值；低於停止門檻時付費呼叫會自動停止。",
        "en": "The upstream account balance is {balance} {unit}, below the warning threshold. Top it up soon: paid calls stop automatically below the stop threshold."}},
    "billing.risk.reconciliation": {"category": "platform", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "模型成本对账（{day}）：{risk}", "zh-Hant": "模型成本對帳（{day}）：{risk}", "en": "Model cost reconciliation ({day}): {risk}"}, "body": {
        "zh-Hans": "上游余额变化与账本成本的核对结果需要处理，请在管理后台查看金额与原因代码。", "zh-Hant": "上游餘額變化與帳本成本的核對結果需要處理，請在管理後台檢視金額與原因代碼。",
        "en": "The comparison of the upstream balance change with ledger cost needs attention. Review the amounts and reason code in the admin console."}},
    "billing.risk.ledger_audit": {"category": "platform", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "账本余额审计发现 {count} 处差异", "zh-Hant": "帳本餘額稽核發現 {count} 處差異", "en": "Ledger balance audit found {count} differences"}, "body": {
        "zh-Hans": "缓存余额或余数与不可变的账本分录不一致，请在管理后台核对。审计不会自动修改任何余额。", "zh-Hant": "快取餘額或餘數與不可變的帳本分錄不一致，請在管理後台核對。稽核不會自動修改任何餘額。",
        "en": "Cached balances or remainders disagree with the immutable journal. Review them in the admin console; the audit changes no balance by itself."}},
    "billing.risk.pending_exposure": {"category": "platform", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "用量待确认的成本偏高", "zh-Hant": "用量待確認的成本偏高", "en": "Unconfirmed usage exposure is high"}, "body": {
        "zh-Hans": "尚未确认用量的调用预留成本合计 {amount} {unit}，超过提醒阈值。", "zh-Hant": "尚未確認用量的呼叫預留成本合計 {amount} {unit}，超過提醒門檻。",
        "en": "Calls without confirmed usage hold {amount} {unit} of reserved cost, above the alert threshold."}},
    # ---- A26: a platform-requested second-factor reset that fell due and was not carried out, told
    # to the administrator who asked (synwevo_identity.admin_second_factor); `void_reason` is a
    # VOID_WORDS key, `email` the account's address as the administrator already saw it.
    "platform.second_factor_reset_void": {"category": "platform", "severity": "info", "email": "immediate", "title": {
        "zh-Hans": "第二因素重置未执行：{email}", "zh-Hant": "第二因素重設未執行：{email}",
        "en": "Second-factor reset not carried out: {email}"}, "body": {
        "zh-Hans": "到期时{void_reason}，这项重置没有执行，该账户的第二因素没有改变。如仍需要，请重新核实本人身份后再申请。",
        "zh-Hant": "到期時{void_reason}，這項重設沒有執行，該帳戶的第二因素沒有變更。如仍需要，請重新核實本人身分後再申請。",
        "en": "When it fell due, {void_reason}; the reset was not carried out and the account's second factor is unchanged. If it is still needed, confirm the person's identity again and ask anew."}},
    "billing.risk.payment_webhook_failed": {"category": "platform", "severity": "action", "email": "immediate", "title": {
        "zh-Hans": "支付回调处理失败", "zh-Hant": "付款回呼處理失敗", "en": "A payment webhook failed"}, "body": {
        "zh-Hans": "{provider} 的支付回调未能处理（{code}）。请检查支付渠道配置与回调记录。", "zh-Hant": "{provider} 的付款回呼未能處理（{code}）。請檢查付款管道設定與回呼記錄。",
        "en": "A {provider} payment webhook could not be processed ({code}). Check the payment channel configuration and webhook records."}},
}

#: Fixed words for the paid-model risk alerts' `risk` parameter (never free text).
RISK_WORDS = {
    "payer": {"zh-Hans": "付款方", "zh-Hant": "付款方", "en": "payer"},
    "route": {"zh-Hans": "模型路线", "zh-Hant": "模型路線", "en": "model route"},
    "balance": {"zh-Hans": "上游余额低于停止阈值或不可用", "zh-Hant": "上游餘額低於停止門檻或不可用", "en": "the upstream balance is below the stop threshold or unavailable"},
    "upstream_402": {"zh-Hans": "模型提供方报告平台账户余额不足", "zh-Hant": "模型供應商回報平台帳戶餘額不足", "en": "the model provider reported the platform account out of balance"},
    "admin": {"zh-Hans": "平台管理员手动关闭", "zh-Hant": "平台管理員手動關閉", "en": "switched off by a platform administrator"},
    "drift": {"zh-Hans": "差额超出容差", "zh-Hant": "差額超出容差", "en": "difference beyond tolerance"},
    "incomplete": {"zh-Hans": "缺少余额读数，无法核对", "zh-Hant": "缺少餘額讀數，無法核對", "en": "balance readings missing, not reconciled"},
    "error": {"zh-Hans": "执行失败", "zh-Hant": "執行失敗", "en": "failed"},
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
    "push_revoked": {"zh-Hans": "撤销了全部浏览器推送设备", "zh-Hant": "已撤銷全部瀏覽器推播裝置", "en": "All browser push devices were revoked"},
    # A26: the platform-controlled reset of a lost second factor (synwevo_identity.admin_second_factor).
    "second_factor_reset_requested": {"zh-Hans": "平台管理员申请重置你的第二因素，到期才会生效；如果不是你本人要求的，请用邮件里的链接取消",
                                      "zh-Hant": "平台管理員申請重設你的第二因素，到期才會生效；若非你本人要求，請用郵件中的連結取消",
                                      "en": "A platform administrator asked to reset your second factor; it takes effect only after a delay. If you did not ask for this, cancel it with the link in the e-mail"},
    "second_factor_reset_cancelled": {"zh-Hans": "第二因素重置已取消，什么都没有改变", "zh-Hant": "第二因素重設已取消，沒有任何變更",
                                      "en": "The second-factor reset was cancelled; nothing changed"},
    "second_factor_reset_void": {"zh-Hans": "预定的第二因素重置没有执行：{void_reason}；你的第二因素没有改变",
                                 "zh-Hant": "預定的第二因素重設沒有執行：{void_reason}；你的第二因素沒有變更",
                                 "en": "The scheduled second-factor reset did not take place: {void_reason}. Your second factor is unchanged"},
    "second_factor_reset_completed": {"zh-Hans": "你的第二因素已重置：请用口令登录并重新设置动态验证码",
                                      "zh-Hant": "你的第二因素已重設：請以密碼登入並重新設定驗證器動態驗證碼",
                                      "en": "Your second factor was reset: sign in with your password and set up authenticator verification again"},
}
#: Why a platform-requested second-factor reset that fell due was not carried out (A26,
#: synwevo_identity.admin_second_factor `void_reason`), as a clause: "When it fell due, <clause>".
VOID_WORDS = {
    "account_deleted": {"zh-Hans": "账户已删除", "zh-Hant": "帳戶已刪除", "en": "the account was deleted"},
    "account_state_conflict": {"zh-Hans": "账户已不是正常使用状态", "zh-Hant": "帳戶已不是正常使用狀態", "en": "the account was no longer active"},
    "account_suspended": {"zh-Hans": "账户已被停用", "zh-Hant": "帳戶已被停用", "en": "the account was suspended"},
    "email_unverified": {"zh-Hans": "账户邮箱尚未验证", "zh-Hant": "帳戶電子郵件地址尚未驗證", "en": "the account's email address was not verified"},
    "email_changed": {"zh-Hans": "登录邮箱在等待期间更改过", "zh-Hant": "登入電子郵件地址在等待期間變更過", "en": "the sign-in email had changed while it was waiting"},
    "target_is_platform_admin": {"zh-Hans": "账户已持有平台权限", "zh-Hant": "帳戶已持有平台權限", "en": "the account held a platform role"},
    "application_identity": {"zh-Hans": "账户是应用身份", "zh-Hant": "帳戶是應用程式身分", "en": "the account was an application identity"},
    "authority_changed": {"zh-Hans": "申请的管理员已不再有权执行这项重置", "zh-Hant": "申請的管理員已不再有權執行這項重設",
                          "en": "the administrator who asked no longer held the authority to carry it out"},
    "announcement_undelivered": {"zh-Hans": "通知邮件没有送达", "zh-Hant": "通知郵件沒有送達", "en": "the announcement e-mail had not been delivered"},
    "nothing_to_reset": {"zh-Hans": "已没有可以重置的第二因素", "zh-Hant": "已沒有可以重設的第二因素", "en": "there was no second factor left to reset"},
    "factors_changed": {"zh-Hans": "第二因素在等待期间被使用或更改过，说明它并未丢失", "zh-Hant": "第二因素在等待期間被使用或變更過，表示它並未遺失",
                        "en": "the second factor had been used or changed while it was waiting, so it is not lost"},
    "restored_invalidated": {"zh-Hans": "平台从备份恢复了数据", "zh-Hant": "平台從備份還原了資料", "en": "the platform was restored from a backup"},
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
UNREAD_TITLE = {
    "zh-Hans": "「{conversation}」有 {n} 条未读消息", "zh-Hant": "「{conversation}」有 {n} 則未讀訊息", "en": "{n} unread in “{conversation}”",
}
ATTACHMENT_WORD = {"zh-Hans": "[附件]", "zh-Hant": "[附件]", "en": "[attachment]"}
#: Who did it, when the actor is a role rather than a person the reader knows (`actor_kind`, set
#: only by server code): a platform administrator acting on a workspace — undoing the assignment
#: of the historical data — is named by the role. Their email or name would otherwise go to every
#: former member, including an account the data was assigned to by mistake.
ACTOR_WORDS = {
    "platform_admin": {"zh-Hans": "平台管理员", "zh-Hant": "平台管理員", "en": "a platform administrator"},
}
OUTCOME_WORDS = {
    "completed": {"zh-Hans": "已完成", "zh-Hant": "已完成", "en": "finished"},
    "completed_with_errors": {"zh-Hans": "已完成，部分失败", "zh-Hant": "已完成，部分失敗", "en": "finished with errors"},
    "failed": {"zh-Hans": "失败", "zh-Hant": "失敗", "en": "failed"},
    "cancelled": {"zh-Hans": "已取消", "zh-Hant": "已取消", "en": "was cancelled"},
}


def default_preferences() -> dict:
    cats = {c: {"in_app": True, "email": "immediate", "push": True} for c in CATEGORIES}
    cats["docs"]["email"] = "digest"
    cats["conversation"]["email"] = "off"          # opt-in, and then only the unread digest
    # `preview` decides whether message text leaves the platform in mail and push; off by default.
    return {"categories": cats, "quiet_start": None, "quiet_end": None, "timezone": None, "preview": "minimal"}


def _locale(value: str | None) -> str:
    return value if value in LOCALES else "zh-Hans"


class Notifier:
    def __init__(self, store: Store):
        self.store = store
        self._docs_base: tuple[float, str | None] | None = None
        self._messages_base: tuple[float, str | None] | None = None

    def messages_base(self) -> str | None:
        """The Synwevo Message origin for links, from the app registry (written by the process
        that serves it), like :meth:`docs_base`. None until the application is registered, in
        which case a `messages` target has no link rather than a guessed one. Cached for a minute."""
        if self._messages_base and time.time() - self._messages_base[0] < 60:
            return self._messages_base[1]
        try:
            from .apps import messages_origin
            base = messages_origin(self.store)
        except Exception:  # noqa: BLE001 - a link is optional; the notification is not
            base = None
        self._messages_base = (time.time(), base)
        return base

    def docs_base(self) -> str | None:
        """The Synwevo Docs origin for links, from the app registry (written by the process that
        serves Docs), so the account service — whose own `SYNWEVO_APP_URL` is its own address —
        never builds a Docs link on the wrong host. Cached for a minute."""
        if self._docs_base and time.time() - self._docs_base[0] < 60:
            return self._docs_base[1]
        base = None
        try:
            from .apps import DOCS_APP_ID
            row = self.store.one("SELECT launch_url FROM apps WHERE id=?", (DOCS_APP_ID,))
            url = (row or {}).get("launch_url") or ""
            if url.startswith(("http://", "https://")):
                from urllib.parse import urlsplit
                u = urlsplit(url)
                base = f"{u.scheme}://{u.netloc}"
        except Exception:  # noqa: BLE001 - a link is optional; the notification is not
            base = None
        self._docs_base = (time.time(), base)
        return base

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
                t.execute("INSERT INTO notification_deliveries (notification_id,channel,status,not_before,updated_at,defer_reason,enqueue_protocol) VALUES (?,?,?,?,?,?,?)",
                          (row["id"], "email", "pending", not_before, now(),
                           "aggregation" if email_mode == "digest" and not critical else None, "atomic-v1"))
            else:
                t.execute("INSERT INTO notification_deliveries (notification_id,channel,status,not_before,updated_at,last_error) "
                          "VALUES (?,?,?,?,?,?)", (row["id"], "email", "skipped", None, now(), "channel off"))
            # Web Push: only when the reader has a device subscribed and the category's push
            # switch is on (security always). The dispatcher fans out per subscription later.
            if (critical or cat.get("push", True)) and self._has_push_device(user_id):
                t.execute("INSERT INTO push_queue (notification_id,user_id,status,not_before,updated_at) VALUES (?,?,?,?,?)",
                          (row["id"], user_id, "pending", row["created_at"], now()))
        return row

    def _has_push_device(self, user_id: str) -> bool:
        try:
            from .push import PushSubscriptions
            return PushSubscriptions(self.store).has_active(user_id)
        except Exception:  # noqa: BLE001 - the inbox row must land even if the push tables are unavailable
            return False

    # ------------------------------------------------------------------ the Web Push queue
    def due_push(self, limit: int = 200) -> list[dict]:
        ts = now()
        return self.store.all("SELECT q.notification_id, q.user_id, q.attempts, n.kind, n.category, n.severity, n.params, n.target, n.created_at AS notified_at "
                              "FROM push_queue q JOIN notifications n ON n.id=q.notification_id "
                              "WHERE q.status='pending' AND (q.not_before IS NULL OR q.not_before<=?) ORDER BY q.not_before LIMIT ?", (ts, limit))

    def defer_push(self, notification_id: str, until: float) -> None:
        self.store.execute("UPDATE push_queue SET not_before=?, updated_at=? WHERE notification_id=?", (until, now(), notification_id))

    def claim_push(self, notification_id: str) -> bool:
        return self.store.execute("UPDATE push_queue SET status='claimed', updated_at=? WHERE notification_id=? AND status='pending'",
                                  (now(), notification_id)) > 0

    def finish_push(self, notification_id: str, status: str, *, error: str | None = None) -> None:
        if status not in ("sent", "skipped", "failed"):
            raise ValueError(status)
        self.store.execute("UPDATE push_queue SET status=?, last_error=?, updated_at=? WHERE notification_id=?",
                           (status, (error or None) and error[:200], now(), notification_id))

    def retry_push(self, notification_id: str, error: str) -> None:
        row = self.store.one("SELECT attempts FROM push_queue WHERE notification_id=?", (notification_id,))
        attempts = int((row or {}).get("attempts") or 0) + 1
        if attempts > len(RETRY_DELAYS_S):
            self.store.execute("UPDATE push_queue SET status='failed', attempts=?, last_error=?, updated_at=? WHERE notification_id=?",
                               (attempts, error[:200], now(), notification_id))
            return
        self.store.execute("UPDATE push_queue SET status='pending', attempts=?, not_before=?, last_error=?, updated_at=? WHERE notification_id=?",
                           (attempts, now() + RETRY_DELAYS_S[attempts - 1], error[:200], now(), notification_id))

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
                "href": resolve_target(target, docs_base=self.docs_base(), messages_base=self.messages_base()), "action_state": row.get("action_state"),
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

    def mark_object_read(self, user_id: str, object_kind: str, object_id: str, *, kinds: tuple[str, ...] | None = None) -> int:
        """The reader saw the object itself, so the inbox items about it are read too: a
        conversation read to its end reads its "X 给你发了私信" items (the messages application
        calls this when the read cursor reaches the last message). An inbox read and nothing more —
        `action_state` is untouched, nothing is approved or completed — and only this reader's rows.
        `kinds` narrows it to the item kinds the object produces for a reader."""
        sql = "UPDATE notifications SET read_at=COALESCE(read_at, ?) WHERE user_id=? AND object_kind=? AND object_id=? AND read_at IS NULL"
        args: list = [now(), user_id, object_kind, object_id]
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            args.extend(kinds)
        return int(self.store.execute(sql, tuple(args)) or 0)

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
                    email = saved[c].get("email") if saved[c].get("email") in EMAIL_MODES else "immediate"
                    if c == "conversation" and email not in CONVERSATION_EMAIL_MODES:
                        email = "digest"                 # a row from before the rule: the closest allowed mode
                    out["categories"][c] = {"in_app": bool(saved[c].get("in_app", True)), "email": email,
                                            "push": bool(saved[c].get("push", True))}
            out.update({"quiet_start": row["quiet_start"], "quiet_end": row["quiet_end"], "timezone": row["timezone"], "preview": row["preview"]})
        out["conversation_digest_policy"] = self.conversation_digest_policy()
        return out

    def conversation_digest_policy(self) -> dict:
        """Safe current limits for a reader; no governance history, sources or actor data."""
        from .platform_settings import PlatformSettings, INTERVAL_KEY, DAILY_CAP_KEY
        settings = PlatformSettings(self.store).read()
        return {"revision": settings["revision"], "interval_s": settings["values"][INTERVAL_KEY],
                "daily_cap": settings["values"][DAILY_CAP_KEY],
                "aggregation_window_s": DIGEST_WINDOW_S, "window_s": 86400}

    def set_preferences(self, user_id: str, data: dict) -> dict:
        if not isinstance(data, dict) or set(data) - {"categories", "quiet_start", "quiet_end", "timezone", "preview"}:
            raise ServiceError(400, "invalid_field", "通知偏好包含不支持的字段")
        current = self.preferences(user_id)
        cats = current["categories"]
        for c, v in (data.get("categories") or {}).items():
            if c not in CATEGORIES or not isinstance(v, dict) or set(v) - {"in_app", "email", "push"}:
                raise ServiceError(400, "invalid_field", "未知的通知类别或字段", {"field": "categories"})
            if "in_app" in v:
                cats[c]["in_app"] = bool(v["in_app"])
            if "email" in v:
                if v["email"] not in EMAIL_MODES:
                    raise ServiceError(400, "invalid_field", "邮件方式必须是 immediate、digest 或 off", {"field": "categories"})
                if c == "conversation" and v["email"] not in CONVERSATION_EMAIL_MODES:
                    raise ServiceError(400, "invalid_field", "消息不逐条发邮件：只能选 digest（未读摘要）或 off", {"field": "categories"})
                cats[c]["email"] = v["email"]
            if "push" in v:
                cats[c]["push"] = bool(v["push"])
        # Security stays on: the switches are stored (so the page can say "always on"), never honoured.
        cats["security"] = {"in_app": True, "email": "immediate", "push": True}
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

    # ------------------------------------------------------------------ send-time re-check
    def stale_reason(self, d: dict, prefs: dict) -> str | None:
        """Why a mail or push should not leave any more, decided when it is about to be sent rather
        than when the event happened: the reader read the item in the app, turned the channel
        off since, or — for a conversation item — read the conversation, muted or left it, or
        every unread message in it was recalled or comes from someone the reader blocked (CI-78:
        "nothing unread"). An item about something a workspace holds — a project discussion, a
        report of a message in one, a Docs, Forms or project-copy item — whose workspace is not
        available now — frozen, deleted (recoverable or not), itself or its organization — is
        `workspace_unavailable` (A44, `workspace_closed`): the app refuses every read of it, so
        nothing about it leaves by mail or push either. A conversation item, or a report of a
        message, whose organization has Synwevo Message switched off (or the platform has) is
        `messages_disabled` (B3), and a report of a message in a frozen or deleted organization is
        `organization_unavailable` (G2; `messages_closed`): skipped, never re-sent once it is back;
        the inbox item stays. Security items are never stale. `d` is a
        delivery/queue row joined with its notification (`due_deliveries` / `due_push`)."""
        if d.get("severity") == "critical":
            return None
        row = self.store.one("SELECT read_at, archived_at, category, workspace_id, object_kind, object_id FROM notifications WHERE id=?",
                             (d["notification_id"],))
        if not row:
            return "notification gone"
        if row["read_at"] or row["archived_at"]:
            return "read in app"
        closed = self.messages_closed(row)
        if closed:
            return closed
        if self.workspace_closed(row):
            return "workspace_unavailable"
        if d.get("category") == "conversation" and row["object_kind"] == "conversation" and row["object_id"]:
            state = self.conversation_unread(row["object_id"], d["user_id"])
            if state is None:
                return "no longer a member"
            if state["muted"]:
                return "conversation muted"
            if state["unread"] == 0:
                return "nothing unread"
            # One item per unread span; when the reader read a span and a newer one opened before
            # the digest went out, the newer item describes the conversation — the older is noise.
            newer = self.store.one("SELECT 1 AS x FROM notifications WHERE user_id=? AND object_kind='conversation' AND object_id=? AND kind=? AND created_at>?",
                                   (d["user_id"], row["object_id"], d["kind"], d.get("notified_at") or 0))
            if newer:
                return "superseded"
        return None

    #: Categories whose items are about content a workspace holds, stored with that workspace's
    #: id: Docs (`docs.comment`, `docs.mention`, `docs.job_finished`, `docs.file_rejected` — the
    #: project's workspace), Forms (`automation`: `forms.request.assigned`, `forms.submission.*` —
    #: the workspace the form lives in, `forms/service.py` `_notify`) and project copies
    #: (`approval`: `copy.*` — the target workspace, where the request is decided and the copy
    #: lands; `copies.py`, `approval_routes.py`). The `organization` category is left out on
    #: purpose although its invitation and member items carry a `workspace_id` too: they tell a
    #: person about their *own* standing (invited, role changed, removed, a member joined or left
    #: through their invitation), which `workspace_availability` deliberately does not judge and
    #: which stays true whatever the workspace's state — "you were removed from X" must reach
    #: them while X is frozen, and recovering X does not undo it.
    WORKSPACE_CONTENT_CATEGORIES = ("docs", "automation", "approval")

    def workspace_closed(self, row: dict) -> bool:
        """Whether an item is about something its workspace's state closes now: a conversation
        item of a project discussion whose workspace is not available
        (`messaging_bridge.conversation_availability`, the rule `Messaging` refuses reads by); a
        report of a message in such a discussion (`message_report`: report → message →
        conversation — the managers' `conversation.reported` and the reporter's outcome); or a
        Docs, Forms or project-copy item (`WORKSPACE_CONTENT_CATEGORIES`) whose `workspace_id` is
        not available (`workspace_availability`). The item stays in the inbox; its mail or push
        is skipped rather than deferred, because the app shows the same item once the workspace
        is back."""
        if row.get("category") == "conversation" and row.get("object_id"):
            if row.get("object_kind") == "conversation":
                return self._conversation_closed(row["object_id"])
            if row.get("object_kind") == "message_report":
                cid = self._reported_conversation(row["object_id"])
                return bool(cid) and self._conversation_closed(cid)
            return False
        if row.get("category") in self.WORKSPACE_CONTENT_CATEGORIES and row.get("workspace_id"):
            from .workspace_availability import AVAILABLE, availability
            return availability(self.store, row["workspace_id"]) != AVAILABLE
        return False

    def messages_closed(self, row: dict) -> str | None:
        """Why an item about a conversation must not leave by mail or push because of its
        organization (B3): `messages_disabled` while Synwevo Message is switched off for the
        conversation's organization, or platform-wide — for a conversation item and for a report
        of a message in it (`message_report`: the managers' to-do and the reporter's outcome);
        `organization_unavailable` for such a report while the organization is frozen, deleted or
        gone (G2 — a conversation item of such an organization is already `no longer a member`,
        `conversation_unread`). None otherwise. The item stays in the inbox; skipped mail is not
        sent again when the organization comes back."""
        if row.get("category") != "conversation" or not row.get("object_id"):
            return None
        if row.get("object_kind") == "conversation":
            cid = row["object_id"]
        elif row.get("object_kind") == "message_report":
            cid = self._reported_conversation(row["object_id"])
        else:
            return None
        conv = self.store.one("SELECT organization_id FROM conversations WHERE id=?", (cid,)) if cid else None
        if not conv:
            return None
        from .messaging import DISABLED, organization_state
        from .workspace_availability import AVAILABLE
        state, _ = organization_state(self.store, conv["organization_id"])
        if state == DISABLED:
            return "messages_disabled"
        if state != AVAILABLE and row.get("object_kind") == "message_report":
            return "organization_unavailable"
        return None

    def _reported_conversation(self, rid: str) -> str | None:
        """The conversation of the message a report is about (the report keeps it too, for a
        message removed since)."""
        row = self.store.one("SELECT COALESCE(m.conversation_id, r.conversation_id) AS cid FROM message_reports r "
                             "LEFT JOIN messages m ON m.id=r.message_id WHERE r.id=?", (rid,))
        return (row or {}).get("cid")

    def _conversation_closed(self, cid: str) -> bool:
        from .messaging_bridge import conversation_availability
        from .workspace_availability import AVAILABLE
        return conversation_availability(self.store, cid) != AVAILABLE

    def conversation_unread(self, cid: str, user_id: str) -> dict | None:
        """The reader's live unread state in one conversation — messages after their read cursor
        that still exist, and the latest of them — or None when they are not (or no longer) a
        member, or the conversation is a project discussion whose workspace is not available
        (`workspace_closed`): nothing of it is rendered for mail or push then. Read here rather
        than through the messaging service because the dispatcher has no request context; the
        tables are synwevo_core.messaging's."""
        m = self.store.one("SELECT m.last_read_seq, m.joined_seq, m.muted, m.muted_until, m.notify, c.organization_id "
                           "FROM conversation_members m JOIN conversations c ON c.id=m.conversation_id "
                           "WHERE m.conversation_id=? AND m.user_id=? AND m.left_at IS NULL", (cid, user_id))
        if not m:
            return None
        from .organizations import current_membership
        # Governance membership deliberately survives frozen/deleted organizations for
        # recovery. That standing is not permission to send their business notifications.
        org = self.store.one("SELECT status,deleted_at FROM organizations WHERE id=?", (m["organization_id"],))
        if (not org or org["status"] != "active" or org["deleted_at"] is not None
                or not current_membership(self.store, m["organization_id"], user_id)):
            return None
        if self._conversation_closed(cid):
            return None
        floor = max(int(m["last_read_seq"] or 0), int(m["joined_seq"] or 0))
        # `deleted_at` is set by every delete-for-everyone — the sender's recall, a group manager's
        # removal, a report review (CI-74) — so a recalled message never counts as unread here.
        # Messages from someone the reader blocked (CI-78) stay visible in a shared group but never
        # make a digest: they are not counted, and never become the "latest" line.
        agg = self.store.one("SELECT COUNT(*) AS n, MAX(seq) AS last FROM messages WHERE conversation_id=? AND seq>? AND deleted_at IS NULL "
                             "AND sender_id NOT IN (SELECT blocked_id FROM message_blocks WHERE user_id=?)", (cid, floor, user_id))
        latest = None
        if agg and agg["n"]:
            latest = self.store.one("SELECT m.body, m.attachment_id, u.display_name, u.email FROM messages m JOIN users u ON u.id=m.sender_id "
                                    "WHERE m.conversation_id=? AND m.seq=?", (cid, agg["last"]))
        # silent: the standing mute, "no notifications", or a `muted_until` still ahead (CI-74)
        muted = bool(m["muted"]) or m["notify"] == "none" or bool(m.get("muted_until") and float(m["muted_until"]) > now())
        return {"unread": int((agg or {}).get("n") or 0), "muted": muted, "latest": latest}

    def conversation_digest_hold(self, user_id: str, at: float | None = None, *, policy: dict | None = None) -> float | None:
        """Next allowed unread digest under the current platform budget, counting mails.

        The dispatcher passes the snapshot read while holding its writer transaction; other
        callers get a fresh read. The initial aggregation, quiet hours and retry wait are
        independent and are never shortened by this budget calculation.
        """
        policy = self.conversation_digest_policy() if policy is None else policy
        ts = at if at is not None else now()
        rows = self.store.all("SELECT d.mail_id, MIN(d.updated_at) AS sent_at FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id "
                              "WHERE n.user_id=? AND n.category='conversation' AND d.channel='email' AND d.status='queued' AND d.mail_id IS NOT NULL "
                              "AND d.updated_at>? GROUP BY d.mail_id ORDER BY sent_at", (user_id, ts - 86400))
        if not rows:
            return None
        last = max(float(r["sent_at"]) for r in rows)
        until = last + policy["interval_s"]
        cap = policy["daily_cap"]
        if len(rows) >= cap:
            # A lowered cap can be smaller than the existing history. Enough mails must age
            # out, not just its oldest one, before another digest has a slot.
            until = max(until, float(rows[len(rows) - cap]["sent_at"]) + 86400)
        return until if until > ts else None

    def render_external(self, row: dict, locale: str, prefs: dict) -> dict:
        """What leaves the platform (mail, push) for one item. A conversation item is rebuilt from
        the live unread state — how many messages are unread now, the latest one's text only when
        the reader chose `preview` = full — so a recalled message is never mailed and, by default,
        no message text is."""
        item = self.render(row, locale)
        if self.messages_closed(row) or self.workspace_closed(row):
            # `stale_reason` skips these before they are rendered; should one be rendered anyway,
            # no comment excerpt, message text or announcement leaves with it.
            item["body"] = ""
            return item
        if row.get("category") != "conversation" or row.get("object_kind") != "conversation" or not row.get("object_id"):
            return item
        loc = _locale(locale)
        state = self.conversation_unread(row["object_id"], row["user_id"])
        params = _json(row.get("params")) or {}
        if row["kind"] in ("conversation.dm", "conversation.mention", "conversation.group") and state and state["unread"]:
            label = params.get("conversation") or params.get("sender") or ""
            item["title"] = _fmt(UNREAD_TITLE[loc], {"conversation": label, "n": state["unread"]})
            latest = state.get("latest")
            if prefs.get("preview") == "full" and latest:
                who = latest["display_name"] or (latest["email"] or "").split("@")[0]
                text = (latest["body"] or "")[:120] or (ATTACHMENT_WORD[loc] if latest["attachment_id"] else "")
                item["body"] = f"{who}：{text}" if loc != "en" else f"{who}: {text}"
            else:
                item["body"] = ""
        elif prefs.get("preview") != "full" and row["kind"] in ("conversation.dm", "conversation.mention", "conversation.group", "conversation.announcement"):
            item["body"] = ""
        return item

    # ------------------------------------------------------------------ the e-mail outbox side
    @staticmethod
    def _email_recovery_metadata(row: dict) -> dict:
        """No content or arbitrary transport error crosses this governance boundary."""
        blocked = (
            "not_failed" if row["status"] != "failed" else
            "mail_already_queued" if row["mail_id"] or row["has_mail"] else
            "historical_delivery" if row["enqueue_protocol"] != "atomic-v1" else
            "unverified_failure" if row["last_error"] != "notification_enqueue_failed" else
            "retry_not_exhausted" if row["attempts"] <= len(RETRY_DELAYS_S) else None
        )
        return {key: row[key] for key in ("notification_id", "user_id", "category", "status", "attempts", "updated_at")} | {
            "error_code": ("notification_enqueue_failed" if row["last_error"] == "notification_enqueue_failed"
                           else "notification_delivery_failed" if row["last_error"] else None),
            "recoverable": blocked is None, "blocked_reason": blocked,
        }

    def email_recovery_row(self, notification_id: str) -> dict:
        """A metadata snapshot for the authorized admin service, not an authorization grant.

        Newly emitted rows carry atomic-v1 from birth. An old row never gains that marker
        merely because a newer dispatcher touched it. A direct outbox match additionally
        vetoes recovery, but its absence cannot establish historical digest membership.
        """
        row = self.store.one(
            "SELECT d.notification_id,d.status,d.attempts,d.updated_at,d.last_error,d.mail_id,"
            "d.not_before,d.defer_reason,d.policy_revision,d.enqueue_protocol,n.user_id,n.category,"
            "EXISTS(SELECT 1 FROM mail_outbox m WHERE m.kind='notification' AND m.ref_id=d.notification_id) AS has_mail "
            "FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id "
            "WHERE d.notification_id=? AND d.channel='email'", (notification_id,))
        if not row:
            raise ServiceError(404, "notification_delivery_missing", "找不到该通知投递记录")
        version = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        return self._email_recovery_metadata(row) | {"version": version}

    def email_recovery_page(self, *, q: str = "", limit: int = 25, offset: int = 0,
                            snapshot: int | None = None) -> dict:
        if (not isinstance(q, str) or len(q) > 100 or type(limit) is not int or not 1 <= limit <= 100
                or type(offset) is not int or not 0 <= offset <= 2**63 - 1
                or (snapshot is not None and (type(snapshot) is not int or not 0 <= snapshot <= 2**63 - 1))):
            raise ServiceError(400, "bad_notification_page", "通知查询参数无效")
        with self.store.tx():
            high = int(self.store.one("SELECT COALESCE(MAX(seq),0) AS n FROM notifications")["n"])
            snapshot = high if snapshot is None else min(snapshot, high)
            where = ("d.channel='email' AND d.status='failed' AND n.seq<=? "
                     "AND (?='' OR instr(d.notification_id,?)>0 OR instr(n.user_id,?)>0)")
            params = (snapshot, q, q, q)
            total = int(self.store.one(
                "SELECT COUNT(*) AS n FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id WHERE " + where,
                params)["n"])
            rows = self.store.all(
                "SELECT d.notification_id,d.status,d.attempts,d.updated_at,d.last_error,d.mail_id,d.enqueue_protocol,n.user_id,n.category,"
                "EXISTS(SELECT 1 FROM mail_outbox m WHERE m.kind='notification' AND m.ref_id=d.notification_id) AS has_mail "
                "FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id WHERE " + where +
                " ORDER BY n.seq DESC LIMIT ? OFFSET ?", (*params, limit, offset))
        return {"items": [self._email_recovery_metadata(row) for row in rows], "total": total,
                "limit": limit, "offset": offset, "snapshot": snapshot,
                "next_offset": offset + len(rows) if offset + len(rows) < total else None}

    def recover_email_enqueue(self, notification_id: str, version: str) -> dict:
        """Schedule one eligibility cycle; caller owns authorization, preview and audit.

        The enclosing admin transaction commits this change with its durable receipt. The
        dispatcher remains the only enqueuer and rechecks preferences, read state and budget.
        Cumulative attempts are never reset: another enqueue failure is immediately terminal.
        """
        with self.store.tx():
            row = self.email_recovery_row(notification_id)
            if row["version"] != version:
                raise ServiceError(409, "notification_delivery_changed", "通知状态已变化，请重新预览")
            if not row["recoverable"]:
                raise ServiceError(409, "notification_not_recoverable", "该通知不能安全恢复排队",
                                   {"blocked_reason": row["blocked_reason"]})
            changed = self.store.execute(
                "UPDATE notification_deliveries SET status='pending',not_before=?,updated_at=?,defer_reason=NULL,policy_revision=NULL "
                "WHERE notification_id=? AND channel='email' AND status='failed' AND attempts=? AND updated_at=?",
                (now(), now(), notification_id, row["attempts"], row["updated_at"]))
            if changed != 1:
                raise ServiceError(409, "notification_delivery_changed", "通知状态已变化，请重新预览")
        return {"notification_id": notification_id, "status": "pending", "attempts": row["attempts"], "recovery_scheduled": True}

    def due_deliveries(self, limit: int = 200) -> list[dict]:
        ts = now()
        revision = self.conversation_digest_policy()["revision"]
        return self.store.all("SELECT d.*, n.user_id, n.kind, n.category, n.severity, n.params, n.target, n.created_at AS notified_at "
                              "FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id "
                              "WHERE d.channel='email' AND d.status='pending' "
                              "AND (d.not_before IS NULL OR d.not_before<=? "
                              "OR (d.defer_reason='budget' AND COALESCE(d.policy_revision,-1)<>?)) "
                              "ORDER BY d.not_before, d.notification_id LIMIT ?", (ts, revision, limit))

    def pending_email_deliveries(self, user_id: str, notification_ids: list[str], *, policy_revision: int | None = None) -> list[dict]:
        """Re-read a dispatcher's bounded candidates under its write transaction.

        Only a wait caused by an earlier budget can be reconsidered before not_before.
        Initial aggregation, quiet hours, retry and unclassified historical waits remain due
        on their original schedule. No row's schedule changes merely by reading candidates.
        """
        if not notification_ids:
            return []
        revision = self.conversation_digest_policy()["revision"] if policy_revision is None else policy_revision
        placeholders = ",".join("?" for _ in notification_ids)
        return self.store.all("SELECT d.*, n.user_id, n.kind, n.category, n.severity, n.params, n.target, n.created_at AS notified_at "
                              "FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id "
                              "WHERE n.user_id=? AND d.channel='email' AND d.status='pending' "
                              "AND (d.not_before IS NULL OR d.not_before<=? "
                              "OR (d.defer_reason='budget' AND COALESCE(d.policy_revision,-1)<>?)) "
                              f"AND d.notification_id IN ({placeholders}) ORDER BY d.not_before, d.notification_id",
                              (user_id, now(), revision, *notification_ids))

    def defer_delivery(self, notification_id: str, until: float, *, reason: str = "quiet", policy_revision: int | None = None) -> None:
        if reason not in ("quiet", "budget") or (reason == "budget" and type(policy_revision) is not int):
            raise ValueError("invalid notification defer reason or policy revision")
        self.store.execute("UPDATE notification_deliveries SET not_before=?, updated_at=?, defer_reason=?, policy_revision=? "
                           "WHERE notification_id=? AND channel='email'",
                           (until, now(), reason, policy_revision if reason == "budget" else None, notification_id))

    def finish_delivery(self, notification_id: str, status: str, *, mail_id: str | None = None, error: str | None = None) -> None:
        if status not in ("queued", "skipped", "failed"):
            raise ValueError(status)
        self.store.execute("UPDATE notification_deliveries SET status=?, mail_id=?, last_error=?, updated_at=?, defer_reason=NULL, "
                           "policy_revision=CASE WHEN ?='queued' THEN policy_revision ELSE NULL END "
                           "WHERE notification_id=? AND channel='email'",
                           (status, mail_id, (error or None) and error[:200], now(), status, notification_id))

    def retry_delivery(self, notification_id: str, error: str) -> None:
        row = self.store.one("SELECT attempts FROM notification_deliveries WHERE notification_id=? AND channel='email'", (notification_id,))
        attempts = int((row or {}).get("attempts") or 0) + 1
        if attempts > len(RETRY_DELAYS_S):
            self.store.execute("UPDATE notification_deliveries SET status='failed', attempts=?, last_error=?, updated_at=?, "
                               "defer_reason=NULL, policy_revision=NULL WHERE notification_id=? AND channel='email'",
                               (attempts, error[:200], now(), notification_id))
            return
        self.store.execute("UPDATE notification_deliveries SET attempts=?, not_before=?, last_error=?, updated_at=?, "
                           "defer_reason='retry', policy_revision=NULL WHERE notification_id=? AND channel='email'",
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
    if out.get("actor_kind") in ACTOR_WORDS:
        # A separate key, never the `actor` text itself: a person whose display name happens to
        # read "platform_admin" must not be rendered as the platform's administrator.
        out["actor"] = ACTOR_WORDS[out["actor_kind"]][loc]
    if "role" in out and out["role"] in ROLE_WORDS:
        out["role"] = ROLE_WORDS[out["role"]][loc]
    if "void_reason" in out and out["void_reason"] in VOID_WORDS:
        out["void_reason"] = VOID_WORDS[out["void_reason"]][loc]
    if "what" in out and out["what"] in SECURITY_WHAT:
        out["what"] = _fmt(SECURITY_WHAT[out["what"]][loc], out)
    if "outcome" in out and out["outcome"] in OUTCOME_WORDS:
        out["outcome"] = OUTCOME_WORDS[out["outcome"]][loc]
    if "risk" in out and out["risk"] in RISK_WORDS:
        out["risk"] = RISK_WORDS[out["risk"]][loc]
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


def resolve_target(target: dict | None, *, docs_base: str | None = None,
                   messages_base: str | None = None) -> str | None:
    """The absolute link for a stored target, from this deployment's configured origins only.
    `docs` links resolve through the registered Docs origin (`docs_base`, from the app registry)
    and, failing that, through SYNWEVO_APP_URL only in a process that serves Docs itself — in the
    account service that variable is the account service's own address (mails once carried
    `accounts…/p/<id>`). `messages` links (conversations, since CI-66) resolve the same way
    through the registered Synwevo Message origin (`messages_base`), or SYNWEVO_APP_URL in the
    process that serves messages. Otherwise the item has no link rather than a guessed one."""
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
        role = (env.get("ROLE") or "all").strip().lower()
        base = docs_base or (env.get("APP_URL") if role in ("app", "all", "") else None)
    elif origin == "messages":
        role = (env.get("ROLE") or "all").strip().lower()
        base = messages_base or (env.get("APP_URL") if role in ("messages", "all", "") else None)
    if not base:
        return None
    return base.rstrip("/") + path
