/**
 * The password rules, checked in the browser before a one-time link is spent.
 *
 * Why this exists at all: `POST /api/auth/reset` consumes the emailed token *before* it checks
 * the new password, so a password the server refuses also burns the link, and the person has to
 * request and wait for another mail. Until the server validates first, the page must not submit
 * anything it can already tell will be refused.
 *
 * It mirrors `check_policy` in packages/core/synwevo_core/passwords.py. The server stays the
 * authority: this may accept something the server refuses (then the page says the link is spent
 * and offers a new one), but it must never refuse something the server would accept — except
 * the length floor, where the page cannot know whether the account has a second factor and so
 * asks for the single-factor 15, which is what the page has always told people.
 */

export const MIN_LENGTH = 15;
const MAX_LENGTH = 1024;

const COMMON = new Set([
  "password", "passw0rd", "123456", "12345678", "123456789", "1234567890", "qwerty", "abc123",
  "letmein", "welcome", "admin", "administrator", "iloveyou", "monkey", "dragon", "sunshine",
  "princess", "football", "baseball", "trustno1", "changeme", "default", "root", "toor",
  "111111", "000000", "1qaz2wsx", "qwertyuiop", "asdfghjkl", "zxcvbnm", "passw0rd!",
  "p@ssword", "p@ssw0rd", "secret", "master", "hello", "freedom", "whatever", "qazwsx",
  "correcthorsebatterystaple",
]);
const SERVICE_TERMS = ["synwevo", "synwevodocs", "docweave", "wegreen", "dw.wegreen.ltd", "文档", "模板"];

const squash = (s: string) => s.toLowerCase().replace(/[^a-z0-9一-鿿]+/g, "");

/** Length as the server counts it: code points after NFKC, spaces included. */
export function passwordLength(pw: string): number {
  return Array.from(pw.normalize("NFKC")).length;
}

/** The first problem with this password, or null. */
export function passwordProblem(pw: string, opts: { email?: string | null; name?: string | null } = {}): string | null {
  const p = pw.normalize("NFKC");
  const n = Array.from(p).length;
  if (n < MIN_LENGTH) return `口令至少需要 ${MIN_LENGTH} 个字符（当前 ${n} 个）。`;
  if (n > MAX_LENGTH) return `口令过长（上限 ${MAX_LENGTH} 个字符）。`;
  const sq = squash(p);
  const stem = sq.replace(/\d+$/, "");
  if (COMMON.has(sq) || COMMON.has(p.toLowerCase()) || (stem.length >= 4 && COMMON.has(stem))) {
    return "这个口令在常见泄露口令名单里，请换一个。";
  }
  if (new Set(Array.from(p)).size <= 4) return "这个口令重复度太高，请换一个。";
  for (const t of SERVICE_TERMS) {
    const st = squash(t);
    if (st && sq.includes(st)) return "口令不能包含本服务的名称，请换一个。";
  }
  for (const v of [opts.email || "", opts.name || ""]) {
    const local = squash(v.includes("@") ? v.split("@")[0] : v);
    if (local.length >= 4 && sq.includes(local)) return "口令不能包含你的邮箱或姓名，请换一个。";
  }
  return null;
}
