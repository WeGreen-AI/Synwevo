# 充值客户端重试幂等修复完成报告

工作目录：`/Users/lihiko/repo/wegreen/synwevo/.claude/worktrees/agent-billing`。
首次代码冻结：**2026-10-08 18:54:52 HKT**。经用户随后授权补齐订单状态卡，最新冻结为 **2026-10-08 19:09:57 HKT**（见文末追加报告）。

## 结果与契约

- 新 UI 在发送前将原始意图写入 sessionStorage，按 environment、initiator_id、payer_kind、payer_id 隔离；每次提交显式固定这四项。同一次请求双击合并，网络错误、未知响应和刷新后继续使用同一随机键及原参数。
- 保存内容只有上下文、键、套餐或金额、当时展示的金额及已知 order_id；不保存 checkout URL、challenge token、支付秘密或卡号。存储不可用/损坏时停止发起请求，保留已有记录。原订单未知时不能修改金额或重新创建；收到 paid/failed/expired 终态后，用户显式“重新选择金额”才清除旧意图，新提交生成新键。
- POST 新增可选 idempotency_key、initiator_id、environment。客户端上下文与当前登录/部署不符时 409 topup_context_changed；原意图唯一范围为 environment + initiator_id + key，原付款方/套餐/金额 SHA256 固定，同键异参 409 topup_idempotency_conflict。
- lifecycle 和 intent 在同一 Store 写事务占位；账本维持已有独立耐久事务。并发重试只读原订单，不再次调用 provider、不增加 velocity、不重复写“创建”审计。首次返回 201，重复 200，未知 creating 返回 202 + creation_status=verifying；不根据 5 分钟/1 小时等超时自动宣布失败。
- Turnstile 只用于首次准入，不要求重复消费已经使用过的 token。challenge 和 provider 网络调用均在数据库事务之外。
- 每次重放及释放 URL 前重新检查当前账号/邮箱、钱包冻结、组织付款权限；订单读取不再允许已经失去组织付款权的原 initiator 绕过检查。旧订单保留原受治理金额/渠道/版本，套餐重新定价不改变 hash 或价格；配置关闭、凭据替换或撤销时不返回旧 URL；终态和过期不返回 URL。
- 未读完整/不合法的 provider body 按未知结果处理，不回显或记录上游敏感 body。签名回调仍按原规则恰一次入账；创建本身不记余额。
- **兼容范围**：旧调用省略 key 仍被接受，其两次 POST 仍是两次独立意图；只有带稳定 key 的请求提供跨请求恢复。新 UI 必须携带 key 和固定上下文。

## 本任务修改文件

1. `services/identity/synwevo_identity/payments.py`
2. `services/identity/synwevo_identity/payment_routes.py`
3. `services/identity/tests/test_wallet_topups.py`
4. `services/identity/tests/test_topup_idempotency.py`（新增）
5. `services/identity/web/src/pages/account/TopupDialog.tsx`
6. `services/identity/web/src/pages/account/topup.messages.ts`
7. `services/identity/web/tests/topup-idempotency.test.mjs`（新增）
8. `services/identity/web/tests/payment-methods.test.mjs`（仅增加充值词库加载，以覆盖新文案）

没有修改 notifications、governance、共享 docs 或迁移。`payment_topup_intents` 表及其不可变/作用域触发器由协调者添加；协调者反馈其 9 项迁移测试通过，此数不是本任务自行执行的结果。

## 缺口复现与旧断言更新依据

修复前已执行两个用例：无 key 的同参重复 POST 确实返回不同 top_* 且 provider test double 被调用两次；新增带 key 恢复用例被旧路由以 400 拒绝。

原 `test_a_provider_failure_is_reported_honestly_and_charges_nothing` 将 provider 500 断言成 failed，并对应“未扣款、稍后重试”提示。500/超时/截断 body 只能说明调用方未收到可用结果，不能证明远端未创建。更新用例保留“本地余额未增加”的断言，并严格要求 creating/verifying、无 URL。新增夹具先模拟远端接受创建再丢失响应，证明两小时后同键仍不重发，随后有效签名事件对原订单重复交付也只入账一次。没有降低幂等或支付凭证标准。

