import { useEffect, useState } from "react";
import { ApiError, get } from "@synwevo/auth-client";
import { useLoad } from "../lib/hooks";
import { errText } from "./labels";
/** Shared loader: 404 here means "not an administrator", which the page treats as not existing. */
export function useAdminData<T>(path: string, onDenied: () => void) {
  const load = useLoad<T>(path, (signal) => get<T>(path, signal));
  useEffect(() => {
    if (load.error instanceof ApiError && !load.error.upstream && load.error.status === 404) onDenied();
  }, [load.error, onDenied]);
  return { data: load.data, err: load.error ? errText(load.error) : null, loading: load.loading, reload: load.reload };
}

/** `total_capped` and `more` come from the lists that count no further than a cap (the run
 *  pages): `total` is then the cap, and `more` says whether a next page exists. */
type AdminPage<T> = { items: T[]; total: number; offset: number; limit: number; snapshot: number;
  total_capped?: boolean; more?: boolean };
const ignoreDenied = () => {};

/** Whether `offset` is past the end of the list: nothing on the page, or — for a list whose
 *  total is exact — at or past that total. A capped total says nothing about where the list
 *  ends, so a page with rows on it past the cap is a page, not an overshoot. */
export function pastTheEnd(page: { items: unknown[]; total: number; total_capped?: boolean }, offset: number): boolean {
  if (offset <= 0) return false;
  return page.total_capped ? page.items.length === 0 : offset >= page.total;
}

/** Keep the server's insertion boundary when changing pages. Search and explicit refresh
 * start a new listing. Rows still reflect current permissions and account state. */
export function useAdminPage<T>(filterPath: string, onDenied: () => void = ignoreDenied) {
  const [page, setPage] = useState<{ key: string; offset: number; snapshot?: number }>({ key: filterPath, offset: 0 });
  useEffect(() => { setPage({ key: filterPath, offset: 0 }); }, [filterPath]);
  const current = page.key === filterPath ? page : { key: filterPath, offset: 0 };
  const path = `${filterPath}${filterPath.includes("?") ? "&" : "?"}limit=50&offset=${current.offset}${current.snapshot === undefined ? "" : `&snapshot=${current.snapshot}`}`;
  const load = useAdminData<AdminPage<T>>(path, onDenied);
  const setOffset = (offset: number) => setPage({ key: filterPath, offset, snapshot: load.data?.snapshot });
  // A removal or state filter change can empty the last page; recover to the last valid page.
  useEffect(() => {
    if (!load.loading && !load.err && load.data && pastTheEnd(load.data, current.offset)) {
      setPage({ key: filterPath, offset: Math.max(0, Math.floor((load.data.total - 1) / 50) * 50), snapshot: load.data.snapshot });
    }
  }, [load.loading, load.err, load.data, current.offset, filterPath]);
  return { ...load, offset: load.data?.offset ?? 0, setOffset,
    reload: () => { setPage({ key: filterPath, offset: 0 }); load.reload(); } };
}
