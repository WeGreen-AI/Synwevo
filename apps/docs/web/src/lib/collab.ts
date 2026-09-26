/** The browser's side of a live-editing room (see synwevo_docs/collab.py).
 *
 *  One shared document per template, synchronised over a same-origin WebSocket with the Yjs
 *  protocol; beside the sync and awareness frames the server speaks its own frame (type 100)
 *  carrying JSON about the room: who this connection is, whether it may write, which template
 *  version the live document belongs to, whether it has unsaved changes, who joined or left.
 *
 *  The server decides everything that matters: identity, role, workspace, whether an update is
 *  applied. This module only mirrors those decisions into React state and stops reconnecting when
 *  the server closes with a definitive code (44xx). If the room cannot be reached at all, the
 *  editor falls back to single-person editing of the saved version (see DocEditor).
 */
import * as decoding from "lib0/decoding";
import { WebsocketProvider } from "y-websocket";
import * as Y from "yjs";

export const MESSAGE_META = 100;

export interface PeerInfo { id: string; name: string; color: number; readonly?: boolean; focus?: Focus | null }
/** Where a person is in a project: a stable object, never a screen position. */
export interface Focus { kind: "slot" | "mapping" | "source" | "cell" | "sheet"; id: string }
export interface ChangeFrame { kind: string; detail: Record<string, any>; by: PeerInfo | null; at: number; seq: number }
export type MetaFrame =
  | { type: "hello"; kind?: "template" | "project"; writable?: boolean; recovered?: boolean; user: PeerInfo; readonly: boolean; head: number | null; dirty: boolean; changes_rev: number; peers: PeerInfo[]; recheck_s: number }
  | { type: "change"; kind: string; detail: Record<string, any>; by: PeerInfo | null; at: number }
  | { type: "state"; head: number | null; dirty: boolean; changes_rev: number; online: number }
  | { type: "head"; head: number | null; dirty: boolean; changes_rev: number; online: number; reason: "save" | "restore" | "undo" | "redo" | string; by: string | null; by_id?: string | null }
  | { type: "role"; readonly: boolean; role: string }
  | { type: "refused"; reason: string }
  | { type: "closed"; reason: string }
  | { type: "joined"; user: PeerInfo; online: number }
  | { type: "left"; user: { id: string; name: string }; online: number }
  | { type: "activity"; changes_rev: number };

export type Status = "connecting" | "connected" | "disconnected";

export interface RoomState {
  status: Status;
  synced: boolean;
  me: PeerInfo | null;
  readonly: boolean;
  head: number | null;
  dirty: boolean;
  changesRev: number;
  /** Other people currently in the room, one entry per person (a person may have several tabs). */
  peers: PeerInfo[];
  /** The server could not read the persisted draft and seeded the room from the head version instead. */
  recovered: boolean;
  /** The server closed for good (44xx): the reason it gave. */
  closed: { code: number; reason: string } | null;
  /** The server dropped this connection's writes (read-only) at least once since the last role change. */
  refused: boolean;
  /** The last version operation someone performed on the template while we were connected. */
  lastHead: { reason: string; by: string | null; byId: string | null; head: number | null } | null;
  /** The last change frame of a project room (a save, a file, a setting), numbered so effects can key on it. */
  lastChange: ChangeFrame | null;
}

export const initialState = (): RoomState => ({
  status: "connecting", synced: false, me: null, readonly: false, head: null, dirty: false, changesRev: 0, peers: [], recovered: false, closed: null, refused: false, lastHead: null, lastChange: null,
});

export type RoomKind = "template" | "project";

export function roomUrl(origin: string, kind: RoomKind = "template"): string {
  return origin.replace(/^http/, "ws") + `/api/collab/${kind}s`;
}

export interface Room {
  doc: Y.Doc;
  provider: WebsocketProvider;
  /** Subscribe to state changes; returns the unsubscribe function. */
  subscribe(fn: (s: RoomState) => void): () => void;
  state(): RoomState;
  /** Tell everyone who we are (the server checks it is really us). */
  setUser(user: PeerInfo): void;
  /** Tell everyone which object we are on (null when none). */
  setFocus(focus: Focus | null): void;
  destroy(): void;
}

