/** The live table room in the browser: the template's room socket (`collab.ts`) with the table's
 *  own frames wired to a `SheetSync`, the waits the server asks for scheduled, the tab's client id
 *  brought back on reconnect, and everyone's selection shared through awareness
 *  (docs-sheet-live.md §5 and §9, B1 steps ③–④). */
import { openRoom, type Room } from "./collab.ts";
import { SheetSync } from "./sheetsync.ts";

/** Where someone is in a live table: a range by row and column ids, and whether they are typing there. */
export interface Selection { sheet: string; r1: string; c1: string; r2: string; c2: string; typing?: boolean }
export interface OtherSelection { user: { id: string; name: string; color: number }; sel: Selection }

export interface SheetRoom {
  room: Room;
  sync: SheetSync;
  /** the last close the browser is recovering from by itself (4503, 1011, a network drop), null once back */
  retrying(): number | null;
  /** tell the others where this tab is */
  select(sel: Selection | null): void;
  /** where everyone else is (their own tabs excluded) */
  others(): OtherSelection[];
  destroy(): void;
}

export function openSheetRoom(tid: string, opts: { origin: string; workspaceId: string | null }): SheetRoom {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let retry: number | null = null;
  let room!: Room;
  const sync = new SheetSync({ send: (frame) => { room.sendSheet(frame); } });
  const schedule = () => {
    if (timer) { clearTimeout(timer); timer = null; }
    const at = sync.wakeAt();
    if (at !== null) timer = setTimeout(() => { timer = null; sync.flush(); schedule(); }, Math.max(0, at - Date.now()) + 5);
  };
  room = openRoom(tid, {
    origin: opts.origin, workspaceId: opts.workspaceId, kind: "template", epochParam: false,
    live: {
      onMeta: (f) => {
        sync.onMeta(f);
        if (f?.type === "hello") {
          retry = null;
          // the same client id on every reconnect: the server resumes its numbering and outcomes
          if (sync.clientId) room.setParam("client", sync.clientId);
        }
        schedule();
      },
      onSheet: (f) => { sync.onSheet(f); schedule(); },
      onOpen: () => sync.onOpen(),
      onDisconnect: (code) => { retry = code ?? 1006; sync.onDisconnect(); },
      onClosed: (code, reason) => { retry = null; sync.onClose(code, reason); },
    },
  });
  const unsubscribe = sync.subscribe(() => schedule());
  return {
    room,
    sync,
    retrying: () => retry,
    select(sel) { room.setField("sel", sel); },
    others() {
      const out: OtherSelection[] = [];
      const me = sync.me;
      room.provider.awareness.getStates().forEach((st: any, clientId: number) => {
        if (clientId === room.doc.clientID) return;
        const u = st?.user, s = st?.sel;
        if (!u || typeof u.id !== "string" || u.id === me || !s || typeof s.sheet !== "string") return;
        if (![s.r1, s.c1, s.r2, s.c2].every((x) => typeof x === "string")) return;
        out.push({ user: { id: u.id, name: String(u.name || "—"), color: Number(u.color) || 1 },
          sel: { sheet: s.sheet, r1: s.r1, c1: s.c1, r2: s.r2, c2: s.c2, typing: !!s.typing } });
      });
      return out;
    },
    destroy() {
      if (timer) clearTimeout(timer);
      unsubscribe();
      room.destroy();
    },
  };
}
