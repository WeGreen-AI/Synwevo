/** The application's API on this origin, and the helpers the page needs beyond the shared
 *  client: an upload that can be cancelled, the one-per-link card lookup, and the small
 *  fire-and-forget calls (typing hints, presence heartbeats) that must never surface as errors.
 *
 *  The prefix. On its own origin (and on the single-process deployment) the application is at
 *  /api/messaging; served from the account origin as the fallback of a deployment that has not
 *  added the application yet, the same routes live under the account service's former prefix.
 *  Set once per page from the origin's role (a page never changes origin), before any request. */
import { ApiError, authHeaders, get, post } from "@synwevo/auth-client";
import type { Capabilities, ObjectInfo, SyncAnswer } from "./types";

let prefix = "/api/messaging";
const LEGACY = "/api/auth/messaging";
export const prefixFor = (role: string | undefined) => (role === "accounts" ? LEGACY : "/api/messaging");
export function setPrefix(role: string | undefined) { prefix = prefixFor(role); }
/** The current prefix; a function so a module that captured it at import time cannot go stale. */
export const M = () => prefix;
export const threadPath = (cid: string) => `/c/${cid}`;
export const LIST = "/";
export const attachmentUrl = (id: string, inline = false) => `${prefix}/attachments/${id}${inline ? "?inline=1" : ""}`;

/** The server's real limits and this person's settings, fetched once per page; `null` until they
 *  arrive (the composer then shows no limit rather than a guessed one). */
let caps: Promise<Capabilities | null> | null = null;
export function capabilities(): Promise<Capabilities | null> {
  if (!caps) caps = get<Capabilities>(`${prefix}/capabilities`).catch(() => null);
  return caps;
}

/** A typing hint (§7.3): nothing is stored; a refusal (rate limit, presence off) is not news. */
export function sendTyping(cid: string, state: "start" | "stop"): void {
  post(`${prefix}/conversations/${cid}/typing`, { state }).catch(() => undefined);
}

/** A presence heartbeat (§7.3), only ever sent from a visible page. */
export function heartbeat(): Promise<{ shown: boolean; state: string | null } | null> {
  return post<{ shown: boolean; state: string | null }>(`${prefix}/presence`, {}).catch(() => null);
}

export function sync(since: number): Promise<SyncAnswer> {
  return get<SyncAnswer>(`${prefix}/sync?since=${encodeURIComponent(String(since))}`);
}

/** What an object conversation is about; `null` for an ordinary conversation (404). */
export function objectOf(cid: string): Promise<ObjectInfo | null> {
  return get<ObjectInfo>(`${prefix}/conversations/${cid}/object`).catch((e: unknown) => {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  });
}

export interface UploadHandle<T> { promise: Promise<T>; cancel: () => void }

/** A multipart upload with progress and a cancel. The shared `upload` helper has no cancel, and
 *  a file the person picked by mistake must be stoppable while it is still going up; this uses
 *  the same CSRF echo and the same error shape (`ApiError` from the server's JSON body) so a
 *  refusal reads like every other one on the page. Cancelling rejects with an AbortError. */
export function uploadWithCancel<T = any>(path: string, form: FormData, onProgress?: (p: number) => void): UploadHandle<T> {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<T>((resolve, reject) => {
    xhr.open("POST", path);
    for (const [k, v] of Object.entries(authHeaders("POST", false))) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onerror = () => reject(new ApiError(0, "network", "上传失败：网络错误"));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    xhr.onload = () => {
      let body: any = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not json */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body);
      const requestId = (body && typeof body.request_id === "string" && body.request_id) || xhr.getResponseHeader("X-Request-ID") || undefined;
      const err = new ApiError(xhr.status, body?.error || "upload_failed", body?.message || `上传失败（HTTP ${xhr.status}）`, body?.detail);
      err.requestId = requestId;
      reject(err);
    };
    xhr.send(form);
  });
  return { promise, cancel: () => xhr.abort() };
}

export type LinkCardData = { kind: "project" | "job"; title: string; workspace?: string | null; archived?: boolean; path: string;
  status?: string; done?: number; failed?: number };
// One lookup per link per page: the answer is this reader's, and a 404 (no access, no such
// object, not a Docs link) simply leaves the plain link as it is.
const cardCache = new Map<string, Promise<LinkCardData | null>>();
export function cardFor(url: string): Promise<LinkCardData | null> {
  let p = cardCache.get(url);
  if (!p) {
    p = get(`${prefix}/link-card?url=${encodeURIComponent(url)}`).then((c: LinkCardData) => c, () => null);
    cardCache.set(url, p);
  }
  return p;
}
