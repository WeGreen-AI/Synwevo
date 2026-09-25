/** The messages entry in the platform bar: a link to the account origin's /messages with the
 *  unread badge from the shared table. Polls like the bell (60 s, only while visible) and
 *  listens for `synwevo:messages-unread` so the messages page can update it at once. */
import { useEffect, useState } from "react";
import { get } from "./api";
import { useI18n } from "@synwevo/ui";

const POLL_MS = 60_000;

export function announceMessagesUnread(unread: number) {
  window.dispatchEvent(new CustomEvent("synwevo:messages-unread", { detail: unread }));
}

export function useMessagesUnread(enabled = true): number {
  const [unread, setUnread] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    const tick = () => {
      if (stop || document.visibilityState !== "visible") return;
      get<{ unread: number }>("/api/auth/messaging/unread-count").then(x => { if (!stop) setUnread(x.unread); }).catch(() => undefined);
    };
    tick();
    const timer = window.setInterval(tick, POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") tick(); };
    const onAnnounce = (e: Event) => setUnread((e as CustomEvent<number>).detail);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("synwevo:messages-unread", onAnnounce);
    return () => { stop = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); window.removeEventListener("synwevo:messages-unread", onAnnounce); };
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
