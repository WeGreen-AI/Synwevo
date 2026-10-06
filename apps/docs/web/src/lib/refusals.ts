/** How a refusal from the Synwevo Docs server reaches the reader as a sentence in their language
 *  (CI-86), before any code map is consulted:
 *
 *   1. a template per code, worded from the numbers and names the server puts in `detail` — a
 *      size limit, a queue limit, a file name — because a sentence carrying a deployment
 *      setting cannot be matched by its text once the setting changes;
 *   2. the server's sentence itself, when the catalogue has it (`messages.refusals.ts` and the
 *      other Docs catalogues);
 *   3. a catalogued pattern: the format engines refuse with sentences that carry a value (a
 *      page, a field name, a limit) and no structured detail, so the value is read back out of
 *      the sentence by its shape. A `{reason}` inside is itself a server sentence and is
 *      presented the same way; when it cannot be, a reader outside zh-Hans gets the code's
 *      sentence instead of Chinese prose.
 *
 *  Anything else falls through to the code maps in `presentation.ts` and the shared ones.
 *  `tests/refusals.test.ts` reads the server's refusals and holds all of this to them. */
import { getLocale, hasMessage, registerMessages, t } from "@synwevo/ui/i18n-core";
import { registerErrorTemplates, type ErrorTemplate } from "@synwevo/auth-client/api-messages";
import { refusalMessages, refusalPatterns } from "../messages.refusals.ts";

registerMessages(refusalMessages);
registerMessages(refusalPatterns);

type Params = Record<string, string | number>;
interface Pattern { source: string; re: RegExp; names: string[]; weight: number }

const PARAM = /\{([a-zA-Z]\w*)\}/g;
const HAS_PARAM = /\{[a-zA-Z]\w*\}/;

function compile(source: string): Pattern {
  const names: string[] = [];
  let body = "", last = 0;
  for (const m of source.matchAll(PARAM)) {
    body += source.slice(last, m.index).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "([\\s\\S]+?)";
    names.push(m[1]);
    last = m.index! + m[0].length;
  }
  body += source.slice(last).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return { source, re: new RegExp(`^${body}$`), names, weight: source.replace(PARAM, "").length };
}
/** Most literal text first, so a longer sentence is never read as a shorter one's value. */
const PATTERNS: Pattern[] = Object.keys(refusalPatterns).map(compile).sort((a, b) => b.weight - a.weight);

/** A known server sentence in the reader's language, or null when it is not one. Only for
 *  refusals and diagnostics the server wrote — never file contents or arbitrary text. */
export function refusalText(message: unknown, depth = 0): string | null {
  if (typeof message !== "string" || !message) return null;
  if (hasMessage(message) && !HAS_PARAM.test(message)) return t(message);
  for (const p of PATTERNS) {
    const m = p.re.exec(message);
    if (!m) continue;
    const params: Params = {};
    let presentable = true;
    p.names.forEach((name, i) => {
      let value = m[i + 1];
      if (name === "reason") {
        const nested = depth < 2 ? refusalText(value, depth + 1) : null;
        if (nested !== null) value = nested;
        else if (getLocale() !== "zh-Hans") presentable = false;   // no Chinese prose inside another language
      }
      params[name] = value;
    });
    if (presentable) return t(p.source, params);
  }
  return null;
}

/** zh-Hans readers may be shown a server sentence the catalogue does not have: it is theirs. */
const orOwn = (value: unknown): string | null =>
  refusalText(value) ?? (getLocale() === "zh-Hans" && typeof value === "string" && value ? value : null);

const positive = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null);
const count = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null);
const finite = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

const EDITOR_TOO_LARGE_SIZE = "这份文档有 {size} MB，超过 {n} MB 上限。通常是插入的图片太大——图片是连同文档一起提交的。请换用更小的图片，或把它裁剪、压缩之后再插入。你的其它修改还在编辑器里，没有丢失。";
const EDITOR_TOO_LARGE = "这份文档超过 {n} MB 上限。通常是插入的图片太大——图片是连同文档一起提交的。请换用更小的图片，或把它裁剪、压缩之后再插入。你的其它修改还在编辑器里，没有丢失。";

/** code → sentence from `detail`, or null to fall through to the server's sentence. Every source
 *  string is in `messages.refusals.ts`. */
