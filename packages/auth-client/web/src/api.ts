export class ApiError extends Error {
  status: number;
  code: string;
  detail: any;
  /** True when the response did not come from Synwevo but from something in front of it.
   *  Call sites that branch on `status` need this: an intermediary's 403 is not a refusal by
   *  the application, and a poll must not treat it as a terminal answer. */
  upstream: boolean;
  /** Seconds to wait before trying again, from `Retry-After` (or `detail.retry_after`) on a 429
   *  or 503. A limit that says when it lifts is only useful if the page repeats it. */
  retryAfter?: number;
  /** The server's request id, when it sent one: what an operator needs to find the log line. */
  requestId?: string;
  constructor(status: number, code: string, message: string, detail?: any, upstream = false) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.upstream = upstream;
  }
}

/** Header lookup shared by the fetch and XHR paths.  Every header is readable here because the
 *  UI is served by the same origin as the API, so the response is not a CORS response. */
type Headers_ = { get: (name: string) => string | null };

/** Name the intermediary for the message.  Vendor headers are good enough to name a culprit but
 *  far too narrow to *detect* one, so they are never used as the test. */
function gatewayName(h: Headers_): string {
  const v = (n: string) => h.get(n) || "";
  if (v("cf-mitigated") || v("cf-ray") || /cloudflare/i.test(v("server"))) return "Cloudflare";
  const server = v("server").split("/")[0].trim();
  if (server && server !== "uvicorn") return server;
  return "前置网关";
}

/** Did Synwevo itself produce this response?
 *
 *  Three independent marks say yes, and a response needs only one of them:
 *   - the body parses as JSON — Synwevo answers *every* error with `{error, message, detail,
 *     request_id}`, including 401, 404 and 400;
 *   - an `X-Request-ID` header — stamped on every response that passes through the gate
 *     middleware;
 *   - `Server: uvicorn` — the ASGI server Synwevo runs under. Starlette's outermost 500 handler
 *     sits outside the gate middleware and answers an unhandled exception with a plain-text
 *     body and no request id, so without this mark every server bug was reported to people as
 *     "blocked by a gateway, never reached Synwevo" and operators were sent to the CDN. A
 *     gateway that answers on its own puts its own name in `Server`, so this mark cannot be
 *     borrowed by one.
 *
 *  No mark means a CDN, WAF, SSO gateway or reverse proxy answered instead and the request never
 *  reached the service. Reporting that as「请求失败（HTTP 403）」blames the application for a
 *  refusal it never made, and sends whoever debugs it to the wrong logs — the access log has no
 *  trace of the request, because it never arrived. */
function fromSynwevo(h: Headers_, body: any): boolean {
  return !!body || !!h.get("X-Request-ID") || /^uvicorn\b/i.test((h.get("server") || "").trim());
}

/** 502/503/504 from something in front of Synwevo mean it could not reach the service (down,
 *  restarting, timed out) — a different fault, with a different fix, from a rule that blocked
 *  the request. Saying "blocked" for a stopped service sends people to the wrong setting. */
const UNREACHABLE = new Set([502, 503, 504]);

function gatewayError(status: number, h: Headers_): ApiError {
  const via = gatewayName(h);
  if (UNREACHABLE.has(status)) {
    return new ApiError(status, "upstream_unavailable",
      `${via} 没有从 Synwevo 得到响应（HTTP ${status}）。服务可能没有启动、正在重启，或处理超时。请稍后重试。`,
      { gateway: via, status, hint: "请检查服务进程是否在运行，以及反向代理指向的地址与端口。排查步骤见 docs/integration/ports.md。" },
      true);
  }
  return new ApiError(status, "gateway_blocked",
    `请求被 ${via} 拦截（HTTP ${status}），没有到达 Synwevo。请检查 CDN / WAF / 反向代理的安全规则。`,
    { gateway: via, status, hint: "服务端访问日志里不会有这条请求——它没有到达服务。排查步骤见 docs/integration/ports.md。" },
    true);
}

/** `Retry-After` in seconds (the HTTP-date form is not used by Synwevo), else the body's
 *  `detail.retry_after`, which the account service's own limits carry. */
function retryAfterOf(h: Headers_, body: any): number | undefined {
  const raw = (h.get("Retry-After") || "").trim();
  if (/^\d+$/.test(raw)) return Number(raw);
  const d = body?.detail?.retry_after;
  return typeof d === "number" && isFinite(d) && d > 0 ? Math.ceil(d) : undefined;
}

/** The error a Synwevo response describes. A 5xx without a JSON body is Synwevo's own crash
 *  handler: it is named as a server fault, with the request id when there is one, rather than
 *  as a bare status code. */
