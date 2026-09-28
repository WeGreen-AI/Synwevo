/** Refusals whose sentence carries a number — a time window, a size or count limit — are worded
 *  here from the refusal's `detail`, not by matching the server's sentence: the recall and edit
 *  windows are deployment settings, and a sentence that no longer matches the catalogue would
 *  reach the reader as a bare code (CI-83). A template returns null when `detail` lacks the
 *  number, and the shared presentation falls back to the server's sentence. Every source string
 *  here is in `messages.messages.ts`; `tests/refusals.test.ts` holds the server to that. */
import { registerErrorTemplates, type ErrorTemplate } from "@synwevo/auth-client/api-messages";

const positive = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null);
const MB = 1024 * 1024;

/** Seconds as the reader would say them: whole minutes when it divides, seconds otherwise. */
function window_(seconds: number, inSeconds: string, inMinutes: string) {
  return seconds >= 60 && seconds % 60 === 0 ? ([inMinutes, { n: seconds / 60 }] as const) : ([inSeconds, { n: seconds }] as const);
}

export const REFUSAL_TEMPLATES: Record<string, ErrorTemplate> = {
  recall_window_closed: (d) => {
    const s = positive(d.recall_window_s);
    return s == null ? null : window_(s, "发出超过 {n} 秒的消息不能再撤回，仍可以「删除（仅自己）」", "发出超过 {n} 分钟的消息不能再撤回，仍可以「删除（仅自己）」");
  },
  edit_window_closed: (d) => {
    const s = positive(d.edit_window_s);
    return s == null ? null : window_(s, "发出超过 {n} 秒的消息不能再编辑", "发出超过 {n} 分钟的消息不能再编辑");
  },
  message_too_long: (d) => { const n = positive(d.limit); return n == null ? null : ["消息最多 {n} 个字符", { n }] as const; },
  too_many_targets: (d) => { const n = positive(d.limit); return n == null ? null : ["一次最多转发到 {n} 个会话", { n }] as const; },
  too_many_members: (d) => { const n = positive(d.limit); return n == null ? null : ["群成员最多 {n} 人", { n }] as const; },
  file_too_large: (d) => {
    const n = positive(d.limit);
    if (n == null) return null;
    return [d.kind === "voice" ? "语音超过 {n} MB 上限" : "附件超过 {n} MB 上限", { n: Math.floor(n / MB) }] as const;
  },
  // `invalid_field` is shared by every field; only the three that quote a length are worded here
  invalid_field: (d) => {
    const n = positive(d.limit);
    if (n == null) return null;
    if (d.field === "name") return ["群名称需要 1–{n} 个字符", { n }] as const;
    if (d.field === "description") return ["群描述最多 {n} 个字符", { n }] as const;
    if (d.field === "body") return ["消息需要 1–{n} 个字符", { n }] as const;
    return null;
  },
};

registerErrorTemplates(REFUSAL_TEMPLATES);
