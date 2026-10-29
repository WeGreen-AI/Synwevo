# 正式 Agent 启用断点

产品源8ba2956750c2080d300fa17523bcc391e85cae40已发布。production-state.json是2026-10-08T01:52:38Z只读生产检查，确认2026-10-07真实后台发布的日历v1及其操作记录；其他配置/调用仍0，Agent未开放。

provider-readiness.json是DeepSeek官方只读API检查，保留首次models失败及一次重试；没有推理或精确账户余额。activation-preparation.json是准备参数，未发布。preparation-validation.json用产品真实校验器/网关公式及独立约束检查，不联网、不打开Store、不写资金；成本0.196609元/售价预留0.393217元是上界，不是实际消费。

Mac现已解锁，真实成本草稿mpd_3870d36aa953/rev1已保存。恢复时先检查管理员发布重认证是否完成，保留现有重认证门禁；不重复日历发布，不伪造价格ID。发布价格、成本、提供方、付款方放行后才按现有运维流程开放Agent并验证模型/工具/账本。公开放量与真实充值仍有独立门槛。详见../../STATE.md和../../DEEPSEEK_ENABLEMENT.md。

production-draft-state.json于02:05:10UTC确认唯一成本草稿mpd_3870d36aa953/rev1仍为draft，已发布价为0。预览在浏览器五分钟过期后须重新获取，不得用新草稿替代状态核对。

**redacted 2026-10-25：** provider-and-payment-sealed.json 的 `risk.allowlisted_payer_id` 原为放行付款人（平台管理员）的生产账户 id，已改为占位；其余字段未动。
