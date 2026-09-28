/** The shapes Synwevo Message's API answers with (`synwevo_core.messaging._conv_out` /
 *  `_message_out`, docs/platform/messaging.md §7–§8), plus the two client-only marks a message
 *  carries while it is in flight. Nothing here is inferred: a field is listed because the server
 *  sends it, and a `null` from the server means "not shown", never "no". */

export type Presence = "online" | "away" | "offline" | null;

export interface Member { user_id: string; name: string; role: "owner" | "admin" | "member"; presence?: Presence }

export interface LastMessage {
  seq: number; sender_id: string; sender: string; kind: string; body: string; deleted: boolean; created_at: number; attachment: boolean;
  /** deleted for this reader only (§7.6) */
  hidden?: boolean;
}

/** Another member's read position, listed only when both sides turned receipts on (§7.4). */
export interface ReadBy { user_id: string; last_read_seq: number; at: number | null }

export interface Me {
  role: string; joined_seq: number; last_read_seq: number; muted: boolean; pinned: boolean; notify: "all" | "mentions" | "none";
  muted_until?: number | null; marked_unread?: boolean; history_from?: "join" | "all";
}

export interface Conversation {
  id: string; organization_id: string; kind: "dm" | "group"; name: string; description: string | null; archived: boolean; last_seq: number;
  announcement: string | null; announcement_at: number | null; last_message_at: number | null;
  me: Me; unread: number;
  /** `blocked_by_me`: this reader blocked the other side (§7.18). Whether the other side blocked
   *  this reader is never sent — the blocked person is not told. */
  peer: { user_id: string; name: string; presence?: Presence; blocked_by_me?: boolean } | null;
  members: Member[]; member_count: number;
  last_message: LastMessage | null;
  /** null = not shown (this reader's receipts are off); a list otherwise */
  read_by?: ReadBy[] | null;
  /** whether this reader may use @所有人 here (§7.22): a group's owner or admin, not an object discussion */
  can_mention_all?: boolean;
  /** unread messages that mention this reader (§7.15): after the read floor, not recalled/removed,
   *  not deleted for this reader, not from someone this reader blocked. */
  unread_mentions?: number;
  /** a group's avatar (§7.17); `updated_at` versions the image address. null = no avatar. */
  avatar?: { updated_at: number } | null;
}

/** Someone this person blocked (§7.18, `GET /blocks`). */
export interface BlockEntry { user_id: string; name: string; created_at: number }

export interface Reaction { emoji: string; users: string[]; count: number }

export interface Attachment {
  id: string; filename: string; size: number; content_type: string; inline: boolean;
  /** a voice message (§7.8); `duration_ms` is null when the sender declared none — no invented length */
  voice?: boolean; duration_ms?: number | null;
  /** a rescan found the bytes infected (CI-90): shown by name only, the download answers 410 */
  rejected?: boolean;
}

/** A card sent by an application (§8.3), rendered per reader: full, or `degraded` (neutral
 *  title, no id, no subtitle, no actions) for a reader who may not open the object. */
export interface AppCard {
  app: string; app_name: string; title: string; subtitle?: string; state?: string;
  object?: { kind: string; id?: string };
  actions?: { label: string; href: string }[];
  degraded?: boolean;
}

/** What the composer passes for @所有人 among the mentioned ids; sent as `mention_all: true` (§7.22). */
export const MENTION_ALL = "@all";
/** The @所有人 label in every language the shell speaks: the flag follows the text, so a draft
 *  restored after switching conversations (or written in another language) still carries it. */
export const MENTION_ALL_LABELS = ["所有人", "Everyone"];

export interface Message {
  id: string; conversation_id: string; seq: number; sender_id: string; sender: string; kind: "text" | "system"; body: string; reply_to: string | null;
  mentions: string[];
  /** @所有人 by a group's owner or an admin: counts as a mention of every member (§7.22) */
  mention_all?: boolean;
  edited_at: number | null; deleted: boolean; created_at: number; client_id: string | null; reactions: Reaction[];
  attachment: Attachment | null;
  reply?: { id: string; seq: number; sender: string; body: string; deleted: boolean; recalled?: boolean };
  /** a system line, or the marker left by a recall / a manager's removal (§7.6) */
  system?: Record<string, any>;
  recalled?: boolean;
  removed_by?: { user_id: string; name: string } | null;
  /** the source conversation id only when this reader is a member there (§7.7) */
  forwarded_from?: { sender: string; conversation_id: string | null } | null;
  sender_kind?: "user" | "app";
  card?: AppCard | null;
  /** client-only: stored locally, not yet acknowledged by the server */
  pending?: boolean;
  /** client-only: the server refused or could not be reached; the text says why */
  failed?: string;
}

export interface Org { id: string; name: string; status: string }

export interface Settings { show_presence: boolean; read_receipts: boolean }

export interface Capabilities {
  max_body: number; max_attachment_bytes: number; reactions: string[]; heartbeat_s: number;
  limits: { messages_per_minute: number; attachments_per_hour: number; groups_per_day: number; forward_targets?: number; [k: string]: number | undefined };
  recall_window_s?: number; edit_window_s?: number;
  voice?: { types: string[]; max_bytes: number; max_duration_ms: number };
  search?: { engine: string; tokenizer: string | null; min_chars?: number; filters: string[]; max_limit: number };
  presence?: { online_s: number; away_s: number; interval_s: number; states: string[] };
  typing_ttl_s?: number;
  stream?: { event_ids: boolean; replay_ring: number; ephemeral: string[] };
  history_from?: string[];
  settings?: Settings;
  /** group avatars (§7.17): the platform's avatar rules */
  group_avatar?: { types: string[]; max_bytes: number; max_side: number };
  /** blocking (§7.18) is offered by this server */
  blocks?: boolean;
}

export interface HistoryPage { items: Message[]; has_more: boolean; floor: number; last_seq: number; last_read_seq: number; read_by?: ReadBy[] | null }

export type SearchItem = Message & { cid: string; snippet: string; ranges: [number, number][] };
export interface SearchAnswer { items: SearchItem[]; engine: "fts5" | "like"; tokenizer: string | null; fallback: boolean }

export interface SyncAnswer {
  cursor: number; since: number;
  conversations: { id: string; last_seq: number }[]; left: string[];
  recalled: { id: string; conversation_id: string; seq: number; how: "recalled" | "removed" | "deleted" }[];
  truncated: boolean;
}

/** What an object conversation is about (§8.1): full, or neutral when the reader may not open it. */
export interface ObjectInfo { app: string; app_name: string; kind: string; id?: string; title: string; href?: string | null; workspace_id?: string; degraded?: boolean }

export interface TypingState { user_id: string; name: string; until: number }

export type Live = "connecting" | "live" | "polling";
