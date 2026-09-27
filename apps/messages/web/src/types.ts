/** The shapes Synwevo Message's API answers with (`synwevo_core.messaging._conv_out` /
 *  `_message_out`), plus the two client-only marks a message carries while it is in flight.
 *  Nothing here is inferred: a field is listed because the server sends it. Optional fields
 *  marked "contract" are rendered only when present, so the shell works the moment the backend
 *  ships them and shows nothing until then. */

export interface Member { user_id: string; name: string; role: "owner" | "admin" | "member" }

export interface LastMessage {
  seq: number; sender_id: string; sender: string; kind: string; body: string; deleted: boolean; created_at: number; attachment: boolean;
}

export interface Conversation {
  id: string; organization_id: string; kind: "dm" | "group"; name: string; description: string | null; archived: boolean; last_seq: number;
  announcement: string | null; announcement_at: number | null; last_message_at: number | null;
  me: { role: string; joined_seq: number; last_read_seq: number; muted: boolean; pinned: boolean; notify: "all" | "mentions" | "none" };
  unread: number; peer: { user_id: string; name: string } | null; members: Member[]; member_count: number;
  last_message: LastMessage | null;
  /** contract: unread messages that mention this reader; absent until the backend sends it. */
  unread_mentions?: number;
}

export interface Reaction { emoji: string; users: string[]; count: number }

export interface Attachment { id: string; filename: string; size: number; content_type: string; inline: boolean }

/** contract (backend agent, CI-73): a message about an application object, rendered as a card. */
export interface AppCard {
  app: string; app_name: string; title: string; subtitle?: string; state?: string;
  object?: { kind: string; id: string };
  actions?: { label: string; href: string }[];
}

export interface Message {
  id: string; conversation_id: string; seq: number; sender_id: string; sender: string; kind: "text" | "system"; body: string; reply_to: string | null;
  mentions: string[]; edited_at: number | null; deleted: boolean; created_at: number; client_id: string | null; reactions: Reaction[];
  attachment: Attachment | null;
  reply?: { id: string; seq: number; sender: string; body: string; deleted: boolean };
  system?: Record<string, any>;
  card?: AppCard | null;
  /** client-only: stored locally, not yet acknowledged by the server */
  pending?: boolean;
  /** client-only: the server refused or could not be reached; the text says why */
  failed?: string;
}

export interface Org { id: string; name: string; status: string }

export interface Capabilities {
  max_body: number; max_attachment_bytes: number; reactions: string[]; heartbeat_s: number;
  limits: { messages_per_minute: number; attachments_per_hour: number; groups_per_day: number };
}

export interface HistoryPage { items: Message[]; has_more: boolean; floor: number; last_seq: number; last_read_seq: number }

export type Live = "connecting" | "live" | "polling";