export const REFUSAL_TEMPLATES: Record<string, ErrorTemplate> = {
  too_large: (d) => {
    const name = text(d.filename), n = positive(d.limit_mb);
    return name && n ? ["文件「{name}」超过 {n} MB 上限", { name, n }] : null;
  },
  editor_body_too_large: (d) => {
    const n = positive(d.limit_mb), size = positive(d.size_mb);
    return n == null ? null : size ? [EDITOR_TOO_LARGE_SIZE, { size, n }] : [EDITOR_TOO_LARGE, { n }];
  },
  malware_detected: (d) => {
    const name = text(d.filename), signature = text(d.signature);
    if (!name) return null;
    if (d.earlier === true) return ["文件「{name}」的内容此前已被判定为恶意内容，已拒绝", { name }];
    return signature ? ["文件「{name}」被判定为恶意内容（{signature}），已拒绝", { name, signature }] : null;
  },
  scan_limit: (d) => {
    const n = positive(d.limit_mb);
    return n ? ["文件超过安全检查的大小上限（{n} MB），无法接纳；请拆分或压缩后重新上传", { n }] : null;
  },
  block_missing: (d) => {
    const name = text(d.filename), blocks = count(d.blocks);
    return name && blocks != null ? ["「{name}」只有 {count} 个表单块", { name, count: blocks }] : null;
  },
  unsupported_template: (d) => {
    const format = text(d.format);
    return format ? ["{format} 不能作为模板。可作为模板的格式：DOCX、XLSX、PDF、PPTX、PNG、JPEG（以及可转换的 DOC/XLS/PPT/ODT/ODS/ODP/RTF、BMP/TIFF/WEBP）", { format }] : null;
  },
  template_in_use: (d) => {
    const name = text(d.template), total = positive(d.total), project = text(d.projects?.[0]?.name);
    return name && total && project ? ["模板「{name}」正被 {count} 个项目使用（如「{project}」）。请先在这些项目中更换模板或删除这些项目，再删除模板", { name, count: total, project }] : null;
  },
  // `bad_rule` covers every sample-rule refusal; only the regular expression one carries a value
  bad_rule: (d) => {
    const error = text(d.regex_error);
    return error ? ["标记的正则表达式无效：{error}", { error }] : null;
  },
  too_many_pages: (d) => {
    const limit = positive(d.limit), requested = positive(d.requested);
    if (!limit || !requested) return null;
    // The workbench's OCR button sends no page list: "specify the pages in batches" is not
    // something its reader can do.
    return d.pages_given === false
      ? ["这个文件有 {count} 页需要识别，超过一次 {limit} 页的上限；请把文件拆分成较小的文件后分别上传", { count: requested, limit }]
      : ["一次最多识别 {limit} 页，本次 {count} 页；请分批指定页码", { limit, count: requested }];
  },
  // `not_found` is shared by every object; only the sample search quotes what was searched for
  not_found: (d) => {
    const search = text(d.search);
    return search ? ["没有找到包含「{search}」的样本", { search }] : null;
  },
  queue_full: (d) => {
    const active = positive(d.active_jobs), limit = positive(d.limit);
    return active && limit ? ["本工作空间已有 {count} 个任务在排队或进行中（上限 {limit}）；请等部分任务结束后再提交", { count: active, limit }] : null;
  },
  format_unavailable: (d) => {
    const format = text(d.format);
    if (!format) return null;
    const reason = orOwn(d.reason);
    return reason ? ["无法导出 {format}：{reason}", { format, reason }] : ["无法导出 {format}。", { format }];
  },
  sources_not_ready: (d) => {
    const files = Array.isArray(d.files) ? d.files : [];
    const total = positive(d.parsing_total) ?? (files.length || null), name = text(files[0]);
    return total && name ? ["还有 {count} 个来源文件正在解析（如「{name}」）；请等解析完成后再提交", { count: total, name }] : null;
  },
  too_many: (d) => {
    const records = positive(d.records), limit = positive(d.limit);
    return records && limit ? ["样本数 {count} 超过单次任务上限 {limit}", { count: records, limit }] : null;
  },
  has_errors: (d) => {
    const invalid = positive(d.invalid_total);
    return invalid ? ["{count} 个样本存在错误，已按「有错误则不开始」停止。请修正或选择「跳过错误样本」", { count: invalid }] : null;
  },
  bad_naming: (d) => {
    const n = positive(d.limit);
    return n ? ["文件命名规则最多 {n} 个字符", { n }] : null;
  },
  // `invalid_field` is shared by every field; only the comment ones quote a limit
  invalid_field: (d) => {
    const n = positive(d.limit);
    if (!n) return null;
    if (d.field === "body") return ["评论最多 {n} 个字符", { n }];
    if (d.field === "mentions") return ["一条评论最多提及 {n} 位成员", { n }];
    return null;
  },
  // the editors' numeric ranges; every other editor refusal is worded by its sentence
  bad_editor: (d) => {
    const min = finite(d.min), max = finite(d.max), field = text(d.field) || "";
    if (min == null || max == null) return null;
    if (/\.page\.margins\.\w+$/.test(field)) return ["页边距需要在 {min}–{max} 毫米之间", { min, max }];
    if (/(\.fontSize|\.base_size|\.s\.sz)$/.test(field)) return ["字号需要在 {min}–{max} 之间", { min, max }];
    if (/\.attrs\.width$/.test(field)) return ["图片宽度需要在 {min}–{max} 之间", { min, max }];
    return ["数值需要在 {min}–{max} 之间", { min, max }];
  },
};

registerErrorTemplates(REFUSAL_TEMPLATES);

/** The template's sentence for this refusal, or null. */
export function templatedText(code: string | undefined, detail: unknown): string | null {
  const tpl = code && Object.hasOwn(REFUSAL_TEMPLATES, code) ? REFUSAL_TEMPLATES[code] : undefined;
  if (!tpl) return null;
  try {
    const r = tpl(detail && typeof detail === "object" ? detail : {});
    return r ? t(r[0], r[1]) : null;
  } catch {
    return null;
  }
}

/** Why an approved copy did not run: the request stores a reason code (project_copies
 *  `preconditions`, copies.CopyExecutor), never a sentence — word it instead of showing it (CI-86). */
const COPY_ERROR: Record<string, string> = {
  source_changed: "源项目在批准后有改动，批准范围不再有效；请重新申请。",
  approval_expired: "批准已过期；请重新申请。",
  source_authority_lost: "申请人不再拥有源项目。",
  decider_authority_lost: "审批者不再具有审批权限；请重新申请。",
  organization_unavailable: "目标组织当前不可用。",
};
export function copyErrorText(code: string): string {
  if (Object.hasOwn(COPY_ERROR, code)) return t(COPY_ERROR[code]);
  if (code.startsWith("target:")) return t("你已不能把项目复制到目标工作空间；请确认你仍是它的编辑成员。");
  if (code.startsWith("source:")) return t("源项目已不可用。");
  if (code.startsWith("transfer:")) return t("当前数据流政策或导出权限不允许复制到目标空间，请核对后重新申请。");
  if (code.startsWith("status:")) return t("申请的状态已改变。");
  return t("复制过程出错，没有创建任何内容；请稍后重新申请。");
}
