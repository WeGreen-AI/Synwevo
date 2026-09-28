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
 *  Adding a server refusal therefore means deciding which of these it is. */
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
 *  ValueError handler's generic `invalid`. `only` limits a common name to the files where it is
 *  a refusal rather than an internal error or a per-sample diagnostic. */
const CONSTRUCTORS: Record<string, { kind: Kind; code?: string | null; only?: RegExp }> = {
  ServiceError: { kind: "service" },
  not_found: { kind: "not_found" },                                  // synwevo_core.errors.not_found(what)
  _bad: { kind: "editor", only: /^editor_contract\.py$/ },            // bad_editor
  TemplateError: { kind: "coded" }, BlockError: { kind: "coded" }, DocxError: { kind: "coded" },
  PptxError: { kind: "coded" }, XlsxError: { kind: "coded" }, ImageTemplateError: { kind: "coded" },
  PdfError: { kind: "coded" }, RegressionError: { kind: "coded" }, DesensitizeError: { kind: "coded" },
  // form-block splitting and locator building answer requests; elsewhere a ResolveError is a
  // per-sample diagnostic, presented by its code's label beside the located value
  ResolveError: { kind: "coded", only: /^(engine\/blocks\.py|locators\.py)$/ },
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
  // a bare name or a module-qualified one (`T.TemplateError(`), never a method (`self.x(`)
  const re = new RegExp(`(?<![\\w.])(?:[A-Z]\\w*\\.)?(${Object.keys(CONSTRUCTORS).join("|")})\\(`, "g");
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
    "failed_only 不能与 include_skipped 同时启用", "{} 必须是布尔值",
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
    assert.equal(copyErrorText("KeyError"), "Copying failed and nothing was created. Request the copy again later.");
    setLocale("zh-Hant");
    assert.equal(copyErrorText("source:not_found"), "來源項目已不可用。");
  } finally { setLocale("zh-Hans"); }
});
