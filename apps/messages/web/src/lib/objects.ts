/** The client half of the object → conversation contract (`/objects/:app/:kind/:id`): ask the
 *  server which conversation is about this object and classify the answer. Pure (the request
 *  function is passed in) so the 404 branch and the malformed-answer branch can be tested without
 *  a browser. */
import { ApiError } from "@synwevo/auth-client/api";

export type Resolution = { kind: "open"; id: string } | { kind: "missing" } | { kind: "error"; error: unknown };

export function objectConversationPath(prefix: string, app: string, kind: string, id: string): string {
  return `${prefix}/objects/${encodeURIComponent(app)}/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/conversation`;
}

export async function resolveObjectConversation(
  postFn: (path: string, body: Record<string, never>) => Promise<unknown>, prefix: string, app: string, kind: string, id: string,
): Promise<Resolution> {
  try {
    const out = await postFn(objectConversationPath(prefix, app, kind, id), {}) as { id?: unknown } | null;
    if (out && typeof out.id === "string" && out.id) return { kind: "open", id: out.id };
    return { kind: "error", error: new ApiError(502, "invalid_response", "服务没有返回会话编号") };
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return { kind: "missing" };
    return { kind: "error", error: e };
  }
}
