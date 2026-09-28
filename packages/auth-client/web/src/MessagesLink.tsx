/** The messages entry in the platform bar: a link to Synwevo Message with the unread badge from
 *  the shared table. Polls like the bell (60 s, only while visible) and listens for
 *  `synwevo:messages-unread` so the messages page can update it at once. */
import { useEffect, useState } from "react";
import { get } from "./api";
import { platformHandoffUrl } from "./handoff";
import { broadcastCount, MESSAGES_UNREAD, onBroadcastCount } from "./tabSync";
import type { AuthState } from "./session";
import { useI18n } from "@synwevo/ui";

const POLL_MS = 60_000;

export function announceMessagesUnread(unread: number) {
  window.dispatchEvent(new CustomEvent("synwevo:messages-unread", { detail: unread }));
  broadcastCount(MESSAGES_UNREAD, unread);              // the other tabs of this origin
}

/**
 * Where the 消息 link goes, from the server's auth state and nothing else.
 *
 * `messages_url` is the messages application's launch URL from the registry (CI-66): on another
 * origin the link goes through that origin's own sign-in start (`/auth/start?next=…&lang=…`) so
 * the person arrives with a session in one hop, exactly like the portal handoff; on the same
 * origin (the single-process install, where the shell is served under `/messages`) it is a plain
 * path — a sign-in round trip there would replace the session cookie the page is using. Without
 * a registered application the link falls back to the account origin's `/messages`, which the
 * account service answers for (a redirect once the application exists).
 */
export function messagesHref(state: Pick<AuthState, "messages_url" | "account_url"> | null | undefined,
                             opts: { lang?: string; path?: string } = {}): string {
  const configured = (state?.messages_url || "").trim();
  if (configured) {
    try {
      const url = new URL(configured, window.location.origin);
      const next = opts.path || url.pathname || "/";
      if (url.origin === window.location.origin) return next;
      return platformHandoffUrl(url.origin, { next, lang: opts.lang }) ?? url.href;
    } catch { /* fall through to the account origin */ }
  }
  return (state?.account_url || "/account").replace(/\/account$/, "") + "/messages";
}

export function useMessagesUnread(enabled = true): number {
  const [unread, setUnread] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    const tick = () => {
      if (stop || document.visibilityState !== "visible") return;
      get<{ unread: number }>("/api/auth/messaging/unread-count").then(x => { if (!stop) { setUnread(x.unread); broadcastCount(MESSAGES_UNREAD, x.unread); } }).catch(() => undefined);
    };
    tick();
    const timer = window.setInterval(tick, POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") tick(); };
    const onAnnounce = (e: Event) => setUnread((e as CustomEvent<number>).detail);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("synwevo:messages-unread", onAnnounce);
    const offTabs = onBroadcastCount(MESSAGES_UNREAD, n => { if (!stop) setUnread(n); });
    return () => { stop = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); window.removeEventListener("synwevo:messages-unread", onAnnounce); offTabs(); };
  }, [enabled]);
  return unread;
}

export function MessagesLink({ href, current = false }: { href: string; current?: boolean }) {
  const { t } = useI18n();
  const unread = useMessagesUnread();
  return <a className="btn ghost sm bell" href={href} aria-current={current ? "page" : undefined}
    aria-label={unread ? t("消息，{n} 条未读", { n: unread }) : t("消息")}>
    {t("消息")}{unread > 0 && <span className="bell-count" aria-hidden="true">{unread > 99 ? "99+" : unread}</span>}
  </a>;
}
