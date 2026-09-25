import { getLocale, hasMessage, joinMessages, registerMessages, t } from '@synwevo/ui/i18n-core';
import { messages } from "../messages.ts";
import { serverMessages } from '../messages.server.ts';
registerMessages(messages);
registerMessages(serverMessages);
const sourceOf = new Map<string, string>();
for (const [source, entry] of Object.entries({ ...messages, ...serverMessages })) {
  for (const value of Object.values(entry)) {
    for (const form of typeof value === "string" ? [value] : Object.values(value)) {
      if (typeof form === "string" && !form.includes("{")) sourceOf.set(form, source);
    }
  }
}

/** Stable wire codes stay unchanged. Only their presentation belongs to this module. */
export const ERROR_SOURCE: Record<string, string> = {
  network_error: '网络连接失败，请检查连接后重试。',
  network: '网络连接失败，请检查连接后重试。',
  server_error: '服务内部出错，请稍后重试。',
  gateway_blocked: '请求被前置网关拦截，未到达 Synwevo。',
  upstream_unavailable: '前置网关无法连接 Synwevo，请稍后重试。',
  rate_limited: '请求过于频繁，请稍后重试。',
  unauthorized: '请重新登录后再试。',
  forbidden: '你没有执行此操作的权限。',
  not_found: '找不到请求的资源。',
  no_template: '请先选择目标模板。',
  template_in_use: '模板仍被项目使用，无法删除。',
  encrypted: '模板文件已加密，请移除密码后再上传。',
  malware_detected: '安全检查未通过：文件被判定为恶意内容，已拒绝。',
  scan_unavailable: '安全检查服务暂不可用，请稍后重试。',
  scan_limit: '文件超过安全检查的大小上限，无法接纳；请拆分或压缩后重新上传。',
  not_yet_accepted: '该文件尚未通过安全检查，暂不能下载。',
  rejected_malware: '该文件未通过安全检查，已被拒绝并隔离。',
  nothing_to_undo: '当前没有可以撤销的修改。',
  nothing_to_redo: '当前没有可以重做的修改。',
  queue_full: '生成队列已满，请等待当前任务完成。',
  sources_not_ready: '来源文件尚未就绪，请等待解析完成。',
  source_not_ready: '来源文件尚未就绪，请等待解析完成。',
  no_records: '没有识别到样本，请检查样本规则。',
  has_errors: '请先修正样本中的错误。',
  artifact_deleted: '输出文件已被删除或清理，请重新生成。',
  artifact_missing: '输出文件已被删除或清理，请重新生成。',
  no_rule: '请先设置样本规则。',
  simulation_busy: '服务器正在进行其他模拟生成，请稍后重试。',
  idempotency_conflict: '该请求标识已用于不同内容，请重新发起操作。',
  job_active: '任务仍在进行中，请等待结束或先取消。',
  nothing_to_retry: '没有可重试的样本。',
  engine_unavailable: '所需的本地处理组件不可用，请联系管理员。',
  format_unavailable: '所需的本地处理组件不可用，请联系管理员。',
  convert_failed: '文件转换失败，请检查文件与本地转换组件。',
  missing_column: '缺少列', ambiguous_column: '重名列', missing_sheet: '缺少工作表',
  ambiguous_sheet: '工作表歧义', anchor_missing: '找不到定位标签', anchor_ambiguous: '定位标签不唯一',
  layout_mismatch: '版式不符', required_empty: '必填为空', unmapped_required: '必填未映射',
  group_conflict: '组内取值冲突', region_size: '区域尺寸不符', target_missing: '目标字段失效',
  target_conflict: '多个映射写同一字段', needs_ocr: '需要 OCR', ocr_low_confidence: 'OCR 待确认',
  path_missing: '结构不存在', mode_mismatch: '与样本模式不符', value_error: '值错误',
  no_detail: '无明细', type_mismatch: '类型不匹配', combine_missing: '缺少组合规则',
  content_lost: '输出内容验证未通过。', content_truncated: '字段内容无法完整放入目标区域。',
  text_overflow: '字段内容无法完整放入目标区域。', page_overflow: '内容超过允许的模板页数。',
  shrunk_to_fit: '为保持页数，已缩小允许缩小的字段。', layout_unavailable: '无法完成真实分页检查。',
  stale: '内容已在其他窗口或被其他人修改，这次操作没有执行。已刷新为最新内容，请在此基础上重新操作。',
  stale_template: '模板已被其他人修改',
  stale_project: '项目设置已在其他窗口或被其他人修改，这次操作没有执行。已刷新为最新设置，请在此基础上重新操作。',
  conflict: '映射已被修改',
  optional_empty: '可选字段为空，已跳过。',
  transform: '部分值未能应用指定的转换，请检查映射。',
  csv_single_sheet: 'CSV 仅导出第一个工作表。',
  slide_overflow: '内容超出幻灯片的目标区域。',
  rows_shrunk: '明细行已缩小以适应目标区域。',
  continued_pages: '明细已续排到新增页面。',
  control_chars: '已移除不支持的控制字符。',
  ref_removed: '字段引用已被移除，请检查映射。',
  formula_computed: '内置公式引擎已计算公式并写入结果。',
  formula_fallback: '公式已交由排版引擎计算。',
  formula_uncomputed: '部分公式无法计算，无法导出 CSV。请改用 XLSX，或请管理员启用排版引擎。',
  formula_unsupported: '部分公式无法在服务器计算，请在 Excel 中检查。',
  formula_error: '部分公式的计算结果是错误值。',
  recalc_failed: '公式未能重新计算，Excel 打开时会再次计算。',
  xlsx: '表格处理存在限制，请检查模板与输出。',
};
for (const code of ['bad_value', 'bad_output', 'bad_layout', 'bad_error_policy', 'bad_naming', 'bad_formats', 'bad_rule', 'bad_rect', 'bad_mapping', 'bad_json', 'invalid']) ERROR_SOURCE[code] = '请求参数无效，请检查设置。';
for (const code of ['unsupported', 'unsupported_template', 'bad_format', 'not_pdf', 'unsupported_media_type', 'unsupported_export']) ERROR_SOURCE[code] = '该格式不支持此操作。';
for (const code of ['too_large', 'too_many', 'editor_body_too_large']) ERROR_SOURCE[code] = '文件或请求超过大小上限。';

