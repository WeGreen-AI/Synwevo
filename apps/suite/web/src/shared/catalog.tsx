/** Which applications this person can open, and in which workspaces — read once per page load.
 *  A convenience for navigation only: every operation is authorized again by the server. */
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { catalog, type CatalogApp } from "./ops";

interface CatalogState { apps: CatalogApp[] | undefined; error: unknown; reload: () => void }

const Ctx = createContext<CatalogState>({ apps: undefined, error: undefined, reload: () => undefined });

export function CatalogProvider({ children }: { children: ReactNode }) {
  const [apps, setApps] = useState<CatalogApp[] | undefined>(undefined);
  const [error, setError] = useState<unknown>(undefined);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setError(undefined);
    catalog(abort.signal).then((out) => setApps(out.apps)).catch((e) => {
      if ((e as any)?.name !== "AbortError") setError(e);
    });
    return () => abort.abort();
  }, [tick]);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  return <Ctx.Provider value={{ apps, error, reload }}>{children}</Ctx.Provider>;
}

export const useCatalog = () => useContext(Ctx);

export function useApp(id: string): { app: CatalogApp | null | undefined; error: unknown; reload: () => void } {
  const { apps, error, reload } = useCatalog();
  return { app: apps === undefined ? undefined : apps.find((a) => a.id === id) ?? null, error, reload };
}
