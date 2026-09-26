/** Comments on a project, in a drawer: threads (unresolved first), a composer with @mentions of
 *  workspace members and an anchor chosen from what is selected on the page (a field, a mapping,
 *  a file) or the project as a whole; resolve / reopen, edit and delete your own. An anchor is
 *  judged by the server at read time — a field that was deleted or a template that was replaced
 *  shows as stale and cannot be jumped to, instead of landing on whatever now sits there. */
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, del, get, patch, post } from "@synwevo/auth-client";
import { Drawer, isImeKey, t as tr, useDialog, useI18n, useToast } from "@synwevo/ui";
import { errorText } from "../lib/perm";

export type CommentAnchor = { kind: "project" } | { kind: "slot" | "mapping" | "source" | "job"; id: string; label?: string; valid?: boolean; reason?: string | null; changed?: boolean };
export interface Comment {
  id: string; project_id: string; parent_id: string | null;
  author: { user_id: string; name: string }; body: string; mentions: string[];
  anchor: CommentAnchor | null; resolved_at: number | null; resolved_by: { user_id: string; name: string } | null;
  created_at: number; edited_at: number | null;
}
interface Member { user_id: string; name: string }
export interface AnchorOption { anchor: CommentAnchor; label: string }

const REASON: Record<string, string> = { slot_gone: "该字段已删除", template_changed: "模板已更换", mapping_gone: "该映射已删除", source_gone: "该文件已移除" };

