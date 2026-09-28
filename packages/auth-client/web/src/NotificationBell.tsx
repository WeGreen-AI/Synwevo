/**
 * The platform inbox, as seen from any shell: a bell with the unread count and a drawer that lists
 * notifications, lets the reader mark them, and follows a notification to what it is about.
 *
 * Everything shown comes from `/api/auth/notifications` on this shell's own origin — every role
 * serves the same rows from the shared table, rendered in the reader's language by the server —
 * so the portal, Synwevo Docs and the account pages show one list. Links are the server's
 * (`href`), never assembled here. Marking read is the reader's own state and changes nothing
 * else: an invitation is accepted on its page, an approval in its flow.
 *
 * The count is refreshed on open, every minute while the tab is visible, and when the tab
 * becomes visible again; no long-lived connection is opened for it.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get, post } from "./api";
import { useSession } from "./session";
import { Drawer, useI18n, useToast } from "@synwevo/ui";
import "./notifications.messages";
import "./notifications.css";
import { broadcastCount, NOTIFICATIONS_UNREAD, onBroadcastCount } from "./tabSync";

export interface NotificationItem {
  id: string; seq: number; kind: string; category: string; severity: "info" | "action" | "critical";
  title: string; body: string; href: string | null; action_state: "pending" | "done" | "void" | null;
  created_at: number; read_at: number | null; archived_at: number | null;
}
interface Page { items: NotificationItem[]; next_before_seq: number | null; unread_count: number; latest_seq: number }

type Filter = "all" | "unread" | "pending";
const POLL_MS = 60_000;

export function useUnreadCount(enabled: boolean): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    const tick = async () => {
      if (document.visibilityState !== "visible") return;
      try { const r = await get<{ unread_count: number }>("/api/auth/notifications/unread-count"); if (!stop) { setCount(r.unread_count); broadcastCount(NOTIFICATIONS_UNREAD, r.unread_count); } }
      catch { /* a failed poll keeps the last count; the drawer shows the error when opened */ }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") void tick(); };
    document.addEventListener("visibilitychange", onVisible);
    const onChanged = (e: Event) => { const n = (e as CustomEvent<number>).detail; if (typeof n === "number") setCount(n); };
    window.addEventListener("synwevo:unread", onChanged);
    const offTabs = onBroadcastCount(NOTIFICATIONS_UNREAD, n => { if (!stop) setCount(n); });
    // a push delivered to this browser (the service worker relays it) refreshes the count now, not at the next poll
    const sw = typeof navigator !== "undefined" ? navigator.serviceWorker : undefined;
    const onPush = (e: MessageEvent) => { if (e.data && e.data.type === "synwevo:push") void tick(); };
    sw?.addEventListener("message", onPush);
    return () => { stop = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); window.removeEventListener("synwevo:unread", onChanged); sw?.removeEventListener("message", onPush); offTabs(); };
  }, [enabled]);
  return count;
}

function announceUnread(n: number) { window.dispatchEvent(new CustomEvent("synwevo:unread", { detail: n })); broadcastCount(NOTIFICATIONS_UNREAD, n); }

export function NotificationBell({ preferencesHref }: { preferencesHref?: string }) {
  const { me } = useSession();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const unread = useUnreadCount(!!me);
  if (!me) return null;
  const label = unread > 0 ? t("通知，{count} 条未读", { count: unread }) : t("通知");
  return <>
    <button type="button" className="btn ghost sm bell" aria-label={label} title={label} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
      <span aria-hidden="true">🔔</span>
      {unread > 0 && <span className="bell-count" aria-hidden="true">{unread > 99 ? "99+" : unread}</span>}
    </button>
    {open && <NotificationDrawer onClose={() => setOpen(false)} preferencesHref={preferencesHref} />}
  </>;
}

