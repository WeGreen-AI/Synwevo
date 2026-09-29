/** How the Forms pages word the server's codes: field errors, publishing problems, field types,
 *  statuses. Every code the server can return has a sentence here (the catalogue test checks the
 *  strings exist in all three languages). */
import type { FieldError, FieldType } from "./logic";

type T = (key: string, params?: Record<string, any>) => string;

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
