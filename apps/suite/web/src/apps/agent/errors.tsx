import { ApiError } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { Failure } from "../../shared/states";
import { consentCopy } from "./consent.messages";

const errors: Record<string, string> = {
  billing_welcome_capacity: "今日赠送名额已满，请稍后再试。",
  agent_not_configured: "模型服务未配置。请先打开 Agent 设置。",
  provider_history_incompatible: "模型提供方已变化，请新建会话继续。",
  billing_authorization_required: "请先确认本次运行的价格与预算。",
  billing_authorization_conflict: "请只选择一种付款授权。",
  billing_consent_not_found: "这项持续付款授权不可用，请核对当前会话。",
  billing_consent_revoked: "持续付款授权已撤销，尚未开始的付费调用已停止。",
  billing_consent_expired: "持续付款授权已到期，请重新核对价格与范围。",
  billing_consent_exhausted: "持续付款授权的运行次数已用完。继续前请查看已有结果，再重新授权。",
  billing_consent_scope_changed: "会话的工作空间范围已变化，请撤销旧付款授权并重新核对。",
  billing_consent_model_changed: "模型或价格条件已变化，请撤销旧付款授权并重新核对。",
  billing_consent_conversation_unavailable: "此会话目前无法使用持续付款授权。",
  billing_consent_explicit_scope_required: "请先在会话中明确选择工作空间范围，再设置持续付款授权。",
  billing_consent_already_exists: "此会话已有持续付款授权，请先查看或撤销原授权。",
  billing_consent_limit_exceeded: "有效的持续付款授权已达到上限，请先撤销不再需要的授权。",
  billing_consent_invalid_expiry: "请选择未来一年内的到期时间，或明确选择持续有效至撤销。",
  billing_consent_expiry_required: "请选择到期时间，或明确选择持续有效至撤销。",
  billing_consent_total_too_large: consentCopy.totalOverflow[0],
  consent_pending_operation: consentCopy.pending[0],
  consent_state_unavailable: consentCopy.loadError[0],
  billing_traffic_wait_timeout: "模型调用排队超过等待上限，当前调用尚未发送，请稍后重试。",
  billing_traffic_queue_full: "模型调用排队已满，当前调用尚未发送，请稍后再试。",
  billing_traffic_payer_queue_full: "你的模型调用排队已满，当前调用尚未发送，请等待已排队的调用完成。",
  billing_traffic_request_too_large: "单次模型调用超过每分钟 token 上限，无法发送。请联系管理员核对设置。",
  billing_traffic_grant_expired: "模型调用的放行已过期，当前调用尚未发送，请重试。",
  billing_traffic_policy_changed: "模型调用速率设置已变化，当前调用尚未发送，请重试。",
  model_queue_full: "模型调用排队已满，当前消息尚未发送，请稍后再试",
  model_traffic_unavailable: "模型调用容量暂时无法核对，当前消息尚未发送",
  billing_insufficient_funds: "可用余额不足，后续调用尚未开始。",
  billing_usage_pending: "上次模型调用的用量待核实，后续调用已暂停。",
  billing_run_budget_exceeded: "本次运行已达到授权预算，后续调用尚未开始。",
  billing_run_time_too_short: "本次运行剩余的时间不足以完成一次模型调用，本次没有发送，也不扣费。请重新确认运行时间后继续。",
  billing_payer_budget_not_configured: "请在 Agent 设置中配置个人日、周、月支出上限。当前调用尚未发送。",
  billing_payer_budget_disabled: "个人支出已暂停，当前调用尚未发送。可在 Agent 设置中核对。",
  billing_payer_daily_budget_exceeded: "个人今日支出上限不足以容纳本次预留，当前调用尚未发送。",
  billing_payer_weekly_budget_exceeded: "个人本周支出上限不足以容纳本次预留，当前调用尚未发送。",
  billing_payer_monthly_budget_exceeded: "个人本月支出上限不足以容纳本次预留，当前调用尚未发送。",
  billing_payer_budget_capacity: "暂时无法安全核算个人周期支出，已停止新调用。请联系管理员。",
  billing_payer_budget_inconsistent: "暂时无法安全核算个人周期支出，已停止新调用。请联系管理员。",
  // Upstream HTTP 402 is the operator's provider balance, never the person's wallet (gateway.py).
  provider_balance: "模型服务暂时不可用，本次未扣费。请稍后再试。",
  billing_risk_breaker_open: "模型服务暂时不可用，本次未扣费。请稍后再试。",
  billing_provider_model_missing: "所配置的模型暂时不可用，本次未发送、未扣费。平台管理员已收到通知。",
  provider_stream_incomplete: "模型连接已中断，用量待核实，已完成操作保留。",
  provider_transport: "模型连接已中断，用量待核实，已完成操作保留。",
  provider_auth: "模型凭据不可用，请检查 Agent 设置。",
  provider_forbidden: "模型服务拒绝访问，请检查账户权限。",
  provider_model_missing: "所配置的模型不可用，请联系平台管理员。",
  // Upstream 429 is the operator account's concurrency limit: never a quota or balance of the person.
  provider_rate_limited: "模型服务暂时繁忙或请求过于频繁，请稍后重试。",
  provider_timeout: "模型响应超时，已完成的操作保留。",
  provider_unreachable: "暂时无法连接模型服务，请稍后重试。",
  provider_overloaded: "模型服务暂时繁忙，请稍后重试。",
  provider_refused: "模型未接受这个请求。你可以调整任务后重新发送。",
  provider_truncated: "回答已达到长度上限，请拆分任务。",
  budget_exceeded: "已达到本次回答的资源上限，请拆分任务。",
  step_limit: "已达到本次回答的步骤上限，请检查结果后继续。",
  interrupted: "服务重启中断了回答。已完成的操作保留。",
  outcome_unresolved: "部分操作的结果尚未确定，请先在对应应用核实。",
  model_configuration_changed: "模型配置已变化，请重新发起回答。",
  source_access_changed: "来源内容或权限已变化，请新建会话重新读取。",
  source_policy_changed: "来源空间已停止允许当前模型处理内容。",
  confirmation_mismatch: "确认依据、目标或内容已变化，请刷新后重新核对。",
  transfer_source_unavailable: "来源工作空间已不可用，或你已不是其成员，内容没有转移。",
  transfer_export_denied: "你在来源工作空间没有导出权限，不能把其中的内容用到其他工作空间。可请该空间的所有者授予导出权限。",
  transfer_target_unavailable: "目标工作空间不可用，或你不是其成员，内容没有转移。",
  transfer_source_closed: "来源工作空间的数据流政策不允许其内容转移到其他工作空间。",
  transfer_audience_denied: "目标工作空间有不在来源工作空间的成员，来源的数据流政策只允许转移给已能看到这些内容的成员。",
  transfer_audience_external: "来源工作空间的数据流政策只允许转移给已能看到这些内容的成员；对外发送的接收方无法逐一核对，这些内容没有发出。",
  transfer_organization_denied: "来源工作空间的数据流政策只允许转移到同一组织的工作空间。",
  transfer_organization_policy_denied: "来源所属组织不允许其内容转移到组织以外的工作空间。",
  transfer_organization_external: "来源工作空间的内容只能留在所属组织内；对外发送的接收方可能不在组织内，这些内容没有发出。",
  source_transfer_unavailable: "工作空间的内容不能存为个人资料，请在应用中处理。",
  person_required: "个人操作（记忆、会话、流程本身）不能作为流程步骤，请在 Agent 中直接处理。",
  not_awaiting_confirmation: "此操作已处理，请刷新查看结果。",
  turn_running: "已有回答正在进行，请等待或先停止。",
  turn_cancelled: "回答已停止，后续操作未执行。",
  conversation_sealed: "访问权限已改变，相关内容已隐藏。请新建会话继续。",
  // Known codes of the send path and of a running answer that are not payment refusals
  // (refusals.ts words those, with their next step).
  account_unavailable: "账户或会话不可用，回答已停止。",
  billing_account_unavailable: "账户或会话不可用，回答已停止。",
  agent_busy: "进行中的任务已达到上限，请先完成或停止已有任务。",
  agent_internal: "处理这次请求时出错，回答已停止。可以重新生成。",
  agent_unavailable: "服务正在停止，请稍后重试。",
  billing_attempt_budget_exceeded: "本次运行已用完授权的模型调用次数，后续调用尚未开始。",
  billing_run_expired: "本次运行已超过授权的时限，后续调用尚未开始。",
  billing_model_authority_denied: "这次运行已不再获准调用模型（已停止，或会话、来源权限已变化），没有发起新的模型调用。",
  conversation_archived: "会话已归档，恢复后才能继续。",
  conversation_not_found: "会话不存在或已删除。",
  conversation_too_long: "会话已经很长了，请新建会话继续。",
  message_not_found: "这条消息已不存在，请刷新会话。",
  message_too_long: "消息过长，请缩短后再发送。",
  tool_call_not_found: "这个操作已不存在，请刷新会话。",
  turn_not_found: "这次回答已不存在，请刷新会话。",
  not_a_member: "范围里有你不是成员的工作空间，请重新选择。",
  outside_conversation_scope: "目标工作空间不在本会话的范围内，操作没有执行。",
  source_unverifiable: "无法重新核实来源权限，操作没有执行。",
  confirmation_basis_unavailable: "此操作尚不能核实目标版本，请在应用中执行。",
  result_access_changed: "结果所依据的内容或访问权限已变化，请刷新后重试。",
  result_access_unverifiable: "无法核实原结果的访问权限，请刷新后重试。",
  // The provider's own failures: what the person can do; whether anything was charged is the
  // answer's own ledger line (never claimed here).
  provider_error: "模型服务出错，回答已停止。请稍后重新生成；已完成的操作保留。",
  provider_interrupted: "模型连接中途断开，回答已停止。是否计费以本条回答的结算记录为准。",
  provider_bad_request: "模型服务未接受这次请求，回答已停止。请重新生成，或新建会话继续。",
  provider_bad_output: "模型返回了无法使用的结果，回答已停止。请重新生成。",
  provider_redirect: "暂时无法完成模型调用，回答已停止。请稍后重试。",
  provider_invalid_request: "暂时无法完成模型调用，回答已停止。请稍后重试。",
  provider_async_context: "暂时无法完成模型调用，回答已停止。请稍后重试。",
  // A memory the model proposed and the person confirmed, refused by an input rule (agent/safety.py).
  content_blocked_memory: "这条记忆触发了内容安全规则，没有保存。请修改后再保存。",
};

export function agentErrorText(error: unknown, t: (key: string) => string): string {
  let code = error && typeof error === "object" && "code" in error ? String(error.code) : "";
  if (error instanceof ApiError && code === "idempotent_failure") code = error.detail?.code ?? code;
  if (errors[code]) return t(errors[code]);
  if (code.startsWith("billing_cost_")) return t("平台模型服务暂不接纳新调用，当前调用尚未发送。请稍后重试或联系管理员。");
  if (code.startsWith("billing_")) return t("费用或授权状态暂不允许继续，请核对后重试。");
  if (error instanceof ApiError) return error.message;
  return t("未能完成此操作，请核对结果后重试。");
}

export function AgentFailure({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useI18n();
  const safe = new ApiError(error instanceof ApiError ? error.status : 0, "agent_error", "");
  Object.defineProperty(safe, "message", { get: () => agentErrorText(error, t) });
  if (error instanceof ApiError) safe.requestId = error.requestId;
  return <Failure error={safe} onRetry={onRetry} />;
}
