/** How the Forms pages word the server's codes: field errors, publishing problems, field types,
 *  statuses. Every code the server can return has a sentence here (the catalogue test checks the
 *  strings exist in all three languages). */
import type { Definition, DraftRefusal, FieldError, FieldType } from "./logic";

type T = (key: string, params?: Record<string, any>) => string;

/** Each refusal code of the Forms server (synwevo_suite/forms/*.py, plus the operation layer's
 *  invalid_input) as one sentence. A code with several server sentences gets the one that is
 *  true of all of them. `tests/forms-refusals.test.ts` reads the server's codes and fails on a
 *  code without a sentence here. */
const REFUSALS: Record<string, string> = {
  invalid_input: "填写的内容不符合要求，请检查后再试",
  // the fill-in link
  link_not_found: "这个填写链接不存在或已失效。请向发出请求的人索取新的链接。",
  link_revoked: "这个填写链接已被撤销。",
  link_expired: "这份表单已过截止时间，不能再填写。如需延期请联系发出请求的人。",
  request_closed: "这份表单已停止接收填写。",
  link_unavailable: "这份表单当前不能填写。",
  request_not_open: "这份表单当前不接收提交，请刷新页面查看状态",
  form_version_changed: "表单已更新，请刷新页面后再填写",
  already_submitted: "这份表单你已经提交过了。发出请求的人确认或要求补充后，你会看到相应状态。",
  filler_key_required: "页面缺少填写标识，请刷新后重试",
  sign_in_required: "这份表单要求登录后填写。请用收到这份请求的邮箱对应的账户登录。",
  not_the_recipient: "这份表单是发给其他人的，你不能填写",
  draft_too_large: "草稿过大，请减少内容后再试",
  answers_invalid: "还有需要填写或更正的内容",
  rate_limited: "操作过于频繁，请稍后再试",
  // attachments
  empty_file: "文件是空的",
  file_type_not_accepted: "不接受这种文件类型",
  not_a_file_field: "这个字段不接受附件",
  too_many_files: "这个字段的附件数量已达上限",
  file_too_large: "文件超过允许的大小",
  file_not_found: "附件不存在，或已不能再修改",
  export_not_allowed: "你在这个工作空间没有导出权限，不能下载附件",
  // forms, versions and publishing
  form_not_found: "表单不存在或不在这个工作空间",
  form_version_not_found: "这个表单版本不存在",
  draft_conflict: "内容已被其他人或其他页面修改，请刷新后再试",
  draft_unsaved: "有修改还没有保存，没有发布。保存后再发布。",
  form_not_publishable: "表单还不能发布，请先处理列出的问题",
  nothing_to_publish: "草稿与最新发布的版本相同，不需要再次发布",
  form_archived: "表单已归档，恢复后才能这样做",
  form_not_published: "尚未发布：发布后才能发出填写请求或开放公开链接。",
  form_version_retired: "这个版本已停用，请使用仍在使用的版本",
  // requests and review
  request_not_found: "填写请求不存在或不在这个工作空间",
  request_finished: "这份填写请求已经结束，不能再修改",
  public_link_request: "公开填写由表单的分享设置管理，请在那里更换链接或关闭公开填写",
  due_in_past: "截止时间必须晚于现在",
  recipient_required: "要求收件人登录填写时，需要填写收件人的邮箱",
  recipient_not_found: "收件人账户不存在或不可用",
  prefill_too_large: "预填内容过大",
  prefill_unknown_field: "预填里有表单没有的字段",
  prefill_file_field: "附件字段不能预填",
  prefill_invalid: "预填内容不符合表单要求",
  submission_not_found: "提交记录不存在或不在这个工作空间",
  submission_not_reviewable: "这份提交已被处理过，请刷新后查看",
  unknown_field: "补充要求里有表单没有的字段",
  result_access_unverifiable: "无法核实这次操作结果的访问权限，请刷新后再试",
};

