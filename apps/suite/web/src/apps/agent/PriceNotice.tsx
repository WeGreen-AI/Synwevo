/** The price notice (design D-7): when `agent.billing.status.price_notice` says the person's
 *  last answer ran under other pricing, the chat shows one dismissible information notice with
 *  what changed. It never blocks a send — the next send simply runs at the price shown beside
 *  the composer — and it is never a confirmation. The dismissal is a per-viewer convenience
 *  remembered in localStorage per (previous, current) price the person pays; every storage access
 *  is wrapped so a blocked or throwing storage neither hides nor breaks the notice. */
import { useState } from "react";
import { validatePricing, type Pricing } from "./budget";
import { PriceChangeList, useChatText } from "./ChatParts";
import type { PriceNoticeFacts } from "./composer";

type NoticeStorage = Pick<Storage, "getItem" | "setItem"> | null | undefined;

/** What the person pays on one side of a notice: the sale price id and one call's hold (and with
 *  it the maximum), plus the server's `price_key` when it reports one (addendum-2 S2: also the
 *  rates, model and route). Never the route version: a renewal at the same prices is no new
 *  notice, while a raised hold at the same sale price id is (addendum-2 W9). */
export interface PaidPrice { price_id: string; per_call_hold: string; price_key?: string | null }
const paid = (price: PaidPrice) => `${price.price_id}~${price.per_call_hold}${price.price_key ? `~${price.price_key}` : ""}`;
export function priceNoticeKey(userId: string, previous: PaidPrice, current?: PaidPrice | null): string {
  return `synwevo.agent.price_notice.${userId}.${paid(previous)}${current ? `:${paid(current)}` : ""}`;
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

/** `currentKey`: the `price_key` of the run a send would now be bound to (`agent.billing.status.run`). */
export default function PriceNotice({ notice, offer, currentKey, userId, storage = defaultStorage(), onDismiss }: {
  notice: unknown; offer?: unknown; currentKey?: string | null; userId: string; storage?: NoticeStorage; onDismiss?: () => void;
}) {
  const { c } = useChatText();
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  const facts = validNotice(notice);
  const current = validOffer(offer);
  if (!facts) return null;
  const previousKey = (facts.previous as { price_key?: unknown }).price_key;
  const key = priceNoticeKey(userId, { price_id: facts.previous.price_id, per_call_hold: facts.previous.per_call_hold,
    price_key: typeof previousKey === "string" ? previousKey : null },
  current && { price_id: current.price_id, per_call_hold: current.per_call_hold, price_key: currentKey ?? null });
  if (dismissedKey === key || priceNoticeDismissed(key, storage)) return null;
  return <div className="notice info ag-price-notice" role="status">
    <p className="ag-price-notice-text">{c("priceNotice")}</p>
    {current && <PriceChangeList before={facts.previous} after={current} />}
    <button type="button" className="btn sm" onClick={() => { dismissPriceNotice(key, storage); setDismissedKey(key); onDismiss?.(); }}>{c("priceNoticeDismiss")}</button>
  </div>;
}
