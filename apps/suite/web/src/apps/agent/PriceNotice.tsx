/** The price notice (design D-7): when `agent.billing.status.price_notice` says the person's
 *  last answer ran under other pricing, the chat shows one dismissible information notice with
 *  what changed. It never blocks a send — the next send simply runs at the price shown beside
 *  the composer — and it is never a confirmation. The dismissal is a per-viewer convenience
 *  remembered in localStorage per (previous price, current price); every storage access is
 *  wrapped so a blocked or throwing storage neither hides nor breaks the notice. */
import { useState } from "react";
import { validatePricing, type Pricing } from "./budget";
import { PriceChangeList, useChatText } from "./ChatParts";
import type { PriceNoticeFacts } from "./composer";

type NoticeStorage = Pick<Storage, "getItem" | "setItem"> | null | undefined;

export function priceNoticeKey(userId: string, previousPriceId: string, currentPriceId?: string | null): string {
  return `synwevo.agent.price_notice.${userId}.${previousPriceId}${currentPriceId ? `:${currentPriceId}` : ""}`;
}
function defaultStorage(): NoticeStorage {
  try { return globalThis.localStorage; } catch { return undefined; }
}
export function priceNoticeDismissed(key: string, storage: NoticeStorage = defaultStorage()): boolean {
  try { return storage?.getItem(key) === "1"; } catch { return false; }
}
export function dismissPriceNotice(key: string, storage: NoticeStorage = defaultStorage()): void {
  try { storage?.setItem(key, "1"); } catch { /* private mode or blocked site data: the notice shows again next time */ }
}

function validNotice(notice: unknown): PriceNoticeFacts | null {
  const n = notice as PriceNoticeFacts | null | undefined;
  if (!n || typeof n !== "object" || !n.previous || !Number.isSafeInteger(n.previous_run_at) || n.previous_run_at < 0) return null;
  try { validatePricing(n.previous); } catch { return null; }
  return n;
}
function validOffer(offer: unknown): Pricing | null {
  if (!offer || typeof offer !== "object") return null;
  try { validatePricing(offer as Pricing); } catch { return null; }
  return offer as Pricing;
}

export default function PriceNotice({ notice, offer, userId, storage = defaultStorage(), onDismiss }: {
  notice: unknown; offer?: unknown; userId: string; storage?: NoticeStorage; onDismiss?: () => void;
}) {
  const { c } = useChatText();
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  const facts = validNotice(notice);
  const current = validOffer(offer);
  if (!facts) return null;
  const key = priceNoticeKey(userId, facts.previous.price_id, current?.price_id);
  if (dismissedKey === key || priceNoticeDismissed(key, storage)) return null;
  return <div className="notice info ag-price-notice" role="status">
    <p className="ag-price-notice-text">{c("priceNotice")}</p>
    {current && <PriceChangeList before={facts.previous} after={current} />}
    <button type="button" className="btn sm" onClick={() => { dismissPriceNotice(key, storage); setDismissedKey(key); onDismiss?.(); }}>{c("priceNoticeDismiss")}</button>
  </div>;
}
