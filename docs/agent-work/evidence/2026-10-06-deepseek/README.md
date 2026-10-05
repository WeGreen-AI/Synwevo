# 2026-10-06 DeepSeek 真实接入验收

这是隔离验收环境，连接官方 DeepSeek；钱包、用户、工作空间与表单均为测试数据，不是客户充值或生产消费。密钥、登录凭据、个人思考内容、数据库不在这些文件内。

- `adapter-no-thinking.json`：基线 e0a22de，关闭思考，两次真实调用及真实时钟工具结果。
- `adapter-thinking-low.json`：e4a782e，低思考模式，两次真实调用与原生思考字段重放。只记录是否存在和 token 计量，不保存或显示思考内容。
- `agent-ledger-summary.json`：三个真实 Agent 运行，各自来源见 EVIDENCE.md；共七个模型调用、七笔可信用量、七笔结算。读取时版本 e4a782e 不等于三个运行全部发生于该 SHA。
- 桌面截图实际视口 1280×720；手机 390×844。英文和繁体布局保持原始用户内容。
- 打开产物截图为未发布表单的真实内部页面，不是公开分享链接。
- 上述四次 adapter 调用独立于七个 governed Agent 调用；没有用 fixture 或假支付替代上游验收。

生产镜像、CI、健康与启用状态另记 STATE.md/EVIDENCE.md，不能从本目录截图推断已公开收费。

补充：`crossspace-report.json` / `agent-crossspace-final.jpg` 在 e4a782e 验证第四个真实运行：同一用户对 A 为 owner、B 为 viewer，DeepSeek 同一会话读取两边表单。独立同服务负向写入测试返回 403/read_only_role，B 表单列表未变化。此时累计 10 个 Agent 模型调用/10 笔结算；前面七笔摘要是该追加验收前的快照。

## 后续真实写入与修复

`agent-write-before-fix.json` 记录e4a782e写入成功但旧列表来源检查误封会话；`agent-created-form.jpg` 证明原草稿存在，未重放。修复后独立的新任务见 `agent-write-recovery.json`、`agent-write-success.jpg` 与 `agent-write-recovery-form.jpg`：读取→确认→真实写入→回答完成→刷新不重复→打开产物。新增草稿包含两个真实字段，未发布。旧仅哈希的历史证明不猜测迁移。
