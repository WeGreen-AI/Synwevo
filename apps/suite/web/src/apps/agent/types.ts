export type ContextKind = "form" | "table" | "approval_template" | "approval_request" | "extract_template" | "extract_task" | "dataprep_recipe" | "dataprep_run";
export const CONTEXT_KINDS: ContextKind[] = ["form", "table", "approval_template", "approval_request", "extract_template", "extract_task", "dataprep_recipe", "dataprep_run"];
export interface ContextItem { kind: ContextKind; workspace_id: string; resource_id: string; title: string }
/** The exact reference returned by agent.context.resolve; no business content is sent by the browser. */
export interface ContextReference { kind: ContextKind; workspace_id: string; resource_id: string; snapshot: string }
export interface MessageContext { title: string; workspace_name: string | null; open_path: string | null; kind?: "file";
  /** a personal file the local engine read from pixels: recognized text, never verified text */
  recognized?: boolean }
export interface SelectedContext extends MessageContext { reference: ContextReference }
export interface ContextPage { items: ContextItem[]; next_offset: number | null; limited: boolean }

/** What the chat needs to recognise a call of an external MCP tool (engine.call_json "mcp"). */
export interface McpCallFacts {
  connection: string; connection_name: string; tool: string; is_error: boolean | null; duration_ms: number | null;
  truncated: boolean | null; call_id: string | null; external: boolean;
}
export interface ToolCall {
  id: string; tool: string; operation: string | null; operation_title: Record<string, string> | null;
  side_effect?: string | null;
  /** Present for an external MCP tool (operation ids of the form mcp:<alias>/<tool>); null otherwise. */
  mcp?: McpCallFacts | null;
  app_name: string | null; workspace_id: string | null; workspace_name: string | null;
  target_workspace_id: string | null; target_workspace_name: string | null;
  /** Other workspaces whose content this write would carry into its target (synwevo_core.data_flow). */
  transfer_from?: { workspace_id: string; workspace_name: string | null; outbound: string }[] | null;
  status: string; confirmation: string | null; needs_confirmation: boolean;
  input: unknown; result: unknown; hidden: boolean; link: string | null; run_id: string | null;
  error: { code: string; message: string } | null;
}
export interface Message {
  id: string; parent_id: string | null; role: string; status: string; body: string; hidden: boolean;
  error: { code: string; message: string } | null;
  /** `cost` is the ledger's settled figure for this answer (synwevo_suite.agent.turn_cost), or null
   *  when the turn has no ledger run; validate it with `validTurnCost` before showing it. */
  /** `stopping`: the person asked to stop and the answer has not reached a stop point yet. */
  turn: { id: string; status: string; model: string; credential: string; stopping?: boolean; cost?: unknown } | null;
  tool_calls: ToolCall[]; created_at: number;
  /** Current, authorized source labels only. Hidden messages have no contexts. Never replay these. */
  contexts?: MessageContext[];
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

export interface FileReference { file_id: string; snapshot: string }
export interface SelectedFile { title: string; reference: FileReference }
export interface FileCapabilities {
  configured: boolean; formats: string[]; max_bytes: number; max_files: number; max_storage_bytes: number;
  max_context_chars: number; unattached_retention_seconds: number; scanner: Record<string, unknown> | null;
  /** The deployment recognizes PNG/JPEG images and scanned PDF pages with its local engine; recognized
   *  lines are marked and carry a confidence, never presented as verified text. */
  ocr: boolean; ocr_languages?: string[]; ocr_pages?: number | null;
}
export interface AgentFile {
  id: string; filename: string; size: number; created_at: number; updated_at: number; attempts: number;
  status: 'receiving' | 'queued' | 'parsing' | 'ready' | 'failed' | 'cancelled' | 'deleted';
  error_code: string | null; scan_status: string; reference: FileReference | null; download_path: string | null;
  extraction?: { lines: { text: string; source: Record<string, unknown> }[];
    tables: { name: string; source: Record<string, unknown>; rows: unknown[][]; row_numbers?: number[] }[];
    ocr?: { applied: boolean; engine?: string | null; pages?: number; words?: number; low_confidence_words?: number } } | null;
}
export interface FilePage { files: AgentFile[]; next_offset: number | null }
