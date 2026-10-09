import { useEffect, useState, useSyncExternalStore } from "react";
import { read } from "../../shared/ops";
import { createHistoryLoader, type HistoryPage } from "./history";

export function useHistory(q: string, archived: boolean, tick: number) {
  const [loader] = useState(() => createHistoryLoader((input, signal) =>
    read<HistoryPage>("agent.conversation.list", input, null, signal)));
  const state = useSyncExternalStore(loader.subscribe, loader.getSnapshot, loader.getSnapshot);
  useEffect(() => {
    const load = loader.reset({ status: archived ? "archived" : "active", q });
    const timer = setTimeout(() => { void load(); }, q ? 200 : 0);
    return () => { clearTimeout(timer); loader.cancel(); };
  }, [loader, q, archived, tick]);
  return { ...state, more: loader.more };
}
