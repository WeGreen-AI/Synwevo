"""The catalogue of platform-administration actions (admin coverage G24, "动作目录").

Every `admin.*` action the account service audits is listed here with its scope and a label in
the three interface languages, so the audit page can offer the actions by name and a reader sees
what a code means. `test_admin.py` scans the source for `"admin.…"` literals and refuses a new
action that is not catalogued: the catalogue cannot silently fall behind the code.

Labels name the action, never its outcome or its object; the audit row carries those.
"""
from __future__ import annotations

SCOPES: dict[str, dict[str, str]] = {
    "access": {"zh-Hans": "被拒绝的管理请求", "zh-Hant": "被拒絕的管理請求", "en": "Refused administration requests"},
    "user": {"zh-Hans": "账户管理", "zh-Hant": "帳戶管理", "en": "Account administration"},
    "workspace": {"zh-Hans": "工作空间管理", "zh-Hant": "工作空間管理", "en": "Workspace administration"},
    "organization": {"zh-Hans": "组织管理", "zh-Hant": "組織管理", "en": "Organization administration"},
    "platform": {"zh-Hans": "平台权限", "zh-Hant": "平台權限", "en": "Platform permissions"},
    "config": {"zh-Hans": "平台配置与安全策略", "zh-Hant": "平台設定與安全策略", "en": "Platform settings and security policy"},
    "mail": {"zh-Hans": "邮件与通知", "zh-Hant": "郵件與通知", "en": "Mail and notifications"},
    "model": {"zh-Hans": "模型提供方、价格与风险", "zh-Hant": "模型提供方、價格與風險", "en": "Model providers, prices and risk"},
    "billing": {"zh-Hans": "付费与钱包", "zh-Hant": "付費與錢包", "en": "Billing and wallets"},
    "agent_safety": {"zh-Hans": "Agent 内容安全", "zh-Hant": "Agent 內容安全", "en": "Agent content safety"},
}


def _a(scope: str, hans: str, hant: str, en: str) -> dict:
    return {"scope": scope, "label": {"zh-Hans": hans, "zh-Hant": hant, "en": en}}


