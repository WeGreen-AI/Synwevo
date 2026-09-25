/** Messages: direct messages and groups inside an organization.
 *
 *  What the server says is what is shown: a message is "sent" only when the server has stored it
 *  (until then it is pending, and a retry carries the same client id so a lost acknowledgement
 *  never becomes a duplicate); history is whatever the server lets this member read; the stream
 *  is a hint that something changed and every (re)connect resyncs from the history API with the
 *  last sequence this tab knows. When the stream cannot be held open the page polls and says so. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, announceMessagesUnread, api, get, post, put, upload, useSession } from "@synwevo/auth-client";
import { Drawer, Modal, useI18n, useMediaQuery } from "@synwevo/ui";
import { Topbar } from "../components/Topbar";
import { FlowError, Loading } from "./org/AuthFlow";
import "../messages.css";
import "../messages.messages";

interface Member { user_id: string; name: string; role: "owner" | "admin" | "member" }
interface Conversation { id: string; organization_id: string; kind: "dm" | "group"; name: string; description: string | null; archived: boolean; last_seq: number;
  last_message_at: number | null; me: { role: string; joined_seq: number; last_read_seq: number; muted: boolean; pinned: boolean; notify: "all" | "mentions" | "none" };
  unread: number; peer: { user_id: string; name: string } | null; members: Member[]; member_count: number;
  last_message: { seq: number; sender: string; body: string; deleted: boolean; attachment: boolean; kind: string } | null }
interface Reaction { emoji: string; users: string[]; count: number }
interface Message { id: string; conversation_id: string; seq: number; sender_id: string; sender: string; kind: "text" | "system"; body: string; reply_to: string | null;
  mentions: string[]; edited_at: number | null; deleted: boolean; created_at: number; client_id: string | null; reactions: Reaction[];
  attachment: { id: string; filename: string; size: number; content_type: string; inline: boolean } | null; reply?: { id: string; seq: number; sender: string; body: string; deleted: boolean };
  system?: Record<string, any>; pending?: boolean; failed?: string }
interface Org { id: string; name: string; status: string }

const M = "/api/auth/messaging";
const REACTIONS = ["👍", "❤️", "😂", "🎉", "👀", "✅", "❓", "🙏"];
const urlRe = /(https?:\/\/[^\s<>"']+)/g;

function initials(name: string) { return (name || "?").trim().slice(0, 1).toUpperCase(); }
function fmtSize(n: number) { return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`; }
function dayKey(ts: number, locale: string) { return new Date(ts * 1000).toLocaleDateString(locale, { year: "numeric", month: "short", day: "numeric" }); }
function timeOf(ts: number, locale: string) { return new Date(ts * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" }); }
function linkify(text: string) {
  const parts = text.split(urlRe);
  return parts.map((p, i) => urlRe.test(p) && /^https?:\/\//.test(p) ? <a key={i} href={p} target="_blank" rel="noopener noreferrer nofollow">{p}</a> : <span key={i}>{p}</span>);
}
function draftKey(uid: string, cid: string) { return `synwevo.msg.draft.${uid}.${cid}`; }

export default function Messages() {
  const { t, locale } = useI18n(); const { me } = useSession(); const navigate = useNavigate(); const { cid } = useParams();
  const narrow = useMediaQuery("(max-width: 720px)");
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [convs, setConvs] = useState<Conversation[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [live, setLive] = useState<"connecting" | "live" | "polling">("connecting");
  const [dialog, setDialog] = useState<"dm" | "group" | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState<{ q: string; items: Message[] } | null>(null);
  const uid = me?.id || "";
  const selected = useMemo(() => convs?.find(c => c.id === cid) || null, [convs, cid]);

  const loadConvs = useCallback(() => get<{ items: Conversation[] }>(`${M}/conversations`).then(x => { setConvs(x.items); const unread = x.items.filter(c => !c.me.muted).reduce((a, c) => a + c.unread, 0); announceMessagesUnread(unread); return x.items; }), []);
  useEffect(() => {
    const abort = new AbortController();
    Promise.all([get<{ items: Org[] }>("/api/auth/organizations", abort.signal), loadConvs()]).then(([o]) => setOrgs(o.items.filter(x => x.status === "active"))).catch(e => { if (!abort.signal.aborted) setError(e); });
    // drafts of another account on this browser are never shown to this one
    try { Object.keys(localStorage).filter(k => k.startsWith("synwevo.msg.draft.") && !k.startsWith(`synwevo.msg.draft.${uid}.`)).forEach(k => localStorage.removeItem(k)); } catch { /* storage unavailable */ }
    return () => abort.abort();
  }, [loadConvs, uid]);

  // ---- the stream: a hint that something changed; the thread resyncs itself from history
  const threadRef = useRef<{ onEvent: (ev: any) => void; resync: () => void } | null>(null);
  useEffect(() => {
    if (!uid) return;
    let es: EventSource | null = null; let failures = 0; let poll: number | null = null; let closed = false;
    const startPolling = () => { setLive("polling"); if (poll == null) poll = window.setInterval(() => { threadRef.current?.resync(); void loadConvs().catch(() => undefined); }, 5000); };
    const stopPolling = () => { if (poll != null) { window.clearInterval(poll); poll = null; } };
    const connect = () => {
      if (closed) return;
      es = new EventSource(`${M}/stream`);
      es.addEventListener("hello", () => { failures = 0; stopPolling(); setLive("live"); threadRef.current?.resync(); void loadConvs().catch(() => undefined); });
      const forward = (e: MessageEvent) => { try { threadRef.current?.onEvent(JSON.parse(e.data)); } catch { /* ignore */ } };
      // a new message changes the list too (preview, order, unread); one reload per burst
      let listTimer: number | null = null;
      const reloadSoon = () => { if (listTimer != null) return; listTimer = window.setTimeout(() => { listTimer = null; void loadConvs().catch(() => undefined); }, 300); };
      es.addEventListener("message", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("message_updated", forward as EventListener);
      es.addEventListener("read", forward as EventListener);
      es.addEventListener("conversation", (e: MessageEvent) => { forward(e); void loadConvs().catch(() => undefined); });
      es.addEventListener("end", () => { es?.close(); es = null; startPolling(); });
      es.onerror = () => { failures += 1; if (failures >= 3) startPolling(); };   // EventSource retries by itself
    };
    connect();
    return () => { closed = true; es?.close(); stopPolling(); };
  }, [uid, loadConvs]);

  const open = (id: string) => { setSearch(null); navigate(`/messages/${id}`); };
  const visible = (convs || []).filter(c => !filter.trim() || c.name.toLowerCase().includes(filter.trim().toLowerCase()));
  const doSearch = async (q: string) => { if (q.trim().length < 2) { setSearch(null); return; } try { const x = await get<{ items: Message[] }>(`${M}/search?q=${encodeURIComponent(q.trim())}`); setSearch({ q, items: x.items }); } catch (e) { setError(e); } };

  return <div className="app"><Topbar title={t("消息")} /><div className={`msg-layout ${cid ? "thread" : "list"}`}>
    <aside className="msg-side" aria-label={t("会话列表")}>
      <div className="msg-side-h">
        <input className="input sm" type="search" placeholder={t("筛选或搜索消息…")} value={filter} onChange={e => { setFilter(e.target.value); if (!e.target.value) setSearch(null); }} onKeyDown={e => { if (e.key === "Enter") void doSearch(filter); }} aria-label={t("筛选或搜索消息…")} />
        <button type="button" className="btn sm" onClick={() => setDialog("dm")} disabled={!orgs.length}>{t("新私聊")}</button>
        <button type="button" className="btn sm" onClick={() => setDialog("group")} disabled={!orgs.length}>{t("新群聊")}</button>
      </div>
      <FlowError error={error} />
      {search ? <div className="msg-convs" role="list" aria-label={t("搜索结果")}>
        <p className="tiny muted" style={{ padding: "4px 8px" }}>{t("「{q}」的搜索结果：{n} 条（只包含你能看到的消息）", { q: search.q, n: search.items.length })} <button type="button" className="btn ghost sm" onClick={() => setSearch(null)}>{t("清除")}</button></p>
        {search.items.map(m => <button key={m.id} type="button" className="msg-search-hit" onClick={() => open(m.conversation_id)}><div className="tiny muted">{m.sender} · {timeOf(m.created_at, locale)}</div><div className="small">{m.body.slice(0, 160)}</div></button>)}
      </div>
      : !convs ? <Loading /> : !convs.length ? <div className="msg-empty">{t("还没有会话。和同组织的成员开始私聊，或建一个群。")}</div>
      : <div className="msg-convs" role="list">{visible.map(c => <button key={c.id} type="button" role="listitem" className={`msg-conv ${c.id === cid ? "on" : ""}`} onClick={() => open(c.id)} aria-current={c.id === cid ? "true" : undefined}>
        <span className={`msg-avatar ${c.kind}`} aria-hidden="true">{c.kind === "group" ? "#" : initials(c.name)}</span>
        <span className="msg-conv-main"><span className="msg-conv-title"><strong>{c.me.pinned ? "📌 " : ""}{c.name}</strong>{c.unread > 0 && !c.me.muted && <span className="msg-unread">{c.unread}</span>}{c.me.muted && <span className="tiny muted">{t("已静音")}</span>}</span>
          <span className="msg-conv-preview">{c.archived ? t("已归档") : c.last_message ? (c.last_message.kind === "system" ? t("（系统消息）") : `${c.kind === "group" ? c.last_message.sender + ": " : ""}${c.last_message.deleted ? t("消息已删除") : c.last_message.attachment && !c.last_message.body ? t("[附件]") : c.last_message.body}`) : t("尚无消息")}</span></span>
      </button>)}</div>}
    </aside>
    <section className="msg-thread" aria-label={t("会话")}>
      {!cid ? <div className="msg-empty">{t("选择一个会话，或开始新的对话。")}</div>
        : !selected ? (convs ? <div className="msg-empty">{t("找不到这个会话，或你已不在其中。")} <button type="button" className="btn" onClick={() => navigate("/messages")}>{t("返回列表")}</button></div> : <Loading />)
        : <Thread key={selected.id} conv={selected} uid={uid} live={live} narrow={narrow} onBack={() => navigate("/messages")} onOpenInfo={() => setDrawer(true)} onChanged={() => void loadConvs()} bind={fns => { threadRef.current = fns; }} />}
    </section>
    {drawer && selected && <ConversationInfo conv={selected} uid={uid} onClose={() => setDrawer(false)} onChanged={() => { void loadConvs(); }} onLeft={() => { setDrawer(false); navigate("/messages"); void loadConvs(); }} />}
    {dialog && <NewConversation kind={dialog} orgs={orgs} onClose={() => setDialog(null)} onCreated={c => { setDialog(null); void loadConvs().then(() => open(c.id)); }} />}
  </div></div>;
}

