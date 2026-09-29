/** The one way the applications' pages ask for something: `POST /api/suite/ops/{operation}`.
 *
 *  The same entry a script and the personal Agent use, so a page cannot do what the API refuses
 *  or the reverse (acceptance A4). The workspace is named in the body of every call and in the
 *  per-request header — never inherited from whatever this tab was last switched to — so a page
 *  showing workspace B cannot write into workspace A. */
import { api, get } from "@synwevo/auth-client";

export type RunStatus = "queued" | "running" | "waiting_input" | "waiting_approval" | "succeeded" | "partial"
  | "failed" | "cancelled" | "unknown";

export interface OpResult<T> {
  run_id: string;
  operation: string;
  status: RunStatus;
  output: T;
  replayed: boolean;
  workspace_id: string | null;
}

/** A fresh idempotency key. A page keeps one per user intent (one click of "create"), so a retry
 *  after a dropped connection repeats the same request instead of creating a second record. */
export function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export async function op<T = any>(operation: string, input: Record<string, unknown>,
                                  opts: { workspace?: string | null; key?: string; signal?: AbortSignal } = {}): Promise<OpResult<T>> {
  const body: Record<string, unknown> = { input };
  if (opts.workspace) body.workspace_id = opts.workspace;
  if (opts.key) body.idempotency_key = opts.key;
  return api<OpResult<T>>(`/api/suite/ops/${encodeURIComponent(operation)}`, {
    method: "POST", body, signal: opts.signal, workspaceId: opts.workspace ?? undefined,
  });
}

/** Read-only operations return their output directly. */
export async function read<T = any>(operation: string, input: Record<string, unknown>,
                                    workspace: string | null, signal?: AbortSignal): Promise<T> {
  return (await op<T>(operation, input, { workspace, signal })).output;
}

export interface CatalogApp {
  id: string; name: string; path: string; icon: string; scope: "workspace" | "personal";
  summary: Record<string, string>; workspaces: { id: string; name: string; role: string }[];
}

export const catalog = (signal?: AbortSignal) => get<{ apps: CatalogApp[] }>("/api/suite/catalog", signal);
