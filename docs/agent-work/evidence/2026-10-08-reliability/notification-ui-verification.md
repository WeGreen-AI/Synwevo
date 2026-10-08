# 通知恢复管理员前端完成报告（2026-10-08）

状态：前端代码已冻结，可由协调者开始全量前端 build 与隔离浏览器验收。
工作目录：`/Users/lihiko/repo/wegreen/synwevo/.claude/worktrees/agent-billing`。
已读取暂停报告，并核对后端实现与 `/private/tmp/synwevo-notification-backend-resumed-20261008.md`；以下验证结果仅指本前端任务实际执行的检查。

## 写集

- `services/identity/web/src/governance/NotificationRecovery.tsx`（新，324 行）：独立通知入队失败小节，安全元数据、查询/分页/高水位、权限、预览、确认、重认证、恢复回执与异常重试。
- `services/identity/web/src/governance/notification-recovery.messages.ts`（新，81 行）：简体中文、繁体中文、英文业务文案，含八类通知业务名称及对象级错误。
- `services/identity/web/src/governance/GovernanceTabs.tsx`：一处导入、MailTab 外层容器与末尾组件集成；既有邮件投递逻辑保持原样。
- `services/identity/web/tests/notification-recovery.test.mjs`（新，349 行）：10 个隔离 Node 交互/契约/三语言用例。
- `services/identity/web/tests/refusals.test.mjs`（用户追加授权）：补充实际管理端和 core 恢复方法的 SERVER 映射，以及通过真实 Admin 模块图验证恢复拒绝文案的三语言用例；全部既有断言保留，无新增豁免。

未修改 TopupDialog、后端、core、迁移、conftest、共享文档或进度。未操作 Git、部署、浏览器、真实数据或 SMTP。未访问协调者准备的 127.0.0.1:33870。

## 实现与复核

- 合并了仅用一次的 URL/请求体拼装和重复结果分类辅助函数；辅助类型与函数不再为测试导出。保留列表、预览、回执的边界核验及安全字段白名单，避免未知响应变成可操作对象或虚假成功。
- 复用现有 `useLoad`、权限判断和 `ActionDialog`（含重认证）；`useAdminPage` 固定 50 条，故本小节使用同一个可取消读取钩子实现契约要求的 25 条与 `next_offset`，未更改共享钩子。
- q 仅用于通知 ID/用户 ID。翻页保留服务端高水位；搜索或刷新重置高水位；允许服务端因记录删除降低高水位，拒绝扩大既有边界。读取中不显示可操作旧行；旧请求被取消后不能覆盖新结果。空页可返回最新列表。
- 元数据按白名单保留、按字段呈现，不显示通知正文、邮箱、业务 target 或原始传输错误。类别显示账户安全、组织与工作空间、成员导入、审批、消息、文档处理、自动化与接口、平台运行，未知类别显示其他通知。
- `platform.mail.view` 控制读取，`platform.mail.manage` 控制预览/恢复。所有 blocked_reason 有对应只读说明；历史投递、来源未核实、可能已入队等项目不能恢复。
- 确认显示通知/用户编号、累计尝试、恢复影响与预览有效期。要求显式勾选和 8–500 字符理由；恢复只是重新交由当前调度器审查偏好、已读、失效、免打扰及摘要预算，次数不清零。
- 首次 execute 固定 preview_token、reason、UUID 幂等键；重认证、网络未知和重复点击沿用原请求。结果未知时关闭/重开对话框或刷新列表也不更换请求；预览到期仍可重放原请求读取幂等回执。原请求保留在当前组件内存，页面显示保持本页的提示，并使用现有离页提醒。
- 尚无未知执行结果时，stale/expired/changed 会禁用当前确认，要求重新预览并重新勾选。未知执行结果后的拒绝不视为原执行未提交证据，仍保留原请求。
- `notification_delivery_changed`、`notification_delivery_missing` 与 `notification_not_recoverable` 均在当前对象/小节展示并提供重新预览或刷新入口。对象缺失不调用退出后台回调。实际失去管理权限时不能执行或重试。
- 仅核验成功回执后展示“已安排重新检查并排队，不代表已寄出”，附通知 ID、保留的累计次数与操作 ID。
- 使用现有设计类/令牌；桌面为紧凑四列，窄屏复用管理员卡片布局直接呈现字段与操作，容器可收缩、文本及分页可换行；真实窄屏表现留给协调者浏览器验收。

## 实际验证

在上述工作目录运行：

```sh
node --test services/identity/web/tests/refusals.test.mjs services/identity/web/tests/notification-recovery.test.mjs
npm run typecheck -w @synwevo/account-web
```

