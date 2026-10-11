# 模型路线到期续期材料（供平台管理员，2026-10-11 整理）

**现状（生产只读核验 2026-10-11T15:13Z，见 live-route-readonly.json）**：live 环境提供方版本 revision 1（2026-10-08T12:05Z 发布），
启用；路线 `deepseek-flash` → 已核实实际路线 `deepseek-flash`，证据 `DeepSeek-V4.1-Flash:official-models-pricing-2026-10-08`，
成本依据 `official-cny-pricing-2026-10-08`，售价 `price_9fa1a7cbc022`、成本价 `price_85f4371761b6`，输入上限 65536、输出上限 8192，
推理档 none/low，未完成调用一律不向用户收费。**有效期至 2026-10-15T11:00:00Z（上海 19:00）**。

**到期后的真实行为**：`ProviderSettings.resolve()` 在 `valid_until` 过后抛 `route_confirmation_expired`，新的付费调用不再派发
（报价/确认处提示“模型路线确认已过期”），已发送调用的用量接收、结算与对账不受影响。不会自动续期；代理不能、也不应代为延长。

**续期需要管理员做的事（Admin → 治理 → 模型提供方，`/api/admin/billing/model-provider` preview → publish，需近期重新认证）**：
1. 重新核对官方资料并记录日期：模型与价格 <https://api-docs.deepseek.com/quick_start/pricing/>、公告 <https://api-docs.deepseek.com/news/news260910/>、
   接口 <https://api-docs.deepseek.com/api/create-chat-completion/>，以及账户实际模型列表（控制台/模型检查记录 `model_provider_health`）。
2. 若模型、路线、价格均未变：按原值填写路线各字段，仅更新 `confirmation_ref` / `cost_basis_ref` 的核查日期后缀与 `valid_until`
   （UTC，精确到秒；建议与上次一致的 7 天期限或按运营决定），`sale_price_id`/`cost_price_id` 保持 `price_9fa1a7cbc022`/`price_85f4371761b6`。
3. 若官方价格或路线有变：先在价格治理中发布新的成本/售价版本（售价仍为成本×2，由管理员按版本化规则决定），再在路线中引用新价格 ID。
4. 预览 → 核对差异 → 发布。发布产生 revision 2：**所有现有报价与持续付款授权因路线版本变化失效**，用户下次发送需重新报价/重新授权；
   这是既定行为，不要为避免打扰而跳过发布。发布后核对 Admin 状态页显示新 revision、`valid_until` 与健康记录。
5. 不要重新创建凭据（credential_revision 1 已配置）；轮换只在确有需要时进行。

**不在本材料范围**：不改个人预算、不发起生产模型调用、不处理支付配置。