// ---------------------------------------------------------------- the thread
function Thread({ conv, uid, live, narrow, onBack, onOpenInfo, onChanged, bind }: {
  conv: Conversation; uid: string; live: string; narrow: boolean; onBack: () => void; onOpenInfo: () => void; onChanged: () => void;
  bind: (fns: { onEvent: (ev: any) => void; resync: () => void }) => void;
}) {
  const { t, locale } = useI18n();
  const [items, setItems] = useState<Message[] | null>(null); const [hasMore, setHasMore] = useState(false); const [error, setError] = useState<unknown>(null);
  const [draft, setDraft] = useState(() => { try { return localStorage.getItem(draftKey(uid, conv.id)) || ""; } catch { return ""; } });
  const [enterSends, setEnterSends] = useState(() => { try { return localStorage.getItem("synwevo.msg.enter") !== "newline"; } catch { return true; } });
  const [replyTo, setReplyTo] = useState<Message | null>(null); const [editing, setEditing] = useState<Message | null>(null);
  const [attachment, setAttachment] = useState<{ id: string; filename: string; size: number } | null>(null); const [uploading, setUploading] = useState<number | null>(null);
  const [picker, setPicker] = useState<string | null>(null); const [fresh, setFresh] = useState(0); const [busy, setBusy] = useState(false);
  const [mention, setMention] = useState<Member[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null); const atBottom = useRef(true); const lastSeq = useRef(0); const fileRef = useRef<HTMLInputElement>(null);
  const composing = useRef(false); const textRef = useRef<HTMLTextAreaElement>(null);

  const merge = useCallback((incoming: Message[]) => setItems(cur => {
    const byId = new Map((cur || []).map(m => [m.id, m]));
    for (const m of incoming) byId.set(m.id, { ...(byId.get(m.id) || {}), ...m, pending: false, failed: undefined });
    const out = Array.from(byId.values()).filter(m => !(m.pending && incoming.some(i => i.client_id && i.client_id === m.client_id)));
    out.sort((a, b) => (a.seq || 1e12) - (b.seq || 1e12) || a.created_at - b.created_at);
    const max = Math.max(0, ...out.filter(m => !m.pending).map(m => m.seq));
    if (max > lastSeq.current) lastSeq.current = max;
    return out;
  }), []);
  const load = useCallback(async () => {
    const page = await get<{ items: Message[]; has_more: boolean }>(`${M}/conversations/${conv.id}/messages?limit=50`);
    setItems(page.items); setHasMore(page.has_more); lastSeq.current = Math.max(0, ...page.items.map(m => m.seq));
    requestAnimationFrame(() => { const el = scrollRef.current; if (el) el.scrollTop = el.scrollHeight; });
  }, [conv.id]);
  const resync = useCallback(async () => {
    if (!lastSeq.current) return;
    try { const page = await get<{ items: Message[] }>(`${M}/conversations/${conv.id}/messages?after=${lastSeq.current}&limit=200`); if (page.items.length) { merge(page.items); if (!atBottom.current) setFresh(n => n + page.items.length); } } catch { /* next tick */ }
  }, [conv.id, merge]);
  useEffect(() => { load().catch(e => setError(e)); }, [load]);
  useEffect(() => {
    bind({
      onEvent: ev => {
        if (ev.conversation_id !== conv.id) return;
        if (ev.type === "message" || ev.type === "message_updated") { merge([ev.message]); if (ev.type === "message" && ev.message.sender_id !== uid && !atBottom.current) setFresh(n => n + 1); }
      },
      resync: () => { void resync(); },
    });
  }, [bind, conv.id, merge, resync, uid]);
  // keep the view at the bottom when it already was; mark read when at the bottom and visible
  useEffect(() => {
    const el = scrollRef.current; if (!el || !items) return;
    if (atBottom.current) { el.scrollTop = el.scrollHeight; setFresh(0); }
  }, [items]);
  useEffect(() => {
    if (!items || !items.length || document.visibilityState !== "visible" || !atBottom.current) return;
    const last = lastSeq.current;
    if (last > conv.me.last_read_seq) { const timer = window.setTimeout(() => { post(`${M}/conversations/${conv.id}/read`, { seq: last }).then(() => onChanged()).catch(() => undefined); }, 400); return () => window.clearTimeout(timer); }
  }, [items, conv.id, conv.me.last_read_seq, onChanged]);
  useEffect(() => { try { if (draft) localStorage.setItem(draftKey(uid, conv.id), draft); else localStorage.removeItem(draftKey(uid, conv.id)); } catch { /* storage unavailable */ } }, [draft, uid, conv.id]);

  const loadOlder = async () => {
    const el = scrollRef.current; const first = items?.find(m => m.seq)?.seq; if (!el || !first) return;
    const before = el.scrollHeight - el.scrollTop;
    const page = await get<{ items: Message[]; has_more: boolean }>(`${M}/conversations/${conv.id}/messages?before=${first}&limit=50`);
    merge(page.items); setHasMore(page.has_more);
    requestAnimationFrame(() => { el.scrollTop = el.scrollHeight - before; });   // the anchor stays where the reader was
  };
  const onScroll = () => { const el = scrollRef.current; if (!el) return; atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; if (atBottom.current) setFresh(0); };

  const send = async (retry?: Message) => {
    const body = (retry ? retry.body : draft).trim(); const att = retry ? retry.attachment : attachment;
    if (!body && !att) return;
    const client_id = retry?.client_id || `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    const mention_ids = mention.map(m => m.user_id);
    const pending: Message = retry ? { ...retry, pending: true, failed: undefined } : { id: `pending-${client_id}`, conversation_id: conv.id, seq: 0, sender_id: uid, sender: t("我"), kind: "text", body, reply_to: replyTo?.id || null, mentions: mention_ids, edited_at: null, deleted: false, created_at: Date.now() / 1000, client_id, reactions: [], attachment: att ? { ...att, content_type: "", inline: false } : null, pending: true, reply: replyTo ? { id: replyTo.id, seq: replyTo.seq, sender: replyTo.sender, body: replyTo.body, deleted: replyTo.deleted } : undefined };
    setItems(cur => [...(cur || []).filter(m => m.id !== pending.id), pending]); atBottom.current = true;
    if (!retry) { setDraft(""); setReplyTo(null); setAttachment(null); setMention([]); }
    try {
      const out = await post<{ message: Message }>(`${M}/conversations/${conv.id}/messages`, { body, client_id, ...(pending.reply_to ? { reply_to: pending.reply_to } : {}), ...(att ? { attachment_id: att.id } : {}), ...(mention_ids.length ? { mention_ids } : {}) });
      merge([out.message]); onChanged();
    } catch (e) {
      const why = e instanceof ApiError ? e.message : String(e);
      setItems(cur => (cur || []).map(m => m.id === pending.id ? { ...m, pending: false, failed: why } : m));
    }
  };
  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || composing.current || (e.nativeEvent as any).isComposing) return;
    if ((enterSends && !e.shiftKey) || (!enterSends && (e.ctrlKey || e.metaKey))) { e.preventDefault(); void send(); }
  };
  const pickFile = async (f: File | null) => {
    if (!f) return; setError(null); setUploading(0);
    try { const form = new FormData(); form.append("file", f); const out = await upload<{ id: string; filename: string; size: number }>(`${M}/conversations/${conv.id}/attachments`, form, p => setUploading(p)); setAttachment(out); }
    catch (e) { setError(e); } finally { setUploading(null); if (fileRef.current) fileRef.current.value = ""; }
  };
  const saveEdit = async () => { if (!editing) return; setBusy(true); try { merge([await api(`${M}/messages/${editing.id}`, { method: "PATCH", body: { body: draft } })]); setEditing(null); setDraft(""); } catch (e) { setError(e); } finally { setBusy(false); } };
  const del = async (m: Message) => { if (!window.confirm(t("删除这条消息？其他人将看到「消息已删除」。"))) return; try { merge([await api(`${M}/messages/${m.id}`, { method: "DELETE" })]); } catch (e) { setError(e); } };
  const react = async (m: Message, emoji: string) => { const mine = m.reactions.find(r => r.emoji === emoji)?.users.includes(uid); setPicker(null); try { merge([await put(`${M}/messages/${m.id}/reactions`, { emoji, on: !mine })]); } catch (e) { setError(e); } };
  const systemText = (m: Message) => { const s = m.system || {}; const who = (id: string) => conv.members.find(x => x.user_id === id)?.name || t("某成员"); const list = (ids: string[]) => (ids || []).map(who).join("、");
    switch (s.event) { case "created": return t("{actor} 创建了群聊", { actor: who(s.actor) }); case "added": return t("{actor} 把 {who} 加入了群聊", { actor: who(s.actor), who: list(s.added) }); case "removed": return t("{actor} 移除了 {who}", { actor: who(s.actor), who: list(s.removed) });
      case "left": return t("{actor} 退出了群聊", { actor: who(s.actor) }); case "renamed": return t("{actor} 把群名改为「{name}」", { actor: who(s.actor), name: s.name }); case "transferred": return t("{actor} 把群主交接给了 {who}", { actor: who(s.actor), who: who(s.to) });
      case "archived": return t("{actor} 归档了群聊", { actor: who(s.actor) }); case "unarchived": return t("{actor} 恢复了群聊", { actor: who(s.actor) }); default: return ""; } };

  let lastDay = "";
  return <>
    <div className="msg-thread-h">
      {narrow && <button type="button" className="btn ghost sm" onClick={onBack} aria-label={t("返回列表")}>←</button>}
      <span className={`msg-avatar ${conv.kind}`} aria-hidden="true" style={{ width: 28, height: 28, fontSize: 12 }}>{conv.kind === "group" ? "#" : initials(conv.name)}</span>
      <span className="title">{conv.name}{conv.kind === "group" && <span className="tiny muted"> · {t("{n} 人", { n: conv.member_count })}</span>}</span>
      <span className={`msg-status ${live}`} role="status">{t(live === "live" ? "实时" : live === "polling" ? "轮询中" : "连接中")}</span>
      <button type="button" className="btn ghost sm" onClick={onOpenInfo}>{t(conv.kind === "group" ? "群信息" : "会话设置")}</button>
    </div>
    <FlowError error={error} />
    <div className="msg-scroll" ref={scrollRef} onScroll={onScroll} role="log" aria-live="polite" aria-label={t("消息记录")}>
      {items === null ? <Loading /> : <>
        {hasMore ? <button type="button" className="btn sm" style={{ alignSelf: "center" }} onClick={() => void loadOlder().catch(e => setError(e))}>{t("加载更早的消息")}</button>
          : conv.kind === "group" && conv.me.joined_seq > 1 ? <div className="msg-system">{t("你能看到加入之后的消息。")}</div> : null}
        {items.map(m => {
          const day = dayKey(m.created_at, locale); const showDay = day !== lastDay; lastDay = day;
          const own = m.sender_id === uid;
          return <div key={m.id}>
            {showDay && <div className="msg-day" style={{ display: "table", margin: "8px auto" }}>{day}</div>}
            {m.kind === "system" ? <div className="msg-system">{systemText(m)}</div> : <div className={`msg-row ${own ? "own" : ""}`} id={`msg-${m.seq}`}>
              {!own && <span className="msg-avatar" aria-hidden="true" style={{ width: 28, height: 28, fontSize: 12 }}>{initials(m.sender)}</span>}
              <div className="msg-bubble">
                <div className="msg-meta"><span>{own ? t("我") : m.sender}</span><span>{timeOf(m.created_at, locale)}</span>{m.edited_at && <span>{t("已编辑")}</span>}{m.pending && <span className="msg-pending">{t("发送中…")}</span>}</div>
                {m.reply && <div className="msg-reply"><strong>{m.reply.sender}</strong> {m.reply.deleted ? t("消息已删除") : m.reply.body}</div>}
                {m.deleted ? <div className="msg-deleted">{t("消息已删除")}</div> : <div className="msg-body">{linkify(m.body)}</div>}
                {m.attachment && !m.deleted && <div className="msg-attach">{m.attachment.inline && !m.pending ? <a href={`${M}/attachments/${m.attachment.id}`}><img src={`${M}/attachments/${m.attachment.id}?inline=1`} alt={m.attachment.filename} loading="lazy" /></a>
                  : <a href={m.pending ? undefined : `${M}/attachments/${m.attachment.id}`}>📎 {m.attachment.filename} <span className="tiny muted">({fmtSize(m.attachment.size)})</span></a>}</div>}
                {m.reactions.length > 0 && <div className="msg-reactions">{m.reactions.map(r => <button key={r.emoji} type="button" className={`msg-reaction ${r.users.includes(uid) ? "mine" : ""}`} onClick={() => void react(m, r.emoji)} aria-pressed={r.users.includes(uid)}>{r.emoji} {r.count}</button>)}</div>}
                {m.failed && <div className="msg-failed">{t("发送失败：{why}", { why: m.failed })} <button type="button" className="btn ghost sm" onClick={() => void send(m)}>{t("重试")}</button></div>}
              </div>
              {!m.deleted && !m.pending && <div className="msg-tools">
                <button type="button" onClick={() => setPicker(picker === m.id ? null : m.id)} aria-label={t("添加表情回应")} title={t("添加表情回应")}>☺</button>
                <button type="button" onClick={() => { setReplyTo(m); textRef.current?.focus(); }} aria-label={t("回复")} title={t("回复")}>↩</button>
                {own && <button type="button" onClick={() => { setEditing(m); setDraft(m.body); textRef.current?.focus(); }} aria-label={t("编辑")} title={t("编辑")}>✎</button>}
                {(own || (conv.kind === "group" && conv.me.role !== "member")) && <button type="button" onClick={() => void del(m)} aria-label={t("删除")} title={t("删除")}>🗑</button>}
                {picker === m.id && <div className="msg-picker" role="group" aria-label={t("表情回应")}>{REACTIONS.map(e => <button key={e} type="button" onClick={() => void react(m, e)}>{e}</button>)}</div>}
              </div>}
            </div>}
          </div>;
        })}
        {fresh > 0 && <button type="button" className="btn primary sm msg-new" onClick={() => { const el = scrollRef.current; if (el) el.scrollTop = el.scrollHeight; setFresh(0); }}>{t("{n} 条新消息 ↓", { n: fresh })}</button>}
      </>}
    </div>
    {conv.archived ? <div className="msg-composer"><p className="muted small">{t("会话已归档，不能再发消息。")}</p></div> : <form className="msg-composer" onSubmit={e => { e.preventDefault(); editing ? void saveEdit() : void send(); }}>
      {replyTo && !editing && <div className="msg-reply"><span><strong>{t("回复 {who}", { who: replyTo.sender })}</strong> {replyTo.body.slice(0, 80)}</span><button type="button" className="btn ghost sm" onClick={() => setReplyTo(null)} aria-label={t("取消回复")}>×</button></div>}
      {editing && <div className="msg-reply"><span>{t("正在编辑")}</span><button type="button" className="btn ghost sm" onClick={() => { setEditing(null); setDraft(""); }}>{t("取消")}</button></div>}
      {attachment && <div className="msg-reply"><span>📎 {attachment.filename} <span className="tiny muted">({fmtSize(attachment.size)})</span></span><button type="button" className="btn ghost sm" onClick={() => setAttachment(null)} aria-label={t("移除附件")}>×</button></div>}
      {mention.length > 0 && <div className="tiny muted">{t("提及：{who}", { who: mention.map(m => m.name).join("、") })} <button type="button" className="btn ghost sm" onClick={() => setMention([])}>×</button></div>}
      <div className="msg-composer-row">
        <textarea ref={textRef} className="input" rows={2} value={draft} placeholder={t("写消息…")} aria-label={t("写消息…")} disabled={busy} onChange={e => setDraft(e.target.value)} onKeyDown={onKey}
          onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} />
        <div className="col gap4">
          <button type="submit" className="btn primary" disabled={busy || uploading != null || (!draft.trim() && !attachment)}>{t(editing ? "保存" : "发送")}</button>
          {!editing && <button type="button" className="btn" onClick={() => fileRef.current?.click()} disabled={uploading != null}>{uploading != null ? `${Math.round(uploading * 100)}%` : t("附件")}</button>}
        </div>
      </div>
      <input ref={fileRef} type="file" hidden onChange={e => void pickFile(e.target.files?.[0] || null)} />
      <div className="row wrap gap8 tiny muted">
        <label className="check"><input type="checkbox" checked={enterSends} onChange={e => { setEnterSends(e.target.checked); try { localStorage.setItem("synwevo.msg.enter", e.target.checked ? "send" : "newline"); } catch { /* ignore */ } }} /><span>{t("Enter 发送（Shift+Enter 换行）")}</span></label>
        {conv.kind === "group" && !editing && <select className="input sm" value="" aria-label={t("提及成员")} onChange={e => { const m = conv.members.find(x => x.user_id === e.target.value); if (m && !mention.some(x => x.user_id === m.user_id)) { setMention([...mention, m]); setDraft(d => `${d}${d && !d.endsWith(" ") ? " " : ""}@${m.name} `); } }}>
          <option value="">{t("@ 提及…")}</option>{conv.members.filter(m => m.user_id !== uid).map(m => <option key={m.user_id} value={m.user_id}>{m.name}</option>)}</select>}
      </div>
    </form>}
  </>;
}

// ---------------------------------------------------------------- conversation info / group management
function ConversationInfo({ conv, uid, onClose, onChanged, onLeft }: { conv: Conversation; uid: string; onClose: () => void; onChanged: () => void; onLeft: () => void }) {
  const { t } = useI18n();
  const [error, setError] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const [name, setName] = useState(conv.name); const [q, setQ] = useState(""); const [people, setPeople] = useState<{ user_id: string; name: string }[]>([]);
  const manage = conv.kind === "group" && conv.me.role !== "member"; const owner = conv.kind === "group" && conv.me.role === "owner";
  const run = async (fn: () => Promise<unknown>, after?: () => void) => { setBusy(true); setError(null); try { await fn(); onChanged(); after?.(); } catch (e) { setError(e); } finally { setBusy(false); } };
  useEffect(() => { if (!manage || !q.trim()) { setPeople([]); return; } const timer = window.setTimeout(() => { get<{ items: { user_id: string; name: string }[] }>(`${M}/directory?organization_id=${encodeURIComponent(conv.organization_id)}&q=${encodeURIComponent(q.trim())}`).then(x => setPeople(x.items.filter(p => !conv.members.some(m => m.user_id === p.user_id)))).catch(() => setPeople([])); }, 250); return () => window.clearTimeout(timer); }, [q, manage, conv.organization_id, conv.members]);
  return <Drawer title={conv.kind === "group" ? t("群信息") : t("会话设置")} onClose={onClose} side="end">
    <FlowError error={error} />
    <div className="col gap8">
      <label className="check"><input type="checkbox" checked={conv.me.pinned} disabled={busy} onChange={e => void run(() => put(`${M}/conversations/${conv.id}/preferences`, { pinned: e.target.checked }))} /><span>{t("置顶")}</span></label>
      <label className="check"><input type="checkbox" checked={conv.me.muted} disabled={busy} onChange={e => void run(() => put(`${M}/conversations/${conv.id}/preferences`, { muted: e.target.checked }))} /><span>{t("静音（不计未读、不通知）")}</span></label>
      <div className="field"><label htmlFor="msg-notify">{t("通知")}</label><select id="msg-notify" className="input" value={conv.me.notify} disabled={busy} onChange={e => void run(() => put(`${M}/conversations/${conv.id}/preferences`, { notify: e.target.value }))}>
        <option value="all">{t("所有消息")}</option><option value="mentions">{t("仅提及我时")}</option><option value="none">{t("不通知")}</option></select></div>
      {conv.kind === "group" && <>
        <hr />
        {manage && !conv.archived ? <form className="field" onSubmit={e => { e.preventDefault(); void run(() => api(`${M}/conversations/${conv.id}`, { method: "PATCH", body: { name } })); }}><label htmlFor="msg-name">{t("群名称")}</label><div className="row gap4"><input id="msg-name" className="input" value={name} maxLength={80} onChange={e => setName(e.target.value)} /><button className="btn" disabled={busy || !name.trim() || name === conv.name}>{t("保存")}</button></div></form> : null}
        <p className="tiny muted">{t("新成员只能看到加入之后的消息；退出或被移除后不再能看到这个群。")}</p>
        <h3 className="small">{t("成员（{n}）", { n: conv.member_count })}</h3>
        <div className="msg-members">{conv.members.map(m => <div key={m.user_id} className="msg-member"><span>{m.name}{m.user_id === uid && <span className="tiny muted"> · {t("我")}</span>}<span className="tiny muted"> · {t(m.role === "owner" ? "群主" : m.role === "admin" ? "管理员" : "成员")}</span></span>
          {manage && m.user_id !== uid && m.role !== "owner" && <span className="row gap4">
            {owner && <button type="button" className="btn ghost sm" disabled={busy} onClick={() => void run(() => put(`${M}/conversations/${conv.id}/members/${m.user_id}/role`, { role: m.role === "admin" ? "member" : "admin" }))}>{t(m.role === "admin" ? "取消管理员" : "设为管理员")}</button>}
            {owner && <button type="button" className="btn ghost sm" disabled={busy} onClick={() => { if (window.confirm(t("把群主交接给 {who}？你将成为管理员。", { who: m.name }))) void run(() => post(`${M}/conversations/${conv.id}/transfer`, { user_id: m.user_id })); }}>{t("交接群主")}</button>}
            {(owner || m.role === "member") && <button type="button" className="btn ghost sm" disabled={busy} onClick={() => { if (window.confirm(t("把 {who} 移出群聊？", { who: m.name }))) void run(() => api(`${M}/conversations/${conv.id}/members/${m.user_id}`, { method: "DELETE" })); }}>{t("移出")}</button>}
          </span>}</div>)}</div>
        {manage && !conv.archived && <div className="field"><label htmlFor="msg-add">{t("添加成员（同组织）")}</label><input id="msg-add" className="input" value={q} placeholder={t("按姓名搜索…")} onChange={e => setQ(e.target.value)} />
          {people.length > 0 && <div className="msg-members">{people.map(p => <div key={p.user_id} className="msg-member"><span>{p.name}</span><button type="button" className="btn sm" disabled={busy} onClick={() => void run(() => post(`${M}/conversations/${conv.id}/members`, { user_ids: [p.user_id] }), () => setQ(""))}>{t("添加")}</button></div>)}</div>}</div>}
        <div className="org-actions">
          {owner && <button type="button" className="btn" disabled={busy} onClick={() => void run(() => post(`${M}/conversations/${conv.id}/archive`, { archived: !conv.archived }))}>{t(conv.archived ? "恢复群聊" : "归档群聊")}</button>}
          {!owner && <button type="button" className="btn danger" disabled={busy} onClick={() => { if (window.confirm(t("退出这个群聊？你将不再看到它的消息。"))) void run(() => post(`${M}/conversations/${conv.id}/leave`, {}), onLeft); }}>{t("退出群聊")}</button>}
          {owner && conv.member_count > 1 && <p className="tiny muted">{t("群主退出前需要先交接群主。")}</p>}
        </div>
      </>}
    </div>
  </Drawer>;
}

// ---------------------------------------------------------------- new conversation
function NewConversation({ kind, orgs, onClose, onCreated }: { kind: "dm" | "group"; orgs: Org[]; onClose: () => void; onCreated: (c: Conversation) => void }) {
  const { t } = useI18n();
  const [oid, setOid] = useState(orgs[0]?.id || ""); const [q, setQ] = useState(""); const [people, setPeople] = useState<{ user_id: string; name: string }[]>([]);
  const [chosen, setChosen] = useState<{ user_id: string; name: string }[]>([]); const [name, setName] = useState(""); const [error, setError] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  useEffect(() => { if (!oid) return; const timer = window.setTimeout(() => { get<{ items: { user_id: string; name: string }[] }>(`${M}/directory?organization_id=${encodeURIComponent(oid)}&q=${encodeURIComponent(q.trim())}`).then(x => setPeople(x.items)).catch(e => setError(e)); }, 200); return () => window.clearTimeout(timer); }, [oid, q]);
  const submit = async (e: React.FormEvent) => { e.preventDefault(); setBusy(true); setError(null);
    try { onCreated(kind === "dm" ? await post<Conversation>(`${M}/dm`, { organization_id: oid, user_id: chosen[0]?.user_id }) : await post<Conversation>(`${M}/groups`, { organization_id: oid, name, member_ids: chosen.map(c => c.user_id) })); }
    catch (err) { setError(err); setBusy(false); } };
  return <Modal title={t(kind === "dm" ? "新私聊" : "新群聊")} onClose={onClose}>
    <form onSubmit={submit} className="col gap8">
      <FlowError error={error} />
      <div className="field"><label htmlFor="msg-org">{t("组织")}</label><select id="msg-org" className="input" value={oid} onChange={e => { setOid(e.target.value); setChosen([]); }}>{orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select></div>
      {kind === "group" && <div className="field"><label htmlFor="msg-gname">{t("群名称")}</label><input id="msg-gname" className="input" value={name} maxLength={80} required onChange={e => setName(e.target.value)} /></div>}
      <div className="field"><label htmlFor="msg-who">{t(kind === "dm" ? "和谁" : "成员")}</label><input id="msg-who" className="input" value={q} placeholder={t("按姓名搜索…")} onChange={e => setQ(e.target.value)} /></div>
      {chosen.length > 0 && <div className="row wrap gap4">{chosen.map(c => <span key={c.user_id} className="badge">{c.name} <button type="button" className="btn ghost sm" aria-label={t("移除")} onClick={() => setChosen(chosen.filter(x => x.user_id !== c.user_id))}>×</button></span>)}</div>}
      <div className="msg-members" style={{ maxHeight: 220, overflow: "auto" }}>{people.filter(p => !chosen.some(c => c.user_id === p.user_id)).map(p => <div key={p.user_id} className="msg-member"><span>{p.name}</span><button type="button" className="btn sm" onClick={() => setChosen(kind === "dm" ? [p] : [...chosen, p])}>{t("选择")}</button></div>)}
        {!people.length && <p className="tiny muted">{t("没有匹配的成员。只能和同一组织的活跃成员对话。")}</p>}</div>
      <div className="org-actions"><button className="btn primary" disabled={busy || !oid || !chosen.length || (kind === "group" && !name.trim())}>{t(kind === "dm" ? "开始私聊" : "创建群聊")}</button><button type="button" className="btn" onClick={onClose}>{t("取消")}</button></div>
    </form>
  </Modal>;
}
