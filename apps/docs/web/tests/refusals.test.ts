/** CI-86: every refusal the Synwevo Docs server can give a person in the workbench reaches them
 *  as a sentence in their language — never as "请求未完成（code）", never as the generic
 *  "操作失败，请检查设置后重试。" when the server said something specific, and never as Chinese
 *  server prose inside an English or Traditional Chinese page.
 *
 *  This test reads the server's refusals — `ServiceError(...)`, the `not_found(...)` helper, the
 *  editor contract's `_bad(...)` and the format-engine errors the routes pass on — and requires
 *  each to be one of:
 *    - catalogued: the sentence is in a Docs catalogue (`messages.refusals.ts` and the others);
 *    - templated: `lib/refusals.ts` words it from the numbers and names in `detail`;
 *    - a pattern: an engine sentence with values in it, read back out by its shape;
 *    - code-mapped: its code's sentence in `ERROR_SOURCE` carries the same meaning;
 *    - API-only, with the reason no person using the workbench can meet it.
 *  Adding a server refusal therefore means deciding which of these it is.
 *
 *  Q13 holds a sample's own problems — the issues and warnings of its plan, its fill and its job
 *  item — to the same rule: see "per-sample diagnostics" at the end. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { setLocale, translate, validateMessages } from "@synwevo/ui/i18n-core";
import { apiMessages, API_ERROR_SOURCE } from "@synwevo/auth-client/api-messages";
import { ApiError } from "@synwevo/auth-client/api";
import { messages } from "../src/messages.ts";
import { serverMessages } from "../src/messages.server.ts";
import { refusalMessages, refusalPatterns } from "../src/messages.refusals.ts";
import { REFUSAL_TEMPLATES, copyErrorText, refusalText } from "../src/lib/refusals.ts";
import { ERROR_SOURCE, diagnosticText, issueText } from "../src/lib/presentation.ts";
import { errorText } from "../src/lib/perm.ts";

const ROOT = join(import.meta.dirname, "..", "..", "..", "..");
const DOCS = join(ROOT, "apps/docs/synwevo_docs");
/** Copy requests are made in the workbench and decided by the shared service in synwevo_core. */
const SHARED_FILES = ["packages/core/synwevo_core/project_copies.py"];

// ------------------------------------------------------------------ reading the server

type Kind = "service" | "not_found" | "editor" | "coded" | "plain";
/** Every way the Docs server words a refusal. `code` is what an uncoded engine error reaches
 *  the browser with (the route that passes it on names it); null when it reaches it as the
 *  ValueError handler's generic `invalid` (for a per-sample exception: the code of the issue it
 *  becomes). `only` limits a common name to the files where it is a refusal rather than an
 *  internal error. */
const CONSTRUCTORS: Record<string, { kind: Kind; code?: string | null; only?: RegExp }> = {
  ServiceError: { kind: "service" },
  not_found: { kind: "not_found" },                                  // synwevo_core.errors.not_found(what)
  _bad: { kind: "editor", only: /^editor_contract\.py$/ },            // bad_editor
  TemplateError: { kind: "coded" }, BlockError: { kind: "coded" }, DocxError: { kind: "coded" },
  PptxError: { kind: "coded" }, XlsxError: { kind: "coded" }, ImageTemplateError: { kind: "coded" },
  PdfError: { kind: "coded" }, RegressionError: { kind: "coded" }, DesensitizeError: { kind: "coded" },
  // form-block splitting and locator building answer requests with a ResolveError; everywhere
  // else it is a per-sample diagnostic, which reaches the reader inside the sample's issue — as
  // it is, or behind 「映射 #n：」 (Q13: see "per-sample diagnostics" below). Both are sentences.
  ResolveError: { kind: "coded" },
  ColumnError: { kind: "coded" },                                    // tabular.resolve_column, passed on as a ResolveError
  // per-sample value and page problems: the plan and the fill put them into the sample's issues
  TransformError: { kind: "plain", code: "value_error" },
  ConflictError: { kind: "plain", code: "group_conflict" },
  LayoutUnavailable: { kind: "plain", code: "layout_unavailable" },
  BuiltinError: { kind: "plain", code: "bad_editor" },
  ConversionError: { kind: "plain", code: "convert_failed" },
  OcrError: { kind: "plain", code: "ocr_failed" },
  LimitError: { kind: "plain", code: null },
  // file detection (template upload) and sample rules (rule preview) refuse with plain ValueErrors
  ValueError: { kind: "plain", code: null, only: /^engine\/(detect|records)\.py$/ },
};

type Message = { f: boolean; text: string };
type Refusal = { file: string; line: number; ctor: string; code: string | null; codeExpr: string | null; msgs: Message[] | null; msgExpr: string | null; field: string | null };

function pyFiles(dir: string): string[] {
  const out: string[] = [];
  for (const n of readdirSync(dir)) {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) { if (n !== "__pycache__") out.push(...pyFiles(p)); }
    else if (n.endsWith(".py")) out.push(p);
  }
  return out.sort();
}
function skipString(src: string, i: number): number {
  const q = src[i];
  if (src.slice(i, i + 3) === q.repeat(3)) return src.indexOf(q.repeat(3), i + 3) + 2;
  for (i++; i < src.length; i++) { if (src[i] === "\\") { i++; continue; } if (src[i] === q) return i; }
  return i;
}
/** The arguments of the call whose "(" is at `open`, split at its own commas. */
function callArgs(src: string, open: number): string[] {
  const args: string[] = [];
  let depth = 0, start = open + 1;
  for (let i = open; i < src.length; i++) {
    const c = src[i];
    if (c === "#") { while (i < src.length && src[i] !== "\n") i++; continue; }
    if (c === '"' || c === "'") { i = skipString(src, i); continue; }
    if ("([{".includes(c)) depth++;
    else if (")]}".includes(c)) { if (--depth === 0) { args.push(src.slice(start, i)); break; } }
    else if (c === "," && depth === 1) { args.push(src.slice(start, i)); start = i + 1; }
  }
  return args.map(a => a.trim()).filter(Boolean);
}
/** The sentence(s) an argument can produce: adjacent literals concatenate, `A if c else B` gives
 *  two; anything else is an expression (a forwarded message) and returns null. */
