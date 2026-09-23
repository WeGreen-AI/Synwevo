export class ApiError extends Error {
  status: number;
  code: string;
  detail: any;
  /** True when the response did not come from DocWeave but from something in front of it.
   *  Call sites that branch on `status` need this: an intermediary's 403 is not a refusal by
   *  the application, and a poll must not treat it as a terminal answer. */
  upstream: boolean;
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

/** Did DocWeave itself produce this response?
 *
 *  Two independent marks say yes, and a response needs only one of them:
 *   - the body parses as JSON — DocWeave answers *every* error with `{error, message, detail,
 *     request_id}`, including 401, 404 and 400;
 *   - an `X-Request-ID` header — stamped on every response that passes through the gate
 *     middleware. (Starlette's outermost 500 handler sits outside that middleware and can miss
 *     the header, which is exactly why the JSON mark is kept as an alternative.)
 *
 *  Neither mark means a CDN, WAF, SSO gateway or reverse proxy answered instead and the request
 *  never reached the service. Reporting that as「请求失败（HTTP 403）」blames the application for
 *  a refusal it never made, and sends whoever debugs it to the wrong logs — the access log has
 *  no trace of the request, because it never arrived. */
function fromDocWeave(h: Headers_, body: any): boolean {
  return !!body || !!h.get("X-Request-ID");
}

function gatewayError(status: number, h: Headers_): ApiError {
  const via = gatewayName(h);
  return new ApiError(status, "gateway_blocked",
    `请求被 ${via} 拦截（HTTP ${status}），没有到达 DocWeave。请检查 CDN / WAF / 反向代理的安全规则。`,
    { gateway: via, status, hint: "服务端访问日志里不会有这条请求——它没有到达服务。排查步骤见 docs/integration/ports.md。" },
    true);
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
    /* not json — see fromDocWeave */
  }
  if (!fromDocWeave(res.headers, body)) throw gatewayError(res.status, res.headers);
  const msg = body?.message || (typeof body?.detail === "string" ? body.detail : null) || `请求失败（HTTP ${res.status}）`;
  throw new ApiError(res.status, body?.error || "http_error", msg, body?.detail);
}

export async function api<T = any>(path: string, opts: { method?: string; body?: any; signal?: AbortSignal } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
      headers: opts.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (e: any) {
    if (e?.name === "AbortError") throw e;
    throw new ApiError(0, "network", "无法连接到服务器，请确认服务已启动（python run.py）");
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
      // stops first, and「上传失败」reads as DocWeave rejecting the file — so people go and
      // rename, convert or shrink a file that was never the problem. getResponseHeader is
      // unfiltered for a same-origin XHR, so the same two marks are available here.
      const h = { get: (n: string) => xhr.getResponseHeader(n) };
      if (!fromDocWeave(h, body)) return reject(gatewayError(xhr.status, h));
      reject(new ApiError(xhr.status, body?.error || "upload_failed", body?.message || `上传失败（HTTP ${xhr.status}）`, body?.detail));
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