ADMIN_ACTIONS: dict[str, dict] = {
    "admin.access.denied": _a("access", "被拒绝的管理请求", "被拒絕的管理請求", "Refused administration request"),
    "admin.accounts.finalize_deletions": _a("user", "立即处理到期删除", "立即處理到期刪除", "Finalize due deletions"),
    "admin.agent_safety.lift": _a("agent_safety", "恢复 Agent 使用权限", "恢復 Agent 使用權限", "Restore Agent access"),
    "admin.agent_safety.report_decide": _a("agent_safety", "处理 Agent 举报", "處理 Agent 舉報", "Decide an Agent report"),
    "admin.agent_safety.rules_publish": _a("agent_safety", "发布 Agent 内容安全规则", "發布 Agent 內容安全規則", "Publish Agent safety rules"),
    "admin.agent_safety.suspend": _a("agent_safety", "暂停 Agent 使用权限", "暫停 Agent 使用權限", "Suspend Agent access"),
    "admin.billing.freeze.lift": _a("billing", "解除付费冻结", "解除付費凍結", "Lift a billing freeze"),
    "admin.billing.grant": _a("billing", "发放钱包额度", "發放錢包額度", "Grant wallet credit"),
    "admin.billing.payments.publish": _a("billing", "发布支付配置", "發布支付設定", "Publish payment settings"),
    "admin.invitation.revoke": _a("workspace", "撤销邀请", "撤銷邀請", "Revoke an invitation"),
    "admin.invitation.revoke_bulk": _a("workspace", "批量撤销邀请", "批次撤銷邀請", "Revoke invitations in bulk"),
    "admin.mail.retry": _a("mail", "重发邮件", "重發郵件", "Retry a mail"),
    "admin.model_cost.publish": _a("model", "发布模型成本策略", "發布模型成本策略", "Publish the model cost policy"),
    "admin.model_price.draft": _a("model", "起草模型价格", "起草模型價格", "Draft model prices"),
    "admin.model_price.markup.publish": _a("model", "发布模型加价", "發布模型加價", "Publish the model markup"),
    "admin.model_price.publish": _a("model", "发布模型价格", "發布模型價格", "Publish model prices"),
    "admin.model_price.schedule.publish": _a("model", "发布模型价格排期", "發布模型價格排程", "Publish the model price schedule"),
    "admin.model_provider.closed": _a("model", "模型提供方按设定关闭", "模型提供方按設定關閉", "Model provider closed by its settings"),
    "admin.model_provider.publish": _a("model", "发布模型提供方配置", "發布模型提供方設定", "Publish model provider settings"),
    "admin.model_risk.allowlist_add": _a("model", "加入付费风险白名单", "加入付費風險白名單", "Add to the billing-risk allowlist"),
    "admin.model_risk.allowlist_remove": _a("model", "移出付费风险白名单", "移出付費風險白名單", "Remove from the billing-risk allowlist"),
    "admin.model_risk.breaker_close": _a("model", "关闭模型熔断", "關閉模型熔斷", "Close the model circuit breaker"),
    "admin.model_risk.breaker_open": _a("model", "打开模型熔断", "開啟模型熔斷", "Open the model circuit breaker"),
    "admin.model_risk.reconcile": _a("model", "运行付费对账", "執行付費對帳", "Run a billing reconciliation"),
    "admin.model_risk.suspension_lift": _a("model", "解除付费风险暂停", "解除付費風險暫停", "Lift a billing-risk suspension"),
    "admin.model_risk.upstream_topup": _a("model", "记录上游充值", "記錄上游儲值", "Record an upstream top-up"),
    "admin.notification.recover_enqueue": _a("mail", "补发通知邮件", "補發通知郵件", "Re-enqueue a notification mail"),
    "admin.organization.lifecycle": _a("organization", "冻结、解冻、删除或恢复组织", "凍結、解凍、刪除或恢復組織", "Freeze, unfreeze, delete or recover an organization"),
    "admin.organization.recover_owner": _a("organization", "恢复组织所有者", "恢復組織所有者", "Recover an organization's owner"),
    "admin.platform.grant": _a("platform", "授予平台角色", "授予平台角色", "Grant a platform role"),
    "admin.platform.operator_cleared": _a("platform", "清除已停用的运维角色", "清除已停用的運維角色", "Clear the retired operator role"),
    "admin.platform.revoke": _a("platform", "撤销平台角色", "撤銷平台角色", "Revoke a platform role"),
    "admin.registration.update": _a("config", "修改注册策略", "修改註冊策略", "Change the registration policy"),
    "admin.security.second_factor": _a("config", "修改管理员第二因素策略", "修改管理員第二因素策略", "Change the administrator second-factor policy"),
    "admin.security.throttle_lift": _a("config", "解除限流", "解除限流", "Release a throttle"),
    "admin.settings.publish": _a("config", "发布平台配置", "發布平台設定", "Publish platform settings"),
    "admin.super_admin.cancel": _a("platform", "取消最高管理员交接", "取消最高管理員交接", "Cancel a super-administrator handover"),
    "admin.super_admin.offer": _a("platform", "发起最高管理员交接", "發起最高管理員交接", "Offer a super-administrator handover"),
    "admin.super_admin.ready": _a("platform", "确认最高管理员就绪", "確認最高管理員就緒", "Confirm super-administrator readiness"),
    "admin.user.bulk": _a("user", "批量停用账户或撤销会话", "批次停用帳戶或撤銷工作階段", "Suspend accounts or revoke their sessions in bulk"),
    "admin.user.deletion_cancellation_mail": _a("user", "重发取消注销链接", "重發取消註銷連結", "Re-send a deletion cancellation link"),
    "admin.user.external_unlink": _a("user", "解除外部身份", "解除外部身分", "Unlink an external identity"),
    "admin.user.passkey_remove": _a("user", "移除安全密钥", "移除安全金鑰", "Remove a passkey"),
    "admin.user.push_revoke": _a("user", "撤销全部推送订阅", "撤銷全部推播訂閱", "Revoke all push subscriptions"),
    "admin.user.recovery": _a("user", "发送找回邮件", "傳送找回郵件", "Send a recovery mail"),
    "admin.user.revoke_session": _a("user", "结束单个设备的登录", "結束單一裝置的登入", "Sign one device out"),
    "admin.user.revoke_sessions": _a("user", "撤销全部会话", "撤銷全部工作階段", "Revoke all sessions"),
    "admin.user.second_factor_reset.cancel": _a("user", "取消第二因素重置", "取消第二因素重設", "Cancel a second-factor reset"),
    "admin.user.second_factor_reset.complete": _a("user", "第二因素重置到期执行", "第二因素重設到期執行", "Carry out a due second-factor reset"),
    "admin.user.second_factor_reset.request": _a("user", "申请重置第二因素", "申請重設第二因素", "Request a second-factor reset"),
    "admin.user.status": _a("user", "停用或恢复账户", "停用或恢復帳戶", "Suspend or restore an account"),
    "admin.workspace.claim": _a("workspace", "指派历史数据", "指派歷史資料", "Assign the historical data"),
    "admin.workspace.freeze": _a("workspace", "冻结工作空间", "凍結工作空間", "Freeze a workspace"),
    "admin.workspace.recover_owner": _a("workspace", "恢复工作空间所有者", "恢復工作空間所有者", "Recover a workspace's owner"),
    "admin.workspace.unclaim": _a("workspace", "撤销历史数据指派", "撤銷歷史資料指派", "Undo the historical-data assignment"),
    "admin.workspace.unfreeze": _a("workspace", "解冻工作空间", "解凍工作空間", "Unfreeze a workspace"),
}


def catalogue() -> dict:
    """What the audit page offers: every action with its scope and labels, sorted by code."""
    return {"items": [{"code": code, **item} for code, item in sorted(ADMIN_ACTIONS.items())],
            "scopes": [{"id": sid, "label": label} for sid, label in SCOPES.items()]}