function synwevoError(status: number, h: Headers_, body: any, fallback: string): ApiError {
  const requestId = (body && typeof body.request_id === "string" && body.request_id) || h.get("X-Request-ID") || undefined;
  const msg = body?.message || (typeof body?.detail === "string" ? body.detail : null)
    || (status >= 500
      ? `服务内部出错（HTTP ${status}）。请稍后重试；如果一直出现，请把出错时间${requestId ? `和请求编号 ${requestId} ` : ""}告诉管理员，以便在服务日志里查找。`
      : fallback);
  const err = new ApiError(status, body?.error || (status >= 500 ? "server_error" : "http_error"), msg, body?.detail);
  err.retryAfter = retryAfterOf(h, body);
  err.requestId = requestId;
  return err;
}

async function handle(res: Response) {
  if (res.ok) {
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) return res.json();
    // An SSO gateway with an expired session answers 200 with its own login page rather than
    // 401. Returning that Response to callers used to white-screen the app the moment one of
    // them treated it as data (`projects.filter(...)`), with no error boundary to catch it.
    if (ct.includes("text/html") && !res.headers.get("X-Request-ID")) throw gatewayError(res.status, res.headers);
    return res;                                   // 204 and binary responses stay as they were
  }
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* not json — see fromSynwevo */
  }
  if (!fromSynwevo(res.headers, body)) throw gatewayError(res.status, res.headers);
  throw synwevoError(res.status, res.headers, body, `请求失败（HTTP ${res.status}）`);
}

const CSRF_COOKIE = "dw_csrf";
const CSRF_HEADER = "X-Synwevo-CSRF";
const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

/** The CSRF cookie is readable on purpose — the page has to echo it back in a header.
 *  It is not a credential by itself; it only proves the request came from our own page. */
function csrfToken(): string | null {
  const hit = document.cookie.split("; ").find((c) => c.startsWith(CSRF_COOKIE + "="));
  return hit ? decodeURIComponent(hit.slice(CSRF_COOKIE.length + 1)) : null;
}

/** Headers for one request, adding the CSRF echo on anything that changes state.
 *  Absent in deployments without the account system, where the server does not ask for it. */
export function authHeaders(method: string, hasBody: boolean): Record<string, string> {
  const h: Record<string, string> = {};
  if (hasBody) h["Content-Type"] = "application/json";
  if (!SAFE.has(method.toUpperCase())) {
    const t = csrfToken();
    if (t) h[CSRF_HEADER] = t;
  }
  return h;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: any; signal?: AbortSignal } = {}): Promise<T> {
  let res: Response;
  const method = opts.method || (opts.body !== undefined ? "POST" : "GET");
  try {
    res = await fetch(path, {
      method,
      headers: authHeaders(method, opts.body !== undefined),
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (e: any) {
    if (e?.name === "AbortError") throw e;
    // There is no `run.py` any more; the command that starts the services is the one named here.
    throw new ApiError(0, "network", "无法连接到服务器。请检查网络，或确认服务已启动（python scripts/platform.py status）。");
  }
  return handle(res);
}

export const get = <T = any>(p: string, signal?: AbortSignal) => api<T>(p, { signal });
export const post = <T = any>(p: string, body: any = {}, signal?: AbortSignal) => api<T>(p, { method: "POST", body, signal });
export const put = <T = any>(p: string, body: any = {}, signal?: AbortSignal) => api<T>(p, { method: "PUT", body, signal });
export const patch = <T = any>(p: string, body: any = {}, signal?: AbortSignal) => api<T>(p, { method: "PATCH", body, signal });
export const del = <T = any>(p: string, signal?: AbortSignal) => api<T>(p, { method: "DELETE", signal });

/** multipart upload with progress */
export function upload<T = any>(path: string, form: FormData, onProgress?: (p: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    // Uploads are state-changing too, and they go through XHR rather than fetch, so the CSRF
    // echo has to be set here as well or every upload 403s under session auth.
    for (const [k, v] of Object.entries(authHeaders("POST", false))) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new ApiError(0, "network", "上传失败：网络错误"));
    xhr.onload = () => {
      let body: any = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body);
      // A multipart POST carrying the user's own file is the request a WAF or a body-size limit
      // stops first, and「上传失败」reads as Synwevo rejecting the file — so people go and
      // rename, convert or shrink a file that was never the problem. getResponseHeader is
      // unfiltered for a same-origin XHR, so the same two marks are available here.
      const h = { get: (n: string) => xhr.getResponseHeader(n) };
      if (!fromSynwevo(h, body)) return reject(gatewayError(xhr.status, h));
      const err = synwevoError(xhr.status, h, body, `上传失败（HTTP ${xhr.status}）`);
      if (err.code === "http_error") err.code = "upload_failed";
      reject(err);
    };
    xhr.send(form);
  });
}

export function qs(params: Record<string, any>) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}