/** A refusal as the sentence a Forms page shows, or null when Forms has no sentence of its own
 *  for it (the shared client's wording then stands). Takes anything with a `code`. */
export function refusalSentence(t: T, e: unknown): string | null {
  const code = (e as { code?: unknown } | null)?.code;
  if (typeof code !== "string") return null;
  if (code === "invalid_input") {
    const locs: string[][] = ((e as any).detail?.fields ?? []).map((f: any) => (Array.isArray(f?.loc) ? f.loc.map(String) : []));
    if (locs.some((l) => l[0] === "recipient" && l[1] === "email")) return t("收件人邮箱格式不正确");
  }
  return Object.hasOwn(REFUSALS, code) ? t(REFUSALS[code]) : null;
}

/** What a Forms page says when something it asked for did not happen. */
export function failureText(t: T, e: unknown): string {
  const own = refusalSentence(t, e);
  if (own) return own;
  const message = (e as { message?: unknown } | null)?.message;
  return typeof message === "string" ? message : String(e);
}

/** A refused draft save, said as what to change and where (`draftRefusals` in logic.ts). */
export function refusalText(t: T, r: DraftRefusal, defn: Definition): string {
  const top = r.index !== undefined ? defn.fields[r.index] : undefined;
  const name = (f: { label: string; type: FieldType } | undefined, n: number) =>
    f?.label?.trim() ? f.label : t("第 {n} 个字段（{type}）", { n, type: f ? typeLabel(t, f.type) : "" });
  switch (r.part) {
    case "label":
      return r.sub !== undefined
        ? t("「{group}」里的第 {n} 个字段还没有名称", { group: name(top, (r.index ?? 0) + 1), n: r.sub + 1 })
        : top?.type === "section" ? t("{field}还没有标题", { field: name(top, (r.index ?? 0) + 1) })
        : t("{field}还没有填写问题", { field: name(top, (r.index ?? 0) + 1) });
    case "option": {
      const owner = r.sub !== undefined ? top?.fields?.[r.sub] : top;
      return t("「{field}」的选项 {n} 的文字和值都不能为空", { field: name(owner, (r.sub ?? r.index ?? 0) + 1), n: (r.option ?? 0) + 1 });
    }
    case "step": return t("第 {n} 步还没有名称", { n: (r.index ?? 0) + 1 });
    case "rule": return t("第 {n} 条跨字段校验还没有填写提示", { n: (r.index ?? 0) + 1 });
    case "title": return t("表单名称不能为空");
    default:
      return top ? t("「{field}」的设置不符合要求", { field: name(top, (r.index ?? 0) + 1) }) : t("表单的设置不符合要求");
  }
}

export function errorText(t: T, e: FieldError): string {
  const p = e.params ?? {};
  switch (e.code) {
    case "required": return t("必填");
    case "too_short": return t("至少 {n} 个字", { n: p.min });
    case "too_long": return t("最多 {n} 个字", { n: p.max });
    case "bad_email": return t("请填写有效的邮箱地址");
    case "bad_phone": return t("请填写有效的电话号码");
    case "bad_url": return t("请填写以 http:// 或 https:// 开头的网址");
    case "pattern_mismatch": return p.hint ? t("格式不正确：{hint}", { hint: p.hint }) : t("格式不正确");
    case "not_a_number": return t("请填写数字");
    case "too_many_decimals": return t("最多 {n} 位小数", { n: p.max });
    case "below_min": return t("不能小于 {n}", { n: p.min });
    case "above_max": return t("不能大于 {n}", { n: p.max });
    case "wrong_currency": return t("币种必须是 {c}", { c: p.currency });
    case "bad_date": return t("请填写有效的日期");
    case "bad_datetime": return t("请填写有效的日期和时间");
    case "datetime_without_offset": return t("日期时间缺少时区");
    case "not_an_option": return t("请从给出的选项中选择");
    case "too_few": return t("至少选择或填写 {n} 项", { n: p.min });
    case "too_many": return t("最多 {n} 项", { n: p.max });
    case "file_missing": return t("附件不存在，请重新上传");
    case "file_type": return t("不接受这种文件类型");
    case "file_too_large": return t("文件超过 {n} MB", { n: p.max_mb });
    case "rule_failed": return p.message || t("与其他答案不一致");
    case "file_empty": return t("文件是空的");
    case "file_malware": return t("文件被判定为恶意内容，已拒绝");
    case "file_encrypted": return t("文件设有密码或含有加密内容，无法检查，已拒绝。请移除密码后重新上传");
    case "file_unchecked": return t("文件结构无法完成安全检查，已拒绝。请重新保存文件后再上传");
    case "scan_unavailable": return t("安全检查服务暂不可用，请稍后重试");
    case "rate_limited": return t("操作过于频繁，请稍后再试");
    case "upload_failed": return t("上传失败，请重试");
    default: return t("内容无效");
  }
}

