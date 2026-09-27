/** One message in the thread: the bubble with its quote, text, cards, attachment and reactions;
 *  the group head (avatar, name, time) when it starts a group; the footer facts the server has
 *  (time, edited, stored) or this tab has (pending, failed); and the actions — quick reaction,
 *  reply, and a menu reached by the ⋯ button, a right-click or a long press. Nothing here says
 *  "delivered", "read by them" or "typing": the server has no such fact. */
import { useCallback, useRef, useState, type ReactNode } from "react";
import { useI18n, useToast } from "@synwevo/ui";
import type { Attachment, Conversation, Message } from "../types";
import { URL_RE, colorSlot, initials, linksIn } from "../lib/thread";
import { clock, fullDate } from "../lib/time";
import { ActionMenu, Popover, type ActionItem } from "./layers";
import { AppCardView, AttachmentCard, LinkCard } from "./cards";
import { IcCheck, IcClock, IcCopy, IcEdit, IcEmoji, IcFlag, IcMore, IcReply, IcTrash, IcWarn } from "./icons";

const LONG_PRESS_MS = 500;

export function Avatar({ id, name, kind = "dm", size = 32 }: { id: string; name: string; kind?: "dm" | "group"; size?: number }) {
  return <span className={`msg-avatar ${kind} slot-${colorSlot(id)}`} aria-hidden="true" style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }}>
    {kind === "group" ? <svg width={Math.round(size * 0.55)} height={Math.round(size * 0.55)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="9" cy="8" r="3.2" /><circle cx="17" cy="9" r="2.6" /><path d="M3 19a6 6 0 0112 0" /><path d="M15 19a4.5 4.5 0 016-4" /></svg> : initials(name)}
  </span>;
}

export function linkify(text: string): ReactNode[] {
  return text.split(URL_RE).map((p, i) => /^https?:\/\//.test(p)
    ? <a key={i} href={p} target="_blank" rel="noopener noreferrer nofollow">{p}</a>
    : <span key={i}>{p}</span>);
}

export interface MessageActions {
  onReply: (m: Message) => void; onEdit: (m: Message) => void; onDelete: (m: Message) => void; onReport: (m: Message) => void;
  onReact: (m: Message, emoji: string) => void; onRetry: (m: Message) => void; onDiscard: (m: Message) => void;
  onJump: (seq: number) => void; onOpenImage: (a: Attachment) => void;
}

