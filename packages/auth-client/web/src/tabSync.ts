/** Counts shared by every tab of one origin. One origin means one session cookie, so one person:
 *  an unread count one tab learns (a poll, reading a conversation, marking notifications read) is
 *  posted to the others, and two open windows no longer disagree until the next poll. The browser
 *  keeps BroadcastChannel to one origin, so other hosts (the workspace and Synwevo Message) keep
 *  their own poll and their refresh on becoming visible. A tab never receives its own post, so a
 *  received count is not posted again. Without BroadcastChannel nothing changes. */
const channels = new Map<string, BroadcastChannel | null>();

function channel(name: string): BroadcastChannel | null {
  if (!channels.has(name)) {
    let ch: BroadcastChannel | null = null;
    try { ch = typeof BroadcastChannel === "function" ? new BroadcastChannel(name) : null; } catch { ch = null; }
    // Node (the unit tests) keeps a process alive while a channel is open; browsers have no unref
    (ch as unknown as { unref?: () => void } | null)?.unref?.();
    channels.set(name, ch);
  }
  return channels.get(name) ?? null;
}

const valid = (n: unknown): n is number => typeof n === "number" && Number.isInteger(n) && n >= 0 && n < 1e7;

export function broadcastCount(name: string, n: number): void {
  if (!valid(n)) return;
  try { channel(name)?.postMessage({ n }); } catch { /* a closed channel: the next poll corrects it */ }
}

export function onBroadcastCount(name: string, fn: (n: number) => void): () => void {
  const ch = channel(name);
  if (!ch) return () => undefined;
  const handler = (e: MessageEvent) => { const n = (e.data as { n?: unknown } | null)?.n; if (valid(n)) fn(n); };
  ch.addEventListener("message", handler);
  return () => ch.removeEventListener("message", handler);
}

export const MESSAGES_UNREAD = "synwevo-messages-unread";
export const NOTIFICATIONS_UNREAD = "synwevo-notifications-unread";