/** Only call for known service UI text: never file contents, user names or arbitrary DOM. */
export function serverText(value: unknown): string {
  const text = typeof value === 'string' ? value : value == null ? '' : String(value);
  const exact = t(sourceOf.get(text) || text);
  if (exact !== text || getLocale() === 'zh-Hans') return exact;
  const native = /^(\w+)（模板原生格式）$/.exec(text);
  if (native) return t('{format}（模板原生格式）', { format: native[1] });
  const image = /^(\w+)（与模板图片同尺寸）$/.exec(text);
  if (image) return t('{format}（与模板图片同尺寸）', { format: image[1] });
  const section = /^(页眉|页脚)（第 (\d+) 节）$/.exec(text);
  if (section) return t(section[1] === '页眉' ? '页眉（第 {section} 节）' : '页脚（第 {section} 节）', { section: section[2] });
  const rows = /^(\d+) 行$/.exec(text);
  if (rows) return t('{count} 行', { count: Number(rows[1]) });
  const pageRange = /^第 (\d+) 页(?:–第 (\d+) 页)?$/.exec(text);
  if (pageRange) return pageRange[2] ? t('第 {start}–{end} 页', { start: Number(pageRange[1]), end: Number(pageRange[2]) }) : t('第 {v1} 页', { v1: Number(pageRange[1]) });
  const lineRange = /^(?:(.*) )?第 (\d+)–(\d+) 行$/.exec(text);
  if (lineRange) return (lineRange[1] ? `${lineRange[1]} · ` : '') + t('第 {start}–{end} 行', { start: Number(lineRange[2]), end: Number(lineRange[3]) });
  const blockRange = /^第 (\d+)–(\d+) 个段落\/表格$/.exec(text);
  if (blockRange) return t('第 {start}–{end} 个段落/表格', { start: Number(blockRange[1]), end: Number(blockRange[2]) });
  // Capability descriptions occasionally join two fixed server messages.
  for (const source of Object.keys(serverMessages).sort((a, b) => b.length - a.length)) {
    if (source.length > 12 && text.startsWith(source)) {
      const suffix = text.slice(source.length);
      if (!suffix || !/[\u3400-\u9fff]/.test(suffix) || Object.hasOwn(serverMessages, suffix)) return t(source) + t(suffix);
    }
  }
  // Unknown diagnostics remain verbatim: do not invent detail or erase troubleshooting evidence.
  return text;
}
/** Legacy engines also emit strings without codes. Only finite known prose is presented;
 * originals stay in their records for diagnosis, not mixed into another locale's UI. */
export function diagnosticText(value: unknown): string {
  const text = typeof value === 'string' ? value : '';
  const source = sourceOf.get(text) || text;
  const localized = serverText(source);
  return hasMessage(source) || localized !== source ? localized : t('操作失败，请检查设置后重试。');
}
export interface IssueLike { code?: string; error?: string; level?: string; message?: string; originalMessage?: string; requestId?: string; request_id?: string; detail?: { field?: string } }
export function issueText(issue: IssueLike | string | null | undefined): string {
  if (!issue) return t('操作失败，请检查设置后重试。');
  if (typeof issue === 'string') return diagnosticText(issue);
  const code = issue.code || issue.error;
  // ApiError owns localized transport diagnostics (gateway/status/request id included).
  if (issue.originalMessage !== undefined && (!code || !ERROR_SOURCE[code])) return issue.message || t('操作失败，请检查设置后重试。');
  const message = issue.originalMessage ?? issue.message ?? '';
  const source = sourceOf.get(message) || message;
  let result = hasMessage(source) ? t(source) : code && ERROR_SOURCE[code] ? t(ERROR_SOURCE[code])
    : code ? t(issue.level === 'info' || issue.level === 'warning' ? '处理提示（{code}）。' : '请求未完成（{code}）。', { code }) : t('操作失败，请检查设置后重试。');
  // Structured field identifiers are data and remain verbatim.
  if (issue.detail?.field) result += ` (${issue.detail.field})`;
  const id = issue.requestId || issue.request_id;
  if (id) result = joinMessages([result, t('请求编号：{id}', { id })]);
  return result;
}
