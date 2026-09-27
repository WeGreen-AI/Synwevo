/** The left pane: the person's conversations (pinned first, as the server orders them; muted
 *  ones — long-term or on a timer — dimmed; archived ones under their own filter), each with the
 *  last message and its sender, the unread count (a marked-unread conversation counts as one),
 *  the peer's presence when the server shows one, and the time of the last message; a filter
 *  box that also searches message text on Enter, with the server's filters; and the search
 *  results, highlighted from the server's ranges, each of which opens its thread at that message.
 *  A right-click or a long press on a row offers mark-unread, pin and mute. */
import { useCallback, useRef, useState } from "react";
import { useI18n } from "@synwevo/ui";
import type { Conversation, Presence, SearchAnswer } from "../types";
import { filterConversations, mutedNow, preview, slicesByCodepoints } from "../lib/thread";
import { formatDayLabel, listTime } from "../lib/time";
import { Avatar } from "./MessageItem";
import { ActionMenu, type ActionItem } from "./layers";
import { IcBack, IcMute, IcPin, IcSearch } from "./icons";

export type ListFilter = "all" | "unread" | "archived";
export type RowAction = "unread" | "pin" | "unpin" | "mute" | "unmute";
export interface SearchOptions { cid?: string; has_attachment?: boolean }

const LONG_PRESS_MS = 500;