function sentences(arg: string): Message[] | null {
  const toks: Message[] = [];
  let rest = "";
  for (let i = 0; i < arg.length;) {
    const m = /^([rbfRBF]{0,2})(["'])/.exec(arg.slice(i));
    if (m && (i === 0 || !/\w/.test(arg[i - 1]))) {
      const s = i + m[1].length, e = skipString(arg, s);
      toks.push({ f: /f/i.test(m[1]), text: arg.slice(s + 1, e).replace(/\\(["'\\])/g, "$1") });
      i = e + 1;
      continue;
    }
    rest += arg[i++];
  }
  rest = rest.replace(/\s+/g, " ").trim();
  if (!toks.length) return null;
  if (rest === "") return [{ f: toks.some(t => t.f), text: toks.map(t => t.text).join("") }];
  if (/^if .+ else$/.test(rest) && toks.length === 2) return toks;
  return null;
}
/** An f-string with every replacement field reduced to `{}`: the shape a catalogue key must have. */
function skeleton(text: string): string {
  let out = "", depth = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (depth === 0 && c === "{" && text[i + 1] === "{") { out += "{"; i++; continue; }
    if (depth === 0 && c === "}" && text[i + 1] === "}") { out += "}"; i++; continue; }
    if (c === "{") { if (depth++ === 0) out += "{}"; continue; }
    if (c === "}") { depth--; continue; }
    if (depth === 0) out += c;
  }
  return out;
}
const keySkeleton = (key: string) => key.replace(/\{[a-zA-Z]\w*\}/g, "{}");

function refusals(): Refusal[] {
  const out: Refusal[] = [];
  const files = [...pyFiles(DOCS).map(f => [f, relative(DOCS, f)]), ...SHARED_FILES.map(f => [join(ROOT, f), f])];
  // a bare name or a module-qualified one (`T.TemplateError(`, `transforms.ConflictError(`),
  // never a method (`self.x(`)
  const re = new RegExp(`(?<![\\w.])(?:(?!self\\.)[A-Za-z_]\\w*\\.)?(${Object.keys(CONSTRUCTORS).join("|")})\\(`, "g");
  for (const [path, file] of files) {
    const src = readFileSync(path, "utf8");
    const lines = src.split("\n");
    for (const m of src.matchAll(re)) {
      const spec = CONSTRUCTORS[m[1]];
      if (spec.only && !spec.only.test(file)) continue;
      const line = src.slice(0, m.index).split("\n").length;
      if (/^\s*(class|def) /.test(lines[line - 1])) continue;
      const args = callArgs(src, m.index! + m[0].length - 1);
      let code: string | null = null, msgArg: string | undefined, detail = "";
      if (spec.kind === "service") { code = args[1]; msgArg = args[2]; detail = args[3] || ""; }
      else if (spec.kind === "not_found") { code = '"not_found"'; msgArg = args[0]; }
      else if (spec.kind === "editor") { code = '"bad_editor"'; msgArg = args[1]; }
      else if (spec.kind === "coded") { code = args[0]; msgArg = args[1]; }
      else { code = spec.code ? `"${spec.code}"` : null; msgArg = args[0]; }
      const literal = code && /^["'][a-z_]+["']$/.test(code) ? code.slice(1, -1) : null;
      let msgs = msgArg ? sentences(msgArg) : null;
      if (msgs && spec.kind === "not_found") msgs = msgs.map(s => ({ ...s, text: `${s.text}不存在或已删除` }));
      out.push({ file, line, ctor: m[1], code: literal, codeExpr: literal ? null : code, msgs,
                 msgExpr: msgs ? null : (msgArg || "").replace(/\s+/g, " "), field: /"field":\s*"([\w.]+)"/.exec(detail)?.[1] ?? null });
    }
  }
  return out;
}

// ------------------------------------------------------------------ the decisions

/** Whole modules no person using the workbench reaches. */
const API_ONLY_FILES: [RegExp, string][] = [
  [/^api\/v1(_\w+)?\.py$/, "the machine API under /api/v1 (API keys, service accounts, SDK, CLI); the workbench calls only /api routes"],
  [/^webhooks\.py$/, "webhook subscriptions are managed through /api/v1 and the CLI; the workbench has no webhook screen"],
  [/^cli(_webhooks)?\.py$/, "the command line"],
  [/^control_plane\.py$/, "the API console's debug requests: that console is platform administration in the account shell"],
  [/^definitions\.py$/, "declarative field and project definitions are imported and exported only through /api/v1"],
  [/^api\/idempotency\.py$/, "the Idempotency-Key header of /api/v1; the job retry key the workbench sends is generated and valid"],
];

/** Refusals no person can cause from the workbench, with the reason. Sentences are the server's;
 *  an f-string is written as its skeleton (every value `{}`). */
const API_ONLY: [string, string[]][] = [
  ["a request field the workbench fills from its own controls, only ever with valid values", [
    "timezone 必须是 IANA 时区名（如 Asia/Shanghai）",
    "复制申请包含不支持的字段", "scope 必须是对象", "version 必须是正整数", "force_ids 必须是映射 id 列表",
    "请求内容必须是 JSON（Content-Type: application/json）", "请求内容不是有效的 JSON", "请求内容必须是 JSON 对象", "{} 必须是整数",
    "未知的内置模板类型", "force_ids 必须是字符串列表", "base_version 必须是正整数版本号", "note 必须是字符串",
    "评论内容无效", "只能回复本项目的一级评论", "请在一级评论上标记解决", "锚点类型无效",
    "simulate 只能是 true/false 或正整数（模拟的样本数）", "level 只能是 error 或 warning", "offset / limit 必须是整数", "offset 不能为负数",
    "limit 取值范围是 1–1000", "formats 必须是格式名称的数组", "error_policy 只能是 block 或 skip", "layout 只能是 off / warn / fit", "naming 必须是文本",
    "failed_only 不能与 include_skipped 同时启用", "{} 必须是布尔值", "discard_live 必须是布尔值",
    "sort 只能是 updated / created / name", "order 只能是 asc / desc", "limit 取值范围是 1–200", "base_settings_rev 必须是整数", "模板版本必须是正整数",
    "output 必须是对象", "样本规则格式错误", "未知的样本模式", "标记匹配方式无效", "分隔方式无效", "超出处理方式无效",
    "data_type 只能是 auto / text / number / date", "颜色格式应为 #RRGGBB", "映射缺少来源", "{} 取值无效", "empty_policy 只能是 {}；收到「{}」",
    "第 {} 个映射必须是对象", "pages 必须是页码（从 0 开始）的数组", "页码超出范围：{}（该文件共 {} 页，页码从 0 开始）",
    "不支持的选择类型：{}", "无法定位：{}", "用例编号无效", "action 只能是 approve 或 reject",
    "{} 只能是 {}；收到「{}」", "{} 应为数字；收到「{}」", "{} 应在 {}–{} 之间；收到「{}」",
    "DOCX 模板不支持的字段类型：{}", "XLSX 模板不支持的字段类型：{}", "PPTX 模板不支持的字段类型：{}", "无效的文档路径：{}", "路径不完整",
  ]],
  ["the editors produce only well-formed documents: node types, marks, attributes and field ids come from the editor, not from the person", [
    "必须是对象", "不支持的编辑器选项", "必须是数组", "必须是布尔值", "必须是六位十六进制颜色", "当前位置不支持该文档节点；未保存任何版本",
    "标题级别必须是 1–6 的整数", "合并跨度必须是正整数", "文本节点必须提供字符串", "不支持的文字格式", "字号必须是数字，可带 pt 或 px 后缀",
    "单元格值必须是文本、数字、布尔值或 null", "数字必须有限", "数字必须有限且不是布尔值", "字段 ID 必须有效且在整个模板中唯一",
    "kind 只能是 builtin_doc 或 builtin_sheet",
  ]],
  ["mappings by field name are an /api/v1 convenience; the workbench maps to the field's binding", [
    "项目还没有绑定模板，不能按字段名建立映射", "第 {} 个映射的目标字段「{}」在模板中不存在", "模板中有多个名为「{}」的字段，请改用 target.binding 指明",
  ]],
  ["a strict preview (a sample key or index that must exist, a signature that must match) is an /api/v1 request", [
    "没有找到该样本", "index 超出样本范围", "项目输入或模板已改变，请重新预检",
  ]],
  ["the workbench offers the action only where it applies: page images and row detection for PDF and image templates and outputs, the editor for built-in templates, a pinned version only from the template's own versions, a stored layout only from its own choices", [
    "只有 PDF / 图片结果可以渲染页面", "只有 PDF / 图片模板需要识别明细区域的行列", "只有 PDF / 图片模板有页面图像",
    "只有内置编辑器创建的模板可以在编辑器中修改", "模板没有第 {} 版", "项目默认设置中的 layout「{}」无效；请改为 off / warn / fit",
  ]],
  ["a template's format is checked when it is uploaded, so a stored template always has a supported one", [
    "不支持的模板格式：{}",
  ]],
];

/** Sentences whose meaning is what their code's `ERROR_SOURCE` sentence says, so the code map
 *  carries them — listed sentence by sentence, so a new sentence under one of these codes still
 *  needs a decision. A code with no sentences is the fallback of forwarded messages (FORWARDED)
 *  or of a pattern whose inner reason cannot be presented. */
const CODE_MAPPED: [code: string, reason: string, shapes: string[]][] = [
  ["no_rule", "the sample mode is not set; the code's sentence says so", ["请先设置样本模式"]],
  ["idempotency_conflict", "a retry that reuses a key with a different body; the code's sentence says what to do",
    ["该幂等键已用于另一个不同内容的请求；请换一个新的幂等键，或发送与原请求完全相同的内容"]],
  ["rejected_malware", "the code's sentence is the server's sentence", ["该文件未通过安全检查，已被拒绝并隔离"]],
  ["not_yet_accepted", "the code's sentence is the server's sentence", ["该文件尚未通过安全检查，暂不能下载"]],
  ["scan_unavailable", "the code's sentence is the server's sentence", ["安全检查服务暂不可用，请稍后重试"]],
  ["encrypted", "the code's sentence is the server's sentence", ["模板文件已加密，请移除密码后再上传"]],
  ["bad_template", "「模板无法解析：…」 quotes the parser: the pattern presents it when its words are known, the code's sentence when not", ["模板无法解析：{}"]],
  ["bad_editor", "「模板生成失败：…」 quotes an unexpected builder error", ["模板生成失败：{}"]],
  ["stale_template", "both people's table edits merged into a template that cannot be built (CI-116): the editors word it as the stale-version choice, with detail.merge_error translated beside it", ["你的修改与其他人之后保存的修改合在一起无法生成模板：{}"]],
  ["convert_failed", "the converter's own failures (profile directory, exit code) are for an operator; the code's sentence says where to look", [
    "排版引擎未生成 {} 文件（退出码 {}）：{}", "无法创建排版引擎配置目录：{}",
    "排版引擎配置目录 {} 不属于当前用户（或是符号链接），拒绝使用；请删除它，或用 SYNWEVO_DOCS_LO_PROFILE 指定一个私有目录"]],
  ["corrupt", "「无法打开图片/PDF：…」 quotes the decoder", ["无法打开图片：{}", "无法打开 PDF：{}"]],
  ["formula_uncomputed", "the code's sentence carries the advice; the count and the formulas are in the job's issues",
    ["第一个工作表有 {} 个公式无法在服务器计算（如 {}），CSV 无法写出它们的结果；请改为导出 XLSX，或请管理员启用排版引擎"]],
  ["source_failed", "a regression case's source no longer parses and the parser's words cannot be presented (pattern fallback)", []],
  ["ocr_failed", "the recognizer's own output (FORWARDED)", []],
  ["bad_format", "the value transforms' own checks (FORWARDED)", []],
];
const CODE_MAPPED_SHAPES = new Map(CODE_MAPPED.flatMap(([code, , shapes]) => shapes.map(s => [s, code] as const)));
/** Codes another part of the workbench words for every refusal that carries them. */
const SHARED_CODES: Record<string, string> = {
  accounts_disabled: "perm.ts permissionText", read_only_role: "perm.ts permissionText", export_not_allowed: "perm.ts permissionText",
  unauthorized: "the session ends and the shared presentation says to sign in again (API_ERROR_SOURCE)",
  too_many_attempts: "API_ERROR_SOURCE",
};
/** ServiceError sentences — f-strings as skeletons — that a template in lib/refusals.ts words from
 *  `detail`, with the code it is registered under. Each is exercised in "templated refusals". */
const TEMPLATED: Record<string, string> = {
  "文件「{}」超过 {} MB 上限": "too_large",
  "{}超过 {} MB 上限。通常是插入的图片太大——图片是连同文档一起提交的。请换用更小的图片，或把它裁剪、压缩之后再插入。你的其它修改还在编辑器里，没有丢失。": "editor_body_too_large",
  "文件「{}」的内容此前已被判定为恶意内容，已拒绝": "malware_detected",
  "文件「{}」被判定为恶意内容（{}），已拒绝": "malware_detected",
  "文件超过安全检查的大小上限（{} MB），无法接纳": "scan_limit",
  "「{}」只有 {} 个表单块": "block_missing",
  "{} 不能作为模板。可作为模板的格式：DOCX、XLSX、PDF、PPTX、PNG、JPEG（以及可转换的 DOC/XLS/PPT/ODT/ODS/ODP/RTF、BMP/TIFF/WEBP）": "unsupported_template",
  "模板「{}」正被 {} 个项目使用（如「{}」）。请先在这些项目中更换模板或删除这些项目，再删除模板": "template_in_use",
  "标记的正则表达式无效：{}": "bad_rule",
  "一次最多识别 {} 页，本次 {} 页；{}": "too_many_pages",
  "没有找到包含「{}」的样本": "not_found",
  "本工作空间已有 {} 个任务在排队或进行中（上限 {}）；请等部分任务结束后再提交": "queue_full",
  "无法导出 {}：{}": "format_unavailable",
  "还有 {} 个来源文件正在解析（如「{}」）；请等解析完成后再提交": "sources_not_ready",
  "样本数 {} 超过单次任务上限 {}": "too_many",
  "{} 个样本存在错误，已按「有错误则不开始」停止。请修正或选择「跳过错误样本」": "has_errors",
  "评论最多 {} 个字符": "invalid_field",
  "naming 必须是不超过 300 个字符的文本": "bad_naming",
  "必须是 {}–{} 范围内的有限数字": "bad_editor",
};
/** Refusals whose message is an expression, keyed by file and expression: what flows through. */
const FORWARDED: Record<string, string> = {
  "api/app.py: str(e)": "RegressionError, read below",
  "builtin_service.py: str(e)": "BuiltinError, read below",
  "services.py: str(e)": "ResolveError (form blocks), file detection's ValueError/LimitError, ConversionError, ImageTemplateError and TemplateError (with the DocxError, BlockError, XlsxError and PptxError it passes on), all read below",
  "views.py: str(e)": "OcrError and the sample rules' ValueError, read below",
  "jobs.py: str(e)": "the sample rules' ValueError, read below",
  "engine/templates.py: str(e)": "DocxError, BlockError, XlsxError and ImageTemplateError passed on as TemplateError, read below",
  "engine/pptx_engine.py: str(e)": "PptxError passed on as TemplateError, read below",
  "services.py: _pe.TEMPLATES_UNAVAILABLE": "a pdf_engine constant, checked below",
  "services.py: _pe.IMAGE_TEMPLATES_UNAVAILABLE": "a pdf_engine constant, checked below",
  "services.py: \"；\".join(errs)": "the value transforms' own checks, joined; code-mapped as bad_format (the Transforms editor keeps them valid)",
  "locators.py: err": "pdf_engine.locate_with_anchor's sentences, catalogued as patterns",
  "engine/blocks.py: \"标记的正则表达式无法执行：\" + proc.stderr.decode(\"utf-8\", \"ignore\")[-200:]": "catalogued as the pattern 标记的正则表达式无法执行：{error}",
  "engine/ocr.py: \"OCR 失败：\" + err[-300:]": "the recognizer's own output; code-mapped as ocr_failed",
  "engine/ocr.py: \"文字识别无法加载语言数据：\" + err.strip()[-300:]": "the recognizer's own output; code-mapped as ocr_failed",
  "editor_contract.py: message": "the editor contract's own _bad(where, message), whose sentences are read above",
  "editor_contract.py: \"取值只能是 \" + \" / \".join(choices)": "an enumerated editor option: the editors offer only valid ones",
  "engine/transforms.py: \"；\".join(errs)": "the value transforms' own checks, joined, before an unchecked transform could run: a field's format is checked when it is saved (bad_format) and the Transforms editor writes only valid mapping transforms",
};

// ------------------------------------------------------------------ checks

const CATALOGUED = new Set([...Object.keys(messages), ...Object.keys(serverMessages), ...Object.keys(refusalMessages), ...Object.keys(apiMessages)]);
const PATTERN_SKELETONS = new Set(Object.keys(refusalPatterns).map(keySkeleton));
const API_ONLY_SENTENCES = new Map(API_ONLY.flatMap(([reason, list]) => list.map(s => [s, reason] as const)));
const GENERIC = new Set(["请求参数无效，请检查设置。", "该格式不支持此操作。", "文件或请求超过大小上限。", "操作失败，请检查设置后重试。"]);
const apiOnlyFile = (file: string) => API_ONLY_FILES.some(([re]) => re.test(file));
/** Refusals the service layer words itself; their values travel in `detail` (templates). */
const SERVICE = new Set(["ServiceError", "not_found", "_bad"]);

function unclassified(r: Refusal): string[] {
  if (apiOnlyFile(r.file)) return [];
  if (r.code && Object.hasOwn(SHARED_CODES, r.code)) return [];
  const where = `${r.file}:${r.line} ${r.ctor}(${r.code ?? r.codeExpr ?? "—"})`;
  if (!r.msgs) return Object.hasOwn(FORWARDED, `${r.file}: ${r.msgExpr}`) ? [] : [`${where}: forwarded expression not listed: ${r.msgExpr}`];
  const out: string[] = [];
  for (const m of r.msgs) {
    const shape = m.f ? skeleton(m.text) : m.text;
    if (API_ONLY_SENTENCES.has(shape)) continue;
    if (!m.f && CATALOGUED.has(m.text)) continue;
    if (SERVICE.has(r.ctor) && TEMPLATED[shape] === r.code) continue;
    if (!SERVICE.has(r.ctor) && m.f && PATTERN_SKELETONS.has(shape)) continue;
    if (r.code && CODE_MAPPED_SHAPES.get(shape) === r.code) continue;
    out.push(`${where}: ${m.f ? "f-string " : ""}${shape}`);
  }
  return out;
}

test("the scan finds the server's refusals (guards the scanner itself)", () => {
  const all = refusals();
  assert.ok(all.length > 500, `only ${all.length} refusals found`);
  assert.ok(all.some(r => r.ctor === "ServiceError" && r.code === "queue_full" && r.msgs?.[0].f));
  assert.ok(all.some(r => r.ctor === "not_found" && r.msgs?.[0].text === "项目不存在或已删除"));
  assert.ok(all.some(r => r.ctor === "TemplateError" && r.code === "overlap" && r.msgs?.[0].f));
  assert.ok(all.some(r => r.file === "packages/core/synwevo_core/project_copies.py" && r.code === "copy_already_requested"));
  // the concatenated f-string is read whole
  assert.ok(all.some(r => r.code === "editor_body_too_large" && r.msgs?.[0].text.endsWith("没有丢失。")));
  // both branches of a conditional sentence are read
  assert.ok(all.some(r => r.code === "nothing_to_retry" && r.msgs?.length === 2));
});

test("every refusal a person can meet is catalogued, templated, a pattern, code-mapped or API-only with a reason", () => {
  assert.deepEqual(refusals().flatMap(unclassified), []);
});

test("the decisions name nothing the server no longer says", () => {
  const all = refusals();
  const shapes = new Set(all.flatMap(r => (r.msgs || []).map(m => (m.f ? skeleton(m.text) : m.text))));
  assert.deepEqual([...API_ONLY_SENTENCES.keys()].filter(s => !shapes.has(s)), [], "API_ONLY");
  assert.deepEqual(Object.keys(TEMPLATED).filter(s => !shapes.has(s)), [], "TEMPLATED");
  const exprs = new Set(all.filter(r => !r.msgs).map(r => `${r.file}: ${r.msgExpr}`));
  assert.deepEqual(Object.keys(FORWARDED).filter(s => !exprs.has(s)), [], "FORWARDED");
  const codes = new Set(all.map(r => r.code));
  assert.deepEqual(CODE_MAPPED.map(([code]) => code).filter(c => !codes.has(c)), [], "CODE_MAPPED codes");
  assert.deepEqual([...CODE_MAPPED_SHAPES.keys()].filter(s => !shapes.has(s)), [], "CODE_MAPPED sentences");
  for (const code of new Set(Object.values(TEMPLATED))) assert.ok(Object.hasOwn(REFUSAL_TEMPLATES, code), `no template for ${code}`);
});

test("a code-mapped refusal has a specific sentence of its own, in every language", () => {
  for (const [code] of CODE_MAPPED) {
    const source = ERROR_SOURCE[code];
    assert.ok(source, `${code} is not in ERROR_SOURCE`);
    assert.ok(!GENERIC.has(source), `${code} maps to a generic sentence`);
    assert.ok(Object.hasOwn(refusalMessages, source) || Object.hasOwn(messages, source) || Object.hasOwn(serverMessages, source), `${code}: ${source}`);
  }
  // bad_format is the field's value transforms (the only server use), not a file format
  assert.equal(ERROR_SOURCE.bad_format, "字段的格式转换设置无效，请检查后重试。");
});

test("the refusal catalogues have both languages and every parameter", () => {
  assert.deepEqual(validateMessages(refusalMessages), []);
  assert.deepEqual(validateMessages(refusalPatterns), []);
  // lib/refusals.ts registers them before the other Docs catalogues, and the first wording
  // registered wins: a key those already have would silently change its translation there
  const elsewhere = new Set([...Object.keys(messages), ...Object.keys(serverMessages)]);
  assert.deepEqual([...Object.keys(refusalMessages), ...Object.keys(refusalPatterns)].filter(k => elsewhere.has(k)), []);
  for (const [source, entry] of Object.entries({ ...refusalMessages, ...refusalPatterns })) {
    for (const form of typeof entry.en === "string" ? [entry.en] : Object.values(entry.en)) {
      assert.ok(!/[\u3400-\u9fff]/.test(form), `English for ${source} contains Chinese`);
    }
  }
});

/** A server sentence of the pattern's shape, with made-up values; `{reason}` gets a sentence. */
function sample(key: string): string {
  let i = 0;
  return key.replace(/\{([a-zA-Z]\w*)\}/g, (_, name) => (name === "reason" ? "文件为空" : `v${++i}x`));
}

test("every engine sentence with values reads in every language", () => {
  const shapes = new Set(refusals().filter(r => r.ctor !== "ServiceError" && !apiOnlyFile(r.file))
    .flatMap(r => (r.msgs || []).filter(m => m.f).map(m => skeleton(m.text))));
  try {
    for (const key of Object.keys(refusalPatterns)) {
      const said = sample(key);
      for (const locale of ["en", "zh-Hant", "zh-Hans"] as const) {
        setLocale(locale);
        const text = refusalText(said);
        assert.ok(text, `${key} (${locale}) did not match its own shape`);
        if (locale === "en") assert.ok(!/[\u3400-\u9fff]/.test(text!), `${key}: ${text}`);
        if (locale === "zh-Hans") assert.equal(text, said);
      }
    }
    // a longer sentence is never read as a shorter one with its tail in a value
    setLocale("en");
    assert.equal(refusalText("「客户.pdf」中找不到标记文字（扫描页请先运行 OCR）"), "The marker text cannot be found in “客户.pdf” (run OCR on scanned pages first).");
    // a nested reason that cannot be presented is not shown inside another language...
    assert.equal(refusalText("模板无法解析：File is not a zip file"), null);
    // ...but a zh-Hans reader gets the server's own sentence
    setLocale("zh-Hans");
    assert.equal(refusalText("模板无法解析：File is not a zip file"), "模板无法解析：File is not a zip file");
  } finally { setLocale("zh-Hans"); }
  assert.ok(shapes.size > 60, `only ${shapes.size} engine sentences with values`);
});

test("templated refusals read in every language, from the detail the server sends", () => {
  const cases: [string, number, object, Record<string, string>][] = [
    ["too_large", 413, { filename: "客户.xlsx", limit_mb: 50 }, { en: "The file “客户.xlsx” is over the 50 MB limit.", "zh-Hant": "文件「客户.xlsx」超過 50 MB 上限" }],
    ["editor_body_too_large", 413, { limit_mb: 32, size_mb: 40.5 }, { en: "This document is 40.5 MB, over the 32 MB limit. Usually an inserted image is too large — images are submitted with the document. Use a smaller image, or crop or compress it before inserting it. Your other changes are still in the editor." }],
    ["editor_body_too_large", 413, { limit_mb: 32, size_mb: null }, { "zh-Hans": "这份文档超过 32 MB 上限。通常是插入的图片太大——图片是连同文档一起提交的。请换用更小的图片，或把它裁剪、压缩之后再插入。你的其它修改还在编辑器里，没有丢失。" }],
    ["malware_detected", 400, { filename: "a.docx", earlier: true }, { en: "The content of “a.docx” was already identified as malicious and has been rejected." }],
    ["malware_detected", 400, { filename: "a.docx", signature: "Eicar-Test-Signature" }, { "zh-Hant": "文件「a.docx」被判定為惡意內容（Eicar-Test-Signature），已拒絕" }],
    ["scan_limit", 413, { limit_mb: 25 }, { en: "The file is over the security check's 25 MB limit and cannot be accepted. Split or compress it and upload again." }],
    ["block_missing", 404, { filename: "表单.pdf", blocks: 1 }, { en: "“表单.pdf” has only 1 form block." }],
    ["unsupported_template", 400, { format: "TXT" }, { en: "TXT cannot be used as a template. Template formats: DOCX, XLSX, PDF, PPTX, PNG, JPEG (and DOC/XLS/PPT/ODT/ODS/ODP/RTF, BMP/TIFF/WEBP, which are converted)." }],
    ["template_in_use", 409, { template: "报价单", total: 3, projects: [{ id: "p1", name: "三月报价" }] }, { en: "The template “报价单” is used by 3 projects (such as “三月报价”). Change those projects' template or delete the projects first, then delete the template." }],
    ["bad_rule", 400, { field: "block.anchor", regex_error: "missing ), unterminated subpattern at position 0" }, { en: "The marker's regular expression is not valid: missing ), unterminated subpattern at position 0" }],
    ["too_many_pages", 400, { limit: 50, requested: 80, pages_given: false }, { en: "This file has 80 pages to recognize, over the limit of 50 pages at a time. Split it into smaller files and upload them separately." }],
    ["too_many_pages", 400, { limit: 50, requested: 80, pages_given: true }, { "zh-Hant": "一次最多辨識 50 頁，本次 80 頁；請分批指定頁碼" }],
    ["not_found", 404, { search: "张三" }, { en: "No sample contains “张三”." }],
    ["queue_full", 429, { active_jobs: 3, limit: 3, retry_after: 30 }, { en: "This workspace already has 3 jobs queued or running (limit 3). Wait for some of them to finish before submitting." }],
    ["format_unavailable", 400, { format: "PDF", reason: "此部署未启用排版引擎，无法导出 PDF" }, { "zh-Hant": "無法匯出 PDF：" + translate("zh-Hant", "此部署未启用排版引擎，无法导出 PDF") }],
    ["format_unavailable", 400, { format: "XLSX", reason: "不支持导出 XLSX" }, { en: "XLSX cannot be exported: XLSX export is not supported." }],
    ["sources_not_ready", 409, { files: ["客户100.xlsx"], parsing_total: 1, retry_after: 2 }, { en: "1 source file is still being parsed (“客户100.xlsx”). Wait until parsing finishes, then submit." }],
    ["too_many", 400, { records: 25000, limit: 20000 }, { en: "25,000 samples is more than one job can take (20,000)." }],
    ["has_errors", 409, { invalid_total: 6, invalid: [] }, { en: "6 samples have errors, so the job was stopped (“stop if any errors”). Fix them or choose “Skip samples with errors”." }],
    ["bad_naming", 400, { field: "output.naming", limit: 300 }, { en: "The file naming pattern can have at most 300 characters." }],
    ["invalid_field", 400, { field: "body", limit: 4000 }, { "zh-Hant": "評論最多 4,000 個字元" }],
    ["invalid_field", 400, { field: "mentions", limit: 20 }, { en: "A comment can mention at most 20 members." }],
    ["bad_editor", 400, { field: "editor.page.margins.left", min: 0, max: 100 }, { en: "Page margins must be between 0 and 100 mm." }],
    ["bad_editor", 400, { field: "editor.base_size", min: 4, max: 144 }, { "zh-Hant": "字號需要在 4–144 之間" }],
  ];
  try {
    for (const [code, status, detail, expected] of cases) {
      for (const [locale, text] of Object.entries(expected)) {
        setLocale(locale as "en");
        const error = new ApiError(status, code, "服务器原句", detail);
        assert.equal(errorText(error), text, `${code} ${locale} (the workbench)`);
        assert.equal(error.message, text, `${code} ${locale} (the shared presentation)`);
      }
    }
    // a template without its values falls back to the server's sentence, catalogued or not
    setLocale("en");
    assert.equal(errorText(new ApiError(400, "invalid_field", "评论不能为空", { field: "body" })), "A comment cannot be empty.");
    assert.equal(errorText(new ApiError(409, "has_errors", "这个任务因样本错误没有开始生成（有错误则不开始）；请修正数据后重新提交，或以「跳过错误样本」提交新任务")),
      "This job did not start because of sample errors (“stop if any errors”). Fix the data and submit again, or submit a new job with “Skip samples with errors”.");
  } finally { setLocale("zh-Hans"); }
});

test("refusals that used to reach a reader as a code or a generic sentence now say what happened", () => {
  try {
    setLocale("en");
    // an engine refusal with a code no map knows used to read 「请求未完成（overlap）」 in every language
    assert.equal(errorText(new ApiError(400, "overlap", "与已有字段「客户名称」重叠")), "Overlaps the existing field “客户名称”.");
    // a catalogued sentence wins over its code's generic label (bad_value used to be 「请求参数无效」)
    assert.equal(errorText(new ApiError(400, "bad_value", "该列不存在")), "This column does not exist.");
    // the template upload's parser failure keeps what the parser said when it is known
    assert.equal(errorText(new ApiError(400, "bad_template", "模板无法解析：压缩包条目数量超过上限")), "The template cannot be read: The archive has more entries than allowed.");
    assert.equal(errorText(new ApiError(400, "bad_template", "模板无法解析：Bad magic number")), translate("en", ERROR_SOURCE.bad_template));
    // the gate's refusals, and a request id that stays with them
    const frozen = new ApiError(403, "workspace_unavailable", "该工作空间已被冻结，暂时不能使用。请联系工作空间所有者或平台管理员。", { workspace_status: "frozen" });
    frozen.requestId = "fixture-request";
    assert.equal(errorText(frozen), "This workspace is frozen and cannot be used for now. Contact the workspace owner or a platform administrator. · Request ID: fixture-request");
    assert.equal(errorText(new ApiError(403, "app_access_revoked", "你在该工作空间使用「Synwevo Docs」的资格已被撤销，请联系工作空间管理员")),
      "Your access to “Synwevo Docs” in this workspace has been revoked. Contact the workspace administrator.");
    // a diagnostic string (a source's parse error, a skipped desensitization) with a value in it
    assert.equal(diagnosticText("文档包含无法脱敏的嵌入对象（oleObject1.bin）。请在原软件中删除该对象，或把它转换为图片后再脱敏"),
      "The document contains an embedded object that cannot be anonymized (oleObject1.bin). Delete it in the original application, or convert it to an image, then anonymize.");
    assert.equal(diagnosticText("工作表「明细」的行数超过上限 1,048,576（可通过 SYNWEVO_DOCS_MAX_ROWS 调整）"),
      "The worksheet “明细” has more rows than the limit of 1,048,576 (adjustable with SYNWEVO_DOCS_MAX_ROWS).");
    // a code label still carries the structured field beside it; a sentence does not need it
    assert.equal(issueText({ code: "missing_column", message: "无法找到列", detail: { field: "客户名称" } }), "Missing column (客户名称)");
    assert.equal(issueText({ code: "invalid_field", message: "评论不能为空", detail: { field: "body" } }), "A comment cannot be empty.");
    setLocale("zh-Hans");
    // zh-Hans readers see the server's own sentence, never a code
    assert.equal(errorText(new ApiError(400, "overlap", "与已有字段「客户名称」重叠")), "与已有字段「客户名称」重叠");
    // an unknown refusal still says it could not be completed, with its code for whoever debugs it
    assert.equal(errorText(new ApiError(400, "future_code", "未来的服务器原句")), "请求未完成（future_code）。");
  } finally { setLocale("zh-Hans"); }
});

test("the gate's refusals and the template-engine constants are presentable", () => {
  const http = readFileSync(join(ROOT, "packages/core/synwevo_core/http.py"), "utf8");
  const apps = readFileSync(join(ROOT, "packages/core/synwevo_core/apps.py"), "utf8");
  const gate = [
    "你当前没有可用的工作空间，不能进行这个操作。请先选择或加入一个工作空间。", "该工作空间已被冻结，暂时不能使用。请联系工作空间所有者或平台管理员。",
    "该工作空间尚未完成认领，暂时不能使用。", "该工作空间已删除，不能再使用。",
  ];
  const access = ["该应用不存在", "该应用已停用", "你还不属于任何工作空间，请联系管理员邀请你加入", "你不是该工作空间的成员", "账户不可用",
    "该工作空间不可用", "该组织当前不可用"];
  const accessShapes = ["你在该工作空间使用「{}」的资格已被撤销，请联系工作空间管理员", "你所在的工作空间尚未启用「{}」，或你没有使用它的资格", "该工作空间尚未启用「{}」"];
  for (const s of gate) assert.ok(http.includes(`"${s}"`), `http.py no longer says ${s}`);
  for (const s of access) assert.ok(apps.includes(`"${s}"`), `apps.py no longer says ${s}`);
  const said = new Set([...apps.matchAll(/f"((?:[^"\\]|\\.)*)"/g)].map(m => skeleton(m[1])));
  for (const s of accessShapes) assert.ok(said.has(s), `apps.py no longer says ${s}`);
  const pe = readFileSync(join(DOCS, "engine/pdf_engine.py"), "utf8");
  const constants = ["TEMPLATES_UNAVAILABLE", "IMAGE_TEMPLATES_UNAVAILABLE"].map(name => {
    const m = new RegExp(`^${name}\\s*=\\s*\\(?\\s*((?:"[^"]*"\\s*)+)\\)?`, "m").exec(pe);
    assert.ok(m, `pdf_engine.${name} not found`);
    return [...m![1].matchAll(/"([^"]*)"/g)].map(x => x[1]).join("");
  });
  try {
    setLocale("en");
    for (const s of [...gate, ...access, ...constants]) {
      const text = errorText(new ApiError(403, "fixture", s));
      assert.ok(!/[\u3400-\u9fff]/.test(text) && !/fixture/.test(text), `${s} → ${text}`);
    }
  } finally { setLocale("zh-Hans"); }
});

test("the comment drawer, the co-editing notice and copy requests present refusals like every other surface", () => {
  const src = (p: string) => readFileSync(join(import.meta.dirname, "..", "src", p), "utf8");
  // it used to show ApiError.message, which skips the Docs code map and the permission sentences
  assert.doesNotMatch(src("components/CommentsDrawer.tsx"), /e instanceof ApiError \? e\.message/);
  assert.match(src("pages/DocEditor.tsx"), /issueText\(\{ code: reason \}\)/);
  assert.equal(API_ERROR_SOURCE.unauthorized, "请重新登录后再试。");
  // a copy request that did not run stores a reason code; the dialog used to show the code itself
  assert.match(src("components/CopyRequestDialog.tsx"), /copyErrorText\(r\.error\)/);
  const copies = readFileSync(join(ROOT, "packages/core/synwevo_core/project_copies.py"), "utf8");
  for (const code of ["approval_expired", "source_authority_lost", "decider_authority_lost", "organization_unavailable"]) assert.ok(copies.includes(`"${code}"`), code);
  try {
    setLocale("en");
    assert.equal(copyErrorText("approval_expired"), "The approval has expired. Request the copy again.");
    assert.equal(copyErrorText("target:target_write_required"), "You can no longer copy the project into the target workspace. Check that you are still an editor there.");
    assert.equal(copyErrorText("transfer:transfer_source_closed"), "The current data transfer policy or export rights do not allow this copy. Check them before requesting it again.");
    assert.equal(copyErrorText("KeyError"), "Copying failed and nothing was created. Request the copy again later.");
    setLocale("zh-Hant");
    assert.equal(copyErrorText("source:not_found"), "來源項目已不可用。");
    assert.equal(copyErrorText("transfer:transfer_audience_denied"), "目前資料流政策或匯出權限不允許複製到目標空間，請核對後重新申請。");
  } finally { setLocale("zh-Hans"); }
});

// ------------------------------------------------------------------ per-sample diagnostics (Q13)
/* A sample's problems — the `issues` of its plan and of its fill, the warnings of the sample
 * rules — reach the preview pane, the generate dialog, the job's items and the validation panel
 * through `issueText` / `diagnosticText`, which present them only when the catalogue has the
 * sentence. An uncatalogued one used to become its code's label for every reader, zh-Hans
 * included: 「组内取值冲突」 instead of which field, which values and what to do.
 *
 * So every sentence the engine, the job runner and the project views put into a sample's issue
 * (a dict literal's "message"), a warning list, or an exception that becomes one (ResolveError,
 * ColumnError, TransformError, ConflictError and LayoutUnavailable, scanned with the refusals
 * above) is catalogued — the whole sentence as the browser receives it, wrappers included — or
 * listed below with the reason no person reads it. */

/** Where a sample's problems are worded. */
const DIAG_FILE = /^(engine\/\w+\.py|jobs\.py|views\.py)$/;

type Diagnostic = { file: string; line: number; via: string; msgs: Message[] | null; msgExpr: string | null };

/** The expression after `"message":` in a dict literal, up to its own comma or closing brace. */
function dictValue(src: string, start: number): string {
  let depth = 0;
  for (let i = start; i < src.length; i++) {
    const c = src[i];
    if (c === "#") { while (i < src.length && src[i] !== "\n") i++; continue; }
    if (c === '"' || c === "'") { i = skipString(src, i); continue; }
    if ("([{".includes(c)) depth++;
    else if (")]}".includes(c)) { if (depth-- === 0) return src.slice(start, i).trim(); }
    else if (c === "," && depth === 0) return src.slice(start, i).trim();
  }
  return src.slice(start).trim();
}

function diagnostics(): Diagnostic[] {
  const out: Diagnostic[] = [];
  for (const path of pyFiles(DOCS)) {
    const file = relative(DOCS, path);
    if (!DIAG_FILE.test(file)) continue;
    const src = readFileSync(path, "utf8");
    const lines = src.split("\n");
    const add = (at: number, via: string, arg: string | undefined) => {
      const line = src.slice(0, at).split("\n").length;
      if (/^\s*(class|def) /.test(lines[line - 1])) return;
      const msgs = arg ? sentences(arg) : null;
      out.push({ file, line, via, msgs, msgExpr: msgs ? null : (arg || "").replace(/\s+/g, " ") });
    };
    for (const m of src.matchAll(/["']message["']\s*:\s*/g)) add(m.index!, "message", dictValue(src, m.index! + m[0].length));
    for (const m of src.matchAll(/(?:(?<![\w.])(?:warns|warnings)|\[["']warnings["']\])\.append\(/g)) add(m.index!, "warning", callArgs(src, m.index! + m[0].length - 1)[0]);
    // the PPTX fill's `err(slot, code, message)` helper
    if (file === "engine/pptx_engine.py") for (const m of src.matchAll(/(?<![\w.])err\(/g)) add(m.index!, "err()", callArgs(src, m.index! + m[0].length - 1)[2]);
    // why the page check did not run: `_layout_unavailable(mode, reason)` puts it into a wrapper,
    // and the preview's real pagination (views.preview_layout) answers with a "reason"
    if (file === "engine/generate.py") for (const m of src.matchAll(/(?<![\w.])_layout_unavailable\(/g)) add(m.index!, "layout reason", callArgs(src, m.index! + m[0].length - 1)[1]);
    if (file === "views.py") for (const m of src.matchAll(/["']reason["']\s*:\s*/g)) add(m.index!, "layout reason", dictValue(src, m.index! + m[0].length));
  }
  return out;
}

/** Messages that are expressions, keyed by file and expression: what flows through. `keys` are
 *  the catalogue entries it produces when the sentences are not written where it is scanned. */
const DIAG_FORWARDED: Record<string, { why: string; keys?: string[] }> = {
  "engine/plan.py: str(e)": { why: "a ResolveError from the sample's context (records.record_context), read with the refusals above" },
  "engine/plan.py: _needs_split(nested, block)": { why: "plan._needs_split's sentence", keys: ["明细表「{name}」在重复内容块「{block}」里：请在这个内容块的映射中设置分段字段，每一段才会只写入自己的明细行"] },
  "engine/plan.py: _needs_split(sl, block)": { why: "plan._needs_split's sentence", keys: ["明细表「{name}」在重复内容块「{block}」里：请在这个内容块的映射中设置分段字段，每一段才会只写入自己的明细行"] },
  "engine/records.py: str(e)": { why: "a ResolveError of the sample rules (form blocks, the table's sheet, a key column), read with the refusals above" },
  "engine/templates.py: str(e)": { why: "an XlsxError, read with the refusals above" },
  "engine/templates.py: w": { why: "the XLSX writer's warnings (engine/xlsx_doc.py), read here as warnings" },
  "engine/generate.py: str(e)": { why: "LayoutUnavailable, read with the refusals above" },
  "views.py: str(e)": { why: "LayoutUnavailable (the preview's real pagination), read with the refusals above" },
  "engine/pptx_engine.py: str(e)": { why: "a PptxError, read with the refusals above" },
  "engine/pptx_engine.py: message": { why: "the PPTX fill's err(slot, code, message) helper: its sentences are read at its calls" },
  "engine/pdf_engine.py: pl[\"message\"]": { why: "pdf_repeat.plan_pages' sentences, read here as its dicts' \"message\"" },
  "jobs.py: i.get(\"message\")": { why: "a plan issue passed on to the job's summary, read here" },
  "views.py: errs[0][\"message\"]": { why: "a plan issue passed on to the validation panel, read here" },
  "jobs.py: sum(1 for i in issues if i.get(\"level\") == \"warning\")": { why: "a count of the sample's warnings, not a sentence" },
  "jobs.py: halt_message(why)": { why: "jobs.HALT_MESSAGES, each checked in \"why a job stopped\" below" },
  "jobs.py: ver.get(\"error\") or \"输出校验失败\"": { why: "verify_output's reopening failure 「输出文件无法重新打开：{e}」 (the library's words stay as evidence), or the fixed sentence",
    keys: ["输出文件无法重新打开：{error}", "输出校验失败"] },
  "jobs.py: \"输出文件中找不到已填写的值：\" + \"、\".join(f\"{m['slot']}={m['value']}\" for m in ver[\"missing\"][:3])": { why: "the values the reopened output lacks (field = value, the person's data)", keys: ["输出文件中找不到已填写的值：{values}"] },
  "jobs.py: f\"版式：模板 {lay.get('template_pages')} 页，本次排版 {lay.get('pages')} 页；\" f\"实际缩放 {lay.get('effective_factor', 1.0):.0%}\" + (f\"，最小 {lay['min_pt']:.1f} 磅\" if lay.get(\"min_pt\") else \"\") + (f\"；已到最小字号的字段：{'、'.join(lay['clamped'])}\" if lay.get(\"clamped\") else \"\") + f\"；共排版 {lay.get('renders')} 次\"": {
    why: "a failed sample's layout report, with two optional clauses: its four forms", keys: [
      "版式：模板 {templatePages} 页，本次排版 {pages} 页；实际缩放 {percent}；共排版 {renders} 次",
      "版式：模板 {templatePages} 页，本次排版 {pages} 页；实际缩放 {percent}，最小 {pt} 磅；共排版 {renders} 次",
      "版式：模板 {templatePages} 页，本次排版 {pages} 页；实际缩放 {percent}；已到最小字号的字段：{fields}；共排版 {renders} 次",
      "版式：模板 {templatePages} 页，本次排版 {pages} 页；实际缩放 {percent}，最小 {pt} 磅；已到最小字号的字段：{fields}；共排版 {renders} 次"] },
};

/** Sentences assembled from fixed fragments the engine chooses (a label for a kind of field, an
 *  optional clause, an output format): a value of that kind would be Chinese inside another
 *  language, so each form it can take is catalogued. Keyed by the f-string's skeleton. */
const EXPANDED: Record<string, string[]> = {
  // plan.static_check: {"single": "单值", "region": "固定区域", "repeat": "重复明细"}, joined in sorted key order
  "映射 #{} 的目标同时绑定了{}字段；请取消这些字段之间的共享绑定": ["固定区域、重复明细", "固定区域、单值", "重复明细、单值", "固定区域、重复明细、单值"]
    .map(kinds => `映射 #{no} 的目标同时绑定了${kinds}字段；请取消这些字段之间的共享绑定`),
  "映射 #{} 是{}映射，但目标是{}字段": [["单值", "固定区域"], ["单值", "重复明细"], ["固定区域", "单值"], ["固定区域", "重复明细"], ["重复明细", "单值"], ["重复明细", "固定区域"]]
    .map(([mapping, field]) => `映射 #{no} 是${mapping}映射，但目标是${field}字段`),
  // generate.fill_checked: 「，最小 … 磅」 and 「；字段 … 已到各自的最小字号」 are each optional
  "为保持 {} 页，允许缩小的字段字号已缩小到原来的 {}{}{}": ["", "，最小 {pt} 磅", "；字段 {fields} 已到各自的最小字号", "，最小 {pt} 磅；字段 {fields} 已到各自的最小字号"]
    .map(tail => `为保持 {count} 页，允许缩小的字段字号已缩小到原来的 {percent}${tail}`),
  // layout.page_issue: one of two hints
  "输出有 {} 页，模板为 {} 页：新内容更长导致分页增加{}": ["；已尝试缩小允许缩小的字段仍放不下", "；可在字段设置中勾选「超长时允许缩小字号」，或精简内容"]
    .map(hint => `输出有 {pages} 页，模板为 {templatePages} 页：新内容更长导致分页增加${hint}`),
  // jobs: 「{f.upper()}：{e}」 / 「{f.upper()}：{problem}」 for the formats a job can produce
  "{}：{}": ["DOCX", "XLSX", "PPTX", "PDF", "CSV", "PNG", "JPG"].map(format => `${format}：{reason}`),
};

/** Per-sample sentences that are not worded for a person, with the reason. Skeletons. */
const NOT_FOR_PEOPLE: [string, string[]][] = [
  ["jobs.render_name's warnings: every caller discards them (`nm, _ = render_name(…)`, `render_name(…)[0]`)", [
    "文件名字段「{}」为空", "文件名中的「{{}}」不是可用字段",
  ]],
  ["an unexpected exception while one sample was filled: its class and words stay on the item as evidence for whoever debugs it (the server log has the trace), and the reader gets the code's sentence (ERROR_SOURCE.exception)", [
    "{}: {}",
  ]],
];
const NOT_FOR_PEOPLE_SENTENCES = new Map(NOT_FOR_PEOPLE.flatMap(([reason, list]) => list.map(s => [s, reason] as const)));

function unclassifiedDiagnostic(d: Diagnostic): string[] {
  const where = `${d.file}:${d.line} ${d.via}`;
  if (!d.msgs) return Object.hasOwn(DIAG_FORWARDED, `${d.file}: ${d.msgExpr}`) ? [] : [`${where}: forwarded expression not listed: ${d.msgExpr}`];
  const out: string[] = [];
  for (const m of d.msgs) {
    const shape = m.f ? skeleton(m.text) : m.text;
    if (NOT_FOR_PEOPLE_SENTENCES.has(shape) || Object.hasOwn(EXPANDED, shape)) continue;
    if (!shape.includes("{}") ? CATALOGUED.has(shape) : PATTERN_SKELETONS.has(shape)) continue;
    out.push(`${where}: ${m.f ? "f-string " : ""}${shape}`);
  }
  return out;
}

test("the scan finds the per-sample diagnostics (guards the scanner itself)", () => {
  const all = diagnostics();
  assert.ok(all.length > 100, `only ${all.length} diagnostics found`);
  const shapes = new Set(all.flatMap(d => (d.msgs || []).map(m => (m.f ? skeleton(m.text) : m.text))));
  assert.ok(shapes.has("映射 #{}：{}：{}。请在映射中明确选择取值规则（取第一条、拼接、求和等）"));   // group_conflict
  assert.ok(shapes.has("必填字段「{}」的值为空"));                                              // required_empty
  assert.ok(shapes.has("「{}」不是数字，未应用数字格式"));                                       // a transform warning
  assert.ok(shapes.has("{} 个分组编号在多个文件中出现（如 {}），默认按文件分别成组、不会合并；如需跨文件合并请开启「跨文件按编号分组」"));  // result["warnings"]
  assert.ok(shapes.has("字段「{}」与重复明细「{}」位于同一表格行，明细行按记录复制后无法确定写入位置；请把该字段移到明细行以外"));  // pptx err()
  // a conditional message gives both sentences
  assert.ok(shapes.has("{}，其余样本不再生成") && shapes.has("项目已被删除，其余样本不再生成"));
  // the exceptions that become a sample's issue, a module-qualified one (`transforms.ConflictError(`) included
  const raised = refusals();
  assert.ok(raised.some(r => r.ctor === "ConflictError" && r.file === "engine/plan.py"));
  assert.ok(raised.some(r => r.ctor === "ResolveError" && r.file === "engine/resolve.py" && r.code === "needs_ocr"));
  assert.ok(raised.some(r => r.ctor === "ColumnError" && r.code === "ambiguous_column"));
  assert.ok(raised.some(r => r.ctor === "LayoutUnavailable") && raised.some(r => r.ctor === "TransformError"));
});

test("every per-sample diagnostic is catalogued as the browser receives it, or listed with the reason no person reads it", () => {
  assert.deepEqual(diagnostics().flatMap(unclassifiedDiagnostic), []);
});

test("the per-sample decisions name nothing the server no longer says, and every form they name is catalogued", () => {
  const all = diagnostics();
  const shapes = new Set(all.flatMap(d => (d.msgs || []).map(m => (m.f ? skeleton(m.text) : m.text))));
  const exprs = new Set(all.filter(d => !d.msgs).map(d => `${d.file}: ${d.msgExpr}`));
  assert.deepEqual(Object.keys(DIAG_FORWARDED).filter(k => !exprs.has(k)), [], "DIAG_FORWARDED");
  assert.deepEqual(Object.keys(EXPANDED).filter(k => !shapes.has(k)), [], "EXPANDED");
  assert.deepEqual([...NOT_FOR_PEOPLE_SENTENCES.keys()].filter(k => !shapes.has(k)), [], "NOT_FOR_PEOPLE");
  const forms = [...Object.values(DIAG_FORWARDED).flatMap(d => d.keys || []), ...Object.values(EXPANDED).flat()];
  assert.deepEqual(forms.filter(k => !CATALOGUED.has(k) && !Object.hasOwn(refusalPatterns, k)), [], "forms that are not catalogued");
  // each expanded form really is one of the sentence's forms
  for (const [shape, list] of Object.entries(EXPANDED)) {
    const re = new RegExp(`^${shape.split("{}").map(s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s\\S]*?")}$`);
    for (const form of list) assert.match(sample(form), re, `${form} is not a form of ${shape}`);
  }
  // what the reader of an exception gets instead of its words
  assert.ok(ERROR_SOURCE.exception && ERROR_SOURCE.export_failed);
});

/** The wrappers that carry an engine sentence to the browser, as catalogued. */
const MAP = "映射 #{no}：{reason}";
const CONFLICT = "映射 #{no}：{reason}：{values}。请在映射中明确选择取值规则（取第一条、拼接、求和等）";
const FIELD = "字段「{name}」：{reason}";
const FORMAT = "字段「{name}」的格式设置无法应用：{reason}";
const IN_FILE = "「{file}」：{reason}";
const UNCHECKED = "{reason}，本份未做分页检查";
const NOT_FITTED = "{reason}，无法保证页数不增加（layout=fit），因此不产出这一份；可请管理员启用排版引擎，或改用 layout=warn / off";
const HALTED = "{reason}，其余样本不再生成";

/** How an engine sentence reaches the browser: each chain lists the wrappers around it,
 *  innermost first. */
function chains(via: string, file: string): string[][] {
  switch (via) {
    case "ResolveError": return [[], [MAP]];                  // the sample's context as it is (plan, records), a mapping's value behind 「映射 #n：」
    case "ColumnError": return [[IN_FILE], [IN_FILE, MAP]];   // resolve.column_index names the file
    case "TransformError": return [[MAP], [FORMAT]];          // a mapping's value, a field's format
    case "ConflictError": return [[CONFLICT]];
    case "LayoutUnavailable": return [[], [UNCHECKED], [NOT_FITTED]];   // as it is: views.preview_layout's reason
    case "layout reason": return file === "views.py" ? [[]] : [[UNCHECKED], [NOT_FITTED]];
    case "PptxError": return [[], [FIELD]];
    case "warning": return file === "engine/transforms.py" ? [[MAP], [FIELD]] : file === "engine/plan.py" ? [[MAP]] : [[]];
    default: return [];
  }
}
const WRAPPED = new Set(["ResolveError", "ColumnError", "TransformError", "ConflictError", "LayoutUnavailable", "PptxError"]);

/** Every catalogued engine sentence that becomes part of a sample's issue, with its catalogue key. */
function innerSentences(): { via: string; file: string; key: string }[] {
  const keysOf = (m: Message): string[] => {
    const shape = m.f ? skeleton(m.text) : m.text;
    if (!shape.includes("{}")) return CATALOGUED.has(shape) ? [shape] : [];
    return Object.keys(refusalPatterns).filter(k => keySkeleton(k) === shape);
  };
  const out: { via: string; file: string; key: string }[] = [];
  for (const r of refusals()) {
    if (!DIAG_FILE.test(r.file) || !WRAPPED.has(r.ctor)) continue;
    for (const m of r.msgs || []) for (const key of keysOf(m)) out.push({ via: r.ctor, file: r.file, key });
  }
  for (const d of diagnostics()) {
    if (d.via !== "warning" && d.via !== "layout reason") continue;
    for (const m of d.msgs || []) for (const key of keysOf(m)) out.push({ via: d.via, file: d.file, key });
  }
  return out;
}

test("every per-sample sentence reads in every language as the browser receives it, wrappers included", () => {
  // the wrappers are the server's own
  const said = new Set([...refusals().flatMap(r => r.msgs || []), ...diagnostics().flatMap(d => d.msgs || [])].map(m => (m.f ? skeleton(m.text) : m.text)));
  for (const w of [MAP, CONFLICT, FIELD, FORMAT, IN_FILE, UNCHECKED, NOT_FITTED, HALTED]) {
    assert.ok(Object.hasOwn(refusalPatterns, w), `${w} is not catalogued`);
    assert.ok(said.has(keySkeleton(w)), `the server no longer says ${w}`);
  }
  const items = innerSentences();
  assert.ok(items.length > 80, `only ${items.length} engine sentences inside a sample's issues`);
  const wrap = (inner: string, wrapper: string) => { let i = 0; return wrapper.replace(/\{([a-zA-Z]\w*)\}/g, (_, n: string) => (n === "reason" ? inner : `w${++i}x`)); };
  const failures: string[] = [];
  try {
    for (const { via, file, key } of items) {
      for (const chain of chains(via, file)) {
        const message = chain.reduce(wrap, sample(key));
        for (const locale of ["en", "zh-Hant", "zh-Hans"] as const) {
          setLocale(locale);
          const text = refusalText(message);
          if (text === null) failures.push(`${locale} (${via}): ${message} is not presented`);
          else if (locale === "en" && /[㐀-鿿]/.test(text)) failures.push(`en (${via}): ${message} → ${text}`);
          else if (locale === "zh-Hans" && text !== message) failures.push(`zh-Hans (${via}): ${message} → ${text}`);
        }
      }
    }
  } finally { setLocale("zh-Hans"); }
  assert.deepEqual(failures, []);
});

test("why a job stopped reads in every language, on the job's items too", () => {
  const src = readFileSync(join(DOCS, "jobs.py"), "utf8");
  const block = /^HALT_MESSAGES = \{([\s\S]*?)^\}/m.exec(src);
  assert.ok(block, "jobs.HALT_MESSAGES not found");
  const halts = [...block![1].matchAll(/:\s*"([^"]+)"/g)].map(m => m[1]);
  assert.ok(halts.length >= 15, `only ${halts.length} halt messages`);
  try {
    for (const locale of ["en", "zh-Hant"] as const) {
      setLocale(locale);
      for (const h of halts) for (const message of [h, HALTED.replace("{reason}", h)]) {
        const text = refusalText(message);
        assert.ok(text !== null && !(locale === "en" && /[㐀-鿿]/.test(text)), `${locale}: ${message} → ${text}`);
      }
    }
  } finally { setLocale("zh-Hans"); }
});

test("a sample's problems read as whole sentences in every language, the person's data verbatim (Q13)", () => {
  // Each of these used to reach every reader, zh-Hans included, as its code's label only
  // (「组内取值冲突」, 「缺少列」, 「必填为空」…): which field, which values and what to do were lost.
  type Case = [issue: { level: string; code: string; message: string }, data: string[], expected: { en: string; "zh-Hant": string }];
  const cases: Case[] = [
    [{ level: "error", code: "group_conflict", message: "映射 #2：同一组内该字段有多个不同值：华东 / 华南。请在映射中明确选择取值规则（取第一条、拼接、求和等）" }, ["华东", "华南"], {
      en: "Mapping #2: This field has different values within one group: 华东 / 华南. Choose in the mapping which value to use (the first, joined, summed, …).",
      "zh-Hant": "映射 #2：同一組內該欄位有多個不同值：华东 / 华南。請在映射中明確選擇取值規則（取第一筆、串接、加總等）" }],
    [{ level: "error", code: "group_conflict", message: "映射 #7：「A-001」这一段里「数量」有多个不同值：1 / 2。请在映射中明确选择取值规则（取第一条、拼接、求和等）" }, ["A-001", "数量"], {
      en: "Mapping #7: “数量” has more than one value in “A-001”: 1 / 2. Choose in the mapping which value to use (the first, joined, summed, …).",
      "zh-Hant": "映射 #7：「A-001」這一段裡「数量」有多個不同值：1 / 2。請在映射中明確選擇取值規則（取第一筆、串接、加總等）" }],
    // a column the source lacks: the plan's 「映射 #n：」 around resolve's 「「file」：」 around the column's own sentence
    [{ level: "error", code: "missing_column", message: "映射 #3：「客户.xlsx」：找不到列「客户名称」" }, ["客户.xlsx", "客户名称"], {
      en: "Mapping #3: “客户.xlsx”: The column “客户名称” cannot be found.", "zh-Hant": "映射 #3：「客户.xlsx」：找不到列「客户名称」" }],
    [{ level: "error", code: "missing_column", message: "映射 #8：明细表中找不到列：单价、金额" }, ["单价、金额"], {
      en: "Mapping #8: Columns not found in the detail table: 单价、金额", "zh-Hant": "映射 #8：明細表中找不到列：单价、金额" }],
    [{ level: "error", code: "required_empty", message: "必填字段「合同编号」的值为空" }, ["合同编号"], {
      en: "The required field “合同编号” is empty.", "zh-Hant": "必填欄位「合同编号」的值為空" }],
    [{ level: "error", code: "no_detail", message: "重复明细「订单明细」没有数据行" }, ["订单明细"], {
      en: "The repeating detail “订单明细” has no data rows.", "zh-Hant": "重複明細「订单明细」沒有資料行" }],
    [{ level: "error", code: "no_detail", message: "明细表「订单明细」在「A-001」这一段没有明细行" }, ["订单明细", "A-001"], {
      en: "The detail table “订单明细” has no detail rows for “A-001”.", "zh-Hant": "明細表「订单明细」在「A-001」這一段沒有明細行" }],
    // a TransformError inside a mapping's value
    [{ level: "error", code: "value_error", message: "映射 #4：「十二」不是数字，无法求和" }, ["十二"], {
      en: "Mapping #4: “十二” is not a number, so it cannot be summed.", "zh-Hant": "映射 #4：「十二」不是數字，無法加總" }],
    // transform warnings: a mapping's, and a field's format
    [{ level: "warning", code: "transform", message: "映射 #5：「约 3 件」不是数字，未应用数字格式" }, ["约 3 件"], {
      en: "Mapping #5: “约 3 件” is not a number, so the number format was not applied.", "zh-Hant": "映射 #5：「约 3 件」不是數字，未套用數字格式" }],
    [{ level: "warning", code: "transform", message: "字段「签约日期」：「下周一」不是可识别的日期，未改变格式" }, ["签约日期", "下周一"], {
      en: "Field “签约日期”: “下周一” is not a date that can be recognized, so its format was not changed.", "zh-Hant": "欄位「签约日期」：「下周一」不是可辨識的日期，未改變格式" }],
  ];
  try {
    for (const [issue, data, expected] of cases) {
      setLocale("zh-Hans");
      assert.equal(issueText(issue), issue.message, `zh-Hans readers see the server's whole sentence: ${issue.message}`);
      for (const [locale, text] of Object.entries(expected)) {
        setLocale(locale as "en");
        assert.equal(issueText(issue), text, `${issue.code} (${locale})`);
      }
      assert.doesNotMatch(data.reduce((rest, d) => rest.split(d).join(""), expected.en), /[㐀-鿿]/, `${expected.en}: Chinese besides the person's data`);
    }
    // an engine sentence the catalogue lacks (here Python's own words) inside a wrapper: a zh-Hans
    // reader still gets the whole sentence, others the code's sentence rather than Chinese prose
    const odd = { level: "error", code: "value_error", message: "映射 #6：[<class 'decimal.InvalidOperation'>]" };
    setLocale("zh-Hans");
    assert.equal(issueText(odd), odd.message);
    setLocale("en");
    assert.equal(issueText(odd), translate("en", ERROR_SOURCE.value_error));
    // an unexpected exception keeps its words on the item, but the reader is told what happened
    assert.equal(issueText({ level: "error", code: "exception", message: "KeyError: 'rows'" }), translate("en", ERROR_SOURCE.exception));
  } finally { setLocale("zh-Hans"); }
});
