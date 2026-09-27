/** Pure decisions about a thread — how messages are grouped, where the day separators and the
 *  unread divider go, what a preview says — with no DOM and no React, so `node --test` can pin
 *  them and the components only draw. */
import type { Conversation, LastMessage, Message } from "../types";

/** Messages from one sender within this many seconds share a group head (avatar, name, time). */
export const GROUP_WINDOW_S = 5 * 60;

export type Row =
  | { type: "day"; key: string; ts: number }
  | { type: "unread"; key: string }
  | { type: "system"; key: string; message: Message }
  | { type: "message"; key: string; message: Message; head: boolean; tail: boolean; own: boolean };

/** Local calendar day of a UNIX timestamp, as a stable key. */
export function dayOf(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

/** Lay a page of messages out as rows.
 *
 *  - a day separator before the first message of each calendar day;
 *  - consecutive text messages from the same sender within GROUP_WINDOW_S form a group: the
 *    first carries the head, the last the tail (bubble corners), system lines break a group;
 *  - the unread divider goes before the first message (not the reader's own) whose seq is above
 *    `lastRead` — the reader's position when the thread was opened, captured once so the line
 *    stays put while they read (it is not the live read position, which moves under them). */
export function layout(items: Message[], uid: string, lastRead: number | null): Row[] {
  const rows: Row[] = [];
  let prevDay = "";
  let prev: Message | null = null;
  let dividerPlaced = lastRead === null;
  for (let i = 0; i < items.length; i++) {
    const m = items[i];
    const day = dayOf(m.created_at);
    if (day !== prevDay) { rows.push({ type: "day", key: `day-${day}`, ts: m.created_at }); prevDay = day; prev = null; }
    if (!dividerPlaced && m.seq > (lastRead as number) && m.sender_id !== uid && !m.pending) {
      rows.push({ type: "unread", key: "unread" }); dividerPlaced = true; prev = null;
    }
    // a system line, and the marker a recall or a manager's removal leaves in place (§7.6)
    if (m.kind === "system" || isMarker(m)) { rows.push({ type: "system", key: m.id, message: m }); prev = null; continue; }
    const next = items[i + 1];
    const sameAsPrev = !!prev && prev.kind === "text" && !isMarker(prev) && prev.sender_id === m.sender_id && m.created_at - prev.created_at <= GROUP_WINDOW_S;
    const sameAsNext = !!next && next.kind === "text" && !isMarker(next) && next.sender_id === m.sender_id && next.created_at - m.created_at <= GROUP_WINDOW_S
      && dayOf(next.created_at) === day && !(!dividerPlaced && next.seq > (lastRead as number) && next.sender_id !== uid && !next.pending);
    rows.push({ type: "message", key: m.id, message: m, head: !sameAsPrev, tail: !sameAsNext, own: m.sender_id === uid });
    prev = m;
  }
  return rows;
}

/** A recalled or removed message: the row stays, as a marker naming who took it away. */
export function isMarker(m: Message): boolean {
  return m.kind === "text" && (!!m.recalled || !!m.removed_by) && !!m.system;
}

/** Whether a conversation is silenced right now: long-term mute, or a timer that has not run out. */
export function mutedNow(me: { muted: boolean; muted_until?: number | null }, now = Date.now() / 1000): boolean {
  return me.muted || (typeof me.muted_until === "number" && me.muted_until > now);
}

/** Is an action still inside its window (recall, edit)? `0` means no limit (§7.6). */
export function withinWindow(createdAt: number, windowS: number | undefined, now = Date.now() / 1000): boolean {
  if (windowS === undefined) return false;
  return windowS === 0 || now - createdAt <= windowS;
}

/** Read receipts (§7.4): among the reader's own stored messages, the last one that some member
 *  with receipts on has read, with how many read it and who. Only that message shows the mark. */
export function receiptMark(items: Message[], uid: string, readBy: { user_id: string; last_read_seq: number }[] | null | undefined,
                            names: (id: string) => string): { seq: number; count: number; who: string[] } | null {
  if (!readBy || !readBy.length) return null;
  const maxRead = Math.max(0, ...readBy.map(r => r.last_read_seq));
  for (let i = items.length - 1; i >= 0; i--) {
    const m = items[i];
    if (m.sender_id !== uid || m.pending || m.failed || !m.seq || m.deleted || m.kind !== "text" || m.seq > maxRead) continue;
    const readers = readBy.filter(r => r.last_read_seq >= m.seq);
    return { seq: m.seq, count: readers.length, who: readers.map(r => names(r.user_id)) };
  }
  return null;
}

/** Split a snippet by code-point ranges (`[[start, end], …]` from the server, §7.9) into hit and
 *  miss parts; the ranges are in code points, not UTF-16 units. */
export function slicesByCodepoints(text: string, ranges: [number, number][]): { text: string; hit: boolean }[] {
  const cps = Array.from(text);
  const out: { text: string; hit: boolean }[] = [];
  let pos = 0;
  for (const [s, e] of [...ranges].sort((a, b) => a[0] - b[0])) {
    const start = Math.max(pos, Math.min(s, cps.length)), end = Math.max(start, Math.min(e, cps.length));
    if (start > pos) out.push({ text: cps.slice(pos, start).join(""), hit: false });
    if (end > start) out.push({ text: cps.slice(start, end).join(""), hit: true });
    pos = end;
  }
  if (pos < cps.length) out.push({ text: cps.slice(pos).join(""), hit: false });
  return out.length ? out : [{ text, hit: false }];
}

/** Merge a batch of server messages into the current list: by id, newest fields win, a pending
 *  copy is replaced by the stored message that carries its client id, order by seq. A message
 *  event fanned out to everyone carries no `card`; the one already rendered is kept. */
export function mergeMessages(current: Message[], incoming: Message[]): Message[] {
  const byId = new Map(current.map(m => [m.id, m]));
  for (const m of incoming) byId.set(m.id, { ...(byId.get(m.id) || {}), ...m, pending: false, failed: undefined });
  const out = Array.from(byId.values()).filter(m => !(m.pending && incoming.some(i => i.client_id && i.client_id === m.client_id)));
  out.sort((a, b) => (a.seq || Number.MAX_SAFE_INTEGER) - (b.seq || Number.MAX_SAFE_INTEGER) || a.created_at - b.created_at);
  return out;
}

export function maxSeq(items: Message[]): number {
  let max = 0;
  for (const m of items) if (!m.pending && m.seq > max) max = m.seq;
  return max;
}

export function initials(name: string): string { return (name || "?").trim().slice(0, 1).toUpperCase(); }

/** One of the eight collaborator colour slots (`--peer-1..8` in the shared tokens), stable per
 *  person: the same id always gets the same slot on every screen. */
export function colorSlot(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return (h % 8) + 1;
}

export function fmtSize(n: number): string {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

export const URL_RE = /(https?:\/\/[^\s<>"']+)/g;

/** The first three distinct links in a text, in order. */
export function linksIn(text: string): string[] {
  const out: string[] = [];
  for (const p of text.split(URL_RE)) if (/^https?:\/\//.test(p) && !out.includes(p)) out.push(p);
  return out.slice(0, 3);
}

export function draftKey(uid: string, cid: string): string { return `synwevo.msg.draft.${uid}.${cid}`; }
export const ENTER_KEY = "synwevo.msg.enter";

export function newClientId(): string { return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`; }

/** What the list shows under a conversation's name. Returns a source key and its parameters;
 *  the component translates. */
export type Preview =
  | { kind: "none" } | { kind: "archived" } | { kind: "system" } | { kind: "hidden" } | { kind: "deleted"; sender: string | null }
  | { kind: "attachment"; sender: string | null } | { kind: "text"; sender: string | null; body: string };
export function preview(c: Conversation, uid: string): Preview {
  if (c.archived) return { kind: "archived" };
  const m: LastMessage | null = c.last_message;
  if (!m) return { kind: "none" };
  if (m.kind === "system") return { kind: "system" };
  if (m.hidden) return { kind: "hidden" };                 // deleted for this reader only (§7.6)
  // a group names the sender; a private conversation only marks the reader's own line
  const sender = c.kind === "group" ? (m.sender_id === uid ? "me" : m.sender) : (m.sender_id === uid ? "me" : null);
  if (m.deleted) return { kind: "deleted", sender };
  if (m.attachment && !m.body) return { kind: "attachment", sender };
  return { kind: "text", sender, body: m.body };
}

/** Which conversations a list filter shows. Archived ones are only under their own filter. */
export function filterConversations(items: Conversation[], filter: "all" | "unread" | "archived", query: string): Conversation[] {
  const q = query.trim().toLowerCase();
  return items.filter(c => {
    if (filter === "archived" ? !c.archived : c.archived) return false;
    if (filter === "unread" && !(c.unread > 0 && !c.me.muted)) return false;
    return !q || c.name.toLowerCase().includes(q);
  });
}

/** Split a text around the matches of a query (case-insensitive) for highlighting. */
export function highlightParts(text: string, query: string): { text: string; hit: boolean }[] {
  const q = query.trim();
  if (!q) return [{ text, hit: false }];
  const out: { text: string; hit: boolean }[] = [];
  const lower = text.toLowerCase(), ql = q.toLowerCase();
  let i = 0;
  for (;;) {
    const j = lower.indexOf(ql, i);
    if (j < 0) { if (i < text.length) out.push({ text: text.slice(i), hit: false }); break; }
    if (j > i) out.push({ text: text.slice(i, j), hit: false });
    out.push({ text: text.slice(j, j + q.length), hit: true });
    i = j + q.length;
  }
  return out;
}

/** A short window of a long body around the first match, for a search result line. */
export function snippet(body: string, query: string, width = 120): string {
  const j = body.toLowerCase().indexOf(query.trim().toLowerCase());
  if (body.length <= width) return body;
  const start = Math.max(0, Math.min(j < 0 ? 0 : j - Math.floor(width / 3), body.length - width));
  return (start > 0 ? "…" : "") + body.slice(start, start + width) + (start + width < body.length ? "…" : "");
}