收尾还发现并修复：服务端可能在 provider 返回后才因权限撤销返回 401/403，UI 不能把这类错误列为“肯定未创建”。现在首次/恢复请求遇到账号、邮箱、付款权限拒绝都保留原意图，并有前后端回归。

## 本任务验证

以下命令均从上述工作目录执行；所有资金/HTTP 测试使用临时目录、ASGI TestClient 和明确标记的 httpx.MockTransport/test double，没有真实 provider 网络或付款。

```sh
PYTHONPATH=.:packages/core:packages/auth-client/python:services/identity:apps/docs:apps/portal:apps/messages:apps/suite:infra/server /private/tmp/claude-501/-Users-lihiko-repo-wegreen-synwevo/80146fec-e8b8-43eb-aafb-6b062986dc5d/scratchpad/venv312/bin/python -m pytest services/identity/tests/test_wallet_topups.py services/identity/tests/test_topup_idempotency.py services/identity/tests/test_payment_key_revocation.py -q
npm run typecheck -w @synwevo/account-web
node --test services/identity/web/tests/topup-idempotency.test.mjs services/identity/web/tests/wallet-pages.test.mjs services/identity/web/tests/payment-methods.test.mjs
/private/tmp/claude-501/-Users-lihiko-repo-wegreen-synwevo/80146fec-e8b8-43eb-aafb-6b062986dc5d/scratchpad/venv312/bin/python scripts/check_boundaries.py
/private/tmp/claude-501/-Users-lihiko-repo-wegreen-synwevo/80146fec-e8b8-43eb-aafb-6b062986dc5d/scratchpad/venv312/bin/python scripts/check_design.py
/private/tmp/claude-501/-Users-lihiko-repo-wegreen-synwevo/80146fec-e8b8-43eb-aafb-6b062986dc5d/scratchpad/venv312/bin/python scripts/check_component_names.py
```

最终结果：上述命令全部退出 0；三个后端文件全部通过；前端 35 passed、0 failed/skip。Python 有 Starlette/anyio BlockingPortal 弃用提示。中间失败已修正：新测试相对导入路径、合成 live 模式夹具键的字符格式、用户停用状态应为 suspended。未绕过键模式或用户 schema 校验。

覆盖并发同键/异参、重复响应丢失、provider timeout/截断/不合法响应、actor/env 隔离、个人/组织、配置关闭/轮换/撤销、撤权、冻结和邮箱状态、终态、provider 返回前回调完成、意图提交后进程中断；前端覆盖刷新恢复、双击、固定参数/上下文、损坏或不可写存储、首次/重试错误分类、终态后新键、三语言文案。

## 最小冲突、限制及未验

- 协调者 schema 合同已满足；与其他并行实现没有已知文件冲突。本次唯一额外既有前端测试改动是词库 import。协调者若在冻结前构建，需重建账户前端以包含最后的 401/403 保留修复；本任务没有执行或宣称完整前端 build。
- 未实施 provider 主动查询/人工对账工具。没有可信终态的 creating 意图持续“核实中”，尤其提交 intent 后、账本/provider 前进程中断时可能需要后续运营核实；绝不自动重派或自动换订单。这是保守且明确的恢复边界。
- sessionStorage 仅保证当前标签页刷新/同源往返恢复，不承诺关闭标签页、手工清除存储、跨设备后的意图找回；API 的原 key 映射仍持久保留。
- 已泄露给浏览器的既有 checkout URL 无法靠不再返回该 URL 远程撤回；本次阻止新的 URL 释放，延续现有管理员强制撤销凭据的明确治理例外，不扩展权限。
- 未跑全 identity/integration 套件、真实浏览器、真实 Stripe、生产、模型调用或真实网络；UI 交互验证为隔离提交控制器及现有 SSR/文案测试。父协调者的构建/浏览器/迁移检查不冒充本任务验证。
- 本任务没有 Git 操作、提交、推送，没有后台测试/构建会话仍运行。

## 冻结文件 SHA256

