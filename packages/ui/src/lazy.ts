import { lazy, type ComponentType } from "react";

/** Route-level code splitting with a one-time recovery from a missing chunk.
 *
 *  Right after a deployment, a tab that still holds the previous build's HTML asks for chunk
 *  names that no longer exist. The first failure reloads the page once (the path is remembered
 *  in sessionStorage), which fetches the current HTML and its chunks; a second failure on the
 *  same path surfaces as the error it is, so a broken deployment cannot loop.
 *
 *  `pick` adapts a named export to `lazy`'s `{ default }` shape. */
export function lazyRoute<T extends ComponentType<any>>(load: () => Promise<{ default: T }>, storageKey = "synwevo.chunk-reload"): T {
  return lazy(() => load().catch((error: unknown) => {
    let already = false;
    try { already = sessionStorage.getItem(storageKey) === window.location.pathname; } catch { /* storage unavailable: no retry */ }
    if (!already) {
      try { sessionStorage.setItem(storageKey, window.location.pathname); } catch { /* ignore */ }
      window.location.reload();
      return new Promise<never>(() => { /* the reload takes over */ });
    }
    throw error;
  })) as unknown as T;
}

export const pick = <M, K extends keyof M>(load: () => Promise<M>, name: K) =>
  () => load().then((m) => ({ default: m[name] as unknown as ComponentType<any> }));
