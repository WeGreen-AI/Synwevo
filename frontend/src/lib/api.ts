export class ApiError extends Error {
  status: number;
  code: string;
  detail: any;
  constructor(status: number, code: string, message: string, detail?: any) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

async function handle(res: Response) {
  if (res.ok) {
    const ct = res.headers.get("content-type") || "";
    return ct.includes("application/json") ? res.json() : res;
  }
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    /* not json */
  }
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
export const post = <T = any>(p: string, body: any = {}) => api<T>(p, { method: "POST", body });
export const put = <T = any>(p: string, body: any = {}) => api<T>(p, { method: "PUT", body });
export const patch = <T = any>(p: string, body: any = {}) => api<T>(p, { method: "PATCH", body });
export const del = <T = any>(p: string) => api<T>(p, { method: "DELETE" });

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
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(xhr.status, body?.error || "upload_failed", body?.message || `上传失败（HTTP ${xhr.status}）`, body?.detail));
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
