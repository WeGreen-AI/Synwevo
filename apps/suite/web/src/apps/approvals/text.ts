/** Stable wire values translated into business language at the page boundary. */
export const statuses: Record<string, string> = {
  active: "可用", published: "已发布", retired: "已停用", archived: "已归档", in_review: "审批中",
  stalled: "等待修正审批人", approved: "已批准", rejected: "最终驳回", returned: "退回补充", withdrawn: "已撤回",
  pending: "待处理", transferred: "已转交", revoked: "权限已撤销", cancelled: "已取消", skipped: "条件不符，已跳过", not_started: "尚未开始",
};
/** A stage on a request's approval path: its own progress, never the template's status words. */
export const stageStatuses: Record<string, string> = {
  active: "进行中", stalled: "等待修正审批人", approved: "已通过", rejected: "已驳回", returned: "已退回补充",
  skipped: "条件不符，已跳过", cancelled: "已取消", not_started: "尚未开始",
};
export const fieldTypes: Record<string, string> = { text: "单行文本", textarea: "多行文本", number: "数值", money: "金额", date: "日期", select: "单选" };
export const modes: Record<string, string> = { all: "会签：全员同意", any: "或签：一人同意", n_of_m: "指定票数" };
export const operators: Record<string, string> = { eq: "等于", neq: "不等于", gt: "大于", gte: "大于或等于", lt: "小于", lte: "小于或等于", in: "属于列表", not_in: "不属于列表", filled: "已填写", empty: "为空" };
export const events: Record<string, string> = {
  "request.created": "申请已发起", "request.revised": "内容已修订", "request.approved": "申请已批准", "request.rejected": "申请已驳回",
  "request.returned": "申请已退回补充", "request.withdrawn": "申请已撤回", "request.stalled": "等待修正审批人", "request.resumed": "审批已恢复",
  "task.decided": "审批人已作决定", "task.transferred": "审批任务已转交", "task.reassigned": "审批人已补充", "task.revoked": "审批资格已撤销",
  "stage.opened": "审批阶段已开始", "stage.skipped": "条件不符，已跳过", "stage.approved": "审批阶段已通过", "stage.reminder": "已发出到期提醒",
  "stage.escalated": "审批已升级", "request.commented": "已添加审批意见",
};
export const errors: Record<string, string> = {
  approval_result_unavailable: "审批状态已改变，请刷新后重新核实内容。",
  state_conflict: "审批状态已改变，请刷新后重新核实内容。", draft_conflict: "草稿已改变，请保留自己的修改并重新载入。",
  request_closed: "当前状态不能进行这个操作。", task_closed: "这个审批任务已经处理。", revision_changed: "申请已产生新修订，请先核实内容。",
  invalid_values: "请修正申请字段中的问题。", not_publishable: "请检查字段、条件和审批人设置。", invalid_approver: "请选择本空间有效且独立的审批人。",
  independent_approver_required: "只有当前指定的独立审批人可以作出决定。", independent_manager_required: "发起人不能修改自己申请的审批人。",
  requester_required: "只有发起人可以修订或撤回申请。", note_required: "请先填写意见或原因。", not_stalled: "当前阶段无需修正审批人。",
  version_retired: "该版本已停用，请选择其他发布版本。", template_archived: "模板已归档，请先恢复。", nothing_to_publish: "草稿与最新版本相同。",
  template_not_found: "审批模板不存在或无权访问。", request_not_found: "审批申请不存在或无权访问。", version_not_found: "没有可用的审批版本。",
  revision_not_found: "申请修订不存在。", manage_required: "只有空间管理员可以管理模板。", read_only_role: "你在这个空间只有查看权限。",
  app_access_denied: "你已无权使用这个空间的审批应用。", account_unavailable: "账户不可用，请重新登录。", network: "连接中断，请使用相同内容重试。",
  invalid_input: "输入不符合要求，请检查字段和设置。", input_too_large: "内容过大，请缩短后重试。", too_many_approvers: "审批人超过 50 人，请收窄角色范围。",
  revision_limit: "已达到修订上限，请发起新的申请。", task_limit: "已达到修订上限，请发起新的申请。", idempotency_conflict: "这次操作的内容已改变，请刷新后重试。",
  not_for_agent: "这个决定需要本人在审批页面完成。", person_required: "这个决定不能由自动流程代为执行。",
  stage_needs_requester: "这个申请要经过的一个审批阶段需要你本人同意才能凑齐票数，但发起人不能审批自己的申请。请联系模板管理员调整该阶段的审批人。",
};
export const problems: Record<string, string> = {
  duplicate_field_id: "字段编号重复", select_without_options: "选择字段至少需要一个选项", duplicate_option: "选项值不能重复",
  bad_bound: "最小值或最大值不是数字", min_above_max: "最小值大于最大值", money_without_currency: "金额字段需要币种",
  no_stages: "至少需要一个审批阶段", duplicate_stage_id: "阶段编号重复", no_unconditional_stage: "至少有一个阶段必须始终执行",
  stage_without_approvers: "请指定审批人或角色", required_missing: "请填写所需票数", required_above_approvers: "票数不能多于指定审批人数",
  escalation_without_approvers: "增加审批人的升级规则需要选择成员", condition_unknown_field: "条件引用的字段不存在", condition_bad_op: "此字段不支持这个比较方式",
  condition_bad_value: "条件比较值无效", required: "请填写必填字段", unknown_field: "申请含有未知字段", not_text: "请输入文本", too_long: "文本过长",
  not_number: "请输入有效数值", too_many_decimals: "小数位超过允许范围", too_large: "数值过大", below_min: "数值低于最小值", above_max: "数值超过最大值",
  not_date: "日期格式应为年-月-日", not_option: "请选择有效选项",
};
/** What does not stop publishing but refuses some requests (the server's `warnings`). */
export const warnings: Record<string, string> = {
  stage_needs_requester: "被指定的审批人自己发起申请时，这个阶段凑不齐所需票数，申请会被拒绝。",
};
