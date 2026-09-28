/** A conversation: what the server has stored, grouped and separated for reading; the way the
 *  view moves (older history on scroll-up without a jump, a pill for what arrived while the
 *  reader was up in the history, back to the latest after a jump to an old message, the reading
 *  position kept when the person comes back); and sending, with a message shown as pending until
 *  the server has it and as failed — with a retry that reuses the same client id — when it has not.
 *
 *  Server facts shown as such: who is typing (only while the hint is unexpired), read receipts
 *  (only when both sides turned them on; never "delivered"), recall markers, a manager's
 *  removal, delete-for-me with a five-second undo. The stream is a hint: every (re)connect and
 *  every `resync` goes back to the history API with the last sequence this tab knows.
 *
 *  A direct message this reader blocked shows 「你已屏蔽对方，解除后才能发送」 with 解除屏蔽 in place of
 *  the composer (§7.18). A reader who was blocked is not told in advance: their first send is
 *  refused with the neutral 「暂时无法向对方发送消息」, shown on the failed message and in place of the
 *  composer, with a way to try again. */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, get, post, put } from "@synwevo/auth-client";
import { scrollBehavior, useDialog, useI18n } from "@synwevo/ui";
import { M, attachmentUrl, groupAvatarUrl, sendTyping } from "../api";
import { MENTION_ALL } from "../types";
import type { Attachment, Capabilities, Conversation, Live, Message, Presence, ReadBy, Settings, TypingState } from "../types";
import { draftKey, fmtSize, layout, maxSeq, mergeMessages, newClientId, receiptMark } from "../lib/thread";
import { dayLabel, formatDayLabel } from "../lib/time";
import { FlowError, Loading } from "../flow";
import { Composer, type ComposerHandle } from "./Composer";
import { Avatar, MessageItem } from "./MessageItem";
import { Lightbox } from "./Lightbox";
import { ReportDialog } from "./dialogs";
import { IcArrowDown, IcArrowUp, IcBack, IcInfo } from "./icons";

const PAGE = 50;
const NEAR_TOP = 240;
const NEAR_BOTTOM = 40;
const FAR = 600;
const UNDO_MS = 5000;

/** Where the reader was in each thread, for the length of this page: the loaded messages and the
 *  scroll position, so coming back shows the same place instead of the end. */
const positions = new Map<string, { items: Message[]; hasMore: boolean; scrollTop: number; atBottom: boolean }>();

export interface Jump { seq: number; n: number }