export function NotificationDrawer({ onClose, preferencesHref }: { onClose: () => void; preferencesHref?: string }) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("all");
  const [page, setPage] = useState<Page | null>(null);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const openedAt = useRef<number>(0);          // latest_seq when the list was opened: "read all" stops here

  const load = useCallback(async (f: Filter, before?: number | null) => {
    setBusy(true); setError(null);
    try {
      const q = new URLSearchParams({ filter: f, lang: locale, limit: "30" });
      if (before) q.set("before_seq", String(before));
      const p = await get<Page>(`/api/auth/notifications?${q}`);
      setPage(p);
      setItems((prev) => before ? [...prev, ...p.items] : p.items);
      if (!before) openedAt.current = p.latest_seq;
      announceUnread(p.unread_count);
    } catch (e) { setError(e); } finally { setBusy(false); }
  }, [locale]);
  useEffect(() => { void load(filter); }, [filter, load]);

  async function mark(ids: string[], patch: { read?: boolean; archived?: boolean }) {
    try {
      const r = await post<{ unread_count: number }>("/api/auth/notifications/mark", { ids, ...patch });
      announceUnread(r.unread_count);
      setItems((prev) => prev.flatMap((n) => {
        if (!ids.includes(n.id)) return [n];
        if (patch.archived) return [];
        return [{ ...n, read_at: patch.read === undefined ? n.read_at : patch.read ? Date.now() / 1000 : null }];
      }));
    } catch (e) { toast(e instanceof ApiError ? e.message : String(e), "err"); }
  }
  async function readAll() {
    try {
      const r = await post<{ unread_count: number }>("/api/auth/notifications/read-all", { up_to_seq: openedAt.current });
      announceUnread(r.unread_count);
      setItems((prev) => prev.map((n) => n.seq <= openedAt.current && !n.read_at ? { ...n, read_at: Date.now() / 1000 } : n));
    } catch (e) { toast(e instanceof ApiError ? e.message : String(e), "err"); }
  }
  async function follow(n: NotificationItem) {
    if (!n.read_at) await mark([n.id], { read: true });
    if (n.href) window.location.assign(n.href);
  }

  const tabs: [Filter, string][] = [["all", t("全部")], ["unread", t("未读")], ["pending", t("待处理")]];
  return <Drawer title={t("通知")} onClose={onClose}>
    <div className="row wrap gap4 notif-tools" role="tablist" aria-label={t("通知筛选")}>
      {tabs.map(([f, name]) => <button key={f} type="button" role="tab" aria-selected={filter === f} className={`btn sm ${filter === f ? "primary" : "ghost"}`} onClick={() => setFilter(f)}>{name}</button>)}
      <span className="grow" />
      <button type="button" className="btn ghost sm" disabled={busy || !page || page.unread_count === 0} onClick={() => void readAll()}>{t("全部标为已读")}</button>
      {preferencesHref && <a className="btn ghost sm" href={preferencesHref}>{t("偏好")}</a>}
    </div>
    {error ? <div className="notice err" role="alert">{error instanceof ApiError ? error.message : t("无法加载通知，请稍后重试。")} <button type="button" className="btn ghost sm" onClick={() => void load(filter)}>{t("重试")}</button></div>
      : !page ? <p className="muted" role="status">{t("加载中…")}</p>
      : items.length === 0 ? <p className="muted notif-empty">{t(filter === "unread" ? "没有未读通知。" : filter === "pending" ? "没有待处理的事项。" : "还没有通知。")}</p>
      : <ul className="notif-list">
        {items.map((n) => <li key={n.id} className={`notif-item sev-${n.severity}${n.read_at ? "" : " unread"}`}>
          <div className="notif-main">
            {n.href ? <a href={n.href} className="notif-title" onClick={(e) => { e.preventDefault(); void follow(n); }}>{n.title}</a>
                    : <span className="notif-title">{n.title}</span>}
            {n.body && <p className="notif-body">{n.body}</p>}
            <p className="tiny muted notif-meta">
              <time dateTime={new Date(n.created_at * 1000).toISOString()}>{new Date(n.created_at * 1000).toLocaleString(locale === "en" ? "en" : locale === "zh-Hant" ? "zh-TW" : "zh-CN")}</time>
              {n.action_state === "pending" && <> · <span className="tag warn">{t("待处理")}</span></>}
              {n.action_state === "done" && <> · {t("已处理")}</>}
              {n.action_state === "void" && <> · {t("已失效")}</>}
            </p>
          </div>
          <div className="notif-actions">
            <button type="button" className="btn ghost sm" onClick={() => void mark([n.id], { read: !n.read_at })} aria-label={n.read_at ? t("标为未读") : t("标为已读")} title={n.read_at ? t("标为未读") : t("标为已读")}>{n.read_at ? "○" : "●"}</button>
            <button type="button" className="btn ghost sm" onClick={() => void mark([n.id], { archived: true })} aria-label={t("归档")} title={t("归档")}>✕</button>
          </div>
        </li>)}
      </ul>}
    {page?.next_before_seq && <button type="button" className="btn sm" disabled={busy} onClick={() => void load(filter, page.next_before_seq)}>{t("加载更早的通知")}</button>}
  </Drawer>;
}
