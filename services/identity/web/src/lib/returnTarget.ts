import type { Me } from "@synwevo/auth-client";

/** Where "back to my work" goes from the account pages.
 *
 *  The candidates come from the server (`/api/auth/me` → `return_targets`: the configured portal
 *  and the applications this person can open). The page only ever *chooses* among them. A
 *  `from=<id>` in the account URL — added by the portal and by Synwevo Docs when they link here —
 *  names the application to return to; it is an id matched against the list, never a URL, so a
 *  crafted link cannot send anyone anywhere the server did not list. The choice is kept for the
 *  visit in sessionStorage (again the id only), so it survives moving between account sub-pages
 *  and a refresh, and is forgotten when the tab closes.
 */
export interface ReturnTarget { id: string; name: string; url: string; workspace_id?: string | null; workspace_name?: string | null }

const KEY = "synwevo.account.from";

export function rememberOrigin(search: string = window.location.search): void {
  const from = new URLSearchParams(search).get("from");
  if (!from) return;
  if (!/^[a-z0-9_-]{1,40}$/i.test(from)) return;
  try { sessionStorage.setItem(KEY, from); } catch { /* private mode: the portal is still the fallback */ }
}

export function originHint(): string | null {
  try { return sessionStorage.getItem(KEY); } catch { return null; }
}

/** The target to offer, or null when the server listed none (a deployment without a portal
 *  URL and no application entitlement — the page then shows the account overview only). */
export function chooseReturnTarget(me: Pick<Me, "return_targets"> | null | undefined, hint: string | null = originHint()): ReturnTarget | null {
  const targets = (me?.return_targets || []) as ReturnTarget[];
  if (!targets.length) return null;
  if (hint) {
    const named = targets.find((t) => t.id === hint);
    if (named) return named;
  }
  return targets.find((t) => t.id === "portal") || targets[0];
}
