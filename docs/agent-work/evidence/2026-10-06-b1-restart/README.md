# B1/B3/B6：同一跨应用运行的实际进程重启与浏览器验收

产品源提交 `f1dae29ed028cc0c38aacb03dccc6da1f76047e6`。2026-10-06 本机隔离服务 `127.0.0.1:37772`；独立合成用户、空间和 PDF，不涉及真实客户、付费模型或支付。

## 执行与证据

- 通过现有真实业务服务准备 Forms 附件、Extract 解析/人工确认、DataPrep 一条超额审核问题、Tables 待审记录及 Approvals 请求，沿现有持久流程走到 `waiting_approval/review`。准备阶段复用集成夹具时钟与显式 worker tick，历史起始日期为9月30日；不能把它说成全程由浏览器创建。
- 启动实际 `role=all/start_jobs=True` 服务进程49789，读取同一审批中运行；TERM退出后启动新进程56148，`before-restart.json` / `after-restart.json` 显示相同运行及审批ID，状态不变。
- 新进程中真实浏览器以独立审批人 owner 登录，核对依据和1条行问题后提交批准一次。其后只由已部署实现的后台 worker 自动推进，无手工tick。发起人是 editor；审批人分离。
- 最终 `partial/end` 保留 DataPrep 行问题，其他18步中对应处理结果见 `final-counts.json`，不是用成功布尔值掩盖异常。Docs生成已完成且同运行只有1项目、1任务、1文档、1业务消息，各有副作用操作1次；刷新后重新计数仍一致。
- 同运行浏览器打开 Forms 提交/附件、Extract 原文与值、DataPrep 异常与结果、Tables 记录、Approvals 时间线、Docs 任务、Message 收件会话、发起人个人自动化运行历史。截图已逐张目视检查。390×844 Message 页面无横向溢出（document宽390），已启用 reduced-motion。
- 从真实界面下载 PDF、Extract 已确认 JSON、DataPrep 异常 JSON/结果JSON和Message内真实DOCX链接。`download-verification.json` 记录实际文件SHA256及断言：Forms→Extract PDF哈希一致、页1行1/2证据、确认修订2、来源行1、金额12500.50、DOCX文本Invoice INV-B1-0042 / Amount 12500.50；没有使用模板夹具行。

## 范围与限制

- 本轮是实际服务进程重新启动；不是跨主机故障、生产容器恢复或备份恢复演练。事件重复/回执丢失覆盖见相邻transfer-policy目录的7项B1集成测试，不在这次浏览器操作中重复发送。
- 扫描器明确为未配置，PDF UI也显示未进行病毒扫描；未伪称扫描成功。提取使用实际PDF文本解析与确定性规则，无假模型。
- Flow页面控制台0 error/0 warning。登录前401为预期；Message可选卡片探测对产物API链接返回404 `no_card`（当前卡片仅支持项目/任务链接），保留可点击原链接并成功下载，未当成发送失败或隐藏HTTP错误。
- Tables记录保留流程设计的 `pending_review` 值；正式决定由独立Approvals记录提供，本流程未声明审批后改写该字段。个人自动化的请求创建结果隐藏一次性访问资料，不据此判为业务丢失。
- 首个临时验收脚本缺少macOS多进程主入口保护的失败记录保留，见harness-note；修正测试脚本后使用新的明确隔离运行，未删除失败数据或改生产代码。
- 原始数据库、下载物及服务日志仅留临时验收目录，不提交令牌、会话或用户数据库。这些证据不替代真实DeepSeek跨空间任务、生产登录和真实支付验收。