export function Thread({ conv, uid, live, narrow, coarse, caps, settings, presence, typing, infoOpen, onBack, onOpenInfo, onChanged, onForward, bind, jump, onJumped }: {
  conv: Conversation; uid: string; live: Live; narrow: boolean; coarse: boolean; caps: Capabilities | null; settings: Settings | null;
  presence: Record<string, Presence>; typing: TypingState[]; infoOpen: boolean;
  onBack: () => void; onOpenInfo: () => void; onChanged: () => void; onForward: (m: Message) => void;
  bind: (fns: { onEvent: (ev: any) => void; resync: () => void }) => void; jump: Jump | null; onJumped: () => void;
}) {
  const { t, locale } = useI18n();
  const { confirm } = useDialog();
  const saved = positions.get(conv.id);
  const [items, setItems] = useState<Message[] | null>(saved?.items ?? null);
  const [hasMore, setHasMore] = useState(saved?.hasMore ?? false);
  const [detached, setDetached] = useState(false);          // showing an old window, not the latest
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [draft, setDraftState] = useState(() => { try { return localStorage.getItem(draftKey(uid, conv.id)) || ""; } catch { return ""; } });
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [editing, setEditing] = useState<Message | null>(null);
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState(0);
  const [far, setFar] = useState(false);
  const [unreadPill, setUnreadPill] = useState(false);
  const [target, setTarget] = useState<number | null>(null);
  const [lightbox, setLightbox] = useState<Attachment | null>(null);
  const [reporting, setReporting] = useState<Message | null>(null);
  const [announceOpen, setAnnounceOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [liveText, setLiveText] = useState("");
  const [readBy, setReadBy] = useState<ReadBy[] | null>(conv.read_by ?? null);
  const [undo, setUndo] = useState<{ message: Message } | null>(null);
  // the server refused a send in this direct message with the neutral `blocked` answer (§7.18)
  const [refused, setRefused] = useState(false);
  const [unblocking, setUnblocking] = useState(false);
  // the reader's position when the thread was opened: captured once, so the unread divider stays
  // where it was for as long as they read; opening the thread again places it afresh from the
  // server's read position (or not at all, when nothing is unread)
  const [lastRead] = useState<number | null>(() => (conv.unread > 0 ? conv.me.last_read_seq : null));
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<ComposerHandle>(null);
  const atBottom = useRef(saved ? saved.atBottom : true);
  const lastSeq = useRef(saved ? maxSeq(saved.items) : 0);
  const anchor = useRef<{ height: number; top: number } | null>(null);
  const pendingJump = useRef<number | null>(null);
  const restore = useRef<number | null>(saved && !saved.atBottom ? saved.scrollTop : null);
  const freshIds = useRef(new Set<string>());
  const readTimer = useRef<number | null>(null);
  const undoTimer = useRef<number | null>(null);
  const draftBackup = useRef("");
  const detachedRef = useRef(false);
  detachedRef.current = detached;
  // true from the moment a jump replaces the content until the browser has applied the new scroll
  // position: replacing tall content clamps the old scrollTop and fires a scroll event that would
  // read as "at the bottom" and pull the newer history in at once
  const settling = useRef(false);

  const setDraft = useCallback((v: string) => setDraftState(v), []);
  useEffect(() => { try { if (draft) localStorage.setItem(draftKey(uid, conv.id), draft); else localStorage.removeItem(draftKey(uid, conv.id)); } catch { /* storage unavailable */ } }, [draft, uid, conv.id]);
  useEffect(() => { if (conv.read_by !== undefined) setReadBy(conv.read_by); }, [conv.read_by]);

  const merge = useCallback((incoming: Message[]) => setItems(cur => {
    const out = mergeMessages(cur || [], incoming);
    const max = maxSeq(out);
    if (max > lastSeq.current) lastSeq.current = max;
    return out;
  }), []);

  const scrollToBottom = useCallback((smooth = false) => {
    const el = scrollRef.current; if (!el) return;
    if (smooth) el.scrollTo({ top: el.scrollHeight, behavior: scrollBehavior() }); else el.scrollTop = el.scrollHeight;
    atBottom.current = true; setFresh(0); setFar(false);
  }, []);

  const load = useCallback(async () => {
    const page = await get<{ items: Message[]; has_more: boolean; read_by?: ReadBy[] | null }>(`${M()}/conversations/${conv.id}/messages?limit=${PAGE}`);
    lastSeq.current = maxSeq(page.items);
    setItems(page.items); setHasMore(page.has_more); setDetached(false); setFresh(0);
    if (page.read_by !== undefined) setReadBy(page.read_by);
    atBottom.current = true;
    requestAnimationFrame(() => { scrollToBottom(); });
  }, [conv.id, scrollToBottom]);

  const resync = useCallback(async () => {
    if (!lastSeq.current || detachedRef.current) return;
    try {
      const page = await get<{ items: Message[]; read_by?: ReadBy[] | null }>(`${M()}/conversations/${conv.id}/messages?after=${lastSeq.current}&limit=200`);
      if (page.read_by !== undefined) setReadBy(page.read_by);
      if (page.items.length) {
        for (const m of page.items) if (m.sender_id !== uid) freshIds.current.add(m.id);
        merge(page.items);
        if (!atBottom.current) setFresh(n => n + page.items.filter(m => m.sender_id !== uid).length);
      }
    } catch { /* next tick */ }
  }, [conv.id, merge, uid]);

  useEffect(() => {
    if (saved) { void resync(); return; }
    load().catch(e => setError(e));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);
  useEffect(() => () => {
    const el = scrollRef.current;
    if (el && items) positions.set(conv.id, { items, hasMore, scrollTop: el.scrollTop, atBottom: atBottom.current });
  });   // runs on every render so the latest values are captured at unmount

  /** A message this reader deleted for themselves (from any device) leaves the list at once. */
  const dropHidden = useCallback((mid: string) => setItems(cur => (cur || []).filter(m => m.id !== mid)), []);
  const fetchOne = useCallback(async (seq: number) => {
    try { const page = await get<{ items: Message[] }>(`${M()}/conversations/${conv.id}/messages?before=${seq + 1}&limit=1`); merge(page.items); } catch { /* not visible */ }
  }, [conv.id, merge]);

  useEffect(() => {
    bind({
      onEvent: ev => {
        if (ev.conversation_id !== conv.id) return;
        if (ev.type === "message" || ev.type === "message_updated") {
          const m: Message = ev.message;
          if (ev.type === "message" && m.sender_id !== uid) {
            freshIds.current.add(m.id);
            setLiveText(`${m.sender}: ${m.body || (m.attachment ? (m.attachment.voice ? t("语音消息") : t("[附件]")) : "")}`);
            if (!atBottom.current || detachedRef.current) setFresh(n => n + 1);
          }
          if (!detachedRef.current) merge([m]);
        }
        if (ev.type === "message_hidden") { if (ev.hidden) dropHidden(ev.message_id); else void fetchOne(ev.seq); }
        if (ev.type === "receipt") setReadBy(cur => (cur === null ? cur : [...(cur || []).filter(r => r.user_id !== ev.user_id), { user_id: ev.user_id, last_read_seq: ev.last_read_seq, at: ev.at ?? null }]));
        if (ev.type === "sync_recalled") {
          const gone: { id: string; how: string }[] = ev.items || [];
          setItems(cur => (cur || []).map(m => { const g = gone.find(x => x.id === m.id); return g ? { ...m, deleted: true, body: "", attachment: null, reactions: [], recalled: g.how === "recalled", system: g.how === "recalled" ? { event: "recalled", actor: m.sender_id } : g.how === "removed" ? { event: "removed", actor: m.removed_by?.user_id } : m.system } : m; }));
        }
        if (ev.type === "conversation") void resync();     // a rename or a member change also wrote a system line
      },
      resync: () => { void resync(); },
    });
  }, [bind, conv.id, merge, resync, uid, t, dropHidden, fetchOne]);

  // ---- keep the view where the reader is: bottom when at the bottom, anchored after older
  //      history, at the saved place when coming back, at the target after a jump
  useLayoutEffect(() => {
    const el = scrollRef.current; if (!el || !items) return;
    if (anchor.current) { el.scrollTop = el.scrollHeight - anchor.current.height + anchor.current.top; anchor.current = null; return; }
    if (restore.current !== null) { el.scrollTop = restore.current; restore.current = null; onScroll(); return; }
    if (pendingJump.current !== null) {
      const seq = pendingJump.current; pendingJump.current = null;
      const node = el.querySelector<HTMLElement>(`#msg-${seq}`);
      if (node) { node.scrollIntoView({ block: "center" }); flash(seq, node); }      // instant: the flash is the feedback
      atBottom.current = false;
      requestAnimationFrame(() => { settling.current = false; onScroll(); });
      return;
    }
    if (atBottom.current && !detached) el.scrollTop = el.scrollHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);
  useEffect(() => {
    // the unread divider is above the first screen: offer a way up to it
    const el = scrollRef.current; if (!el || !items || lastRead === null) return;
    const div = el.querySelector<HTMLElement>(".msg-unread-div");
    setUnreadPill(!!div && div.offsetTop < el.scrollTop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items === null]);

  const pendingRead = useRef<number | null>(null);
  const markRead = useCallback(() => {
    if (!items || !items.length || document.visibilityState !== "visible" || !atBottom.current || detachedRef.current) return;
    const last = lastSeq.current;
    if (last <= conv.me.last_read_seq && !conv.me.marked_unread) return;
    if (readTimer.current != null) window.clearTimeout(readTimer.current);
    pendingRead.current = last;
    readTimer.current = window.setTimeout(() => { readTimer.current = null; pendingRead.current = null; post(`${M()}/conversations/${conv.id}/read`, { seq: last }).then(() => onChanged()).catch(() => undefined); }, 400);
  }, [items, conv.id, conv.me.last_read_seq, conv.me.marked_unread, onChanged]);
  useEffect(() => { markRead(); return () => { if (readTimer.current != null) window.clearTimeout(readTimer.current); }; }, [markRead]);
  // leaving the thread while a read report is still waiting: report it now rather than drop it
  // (a conversation opened and left within half a second is still a read one)
  useEffect(() => () => {
    if (pendingRead.current !== null) { const seq = pendingRead.current; pendingRead.current = null; post(`${M()}/conversations/${conv.id}/read`, { seq }).then(() => onChanged()).catch(() => undefined); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conv.id]);
  useEffect(() => { const on = () => markRead(); document.addEventListener("visibilitychange", on); return () => document.removeEventListener("visibilitychange", on); }, [markRead]);

  const loadOlder = useCallback(async () => {
    const el = scrollRef.current; const first = items?.find(m => m.seq)?.seq;
    if (!el || !first || loadingOlder || !hasMore) return;
    setLoadingOlder(true);
    try {
      const page = await get<{ items: Message[]; has_more: boolean }>(`${M()}/conversations/${conv.id}/messages?before=${first}&limit=${PAGE}`);
      anchor.current = { height: el.scrollHeight, top: el.scrollTop };      // the anchor stays where the reader was
      merge(page.items); setHasMore(page.has_more);
    } catch (e) { setError(e); } finally { setLoadingOlder(false); }
  }, [items, loadingOlder, hasMore, conv.id, merge]);
  const loadNewer = useCallback(async () => {
    if (!detached || !items) return;
    const from = maxSeq(items);
    try {
      const page = await get<{ items: Message[]; has_more: boolean }>(`${M()}/conversations/${conv.id}/messages?after=${from}&limit=${PAGE}`);
      setItems(cur => mergeMessages(cur || [], page.items));
      if (!page.has_more) { setDetached(false); lastSeq.current = Math.max(lastSeq.current, maxSeq(page.items), from); }
    } catch (e) { setError(e); }
  }, [detached, items, conv.id]);

  const onScroll = () => {
    const el = scrollRef.current; if (!el || settling.current) return;
    const fromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const wasAtBottom = atBottom.current;
    atBottom.current = fromBottom < NEAR_BOTTOM;
    if (atBottom.current) { setFresh(0); if (detached) void loadNewer(); if (!wasAtBottom) markRead(); }
    setFar(fromBottom > FAR);
    if (el.scrollTop < NEAR_TOP && hasMore && !loadingOlder) void loadOlder();
    if (unreadPill) { const div = el.querySelector<HTMLElement>(".msg-unread-div"); if (!div || div.offsetTop >= el.scrollTop) setUnreadPill(false); }
  };

  const flash = (seq: number, node?: HTMLElement | null) => {
    setTarget(seq);
    (node || scrollRef.current?.querySelector<HTMLElement>(`#msg-${seq}`))?.focus({ preventScroll: true });
    window.setTimeout(() => setTarget(cur => (cur === seq ? null : cur)), 2200);
  };
  const jumpTo = useCallback(async (seq: number) => {
    const el = scrollRef.current;
    const node = el?.querySelector<HTMLElement>(`#msg-${seq}`);
    if (node) { node.scrollIntoView({ block: "center", behavior: scrollBehavior() }); flash(seq, node); return; }
    try {
      const [before, after] = await Promise.all([
        get<{ items: Message[]; has_more: boolean }>(`${M()}/conversations/${conv.id}/messages?before=${seq + 1}&limit=30`),
        get<{ items: Message[]; has_more: boolean }>(`${M()}/conversations/${conv.id}/messages?after=${seq}&limit=20`),
      ]);
      if (!before.items.some(m => m.seq === seq)) { setError(new ApiError(404, "no_message", t("这条消息已不在你能看到的范围内。"))); return; }
      atBottom.current = false;
      settling.current = true;
      pendingJump.current = seq;
      setDetached(after.has_more);
      setHasMore(before.has_more);
      setItems(mergeMessages(before.items, after.items));
      if (!after.has_more) lastSeq.current = Math.max(lastSeq.current, maxSeq(after.items), seq);
    } catch (e) { setError(e); }
  }, [conv.id, t]);
  useEffect(() => { if (jump) { void jumpTo(jump.seq); onJumped(); } }, [jump?.n]);   // eslint-disable-line react-hooks/exhaustive-deps
  const backToLatest = () => { void load().catch(e => setError(e)); };

  // ---- sending
  const deliver = useCallback(async (pending: Message) => {
    try {
      const out = await post<{ message: Message }>(`${M()}/conversations/${conv.id}/messages`, {
        body: pending.body, client_id: pending.client_id,
        ...(pending.reply_to ? { reply_to: pending.reply_to } : {}), ...(pending.attachment ? { attachment_id: pending.attachment.id } : {}),
        ...(pending.mentions.length ? { mention_ids: pending.mentions } : {}), ...(pending.mention_all ? { mention_all: true } : {}),
      });
      merge([out.message]); setRefused(false); onChanged();
    } catch (e) {
      const why = e instanceof ApiError ? e.message : String(e);
      if (e instanceof ApiError && e.code === "blocked") { if (e.detail?.by_me) onChanged(); else setRefused(true); }
      setItems(cur => (cur || []).map(m => (m.id === pending.id ? { ...m, pending: false, failed: why } : m)));
    }
  }, [conv.id, merge, onChanged]);
  const queue = useCallback((body: string, att: Attachment | null, mention_ids: string[], reply: Message | null) => {
    const client_id = newClientId();
    const pending: Message = { id: `pending-${client_id}`, conversation_id: conv.id, seq: 0, sender_id: uid, sender: t("我"), kind: "text", body, reply_to: reply?.id || null,
      mentions: mention_ids.filter(x => x !== MENTION_ALL), mention_all: mention_ids.includes(MENTION_ALL),
      edited_at: null, deleted: false, created_at: Date.now() / 1000, client_id, reactions: [], attachment: att, pending: true,
      reply: reply ? { id: reply.id, seq: reply.seq, sender: reply.sender, body: reply.body, deleted: reply.deleted, recalled: reply.recalled } : undefined };
    setItems(cur => [...(cur || []), pending]);
    atBottom.current = true;
    void deliver(pending);
  }, [conv.id, uid, t, deliver]);
  const send = useCallback((body: string, attachments: Attachment[], mentionIds: string[]) => {
    const text = body.trim();
    if (!text && !attachments.length) return;
    const go = () => {
      const [first, ...rest] = attachments;
      queue(text, first || null, mentionIds, replyTo);
      for (const a of rest) queue("", a, [], null);          // each further file is its own message, as in every messenger
      setReplyTo(null);
    };
    if (detachedRef.current) void load().then(go).catch(e => setError(e)); else go();
  }, [queue, replyTo, load]);
  const retry = (m: Message) => { setItems(cur => (cur || []).map(x => (x.id === m.id ? { ...x, pending: true, failed: undefined } : x))); atBottom.current = true; void deliver({ ...m, pending: true, failed: undefined }); };
  const discard = (m: Message) => setItems(cur => (cur || []).filter(x => x.id !== m.id));
  const onTyping = useCallback((state: "start" | "stop") => { if (settings === null || settings.show_presence) sendTyping(conv.id, state); }, [conv.id, settings]);

  const startEdit = (m: Message) => { draftBackup.current = draft; setReplyTo(null); setEditing(m); setDraftState(m.body); composerRef.current?.focus(); };
  const cancelEdit = () => { setEditing(null); setDraftState(draftBackup.current); draftBackup.current = ""; };
  const saveEdit = async (body: string) => {
    if (!editing) return; setBusy(true);
    try { merge([await api(`${M()}/messages/${editing.id}`, { method: "PATCH", body: { body: body.trim() } })]); setEditing(null); setDraftState(draftBackup.current); draftBackup.current = ""; }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  /** Delete for me (§7.6): the row goes at once; the server's answer is `hidden: true`; five
   *  seconds to undo, which asks the server to unhide and puts the row back. */
  const deleteForMe = async (m: Message) => {
    try {
      await api(`${M()}/messages/${m.id}`, { method: "DELETE" });
      dropHidden(m.id);
      if (undoTimer.current != null) window.clearTimeout(undoTimer.current);
      setUndo({ message: m });
      undoTimer.current = window.setTimeout(() => setUndo(null), UNDO_MS);
    } catch (e) { setError(e); }
  };
  const undoDelete = async () => {
    if (!undo) return;
    const { message } = undo; setUndo(null);
    if (undoTimer.current != null) window.clearTimeout(undoTimer.current);
    try { await post(`${M()}/messages/${message.id}/unhide`, {}); merge([message]); } catch (e) { setError(e); }
  };
  const recall = async (m: Message) => {
    if (!await confirm(t("撤回消息"), t("撤回这条消息？所有人都会看到「你撤回了一条消息」。"), t("撤回"), true)) return;
    try { merge([await post(`${M()}/messages/${m.id}/recall`, {})]); } catch (e) { setError(e); }
  };
  const remove = async (m: Message) => {
    if (!await confirm(t("移除消息"), t("对所有人移除这条消息？标记会写明由你移除，此操作不能撤销。"), t("移除"), true)) return;
    try { merge([await post(`${M()}/messages/${m.id}/remove`, {})]); } catch (e) { setError(e); }
  };
  const react = async (m: Message, emoji: string) => {
    const mine = m.reactions.find(r => r.emoji === emoji)?.users.includes(uid);
    try { merge([await put(`${M()}/messages/${m.id}/reactions`, { emoji, on: !mine })]); } catch (e) { setError(e); }
  };
  const reactions = caps?.reactions?.length ? caps.reactions : ["👍", "❤️", "😂", "🎉", "👀", "✅", "❓", "🙏"];

  const nameOf = useCallback((id: string) => (id === uid ? t("我") : conv.members.find(x => x.user_id === id)?.name || t("某成员")), [uid, conv.members, t]);
  const systemText = (m: Message) => {
    const s = m.system || {}; const who = nameOf; const list = (ids: string[]) => (ids || []).map(who).join("、");
    switch (s.event) {
      case "created": return t("{actor} 创建了群聊", { actor: who(s.actor) });
      case "added": return s.history === "all" ? t("{actor} 把 {who} 加入了群聊（可看到全部历史）", { actor: who(s.actor), who: list(s.added) }) : t("{actor} 把 {who} 加入了群聊", { actor: who(s.actor), who: list(s.added) });
      case "removed": return m.kind === "system" ? t("{actor} 移除了 {who}", { actor: who(s.actor), who: list(s.removed) }) : t("{actor} 移除了一条消息", { actor: m.removed_by?.name || who(s.actor) });
      case "recalled": return s.actor === uid ? t("你撤回了一条消息") : t("{actor} 撤回了一条消息", { actor: who(s.actor) });
      case "left": return t("{actor} 退出了群聊", { actor: who(s.actor) });
      case "renamed": return t("{actor} 把群名改为「{name}」", { actor: who(s.actor), name: s.name }); case "transferred": return t("{actor} 把群主交接给了 {who}", { actor: who(s.actor), who: who(s.to) });
      case "description": return s.cleared ? t("{actor} 清除了群描述", { actor: who(s.actor) }) : t("{actor} 更新了群描述", { actor: who(s.actor) });
      case "archived": return t("{actor} 归档了群聊", { actor: who(s.actor) }); case "unarchived": return t("{actor} 恢复了群聊", { actor: who(s.actor) });
      case "announcement": return s.cleared ? t("{actor} 清除了群公告", { actor: who(s.actor) }) : t("{actor} 更新了群公告", { actor: who(s.actor) });
      case "avatar": return s.cleared ? t("{actor} 移除了群头像", { actor: who(s.actor) }) : t("{actor} 更换了群头像", { actor: who(s.actor) });
      default: return "";
    }
  };

  // ---- drag & drop anywhere on the thread
  const onDragOver = (e: React.DragEvent) => { if (Array.from(e.dataTransfer.types).includes("Files") && !conv.archived) { e.preventDefault(); setDragging(true); } };
  const onDrop = (e: React.DragEvent) => { e.preventDefault(); setDragging(false); composerRef.current?.addFiles(Array.from(e.dataTransfer.files || [])); };

  const rows = useMemo(() => (items ? layout(items, uid, lastRead) : []), [items, uid, lastRead]);
  const mark = useMemo(() => (items ? receiptMark(items, uid, readBy, nameOf) : null), [items, uid, readBy, nameOf]);
  const actions = useMemo(() => ({
    onReply: (m: Message) => { setEditing(null); setReplyTo(m); composerRef.current?.focus(); }, onEdit: startEdit, onDeleteForMe: deleteForMe, onRecall: recall, onRemove: remove,
    onForward, onReport: setReporting, onReact: react, onRetry: retry, onDiscard: discard, onJump: (seq: number) => void jumpTo(seq), onOpenImage: setLightbox,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [jumpTo, draft, conv.members, uid, items, onForward]);
  const peerPresence: Presence = conv.kind === "dm" && conv.peer ? (presence[conv.peer.user_id] !== undefined ? presence[conv.peer.user_id] : (conv.peer.presence ?? null)) : null;
  const typingNames = typing.map(x => x.name);
  const blockedByMe = conv.kind === "dm" && !!conv.peer?.blocked_by_me;
  const unblockPeer = async () => {
    if (!conv.peer) return;
    setUnblocking(true);
    try { await api(`${M()}/blocks/${encodeURIComponent(conv.peer.user_id)}`, { method: "DELETE" }); onChanged(); } catch (e) { setError(e); } finally { setUnblocking(false); }
  };

  return <div className={`msg-thread-inner ${dragging ? "dragging" : ""}`} onDragOver={onDragOver} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
    <header className="msg-thread-h">
      {narrow && <button type="button" className="btn ghost sm icon msg-back" onClick={onBack} aria-label={t("返回会话列表")}><IcBack /></button>}
      <Avatar id={conv.kind === "dm" ? (conv.peer?.user_id || conv.id) : conv.id} name={conv.name} kind={conv.kind} size={32} presence={peerPresence} src={groupAvatarUrl(conv)} />
      <div className="msg-thread-title">
        <h1 className="msg-thread-name">{conv.name}</h1>
        <div className="msg-thread-meta">
          {conv.kind === "group" && <span>{t("{n} 人", { n: conv.member_count })}</span>}
          {peerPresence && <span className={`msg-presence-text ${peerPresence}`}>{peerPresence === "online" ? t("在线") : peerPresence === "away" ? t("离开") : t("离线")}</span>}
          {conv.archived && <span>{t("已归档")}</span>}
          <span className={`msg-status ${live}`} role="status">{t(live === "live" ? "实时" : live === "polling" ? "轮询中" : "连接中")}</span>
        </div>
      </div>
      <button type="button" className={`btn ghost sm icon ${infoOpen ? "active" : ""}`} onClick={onOpenInfo} aria-label={t(conv.kind === "group" ? "群信息" : "会话设置")} title={t(conv.kind === "group" ? "群信息" : "会话设置")} aria-pressed={infoOpen}><IcInfo /></button>
    </header>
    {conv.announcement && <div className={`msg-announce ${announceOpen ? "open" : ""}`} role="note">
      <span className="badge info">{t("公告")}</span>
      <span className="msg-announce-text">{conv.announcement}</span>
      {conv.announcement.length > 80 && <button type="button" className="btn ghost sm" aria-expanded={announceOpen} onClick={() => setAnnounceOpen(o => !o)}>{t(announceOpen ? "收起" : "展开")}</button>}
    </div>}
    <FlowError error={error} />
    <div className="msg-scroll-wrap">
      <div className="msg-scroll" ref={scrollRef} onScroll={onScroll} role="log" aria-live="off" aria-label={t("消息记录")} tabIndex={0}>
        {items === null ? <Loading /> : <>
          {hasMore ? <div className="msg-history-top">{loadingOlder ? <span className="tiny muted" role="status">{t("正在加载更早的消息…")}</span> : <button type="button" className="btn ghost sm" onClick={() => void loadOlder()}>{t("加载更早的消息")}</button>}</div>
            : conv.kind === "group" && conv.me.joined_seq > 1 ? <div className="msg-system">{t("你能看到加入之后的消息。")}</div>
            : items.length === 0 ? <div className="msg-empty"><p>{t("还没有消息。")}</p><p className="tiny muted">{t("发一条消息开始对话。")}</p></div> : null}
          {rows.map(r => {
            switch (r.type) {
              case "day": return <div key={r.key} className="msg-day" role="separator" aria-label={formatDayLabel(dayLabel(r.ts), locale, t)}><span>{formatDayLabel(dayLabel(r.ts), locale, t)}</span></div>;
              case "unread": return <div key={r.key} className="msg-unread-div" role="separator" aria-label={t("以下为未读")}><span>{t("以下为未读")}</span></div>;
              case "system": return <div key={r.key} className={`msg-system ${r.message.recalled || r.message.removed_by ? "marker" : ""}`} id={r.message.seq ? `msg-${r.message.seq}` : undefined} data-seq={r.message.seq || undefined}>{systemText(r.message)}</div>;
              case "message": return <MessageItem key={r.key} m={r.message} head={r.head} tail={r.tail} own={r.own} uid={uid} conv={conv} caps={caps} reactions={reactions} coarse={coarse}
                highlight={target === r.message.seq} fresh={freshIds.current.has(r.message.id)} readMark={mark && mark.seq === r.message.seq ? { count: mark.count, who: mark.who } : null} actions={actions} />;
            }
          })}
          {detached && <div className="msg-history-top"><button type="button" className="btn ghost sm" onClick={() => void loadNewer()}>{t("加载更新的消息")}</button></div>}
        </>}
      </div>
      {unreadPill && <button type="button" className="msg-pill top" onClick={() => { const div = scrollRef.current?.querySelector<HTMLElement>(".msg-unread-div"); div?.scrollIntoView({ block: "start", behavior: scrollBehavior() }); setUnreadPill(false); }}><IcArrowUp size={14} /> {t("{n} 条未读", { n: conv.unread })}</button>}
      {detached ? <button type="button" className="msg-pill bottom" onClick={backToLatest}><IcArrowDown size={14} /> {fresh > 0 ? t("{n} 条新消息 · 回到最新", { n: fresh }) : t("回到最新")}</button>
        : fresh > 0 ? <button type="button" className="msg-pill bottom" onClick={() => scrollToBottom(true)}><IcArrowDown size={14} /> {t("{n} 条新消息", { n: fresh })}</button>
        : far ? <button type="button" className="msg-pill bottom round" onClick={() => scrollToBottom(true)} aria-label={t("回到底部")}><IcArrowDown size={16} /></button> : null}
      {dragging && <div className="msg-drop" aria-hidden="true">{t("松开以添加附件")}</div>}
    </div>
    <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{liveText}</div>
    {undo && <div className="msg-undo" role="status"><span>{t("已删除这条消息（仅对你）。")}</span><button type="button" className="btn sm" onClick={() => void undoDelete()}>{t("撤销")}</button></div>}
    {typingNames.length > 0 && <div className="msg-typing" role="status" aria-live="polite">
      <span className="msg-typing-dots" aria-hidden="true"><i /><i /><i /></span>
      {typingNames.length <= 2 ? t("{who} 正在输入…", { who: typingNames.join("、") }) : t("{who} 等 {n} 人正在输入…", { who: typingNames.slice(0, 2).join("、"), n: typingNames.length })}
    </div>}
    {blockedByMe && !conv.archived ? <div className="msg-composer msg-composer-off msg-blocked-note" role="note">
        <span>{t("你已屏蔽对方，解除后才能发送")}</span>
        <button type="button" className="btn sm" disabled={unblocking} onClick={() => void unblockPeer()}>{t("解除屏蔽")}</button>
      </div>
      : refused && !conv.archived ? <div className="msg-composer msg-composer-off msg-blocked-note" role="note">
        <span>{t("暂时无法向对方发送消息")}</span>
        <button type="button" className="btn ghost sm" onClick={() => setRefused(false)}>{t("重试")}</button>
      </div>
      : <Composer ref={composerRef} conv={conv} uid={uid} caps={caps} draft={draft} setDraft={setDraft} replyTo={replyTo} onCancelReply={() => setReplyTo(null)}
        editing={editing} onCancelEdit={cancelEdit} onSaveEdit={saveEdit} onSend={send} onTyping={onTyping} busy={busy} />}
    {lightbox && <Lightbox src={attachmentUrl(lightbox.id, true)} downloadHref={attachmentUrl(lightbox.id)} name={lightbox.filename} size={fmtSize(lightbox.size)} onClose={() => setLightbox(null)} />}
    {reporting && <ReportDialog message={reporting} onClose={() => setReporting(null)} />}
  </div>;
}