/** Peers from the awareness states, one per person, without ourselves. */
export function peersOf(states: Map<number, any>, myClientId: number, meId: string | null): PeerInfo[] {
  const seen = new Map<string, PeerInfo>();
  states.forEach((st, clientId) => {
    const u = st?.user;
    if (clientId === myClientId || !u || typeof u.id !== "string" || u.id === meId) return;
    const f = st?.focus;
    const focus: Focus | null = f && typeof f === "object" && typeof f.id === "string" && ["slot", "mapping", "source", "cell", "sheet"].includes(f.kind) ? { kind: f.kind, id: f.id.slice(0, 64) } : null;
    const prev = seen.get(u.id);
    if (!prev) seen.set(u.id, { id: u.id, name: String(u.name || "—"), color: Number(u.color) || 1, readonly: !!u.readonly, focus });
    else if (!prev.focus && focus) prev.focus = focus;
  });
  return [...seen.values()];
}

export function openRoom(id: string, opts: { origin: string; workspaceId: string | null; kind?: RoomKind }): Room {
  const doc = new Y.Doc();
  const params: Record<string, string> = {};
  if (opts.workspaceId) params.workspace = opts.workspaceId;
  let changeSeq = 0;
  // No BroadcastChannel between tabs: the server is the only source of truth for what was applied.
  const provider = new WebsocketProvider(roomUrl(opts.origin, opts.kind || "template"), id, doc, { params, disableBc: true, maxBackoffTime: 5000 });
  let state = initialState();
  const listeners = new Set<(s: RoomState) => void>();
  const set = (patch: Partial<RoomState>) => {
    state = { ...state, ...patch };
    listeners.forEach((fn) => fn(state));
  };
  const refreshPeers = () => {
    const fromAwareness = peersOf(provider.awareness.getStates(), doc.clientID, state.me?.id ?? null);
    set({ peers: fromAwareness.length ? fromAwareness : state.peers });
  };

  provider.messageHandlers[MESSAGE_META] = (_encoder, decoder) => {
    let frame: MetaFrame;
    try { frame = JSON.parse(decoding.readVarString(decoder)); } catch { return; }
    switch (frame.type) {
      case "hello":
        // My other devices and tabs are not "others": the same person never shows up as a peer of themselves.
        set({ me: frame.user, readonly: frame.readonly, head: frame.head, dirty: frame.dirty, changesRev: frame.changes_rev,
              peers: (frame.peers || []).filter((p: PeerInfo) => p.id !== frame.user.id), recovered: frame.recovered === true, refused: false });
        break;
      case "state":
        set({ head: frame.head, dirty: frame.dirty, changesRev: frame.changes_rev });
        break;
      case "head":
        set({ head: frame.head, dirty: frame.dirty, changesRev: frame.changes_rev, lastHead: { reason: frame.reason, by: frame.by, byId: frame.by_id ?? null, head: frame.head } });
        break;
      case "role":
        set({ readonly: frame.readonly, refused: false });
        break;
      case "refused":
        set({ refused: true });
        break;
      case "joined": {
        const others = state.peers.filter((p) => p.id !== frame.user.id);
        set({ peers: frame.user.id === state.me?.id ? state.peers : [...others, frame.user] });
        break;
      }
      case "left":
        // Awareness tells us whether another tab of theirs is still here; fall back to removing.
        set({ peers: peersOf(provider.awareness.getStates(), doc.clientID, state.me?.id ?? null).length
          ? peersOf(provider.awareness.getStates(), doc.clientID, state.me?.id ?? null)
          : state.peers.filter((p) => p.id !== frame.user.id) });
        break;
      case "activity":
        set({ changesRev: frame.changes_rev });
        break;
      case "change":
        set({ lastChange: { kind: String(frame.kind), detail: frame.detail || {}, by: frame.by, at: frame.at, seq: ++changeSeq } });
        break;
      case "closed":
        break;                                   // the close event that follows carries the code
    }
  };
  provider.on("status", ({ status }: { status: Status }) => set({ status }));
  provider.on("sync", (synced: boolean) => set({ synced }));
  provider.on("closed", ({ code, reason }: { code: number; reason: string }) => set({ closed: { code, reason }, status: "disconnected" }));
  provider.awareness.on("change", refreshPeers);

  return {
    doc,
    provider,
    subscribe(fn) { listeners.add(fn); fn(state); return () => { listeners.delete(fn); }; },
    state: () => state,
    setUser(user) { provider.awareness.setLocalStateField("user", user); },
    setFocus(focus) { provider.awareness.setLocalStateField("focus", focus); },
    destroy() { provider.awareness.off("change", refreshPeers); provider.destroy(); doc.destroy(); listeners.clear(); },
  };
}
