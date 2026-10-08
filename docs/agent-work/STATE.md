# Synwevo 当前交付断点

## 当前状态（2026-10-08）

**12:35:19Z最新：Agent已在完整验证过的现网25b084f启用，portal重建healthy；原五应用保留、放行名单/所有预算未变，备份runtime.env.bak-agent-20261008T123519Z。这是operations§11.1配置激活，不是待验证代码发布。核对5ffec9d相对25b084f的Agent/Portal/Server/core billing无差异，因此可先验真实Agent；充值继续关闭等待新CI。本人已完成提供方同意并自行调用；随后明确要求保持个人支出暂停，不再发起新付费验收。**

- 最新用户要求：做完这一轮先暂停。仅完成当前Agent计量修复、必要CI/CD与上线核验、已准备的充值启用；不再开新工作包。正式售价成本×2、欢迎额度人民币1元；个人支出保持暂停。
- 实施树 `.claude/worktrees/agent-billing`，分支 `claude/agent-billing`，origin 为 WeGreen-AI/Synwevo。主检出的用户/其他任务改动保留。
- 产品提交769a98d84179b2a6c53d3783467ecc3ffbbb5743已推送 main，但 Actions37771066812失败：完整运行镜像4443通过/1失败/3跳过；新payment_topup_intents.request_hash漏记blob分类。部署跳过，线上仍25b084f0ff5d6897d3086ddda9356f1073562ef0。
- 已补充真实请求指纹分类，原3项完整blob登记测试通过，未改断言。本地集成1336通过，11项仅因loopback EPERM未通过的测试获准后全部通过。修复5ffec9d已正常推送，Actions37776600173运行中，按现有CI/CD发布修复；不能把本地通过写成正式部署。
- 单协调者锁在主检出docs/agent-work/.coordinator.lock，持有session01a0f104-e57f-7092-ab67-50359f43b547；不得按时间抢锁。子代理/本地浏览器验收服务均已结束，无竞争部署。

## 已完成且不要重复的生产配置

以下均通过真实已登录Admin及既有重认证/签名预览保存，没有直写治理表。

- 分时日历v1，ao_d7da658ed75f，2026-10-07T14:58:27Z；工作日UTC01–04/06–10及10月1–7日假期，不是全年假期表。
- 官方成本price_85f4371761b6：CNY6，deepseek-flash，峰值每百万输入2/缓存0.04/输出8，低谷1/0.02/4。
- 成本策略v1/ao_50e7aad24bc5，2026-10-08T11:31:11Z；每日20/每周100/每月300元，单次0.2元，在途4、未知4/0.8元，放行名单开启。完整真实值见cost-and-sale-published.json。
- 两倍售价price_9fa1a7cbc022/ao_1bff5aa7340a，11:34:43Z；峰值4/0.08/16，低谷2/0.04/8，terms_ref synwevo-agent-cny-2x-2026-10-08。
- 提供方v1/凭据v1，ao_0da99f60b32e，12:05:26Z；requested_model/confirmed_route均deepseek-flash，版本来源DeepSeek-V4.1-Flash；输入65536/输出8192，none/low，有效期2026-10-15T11:00Z。所有未完成调用收费选项false。
- 当前所有者u_3f48e19e364e已加入受控放行名单；Agent已于12:35:19Z加入环境应用列表。已验证欢迎额度唯一发放1元，原有余额不是本轮充值或赠额。
- 支付设置v1已密封保存live Stripe与回调签名，保持disabled；香港个人商户Hiko Sphere，银行卡，10/50/100元套餐、自定义10–1000元。有权付款人真实付款尚未进行。
- 原唯一回调we_1UOB1ZEMgc4r7oEcqTYP9tLI于12:12:52Z启用并复核；URL https://accounts.synwevo.com/api/auth/payments/stripe/webhook 。不要重跑prepare脚本（会停用它），不要重复创建回调或订单。
- 原0600回调恢复文件保留在/private/tmp/synwevo-stripe-live-callback-20261008.json，含秘密，禁止输出或Git。裸签名临时录入文件已删除；浏览器没有保存密钥到密码管理器。

## 当前代码和证据

- CR-068充值持久幂等：真实请求键绑定、未知结果保持核实、刷新后恢复同订单、组织撤权复核；CR-069通知失败恢复仅处理可信atomic-v1入队失败，保留累计次数并经过原dispatcher，历史/结果不明只读。
- 本地账户1538项初跑1537通过/1新管理路由登记失败，补齐完整权限矩阵后29通过；全前端528、类型/构建/六守卫通过。真实隔离浏览器覆盖三语言/390px/恢复；不冒充真实Stripe付款。详情evidence/2026-10-08-reliability。
- 769a98d完整CI的构建/静态检查已通过；运行镜像在blob分类门槛失败，详见同目录ci-failure-classification.json。修复后必须获取新SHA完整CI证据。
- 25b084f现网此前四应用镜像/OCI、五域健康/静态资源/身份起跳已验证，备份/opt/synwevo/backups/20261008T044220Z存在；没有宣称恢复演练。
- 最新配置证据在evidence/2026-10-08-agent-activation/{cost-and-sale-published,live-readiness-after-pricing,provider-and-payment-sealed,stripe-callback-enabled}.json。旧暂停和逐轮日志保留在EVIDENCE及Git历史，不能覆盖本文件当前状态。

## 下一条具体动作

1. CR-070修复已本地验证：未预留调用拒绝后的用量恢复、次数/金额解释、默认6次仍需金额确认。提交并走现有CI/CD；5ffec9d对应Actions37776600173仍在镜像测试，不取消或绕过。
2. 新版本部署后核对同SHA镜像/健康/迁移、Agent环境开关仍保留，不重复启用或重建。
3. 只读核对本人已有调用的usage/售价/成本/账本。本人已同意DeepSeek数据处理，但明确要求保持个人支出暂停；预算v3 disabled保留，不新发起付费调用或工具产物验收。
4. 通过真实Admin复用已密封凭据启用充值；有权付款人亲自完成CNY10银行卡支付，再核对签名回调、唯一入账和余额。不能用赠额替代真实付款证据。
5. 公开扩量遵守operations.md§11.3连续数日对账ok、账本无差异和无异常扣费门槛；受控正式启用不等于全体公众已可用。完成当前轮后保存断点、释放自身锁并暂停，不开新工作包。

## 具体外部条件与限制

- DeepSeek关于API训练/保留的专门书面答复未取得；询证草稿未发送，不擅自代发。公开说明不承诺零训练/零保留。
- 真实卡支付需有权付款方完成；全球支付方式不能从HK CNY支持推断全部可用，当前仅银行卡。
- 未完成整个六应用/Admin/API/Agent验收，不能标任务完成。公开扩量观察窗口仍保留。