- 最终 Node 测试（拒绝文案扫描与手机卡片修复后）：20 passed，0 failed，0 skipped，退出码 0，约 2.89 秒。其中拒绝文案套件 10 项、通知恢复套件 10 项。
- 最终账户 typecheck：退出码 0。
- 两个进程均已正常退出，无本任务遗留测试/构建进程。

测试覆盖真实 `ActionDialog` 与 `useLoad` 的事件处理及状态流程，HTTP/身份服务均为标明用途的隔离夹具，未启动服务。包括：权限和历史项禁用、私有字段不呈现、勾选/理由边界、重认证稳定重试、网络未知保留请求、失效预览重读、对象缺失不退出后台、权限撤回、重复提交与卸载取消重认证、搜索分页高水位、慢响应取消、错误读取重试、畸形响应不宣称成功、三语言类别及插值。

这些是 Node 中的状态/事件与静态渲染验证，不替代真实 React DOM、Safari 交互或布局验收。全量 build、隔离浏览器/后端验收、迁移与集成由协调者执行。

## Safari 实测反馈后的列表密度修复

协调者在真实 Safari 看到 53 条合成记录，反馈列表将完整 Metadata 堆在第一格导致行过高。本轮仅修改列表呈现及对应表头翻译/既有测试断言：

- 四列分别展示通知/用户 ID，业务类别/状态/错误代码/不可恢复原因，累计尝试/更新时间，恢复操作。
- 表格使用固定列比例，将信息分布到各列；文本可换行，窄屏仍在局部容器横向滚动。
- 通知为语义化行标题，按钮通过 aria-describedby 关联对应对象与只读原因；时间保留可读文本并提供 time/dateTime。
- 对话框继续使用原完整 Metadata；权限、分页、预览、执行、稳定幂等键等逻辑未改动。
- 既有 10 个测试全部保留，补充列表字段完整、四列比较、可访问说明关联和确认框详情保留的断言；相关测试及账户 typecheck 均再次通过。

代码已再次最终冻结，可立即重建视图并在 Safari 复验/截图。本任务没有自行执行浏览器检查，因此不报告实测行高或截图结论。

## 后续拒绝文案集成与手机可达性修复

协调者全量 npm tests 暴露 SERVER 未登记 `admin_notification_recovery.py`。已登记到 admin，同时将 core `email_recovery_row` / `email_recovery_page` / `recover_email_enqueue` 从通知中心的默认排除分组明确映射到 admin。首次重新扫描实际暴露两处缺失：`通知恢复请求格式无效`、`通知查询参数无效`；已在该管理页词库补齐英/繁翻译，并补充 `通知恢复操作未提交，请稍后重试` 的实际服务端句子。无需修改共享拒绝文案词库。所有既有扫描断言与豁免规则保持不变，新增用例通过真实 Admin 模块图验证通知恢复特有拒绝文案的三语言呈现。

协调者随后反馈 390×844 Safari 虽无页面溢出，但横向滚动难以抵达操作。已采用现有 `admin-cards-wrap` / `admin-cards-table`，删除表格内联 minWidth，复用 ≤720px 卡片规则；每个数据单元提供三语言 data-label，分组内容位于单个可收缩容器，操作格采用现有 act 类使手机操作回到正常文档流。桌面四列、字段、原因、确认详情与恢复逻辑均保留。未编辑共享 CSS。测试同时检查卡片类、移动标签与无内联最小宽度覆盖；真实手机复验仍由协调者执行。

本次改动已再次冻结。以上两组测试和账户 typecheck 均在最终源码上通过，相关运行进程均已正常退出；未自行运行全量 build、全量 npm tests 或浏览器。

## 最终冻结时内容指纹（拒绝文案与手机卡片修复后）

| 文件 | SHA-256 |
| --- | --- |
| NotificationRecovery.tsx | 8966f5438e7f3fb7fec0297257469d707593bc4e6d56a4d909171ed49f496b81 |
| notification-recovery.messages.ts | 4c31df95dee29a00bf6e098320f8255e93a5baae3e65ce18c40cf22f2c8f9310 |
| GovernanceTabs.tsx | bf144bbc862be6f3be8e26838de479ccbffe66a599d4966304aa7d442f75d9ba |
| notification-recovery.test.mjs | 6ae4025864571e976f61cd7969034958e46ad22f74f4a62ad6f2afa393a6ad8c |
| refusals.test.mjs | a277b8116c7c58c127dc550d51f41ee328c34f33ba51f44c3e9117e4ab275303 |
