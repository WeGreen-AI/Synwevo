/** The left pane: the person's conversations (pinned first, as the server orders them; muted
 *  ones dimmed; archived ones under their own filter), each with the last message and its
 *  sender, the unread count, and the time of the last message; a filter box that also searches
 *  message text on Enter; and the search results, each of which opens its thread at that message. */
import { useI18n } from "@synwevo/ui";
import type { Conversation, Message } from "../types";
import { filterConversations, highlightParts, preview, snippet } from "../lib/thread";
import { formatDayLabel, listTime } from "../lib/time";
import { Avatar } from "./MessageItem";
import { IcBack, IcMute, IcPin, IcSearch } from "./icons";

export type ListFilter = "all" | "unread" | "archived";

export function ConversationList({ convs, cid, uid, filter, setFilter, query, setQuery, onOpen, onSearch, header }: {
  convs: Conversation[]; cid: string | undefined; uid: string; filter: ListFilter; setFilter: (f: ListFilter) => void;
  query: string; setQuery: (q: string) => void; onOpen: (id: string) => void; onSearch: (q: string) => void; header: React.ReactNode;
}) {
  const { t, locale } = useI18n();
  const visible = filterConversations(convs, filter, query);
  const previewText = (c: Conversation) => {
    const p = preview(c, uid);
    const who = "sender" in p && p.sender ? (p.sender === "me" ? t("我") : p.sender) + ": " : "";
    switch (p.kind) {
      case "none": return t("尚无消息");
      case "archived": return t("已归档");
      case "system": return t("（系统消息）");
      case "deleted": return who + t("消息已删除");
      case "attachment": return who + t("[附件]");
      case "text": return who + p.body;
    }
  };
  return <>
    {header}
    <div className="msg-side-search">
      <span className="msg-side-search-ic" aria-hidden="true"><IcSearch size={16} /></span>
      <input className="msg-side-input" type="search" placeholder={t("筛选会话，按 Enter 搜索消息")} value={query} onChange={e => setQuery(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter" && !e.nativeEvent.isComposing) onSearch(query); }} aria-label={t("筛选会话，按 Enter 搜索消息")} enterKeyHint="search" />
    </div>
    <div className="msg-side-filters" role="tablist" aria-label={t("会话筛选")}>
      {(["all", "unread", "archived"] as ListFilter[]).map(f => <button key={f} type="button" role="tab" aria-selected={filter === f} className={`msg-chip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>
        {t(f === "all" ? "全部" : f === "unread" ? "未读" : "已归档")}</button>)}
    </div>
    {!convs.length ? <div className="msg-empty"><p>{t("还没有会话。")}</p><p className="tiny muted">{t("和同组织的成员开始私聊，或建一个群。")}</p></div>
      : !visible.length ? <div className="msg-empty"><p className="muted">{filter === "unread" ? t("没有未读的会话。") : filter === "archived" ? t("没有已归档的会话。") : t("没有匹配的会话。")}</p>
        {query.trim().length >= 2 && <button type="button" className="btn sm" onClick={() => onSearch(query)}>{t("搜索消息「{q}」", { q: query.trim() })}</button>}</div>
      : <div className="msg-convs" role="list" aria-label={t("会话列表")}>
        {visible.map(c => {
          const unread = c.unread > 0 && !c.me.muted; const dot = c.unread > 0 && c.me.muted;
          const ts = c.last_message_at || null;
          return <button key={c.id} type="button" role="listitem" className={`msg-conv ${c.id === cid ? "on" : ""} ${c.me.muted ? "muted-conv" : ""} ${unread ? "has-unread" : ""}`}
            onClick={() => onOpen(c.id)} aria-current={c.id === cid ? "true" : undefined}
            aria-label={`${c.name}${unread ? "，" + t("{n} 条未读", { n: c.unread }) : ""}${c.me.pinned ? "，" + t("已置顶") : ""}${c.me.muted ? "，" + t("已静音") : ""}`}>
            <Avatar id={c.kind === "dm" ? (c.peer?.user_id || c.id) : c.id} name={c.name} kind={c.kind} size={40} />
            <span className="msg-conv-main">
              <span className="msg-conv-top">
                <span className="msg-conv-name">{c.name}</span>
                {ts && <time className="msg-conv-time" dateTime={new Date(ts * 1000).toISOString()}>{formatDayLabel(listTime(ts), locale, t)}</time>}
              </span>
              <span className="msg-conv-bottom">
                <span className="msg-conv-preview">{previewText(c)}</span>
                <span className="msg-conv-marks">
                  {c.me.pinned && <span className="msg-mark" title={t("已置顶")}><IcPin /></span>}
                  {c.me.muted && <span className="msg-mark" title={t("已静音")}><IcMute /></span>}
                  {typeof c.unread_mentions === "number" && c.unread_mentions > 0 && <span className="msg-unread mention" title={t("有人提及你")}>@</span>}
                  {unread && <span className="msg-unread">{c.unread > 99 ? "99+" : c.unread}</span>}
                  {dot && <span className="msg-unread-dot" title={t("{n} 条未读（已静音）", { n: c.unread })} />}
                </span>
              </span>
            </span>
          </button>;
        })}
      </div>}
  </>;
}

export function SearchResults({ q, items, convs, onOpen, onClose }: { q: string; items: Message[]; convs: Conversation[]; onOpen: (cid: string, seq: number) => void; onClose: () => void }) {
  const { t, locale } = useI18n();
  const nameOf = (cid: string) => convs.find(c => c.id === cid)?.name || t("会话");
  return <>
    <div className="msg-side-h msg-search-h">
      <button type="button" className="btn ghost sm icon" onClick={onClose} aria-label={t("返回会话列表")}><IcBack /></button>
      <span className="msg-side-title">{t("搜索结果")}</span>
    </div>
    <p className="msg-search-summary tiny muted" role="status">{t("「{q}」：{n} 条（只包含你能看到的消息）", { q, n: items.length })}</p>
    {!items.length ? <div className="msg-empty"><p className="muted">{t("没有找到包含「{q}」的消息。", { q })}</p></div>
      : <div className="msg-convs" role="list" aria-label={t("搜索结果")}>
        {items.map(m => <button key={m.id} type="button" role="listitem" className="msg-search-hit" onClick={() => onOpen(m.conversation_id, m.seq)}>
          <span className="msg-conv-top"><span className="msg-conv-name">{nameOf(m.conversation_id)}</span><time className="msg-conv-time" dateTime={new Date(m.created_at * 1000).toISOString()}>{formatDayLabel(listTime(m.created_at), locale, t)}</time></span>
          <span className="msg-search-body"><strong>{m.sender}</strong>: {highlightParts(snippet(m.body, q), q).map((p, i) => (p.hit ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}</span>
        </button>)}
      </div>}
  </>;
}