export function MessageItem({ m, head, tail, own, uid, conv, reactions, coarse, highlight, fresh, actions }: {
  m: Message; head: boolean; tail: boolean; own: boolean; uid: string; conv: Conversation; reactions: string[]; coarse: boolean;
  highlight: boolean; fresh: boolean; actions: MessageActions;
}) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const bubble = useRef<HTMLDivElement>(null);
  const moreBtn = useRef<HTMLButtonElement>(null);
  const reactBtn = useRef<HTMLButtonElement>(null);
  const [menu, setMenu] = useState<null | "button" | "bubble">(null);
  const [picker, setPicker] = useState(false);
  const press = useRef<number | null>(null);
  const moved = useRef(false);

  const canAct = !m.deleted && !m.pending && !m.failed;
  const canEdit = own && canAct;
  const canDelete = canAct && (own || (conv.kind === "group" && conv.me.role !== "member"));
  const copy = useCallback(async () => {
    try { await navigator.clipboard.writeText(m.body); toast(t("已复制消息内容"), "ok"); }
    catch { toast(t("无法访问剪贴板，请手动选中文字复制。"), "err"); }
  }, [m.body, toast, t]);
  const items: ActionItem[] = canAct ? [
    { key: "reply", label: t("回复"), icon: <IcReply size={16} />, onSelect: () => actions.onReply(m) },
    ...(m.body ? [{ key: "copy", label: t("复制"), icon: <IcCopy />, onSelect: copy }] : []),
    { key: "react", label: t("表情回应"), icon: <IcEmoji size={16} />, onSelect: () => setPicker(true) },
    ...(canEdit && m.body ? [{ key: "edit", label: t("编辑"), icon: <IcEdit />, onSelect: () => actions.onEdit(m) }] : []),
    ...(canDelete ? [{ key: "delete", label: t("删除"), icon: <IcTrash />, danger: true, onSelect: () => actions.onDelete(m) }] : []),
    ...(!own ? [{ key: "report", label: t("举报"), icon: <IcFlag />, onSelect: () => actions.onReport(m) }] : []),
  ] : [];

  // long press (touch) and right-click (mouse) open the same menu, hanging from the bubble
  const onPointerDown = (e: React.PointerEvent) => {
    if (!canAct || e.pointerType === "mouse") return;
    moved.current = false;
    press.current = window.setTimeout(() => { press.current = null; if (!moved.current) setMenu("bubble"); }, LONG_PRESS_MS);
  };
  const cancelPress = () => { if (press.current != null) { window.clearTimeout(press.current); press.current = null; } };
  const onContextMenu = (e: React.MouseEvent) => { if (!canAct) return; e.preventDefault(); setMenu("bubble"); };
  const closeMenu = useCallback((refocus: boolean) => { setMenu(cur => { if (refocus && cur === "button") moreBtn.current?.focus(); return null; }); }, []);
  const closePicker = useCallback((refocus: boolean) => { setPicker(false); if (refocus) reactBtn.current?.focus(); }, []);

  const mine = (emoji: string) => m.reactions.find(r => r.emoji === emoji)?.users.includes(uid);
  const names = (users: string[]) => users.map(u => (u === uid ? t("我") : conv.members.find(x => x.user_id === u)?.name || t("某成员"))).join("、");

  return <div className={`msg-row ${own ? "own" : ""} ${head ? "head" : ""} ${tail ? "tail" : ""} ${highlight ? "target" : ""} ${fresh ? "fresh" : ""} ${menu || picker ? "active" : ""}`}
    id={`msg-${m.seq}`} data-seq={m.seq || undefined} tabIndex={-1}>
    <div className="msg-gutter">{!own && head && <Avatar id={m.sender_id} name={m.sender} size={32} />}</div>
    <div className="msg-col">
      {head && <div className="msg-head"><span className="msg-sender">{own ? t("我") : m.sender}</span><time className="msg-time" dateTime={new Date(m.created_at * 1000).toISOString()} title={fullDate(m.created_at, locale)}>{clock(m.created_at, locale)}</time></div>}
      <div className="msg-line">
        <div ref={bubble} className="msg-bubble" onPointerDown={onPointerDown} onPointerUp={cancelPress} onPointerCancel={cancelPress} onPointerMove={() => { moved.current = true; cancelPress(); }} onContextMenu={onContextMenu}>
          {m.reply && <button type="button" className="msg-quote-block" onClick={() => actions.onJump(m.reply!.seq)} aria-label={t("跳到被回复的消息：{who}", { who: m.reply.sender })}>
            <strong>{m.reply.sender}</strong><span>{m.reply.deleted ? t("消息已删除") : m.reply.body}</span>
          </button>}
          {m.deleted ? <div className="msg-deleted">{t("消息已删除")}</div> : m.body ? <div className="msg-body">{linkify(m.body)}</div> : null}
          {!m.deleted && !m.pending && m.body && linksIn(m.body).map(u => <LinkCard key={u} url={u} />)}
          {m.card && !m.deleted && <AppCardView card={m.card} />}
          {m.attachment && !m.deleted && <AttachmentCard attachment={m.attachment} pending={m.pending} onOpenImage={actions.onOpenImage} />}
          <div className="msg-foot">
            {!head && <time className="msg-time" dateTime={new Date(m.created_at * 1000).toISOString()} title={fullDate(m.created_at, locale)}>{clock(m.created_at, locale)}</time>}
            {m.edited_at && <span className="msg-edited" title={fullDate(m.edited_at, locale)}>{t("已编辑")}</span>}
            {m.pending && <span className="msg-pending" role="status"><IcClock /> {t("发送中…")}</span>}
            {own && !m.pending && !m.failed && m.seq > 0 && <span className="msg-sent" title={t("已发送")}><IcCheck size={12} /><span className="sr-only">{t("已发送")}</span></span>}
          </div>
        </div>
        {canAct && <div className="msg-actions" role="group" aria-label={t("消息操作")}>
          {!coarse && <button ref={reactBtn} type="button" className="msg-act" onClick={() => setPicker(p => !p)} aria-label={t("添加表情回应")} title={t("添加表情回应")} aria-expanded={picker}><IcEmoji size={16} /></button>}
          {!coarse && <button type="button" className="msg-act" onClick={() => actions.onReply(m)} aria-label={t("回复")} title={t("回复")}><IcReply size={16} /></button>}
          <button ref={moreBtn} type="button" className="msg-act msg-act-more" onClick={() => setMenu(cur => (cur ? null : "button"))} aria-label={t("更多操作")} title={t("更多操作")} aria-haspopup="menu" aria-expanded={!!menu}><IcMore size={16} /></button>
        </div>}
      </div>
      {m.reactions.length > 0 && <div className="msg-reactions" role="group" aria-label={t("表情回应")}>
        {m.reactions.map(r => <button key={r.emoji} type="button" className={`msg-reaction ${mine(r.emoji) ? "mine" : ""}`} onClick={() => actions.onReact(m, r.emoji)} aria-pressed={!!mine(r.emoji)} title={names(r.users)} aria-label={t("{emoji} {n} 人：{who}", { emoji: r.emoji, n: r.count, who: names(r.users) })}>
          <span aria-hidden="true">{r.emoji}</span> <span className="msg-reaction-n">{r.count}</span>
        </button>)}
      </div>}
      {m.failed && <div className="msg-failed" role="alert"><IcWarn /> {t("发送失败：{why}", { why: m.failed })}
        <button type="button" className="btn ghost sm" onClick={() => actions.onRetry(m)}>{t("重试")}</button>
        <button type="button" className="btn ghost sm" onClick={() => actions.onDiscard(m)}>{t("放弃")}</button>
      </div>}
    </div>
    <ActionMenu open={!!menu} anchor={menu === "bubble" ? bubble : moreBtn} items={items} label={t("消息操作")} onClose={closeMenu} align={own ? "end" : "start"} />
    <Popover open={picker} anchor={coarse ? bubble : reactBtn} onClose={closePicker} label={t("表情回应")} className="msg-reactbar" role="group" align={own ? "end" : "start"}>
      {reactions.map((e, i) => <button key={e} type="button" data-autofocus={i === 0 ? true : undefined} aria-pressed={!!mine(e)} className={mine(e) ? "on" : ""} onClick={() => { closePicker(true); actions.onReact(m, e); }} aria-label={e}>{e}</button>)}
    </Popover>
  </div>;
}