function whenText(ts: number, locale: string) { return new Date(ts * 1000).toLocaleString(locale, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }

export default function CommentsDrawer({ pid, me, canWrite, options, highlight, liveSeq = 0, onLocate, onClose }: {
  pid: string; me: string | null; canWrite: boolean; options: AnchorOption[]; highlight?: string | null;
  /** Bumped by the page when the project room reports a comment change by someone else (CI-50):
   *  the list reloads at once; the 30 s tick below stays as the fallback when the room is down. */
  liveSeq?: number;
  onLocate: (anchor: CommentAnchor) => void; onClose: () => void;
}) {
  const { locale } = useI18n();
  const toast = useToast();
  const { confirm } = useDialog();
  const [items, setItems] = useState<Comment[] | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [showResolved, setShowResolved] = useState(false);
  const [body, setBody] = useState("");
  const [mentions, setMentions] = useState<Member[]>([]);
  const [anchorIdx, setAnchorIdx] = useState(0);
  const [replyTo, setReplyTo] = useState<Comment | null>(null);
  const [editing, setEditing] = useState<Comment | null>(null);
  const [pick, setPick] = useState<{ q: string; at: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const load = async () => {
    try {
      const [c, m] = await Promise.all([get<{ items: Comment[] }>(`/api/projects/${pid}/comments?include_resolved=${showResolved ? 1 : 0}`), get<{ items: Member[] }>(`/api/projects/${pid}/comment-members`)]);
      setItems(c.items); setMembers(m.items); setError(null);
    } catch (e) { setError(errorText(e)); }
  };
  useEffect(() => { void load(); }, [pid, showResolved, liveSeq]);   // eslint-disable-line react-hooks/exhaustive-deps
  // While the drawer is open, other people's comments arrive without reopening it: a quiet
  // reload every 30 s and whenever the tab becomes visible again. The composer's draft is
  // separate state, so a reload never touches what is being typed.
  useEffect(() => {
    const tick = () => { if (document.visibilityState === "visible") void load(); };
    const timer = window.setInterval(tick, 30_000);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [pid, showResolved]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!highlight || !items) return;
    const el = listRef.current?.querySelector(`[data-comment="${highlight}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: "center" });
  }, [highlight, items]);

  const threads = useMemo(() => {
    const roots = (items || []).filter(c => !c.parent_id);
    const replies = new Map<string, Comment[]>();
    for (const c of items || []) if (c.parent_id) replies.set(c.parent_id, [...(replies.get(c.parent_id) || []), c]);
    roots.sort((a, b) => Number(!!a.resolved_at) - Number(!!b.resolved_at) || b.created_at - a.created_at);
    return roots.map(r => ({ root: r, replies: replies.get(r.id) || [] }));
  }, [items]);

  const candidates = useMemo(() => {
    if (!pick) return [];
    const q = pick.q.toLowerCase();
    return members.filter(m => m.user_id !== me && !mentions.some(x => x.user_id === m.user_id) && (!q || m.name.toLowerCase().includes(q))).slice(0, 8);
  }, [pick, members, mentions, me]);

  function onType(v: string) {
    setBody(v);
    const pos = box.current?.selectionStart ?? v.length;
    const before = v.slice(0, pos);
    const m = /(?:^|\s)@([^\s@]{0,20})$/.exec(before);
    setPick(m ? { q: m[1], at: pos - m[1].length - 1 } : null);
  }
  function choose(mem: Member) {
    if (!pick) return;
    const v = body.slice(0, pick.at) + "@" + mem.name + " " + body.slice(box.current?.selectionStart ?? body.length);
    setBody(v); setMentions(ms => [...ms, mem]); setPick(null);
    requestAnimationFrame(() => box.current?.focus());
  }

  async function submit() {
    if (!body.trim()) return;
    setBusy(true);
    try {
      const mentionIds = mentions.filter(m => body.includes("@" + m.name)).map(m => m.user_id);
      if (editing) {
        await patch(`/api/comments/${editing.id}`, { body, mentions: mentionIds });
        toast(tr("评论已修改"), "ok");
      } else {
        const anchor = replyTo ? undefined : options[anchorIdx]?.anchor;
        await post(`/api/projects/${pid}/comments`, { body, mentions: mentionIds, anchor, parent_id: replyTo?.id });
        toast(mentionIds.length ? tr("评论已发布，已通知被提及的成员") : tr("评论已发布"), "ok");
      }
      setBody(""); setMentions([]); setReplyTo(null); setEditing(null);
      await load();
    } catch (e) { toast(e instanceof ApiError ? e.message : errorText(e), "err"); } finally { setBusy(false); }
  }

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try { await fn(); await load(); } catch (e) { toast(e instanceof ApiError ? e.message : errorText(e), "err"); } finally { setBusy(false); }
  }

  const anchorChip = (a: CommentAnchor | null) => {
    if (!a || a.kind === "project") return null;
    const kindLabel = { slot: tr("字段"), mapping: tr("映射"), source: tr("文件"), job: tr("任务") }[a.kind];
    if (a.valid === false) return <span className="badge warn" title={tr(REASON[a.reason || ""] || "锚点已失效")}>{kindLabel}「{a.label || a.id}」· {tr("已失效")}</span>;
    return <button type="button" className="btn xs" onClick={() => onLocate(a)} title={tr("定位到该对象")}>{kindLabel}「{a.label || a.id}」{a.changed ? " · " + tr("已更新") : ""} ↗</button>;
  };

  const renderOne = (c: Comment, isReply: boolean) => <div key={c.id} data-comment={c.id} className={`cmt ${isReply ? "cmt-reply" : ""} ${highlight === c.id ? "cmt-hl" : ""} ${c.resolved_at ? "cmt-resolved" : ""}`}>
    <div className="row wrap gap4 tiny muted"><b className="text">{c.author.name}</b><span>{whenText(c.created_at, locale)}</span>{c.edited_at && <span>{tr("已编辑")}</span>}{!isReply && anchorChip(c.anchor)}</div>
    <div className="cmt-body">{c.body}</div>
    <div className="row wrap gap4">
      {!isReply && !c.resolved_at && <button type="button" className="btn xs ghost" disabled={busy} onClick={() => { setReplyTo(c); setEditing(null); box.current?.focus(); }}>{tr("回复")}</button>}
      {c.author.user_id === me && <button type="button" className="btn xs ghost" disabled={busy} onClick={() => { setEditing(c); setReplyTo(null); setBody(c.body); setMentions(members.filter(m => c.mentions.includes(m.user_id))); box.current?.focus(); }}>{tr("编辑")}</button>}
      {c.author.user_id === me && <button type="button" className="btn xs ghost" disabled={busy} onClick={() => void confirm(tr("删除评论"), tr("删除后无法恢复。")).then(ok => { if (ok) void act(() => del(`/api/comments/${c.id}`)); })}>{tr("删除")}</button>}
      {!isReply && (canWrite || c.author.user_id === me) && (c.resolved_at
        ? <button type="button" className="btn xs ghost" disabled={busy} onClick={() => void act(() => post(`/api/comments/${c.id}/reopen`, {}))}>{tr("重新打开")}</button>
        : <button type="button" className="btn xs ghost" disabled={busy} onClick={() => void act(() => post(`/api/comments/${c.id}/resolve`, {}))}>{tr("标记解决")}</button>)}
      {c.resolved_at && c.resolved_by && <span className="tiny muted">{tr("由 {who} 标记解决", { who: c.resolved_by.name })}</span>}
    </div>
  </div>;

  return <Drawer title={tr("评论")} onClose={onClose}>
    <div className="col gap8 cmt-drawer" data-testid="comments-drawer">
      <div className="row wrap gap4">
        <label className="check tiny"><input type="checkbox" checked={showResolved} onChange={e => setShowResolved(e.target.checked)} /><span>{tr("显示已解决")}</span></label>
      </div>
      {error && <div className="notice err" role="alert">{error}</div>}
      <div className="cmt-list col gap8" ref={listRef}>
        {items && threads.length === 0 && <p className="muted tiny">{tr("还没有评论。选中一个字段、映射或文件后评论，可以把问题定位到它。")}</p>}
        {threads.map(th => <div key={th.root.id} className="cmt-thread col gap4">{renderOne(th.root, false)}{th.replies.map(r => renderOne(r, true))}</div>)}
      </div>
      <form className="col gap4 cmt-composer" onSubmit={e => { e.preventDefault(); void submit(); }}>
        {replyTo && <div className="tiny muted row gap4">{tr("回复 {who}", { who: replyTo.author.name })}<button type="button" className="btn xs ghost" onClick={() => setReplyTo(null)}>×</button></div>}
        {editing && <div className="tiny muted row gap4">{tr("正在编辑你的评论")}<button type="button" className="btn xs ghost" onClick={() => { setEditing(null); setBody(""); setMentions([]); }}>×</button></div>}
        {!replyTo && !editing && options.length > 1 && <label className="field sm"><span>{tr("关于")}</span>
          <select className="input sm" value={anchorIdx} onChange={e => setAnchorIdx(Number(e.target.value))}>{options.map((o, i) => <option key={i} value={i}>{o.label}</option>)}</select></label>}
        <div className="cmt-box">
          <textarea ref={box} className="input" rows={3} value={body} maxLength={4000} placeholder={tr("写评论… 输入 @ 提及成员")} onChange={e => onType(e.target.value)}
            onKeyDown={e => { if (isImeKey(e)) return; if (pick && candidates.length && e.key === "Enter") { e.preventDefault(); choose(candidates[0]); } else if (e.key === "Escape" && pick) { e.stopPropagation(); setPick(null); } }} />
          {pick && candidates.length > 0 && <ul className="cmt-pick" role="listbox">{candidates.map(m => <li key={m.user_id}><button type="button" role="option" className="btn xs ghost" onClick={() => choose(m)}>@{m.name}</button></li>)}</ul>}
        </div>
        <div className="row gap4"><button className="btn primary sm" disabled={busy || !body.trim()}>{editing ? tr("保存修改") : tr("发布评论")}</button></div>
      </form>
    </div>
  </Drawer>;
}