export function problemText(t: T, code: string): string {
  switch (code) {
    case "no_fields": return t("至少需要一个可以填写的字段");
    case "condition_unknown_field": return t("显示或必填条件引用了不存在的字段");
    case "condition_forward_reference": return t("条件只能引用排在前面的字段");
    case "repeat_without_fields": return t("重复明细里至少要有一个字段");
    case "repeat_field_type": return t("重复明细里不能放附件、分节或另一个重复明细");
    case "steps_must_cover_fields_once": return t("分步填写时，每个字段都要且只能属于一个步骤");
    case "rule_unknown_field": return t("校验规则引用了不存在的字段");
    case "rule_incomparable": return t("校验规则只能比较两个同类型的数字、金额或日期字段");
    case "options_required": return t("选择题至少要有一个选项");
    case "options_duplicate": return t("选项的值不能重复");
    case "pattern_required": return t("自定义格式需要填写格式规则");
    case "pattern_unsafe": return t("格式规则无效或过于复杂");
    case "range_inverted": return t("最小值大于最大值");
    case "bound_not_a_number": return t("最小值或最大值不是数字");
    case "currency_required": return t("金额字段需要选择币种");
    case "file_type_not_offered": return t("附件字段包含不支持的文件类型");
    default: return t("定义有问题");
  }
}

export function typeLabel(t: T, type: FieldType): string {
  switch (type) {
    case "text": return t("单行文本");
    case "textarea": return t("多行文本");
    case "number": return t("数字");
    case "money": return t("金额");
    case "date": return t("日期");
    case "datetime": return t("日期时间");
    case "select": return t("单选");
    case "multiselect": return t("多选");
    case "boolean": return t("勾选确认");
    case "file": return t("附件");
    case "repeat": return t("重复明细");
    case "section": return t("分节说明");
  }
}

/** A request page's heading. The public link's request has no title of its own: it is the
 *  form's public fill-in, named after the form. */
export function requestHeading(t: T, r: { kind?: string; title?: string | null; form_title?: string | null }): string {
  if (r.kind === "public") return t("「{form}」的公开填写", { form: r.form_title || t("未命名表单") });
  return r.title || r.form_title || t("未命名表单");
}

export function requestStatus(t: T, s: string): [string, string] {
  switch (s) {
    case "open": return [t("待填写"), "info"];
    case "submitted": return [t("已提交"), "ok"];
    case "changes_requested": return [t("待补充"), "warn"];
    case "accepted": return [t("已确认"), "ok"];
    case "closed": return [t("已关闭"), ""];
    case "revoked": return [t("已撤销"), "err"];
    case "expired": return [t("已过期"), "warn"];
    default: return [s, ""];
  }
}

export function submissionStatus(t: T, s: string): [string, string] {
  switch (s) {
    case "submitted": return [t("待确认"), "info"];
    case "changes_requested": return [t("已要求补充"), "warn"];
    case "accepted": return [t("已确认"), "ok"];
    case "superseded": return [t("已被新修订取代"), ""];
    default: return [s, ""];
  }
}