```text
2ebeb390ffbd5ece9572ea85d55cd4da0e8daac77f03628dd50ae5d0c47d910f  services/identity/synwevo_identity/payments.py
9aa54231226de860c45130e5cd153d08557e42c0bf290d3a42ffaa23f256ac59  services/identity/synwevo_identity/payment_routes.py
4392f9d6cb50c4828a9ab372188a1a5f97721a750affc2a7501740864872515b  services/identity/tests/test_wallet_topups.py
5626e3e93f9ea72dbdc3e92b2a9570ca868ccfd7871d92d9afed510314057c8a  services/identity/tests/test_topup_idempotency.py
f8a9348f4b1704309b90a30cec9ebaf68c8dac26a0112e2613de5543eb7b1f09  services/identity/web/src/pages/account/TopupDialog.tsx
1744ac9ed840a23ae120cd1bd6ea225003923715428601e3505ec6c68c8cfaab  services/identity/web/src/pages/account/topup.messages.ts
426f42e80488d7a1d237c93da948ecd92445fab20bffd4693dc1d6427223130d  services/identity/web/tests/topup-idempotency.test.mjs
810ead03fbf60950186e671669f713f97024bcc85567b0b91e8e8bc3ecfbb592  services/identity/web/tests/payment-methods.test.mjs
```

## 追加：浏览器 QA 发现的未知创建状态文案（最新冻结 19:09:57 HKT）

用户反馈的浏览器故障 QA 证据：390 宽度、刷新恢复、重复请求通过；原订单 `top_354ccce2b879`，一个 intent、一次 provider test double 调用、零真实支付。此为协调者提供的证据，不是本任务自行执行的浏览器验证。发现“查看原订单”仍显示付款确认及几秒承诺后，用户明确扩展 UI 写集。

本次只改以下四个前端文件，不改资金行为、后端状态、网络派发、轮询时限或迁移：

- `services/identity/web/src/pages/account/TopupStatus.tsx`：将 creation_status 传入视图选择，新增独立 verifying 卡片，无旋转等待标记、几秒承诺、付款已发生暗示或重新付款按钮。
- `services/identity/web/src/pages/account/walletModel.ts`：增加可选 TopupCreationStatus 类型和 verifying 视图。真实终态优先；pending + verifying 在慢轮询/取消返回后仍是未知创建；pending + resolved 仍不是付款成功。
- `services/identity/web/src/pages/account/topup.messages.ts`：简中、繁中、英文同步说明“尚未确认支付服务商是否已创建付款页面；原请求和订单已保留，不要重复充值；可能需要管理员核实，可提供订单号”。
- `services/identity/web/tests/wallet-pages.test.mjs`：新增视图转换及实际 SSR 卡片回归，检查未知创建文案、订单号、无不实承诺/重复支付动作，覆盖慢轮询、cancel 标记、三语言和 pending/resolved 契约。

先跑新增两项回归，在修复前均失败，明确复现 pending/cancelled 错误展示；随后修复并执行：

```sh
npm run typecheck -w @synwevo/account-web
node --test services/identity/web/tests/wallet-pages.test.mjs services/identity/web/tests/topup-idempotency.test.mjs services/identity/web/tests/payment-methods.test.mjs
```

结果：类型检查退出 0，37 tests passed、0 failed/skip。没有重跑后端、完整构建或浏览器；这些前端最终修改需协调者在最新冻结后重建并做其浏览器 QA。

本次最终 SHA256（该 topup.messages 哈希替代上文首次冻结值）：

```text
33423a135b586a3175d1463ed7a06f7addebb6ceb73ef040a4a763aa8a1c2585  services/identity/web/src/pages/account/TopupStatus.tsx
db87b02a1122e17d9974287c70dd83c34612b0f08cb06dfcdac5d044cfacfa20  services/identity/web/src/pages/account/walletModel.ts
969057e251fb3ce953b87b372fcd1f38335b8644412d8e8c3e6b522add1b39bb  services/identity/web/src/pages/account/topup.messages.ts
9881faaf2bf1721fd52685c380d6354a7b9c54ecd5da0fb459469bebdd1cf6cd  services/identity/web/tests/wallet-pages.test.mjs
```
