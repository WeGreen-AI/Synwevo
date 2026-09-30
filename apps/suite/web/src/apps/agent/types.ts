export interface ToolCall {
  id: string; tool: string; operation: string | null; operation_title: Record<string, string> | null;
  app_name: string | null; workspace_id: string | null; workspace_name: string | null;
  target_workspace_id: string | null; target_workspace_name: string | null;
  status: string; confirmation: string | null; needs_confirmation: boolean;
  input: unknown; result: unknown; hidden: boolean; link: string | null; run_id: string | null;
  error: { code: string; message: string } | null;
}
export interface Message {
  id: string; parent_id: string | null; role: string; status: string; body: string; hidden: boolean;
  error: { code: string; message: string } | null;
  turn: { id: string; status: string; model: string; credential: string } | null;
  tool_calls: ToolCall[]; created_at: number;
}
export interface Conversation {
  id: string; title: string; status: string; head_id: string | null; scope: string[] | null;
  sealed: string | null; messages: Message[]; event_seq: number; updated_at: number;
}
export interface Workspace { workspace_id: string; name: string; role: string; processing: string }

/** A branch follows stored parent links; sibling answers are never replayed into its transcript. */
export function branchMessages(messages: Message[], head: string | null): Message[] {
  const byId = new Map(messages.map((m) => [m.id, m]));
  const seen = new Set<string>();
  const branch: Message[] = [];
  let id = head;
  while (id && !seen.has(id)) {
    seen.add(id);
    const message = byId.get(id);
    if (!message) break;
    branch.push(message);
    id = message.parent_id;
  }
  return branch.reverse();
}
export const activeTurn = (m: Message) => !!m.turn && ["queued", "running", "waiting_confirmation"].includes(m.turn.status);