function Row({ c, cid, uid, presence, onOpen, onRowAction, t, locale }: {
  c: Conversation; cid: string | undefined; uid: string; presence: Record<string, Presence>; onOpen: (id: string) => void; onRowAction: (c: Conversation, a: RowAction) => void;
  t: (s: string, p?: Record<string, string | number>) => string; locale: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState(false);
  const press = useRef<number | null>(null);
  const muted = mutedNow(c.me);
  const count = Math.max(c.unread, c.me.marked_unread ? 1 : 0);
  const unread = count > 0 && !muted; const dot = count > 0 && muted;
  const ts = c.last_message_at || null;
  const peerPresence: Presence = c.kind === "dm" && c.peer ? (presence[c.peer.user_id] !== undefined ? presence[c.peer.user_id] : (c.peer.presence ?? null)) : null;
  const p = preview(c, uid);
  const who = "sender" in p && p.sender ? (p.sender === "me" ? t("我") : p.sender) + ": " : "";
  const text = p.kind === "none" ? t("尚无消息") : p.kind === "archived" ? t("已归档") : p.kind === "system" ? t("（系统消息）") : p.kind === "hidden" ? t("你删除了这条消息")
    : p.kind === "deleted" ? who + t("消息已删除") : p.kind === "attachment" ? who + t("[附件]") : who + p.body;
  const items: ActionItem[] = [
    { key: "unread", label: t("标为未读"), onSelect: () => onRowAction(c, "unread"), disabled: c.unread > 0 || !!c.me.marked_unread, hint: c.unread > 0 || c.me.marked_unread ? t("已经是未读") : undefined },
    { key: "pin", label: c.me.pinned ? t("取消置顶") : t("置顶"), onSelect: () => onRowAction(c, c.me.pinned ? "unpin" : "pin") },
    { key: "mute", label: c.me.muted ? t("取消静音") : t("静音"), onSelect: () => onRowAction(c, c.me.muted ? "unmute" : "mute") },
  ];
  const cancelPress = () => { if (press.current != null) { window.clearTimeout(press.current); press.current = null; } };
  return <div ref={wrap} role="listitem" className={`msg-conv-wrap ${menu ? "active" : ""}`}
    onContextMenu={e => { e.preventDefault(); setMenu(true); }}
    onPointerDown={e => { if (e.pointerType === "mouse") return; press.current = window.setTimeout(() => { press.current = null; setMenu(true); }, LONG_PRESS_MS); }}
    onPointerUp={cancelPress} onPointerCancel={cancelPress} onPointerMove={cancelPress}>
    <button type="button" className={`msg-conv ${c.id === cid ? "on" : ""} ${muted ? "muted-conv" : ""} ${unread ? "has-unread" : ""}`}
      onClick={() => { if (press.current == null && !menu) onOpen(c.id); }} aria-current={c.id === cid ? "true" : undefined}
      aria-label={`${c.name}${unread ? "，" + t("{n} 条未读", { n: count }) : ""}${c.me.pinned ? "，" + t("已置顶") : ""}${muted ? "，" + t("已静音") : ""}${peerPresence === "online" ? "，" + t("在线") : ""}`}>
      <Avatar id={c.kind === "dm" ? (c.peer?.user_id || c.id) : c.id} name={c.name} kind={c.kind} size={40} presence={peerPresence} />
      <span className="msg-conv-main">
        <span className="msg-conv-top">
          <span className="msg-conv-name">{c.name}</span>
          {ts && <time className="msg-conv-time" dateTime={new Date(ts * 1000).toISOString()}>{formatDayLabel(listTime(ts), locale, t)}</time>}
        </span>
        <span className="msg-conv-bottom">
          <span className="msg-conv-preview">{text}</span>
          <span className="msg-conv-marks">
            {c.me.pinned && <span className="msg-mark" title={t("已置顶")}><IcPin /></span>}
            {muted && <span className="msg-mark" title={t("已静音")}><IcMute /></span>}
            {typeof c.unread_mentions === "number" && c.unread_mentions > 0 && <span className="msg-unread mention" title={t("有人提及你")}>@</span>}
            {unread && <span className="msg-unread">{count > 99 ? "99+" : count}</span>}
            {dot && <span className="msg-unread-dot" title={t("{n} 条未读（已静音）", { n: count })} />}
          </span>
        </span>
      </span>
    </button>
    <ActionMenu open={menu} anchor={wrap} items={items} label={t("会话操作：{name}", { name: c.name })} onClose={() => setMenu(false)} align="start" />
  </div>;
}

export function ConversationList({ convs, cid, uid, presence, filter, setFilter, query, setQuery, onOpen, onSearch, onRowAction, header }: {
  convs: Conversation[]; cid: string | undefined; uid: string; presence: Record<string, Presence>; filter: ListFilter; setFilter: (f: ListFilter) => void;
  query: string; setQuery: (q: string) => void; onOpen: (id: string) => void; onSearch: (q: string, opts: SearchOptions) => void;
  onRowAction: (c: Conversation, a: RowAction) => void; header: React.ReactNode;
}) {
  const { t, locale } = useI18n();
  const visible = filterConversations(convs, filter, query);
  const search = useCallback(() => onSearch(query, {}), [onSearch, query]);
  return <>
    {header}
    <div className="msg-side-search">
      <span className="msg-side-search-ic" aria-hidden="true"><IcSearch size={16} /></span>
      <input className="msg-side-input" type="search" placeholder={t("筛选会话，按 Enter 搜索消息")} value={query} onChange={e => setQuery(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter" && !e.nativeEvent.isComposing) search(); }} aria-label={t("筛选会话，按 Enter 搜索消息")} enterKeyHint="search" />
    </div>
    <div className="msg-side-filters" role="tablist" aria-label={t("会话筛选")}>
      {(["all", "unread", "archived"] as ListFilter[]).map(f => <button key={f} type="button" role="tab" aria-selected={filter === f} className={`msg-chip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>
        {t(f === "all" ? "全部" : f === "unread" ? "未读" : "已归档")}</button>)}
    </div>
    {!convs.length ? <div className="msg-empty"><p>{t("还没有会话。")}</p><p className="tiny muted">{t("和同组织的成员开始私聊，或建一个群。")}</p></div>
      : !visible.length ? <div className="msg-empty"><p className="muted">{filter === "unread" ? t("没有未读的会话。") : filter === "archived" ? t("没有已归档的会话。") : t("没有匹配的会话。")}</p>
        {query.trim().length >= 2 && <button type="button" className="btn sm" onClick={search}>{t("搜索消息「{q}」", { q: query.trim() })}</button>}</div>
      : <div className="msg-convs" role="list" aria-label={t("会话列表")}>
        {visible.map(c => <Row key={c.id} c={c} cid={cid} uid={uid} presence={presence} onOpen={onOpen} onRowAction={onRowAction} t={t} locale={locale} />)}
      </div>}
  </>;
}

export function SearchResults({ q, answer, opts, convs, cid, onSearch, onOpen, onClose }: {
  q: string; answer: SearchAnswer; opts: SearchOptions; convs: Conversation[]; cid: string | undefined;
  onSearch: (q: string, opts: SearchOptions) => void; onOpen: (cid: string, seq: number) => void; onClose: () => void;
}) {
  const { t, locale } = useI18n();
  const nameOf = (id: string) => convs.find(c => c.id === id)?.name || t("会话");
  const items = answer.items;
  return <>
    <div className="msg-side-h msg-search-h">
      <button type="button" className="btn ghost sm icon" onClick={onClose} aria-label={t("返回会话列表")}><IcBack /></button>
      <span className="msg-side-title">{t("搜索结果")}</span>
    </div>
    <div className="msg-side-filters" aria-label={t("搜索范围")}>
      {cid && <label className="msg-chip-check"><input type="checkbox" checked={!!opts.cid} onChange={e => onSearch(q, { ...opts, cid: e.target.checked ? cid : undefined })} /> {t("仅当前会话")}</label>}
      <label className="msg-chip-check"><input type="checkbox" checked={!!opts.has_attachment} onChange={e => onSearch(q, { ...opts, has_attachment: e.target.checked || undefined })} /> {t("有附件")}</label>
    </div>
    <p className="msg-search-summary tiny muted" role="status">{t("「{q}」：{n} 条（只包含你能看到的消息）", { q, n: items.length })}{answer.engine === "like" && <> · {t("逐字匹配")}</>}</p>
    {!items.length ? <div className="msg-empty"><p className="muted">{t("没有找到包含「{q}」的消息。", { q })}</p></div>
      : <div className="msg-convs" role="list" aria-label={t("搜索结果")}>
        {items.map(m => <button key={m.id} type="button" role="listitem" className="msg-search-hit" onClick={() => onOpen(m.cid || m.conversation_id, m.seq)}>
          <span className="msg-conv-top"><span className="msg-conv-name">{nameOf(m.cid || m.conversation_id)}</span><time className="msg-conv-time" dateTime={new Date(m.created_at * 1000).toISOString()}>{formatDayLabel(listTime(m.created_at), locale, t)}</time></span>
          <span className="msg-search-body"><strong>{m.sender}</strong>: {slicesByCodepoints(m.snippet ?? m.body, m.ranges || []).map((p, i) => (p.hit ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}</span>
        </button>)}
      </div>}
  </>;
}
