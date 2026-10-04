#!/usr/bin/env node
// Synwevo Docs machine-API client for Node.js >= 18. One ES module file, no npm dependencies:
// it uses only fetch, FormData/Blob and node:* built-ins, so it can be copied next to a script,
// vendored into a repository or run straight from this directory.
//
//   import { Client, credentialsFromEnv } from './synwevo-docs.mjs';
//   const client = new Client(process.env.SYNWEVO_BASE_URL, await credentialsFromEnv());
//
//   node synwevo-docs.mjs whoami
//   node synwevo-docs.mjs generate --source customers.xlsx --template template.docx \
//        --fields fields.json --definition project.json --format docx --out ./out
//
// It is an *external* client of /api/v1: it knows the HTTP contract (docs/integration/api.md and
// `python -m synwevo_docs.cli openapi`) and nothing about the server's internals.
//
// Behaviour that is easy to get wrong and is therefore done here once:
// * Retries use exponential backoff with full jitter and honour Retry-After (seconds or
//   HTTP-date) and the RateLimit `t=` reset. They are applied only where repeating the request
//   cannot do something twice: GET/HEAD, PUT without a precondition, POSTs the server documents
//   as idempotent, and POSTs carrying an Idempotency-Key. One key is generated per logical call
//   and reused on every retry of that call, so a timeout never turns into a duplicate job.
// * Downloads stream to a temporary file, are hashed while streaming, checked against
//   X-Content-SHA256 and the manifest's sha256, fsync'ed, and only then renamed into place.
// * Webhook verification follows Standard Webhooks (multiple signatures during rotation,
//   timestamp tolerance, constant-time comparison) over the raw body bytes.
// * Secrets come from the environment or from files, never from the command line, and are
//   never printed.

import { createHash, createHmac, randomUUID, timingSafeEqual } from 'node:crypto';
import * as fs from 'node:fs';
import { mkdir, open, readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { basename, extname, join, resolve } from 'node:path';
import { Readable } from 'node:stream';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

export const VERSION = '0.1.0';
export const API_PREFIX = '/api/v1';
export const WORKSPACE_BASE_URL = 'https://accounts.synwevo.com';
export const WORKSPACE_RESOURCE = 'urn:synwevo:platform:workspace';
export const WORKSPACE_API_PREFIX = '/api/workspace/v1';
export const SUITE_BASE_URL = 'https://workplace.synwevo.com';
export const SUITE_TOKEN_URL = 'https://accounts.synwevo.com/oidc/token';
export const SUITE_RESOURCE = 'urn:synwevo:platform:suite';
export const SUITE_API_PREFIX = '/api/suite/v1';
export const SUITE_TERMINAL_STATUSES = Object.freeze(['succeeded', 'partial', 'failed', 'cancelled']);
export const SUITE_EVENT_STARTS = Object.freeze(['oldest', 'now']);
const SUITE_WEBHOOKS = '/webhooks/subscriptions';
export const TERMINAL_STATUSES = Object.freeze(['completed', 'completed_with_errors', 'failed', 'cancelled']);
export const SUCCESS_STATUSES = Object.freeze(['completed', 'completed_with_errors']);
export const ERROR_POLICIES = Object.freeze(['block', 'skip']);
export const LAYOUTS = Object.freeze(['off', 'warn', 'fit']);
export const ITEM_STATUSES = Object.freeze(['pending', 'running', 'success', 'failed', 'skipped', 'cancelled']);
export const WEBHOOK_TOLERANCE_S = 300;

const SAFE_METHODS = new Set(['GET', 'HEAD']);
// 409 idempotency_in_progress means "the first request with this key is still running": the
// server asks for a retry with the same key, so it is transient like a 503.
// 429 rate_limited is Synwevo's own gate refusing before the route runs: nothing executed, so it
// is retried after Retry-After even for writes that are otherwise not retry-safe.
const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);
const RETRYABLE_CODES = new Set(['idempotency_in_progress', 'upload_busy']);
const MIB = 1024 * 1024;
const MIME = {
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12',
  '.xls': 'application/vnd.ms-excel',
  '.csv': 'text/csv',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.doc': 'application/msword',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  '.pdf': 'application/pdf',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.json': 'application/json',
  '.txt': 'text/plain',
};

// --------------------------------------------------------------------------- errors
export class SynwevoError extends Error {
  constructor(message, { status = 0, code = null, requestId = null, field = null, details = null,
    retryAfter = null, method = null, url = null, cause } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = new.target.name;
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.field = field;
    this.details = details;
    this.retryAfter = retryAfter;      // seconds, when the server said how long to wait
    this.method = method;
    this.url = url;
  }

  toJSON() {
    return { error: this.name, status: this.status, code: this.code, message: this.message, field: this.field,
      request_id: this.requestId, details: this.details, retry_after: this.retryAfter };
  }
}
export class TransportError extends SynwevoError {}
export class APIError extends SynwevoError {}
export class BadRequestError extends APIError {}
export class AuthenticationError extends APIError {}
export class PermissionDeniedError extends APIError {}
export class NotFoundError extends APIError {}
export class ConflictError extends APIError {}
export class GoneError extends APIError {}
export class PayloadTooLargeError extends APIError {}
export class RangeNotSatisfiableError extends APIError {}
export class RateLimitError extends APIError {}
export class ServerError extends APIError {}
/** The bytes received are not the bytes the server (or the manifest) described. */
export class IntegrityError extends SynwevoError {}
/** Rejected before sending: the server documents these values and would refuse them too. */
export class ValidationError extends SynwevoError {}
export class WaitTimeoutError extends SynwevoError {}
/** A job reached a terminal status other than completed / completed_with_errors. */
export class JobFailedError extends SynwevoError {}
/** Preflight found problems that would block the job (error_policy=block). */
export class PreflightError extends SynwevoError {}
export class WebhookVerificationError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'WebhookVerificationError';
    this.code = code;
  }
}

const STATUS_CLASSES = { 400: BadRequestError, 401: AuthenticationError, 403: PermissionDeniedError,
  404: NotFoundError, 409: ConflictError, 410: GoneError, 413: PayloadTooLargeError,
  416: RangeNotSatisfiableError, 429: RateLimitError };

/** Retry-After as seconds: delta-seconds or an HTTP-date (RFC 9110 §10.2.3); null if absent. */
export function parseRetryAfter(value, nowMs = Date.now()) {
  if (value == null || value === '') return null;
  const s = String(value).trim();
  if (/^\d+$/.test(s)) return Number(s);
  const at = Date.parse(s);
  return Number.isNaN(at) ? null : Math.max(0, (at - nowMs) / 1000);
}

/** The `t` (seconds until quota is back) of an IETF RateLimit header, e.g. `"key";r=0;t=12`.
 *  With several policies (`"key";r=298;t=1, "workspace";r=0;t=20`) the longest reset among those with
 *  nothing left decides; when every policy has budget left, the first item's reset is returned. */
export function parseRateLimitReset(value) {
  const items = String(value ?? '').split(',').map(item => ({
    r: /(?:^|;)\s*r=(\d+)/.exec(item), t: /(?:^|;)\s*t=(\d+)/.exec(item) }));
  const spent = items.filter(i => i.r && i.t && Number(i.r[1]) === 0).map(i => Number(i.t[1]));
  if (spent.length) return Math.max(...spent);
  const first = items.find(i => i.t);
  return first ? Number(first.t[1]) : null;
}

/** Build the typed error for a non-2xx response (the envelope, RFC 9457 problem+json or OAuth). */
export function errorFromResponse(status, headers, text, { method = null, url = null } = {}) {
  const get = (name) => (typeof headers?.get === 'function' ? headers.get(name) : headers?.[name] ?? headers?.[name.toLowerCase()]);
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = null; }
  let code = null; let message = null; let field = null; let details = null;
  let requestId = get('x-request-id') || null;
  if (payload && typeof payload.error === 'object' && payload.error !== null) {
    ({ code = null, message = null, field = null, details = null } = payload.error);
    requestId = payload.error.request_id || requestId;
  } else if (payload && (payload.type || payload.title || payload.detail)) {
    code = payload.code || (String(payload.type || '').startsWith('urn:synwevo:problem:')
      ? String(payload.type).slice('urn:synwevo:problem:'.length) : null);
    message = payload.detail || payload.title || null;
    const pointer = payload.errors?.[0]?.pointer;
    field = payload.field || (pointer ? String(pointer).replace(/^#\//, '').replace(/\//g, '.') : null);
    details = payload.details || (payload.errors ? { errors: payload.errors } : null);
    requestId = payload.request_id || requestId;
  } else if (payload && typeof payload.error === 'string') {
    code = payload.error;                        // OAuth token endpoint: {error, error_description}
    message = payload.error_description || payload.error;
  }
  let retryAfter = parseRetryAfter(get('retry-after'));
  if (retryAfter == null && (status === 429 || status === 503)) retryAfter = parseRateLimitReset(get('ratelimit'));
  const Cls = STATUS_CLASSES[status] || (status >= 500 ? ServerError : APIError);
  const text2 = message || (text && !payload ? String(text).slice(0, 200) : `HTTP ${status}`);
  return new Cls(text2, { status, code, requestId, field, details, retryAfter, method, url });
}

// --------------------------------------------------------------------------- credentials
async function readSecretFile(path, what) {
  const st = await stat(path);
  if (process.platform !== 'win32' && (st.mode & 0o077)) {
    // Not fatal (the operator may have reasons), but a world-readable secret should be noticed.
    process.emitWarning(`${what} 文件 ${path} 对其他用户可读（权限 ${(st.mode & 0o777).toString(8)}），建议 chmod 600`);
  }
  const value = (await readFile(path, 'utf8')).trim();
  if (!value) throw new ValidationError(`${what} 文件 ${path} 是空的`, { field: what });
  return value;
}

/** A `dw_…` API key issued with `python -m synwevo_docs.cli apikey create`. */
export class ApiKey {
  constructor(key) {
    if (typeof key !== 'string' || !key.trim()) throw new ValidationError('API 密钥不能为空', { field: 'api_key' });
    // Kept off the instance's enumerable properties so console.log(client) cannot print it.
    Object.defineProperty(this, 'key', { value: key.trim(), enumerable: false });
  }

  async authorization() { return `Bearer ${this.key}`; }
  invalidate() {}
  get refreshable() { return false; }
  toJSON() { return { type: 'api_key', key: '***' }; }
}

/**
 * OAuth 2.0 client_credentials for a service account: fetches an access token from the account
 * service, caches it, and fetches a new one shortly before it expires (or once after a 401).
 */
export class ClientCredentials {
  constructor({ tokenUrl, clientId, clientSecret, scope = 'docweave.read docweave.write', resource = null,
    fetch: fetchImpl = globalThis.fetch, timeoutMs = 30_000, refreshMarginS = 60 } = {}) {
    if (!tokenUrl || !clientId || !clientSecret) {
      throw new ValidationError('client_credentials 需要 tokenUrl、clientId 与 clientSecret', { field: 'client_credentials' });
    }
    this.tokenUrl = tokenUrl;
    this.clientId = clientId;
    Object.defineProperty(this, 'clientSecret', { value: clientSecret, enumerable: false });
    this.scope = scope;
    this.resource = resource;
    this.fetch = fetchImpl;
    this.timeoutMs = timeoutMs;
    this.refreshMarginS = refreshMarginS;
    Object.defineProperty(this, '_token', { value: null, writable: true, enumerable: false });
    this._expiresAt = 0;
    this._pending = null;
  }

  get refreshable() { return true; }
  invalidate() { this._token = null; this._expiresAt = 0; }
  toJSON() { return { type: 'client_credentials', token_url: this.tokenUrl, client_id: this.clientId, scope: this.scope }; }

  async authorization() {
    if (this._token && Date.now() / 1000 < this._expiresAt - this.refreshMarginS) return `Bearer ${this._token}`;
    // Concurrent callers share one token request instead of each fetching their own.
    this._pending ??= this._fetchToken().finally(() => { this._pending = null; });
    await this._pending;
    return `Bearer ${this._token}`;
  }

  async _fetchToken() {
    // client_secret_post: the form fields the account service's /oidc/token reads.
    const form = new URLSearchParams({ grant_type: 'client_credentials', client_id: this.clientId,
      client_secret: this.clientSecret });
    if (this.scope) form.set('scope', this.scope);
    if (this.resource) form.set('resource', this.resource);
    let res;
    try {
      res = await this.fetch(this.tokenUrl, { method: 'POST', body: form, signal: AbortSignal.timeout(this.timeoutMs), redirect: 'manual',
        headers: { Accept: 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' } });
    } catch (e) {
      throw new TransportError(`无法连接令牌端点：${e.message}`, { method: 'POST', url: this.tokenUrl, cause: e });
    }
    const text = await res.text();
    if (!res.ok) throw errorFromResponse(res.status, res.headers, text, { method: 'POST', url: this.tokenUrl });
    const body = JSON.parse(text);
    if (!body.access_token) throw new AuthenticationError('令牌端点没有返回 access_token', { status: res.status });
    this._token = body.access_token;
    this._expiresAt = Date.now() / 1000 + Number(body.expires_in || 300);
  }
}

/**
 * A person's delegation to this client (authorization code + PKCE at the account service): the
 * access token and, when the person granted `offline_access`, the refresh token that replaces it.
 *
 * A refresh token is single use. Every refresh returns its successor, which is passed to
 * `onRotate` — store it: the previous one is spent, and presenting a spent refresh token revokes
 * the person's whole grant. A refresh whose answer never arrived is tried again with the same
 * token on the next call; if the server had already spent it the grant ends and the person has to
 * authorise the client again.
 *
 * One holder per refresh token: two processes (or two UserToken objects) started from the same
 * refresh token both try to spend it, the second presents a spent one, and the platform revokes
 * the grant for both. Let one process refresh and share the current access token.
 */
export class UserToken {
  constructor({ accessToken = null, refreshToken = null, tokenUrl = null, clientId = null, clientSecret = null,
    resource = null, expiresAt = null, onRotate = null, fetch: fetchImpl = globalThis.fetch, timeoutMs = 30_000,
    refreshMarginS = 60 } = {}) {
    const access = typeof accessToken === 'string' && accessToken.trim() ? accessToken.trim() : null;
    const refresh = typeof refreshToken === 'string' && refreshToken.trim() ? refreshToken.trim() : null;
    if (!access && !refresh) throw new ValidationError('用户授权凭据需要 accessToken 或 refreshToken', { field: 'user_token' });
    if (refresh && !(tokenUrl && clientId)) {
      throw new ValidationError('使用 refreshToken 时还需要 tokenUrl 与 clientId', { field: 'user_token' });
    }
    this.tokenUrl = tokenUrl;
    this.clientId = clientId;
    this.resource = resource;
    this.onRotate = onRotate;
    this.fetch = fetchImpl;
    this.timeoutMs = timeoutMs;
    this.refreshMarginS = refreshMarginS;
    this.grantedScope = null;
    // Kept off the enumerable properties so console.log(client) cannot print a token or the secret.
    for (const [name, value] of Object.entries({ clientSecret, _access: access, _refresh: refresh })) {
      Object.defineProperty(this, name, { value, writable: name !== 'clientSecret', enumerable: false });
    }
    this._expiresAt = expiresAt == null ? null : Number(expiresAt);
    this._pending = null;
  }

  get refreshable() { return Boolean(this._refresh); }
  get expiresAt() { return this._access ? this._expiresAt : null; }
  /** The API refused the access token. With a refresh token there is one more thing to try. */
  invalidate() { if (this._refresh) this._access = null; }
  toJSON() { return { type: 'user_token', client_id: this.clientId, refreshable: this.refreshable }; }

  async authorization() {
    const stale = !this._access
      || (this._expiresAt != null && Date.now() / 1000 >= this._expiresAt - this.refreshMarginS);
    if (stale && this._refresh) {
      // Concurrent callers share one exchange: a second one would present a spent token.
      this._pending ??= this._rotate().finally(() => { this._pending = null; });
      await this._pending;
    }
    if (!this._access) {
      throw new AuthenticationError('访问令牌已失效，且没有可用的刷新令牌；请让本人重新授权', { status: 401, code: 'token_expired' });
    }
    return `Bearer ${this._access}`;
  }

  async _rotate() {
    const form = new URLSearchParams({ grant_type: 'refresh_token', refresh_token: this._refresh, client_id: this.clientId });
    if (this.clientSecret) form.set('client_secret', this.clientSecret);
    if (this.resource) form.set('resource', this.resource);
    let res;
    try {
      res = await this.fetch(this.tokenUrl, { method: 'POST', body: form, signal: AbortSignal.timeout(this.timeoutMs), redirect: 'manual',
        headers: { Accept: 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' } });
    } catch (e) {
      throw new TransportError(`无法连接令牌端点：${e.message}`, { method: 'POST', url: this.tokenUrl, cause: e });
    }
    const text = await res.text();
    if (!res.ok) {
      const err = errorFromResponse(res.status, res.headers, text, { method: 'POST', url: this.tokenUrl });
      if (res.status === 400 || res.status === 401) {
        // invalid_grant: spent, expired or withdrawn. Nothing this process holds can fix it.
        this._refresh = null;
        throw new AuthenticationError(`令牌端点拒绝了刷新令牌，请让本人重新授权：${err.message}`,
          { status: res.status, code: err.code, requestId: err.requestId, method: 'POST', url: this.tokenUrl });
      }
      throw err;
    }
    let body;
    try { body = JSON.parse(text); } catch { body = null; }
    if (!body || typeof body.access_token !== 'string' || !body.access_token.trim()) {
      throw new AuthenticationError('令牌端点没有返回 access_token', { status: res.status, code: 'invalid_token_response' });
    }
    this._access = body.access_token;
    this._expiresAt = Date.now() / 1000 + Math.max(1, Number(body.expires_in) || 300);
    this._refresh = typeof body.refresh_token === 'string' && body.refresh_token.trim() ? body.refresh_token : null;
    this.grantedScope = body.scope ?? null;
    // The old refresh token is spent now. If storing the successor fails, fail here, while this
    // process still holds it, rather than at the next start with nothing valid.
    await this.onRotate?.({ accessToken: this._access, refreshToken: this._refresh, expiresAt: this._expiresAt,
      scope: this.grantedScope });
  }
}

/**
 * Credentials from the environment, the same variables the Python SDK and CLI read:
 *   SYNWEVO_API_KEY or SYNWEVO_API_KEY_FILE, or
 *   SYNWEVO_CLIENT_ID + SYNWEVO_CLIENT_SECRET_FILE (or SYNWEVO_CLIENT_SECRET) + SYNWEVO_TOKEN_URL
 *   [+ SYNWEVO_SCOPE, SYNWEVO_RESOURCE].
 */
export async function credentialsFromEnv(env = process.env) {
  const v = (k) => (env[k] && String(env[k]).trim()) || null;
  if (v('SYNWEVO_API_KEY')) return new ApiKey(v('SYNWEVO_API_KEY'));
  if (v('SYNWEVO_API_KEY_FILE')) return new ApiKey(await readSecretFile(v('SYNWEVO_API_KEY_FILE'), 'SYNWEVO_API_KEY_FILE'));
  if (v('SYNWEVO_CLIENT_ID')) {
    const secret = v('SYNWEVO_CLIENT_SECRET')
      || (v('SYNWEVO_CLIENT_SECRET_FILE') && await readSecretFile(v('SYNWEVO_CLIENT_SECRET_FILE'), 'SYNWEVO_CLIENT_SECRET_FILE'));
    if (!secret || !v('SYNWEVO_TOKEN_URL')) {
      throw new ValidationError('使用 SYNWEVO_CLIENT_ID 时还需要 SYNWEVO_CLIENT_SECRET_FILE 与 SYNWEVO_TOKEN_URL', { field: 'SYNWEVO_CLIENT_ID' });
    }
    return new ClientCredentials({ tokenUrl: v('SYNWEVO_TOKEN_URL'), clientId: v('SYNWEVO_CLIENT_ID'), clientSecret: secret,
      scope: v('SYNWEVO_SCOPE') || undefined, resource: v('SYNWEVO_RESOURCE') });
  }
  throw new ValidationError('没有找到凭据：请设置 SYNWEVO_API_KEY_FILE（或 SYNWEVO_API_KEY），'
    + '或 SYNWEVO_CLIENT_ID + SYNWEVO_CLIENT_SECRET_FILE + SYNWEVO_TOKEN_URL', { field: 'credentials' });
}

// --------------------------------------------------------------------------- validation
function oneOf(value, allowed, field) {
  if (value == null) return undefined;
  if (!allowed.includes(value)) {
    throw new ValidationError(`${field} 只能是 ${allowed.join(' / ')}，收到 ${JSON.stringify(value)}`, { field });
  }
  return value;
}

function formatList(value, field = 'formats') {
  if (value == null) return undefined;
  const list = typeof value === 'string' ? value.split(',') : value;
  if (!Array.isArray(list) || !list.length) throw new ValidationError(`${field} 必须是非空的格式列表`, { field });
  return list.map((f) => {
    const s = String(f).trim().toLowerCase();
    if (!/^[a-z0-9]{1,10}$/.test(s)) throw new ValidationError(`${field} 中的 ${JSON.stringify(f)} 不是输出格式名（如 docx、pdf）`, { field });
    return s;
  });
}

// --------------------------------------------------------------------------- small helpers
function deadline(ms, outer) {
  const ctl = new AbortController();
  let timer = null;
  let timedOut = false;
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { timedOut = true; ctl.abort(new Error(`超过 ${ms} ms 没有响应`)); }, ms);
  };
  const onOuter = () => ctl.abort(outer.reason);
  if (outer) {
    if (outer.aborted) ctl.abort(outer.reason);
    else outer.addEventListener('abort', onOuter, { once: true });
  }
  arm();
  return {
    signal: ctl.signal,
    reset: arm,                          // an idle timeout for streamed bodies
    get timedOut() { return timedOut; },
    clear() { clearTimeout(timer); outer?.removeEventListener('abort', onOuter); },
  };
}

export async function sha256File(path) {
  const h = createHash('sha256');
  for await (const chunk of fs.createReadStream(path)) h.update(chunk);
  return h.digest('hex');
}

async function fileBlob(path, type) {
  // openAsBlob (Node >= 19.8) is file-backed, so the multipart body streams from disk; on
  // Node 18 the (size-capped) file is read into memory instead.
  if (typeof fs.openAsBlob === 'function') return fs.openAsBlob(path, { type });
  return new Blob([await readFile(path)], { type });
}

/** A file name the server gave us, made safe to use inside a local directory. */
export function safeFilename(name, fallback = 'download.bin') {
  const base = String(name ?? '').replace(/\\/g, '/').split('/').pop().replace(/[\u0000-\u001f\u007f]/g, '').trim();
  return base && base !== '.' && base !== '..' ? base : fallback;
}

function isRetryable(err) {
  if (err instanceof TransportError) return true;
  if (!(err instanceof SynwevoError)) return false;
  return RETRYABLE_STATUS.has(err.status) || RETRYABLE_CODES.has(err.code);
}

export function isTerminal(job) {
  // Prefer the server's own flag when it sends one; otherwise the documented terminal statuses.
  return typeof job?.terminal === 'boolean' ? job.terminal : TERMINAL_STATUSES.includes(job?.status);
}

// --------------------------------------------------------------------------- client
export class Client {
  /**
   * @param {string} baseUrl  e.g. http://127.0.0.1:8765 (without /api/v1)
   * @param {ApiKey|ClientCredentials} credentials
   */
  constructor(baseUrl, credentials, {
    timeoutMs = 30_000, maxRetries = 4, backoffBaseMs = 500, backoffCapMs = 30_000, maxRetryAfterMs = 120_000,
    userAgent = `synwevo-docs-node/${VERSION} node/${process.versions.node}`, fetch: fetchImpl = globalThis.fetch,
    sleep = (ms, signal) => delay(ms, undefined, signal ? { signal } : undefined), random = Math.random,
    multipartMaxBytes = 16 * MIB, chunkBytes = 8 * MIB, onRetry = null,
  } = {}) {
    if (!baseUrl) throw new ValidationError('缺少服务地址（SYNWEVO_BASE_URL）', { field: 'base_url' });
    if (!credentials || typeof credentials.authorization !== 'function') {
      throw new ValidationError('credentials 必须是 ApiKey 或 ClientCredentials', { field: 'credentials' });
    }
    if (typeof fetchImpl !== 'function') throw new ValidationError('需要 Node.js 18 或更高版本（缺少 fetch）', { field: 'fetch' });
    for (const [name, value] of Object.entries({ timeoutMs, chunkBytes })) {
      if (!Number.isSafeInteger(value) || value < 1) throw new ValidationError(`${name} 必须是正整数`, { field: name });
    }
    if (!Number.isSafeInteger(maxRetries) || maxRetries < 0) throw new ValidationError('maxRetries 必须是非负整数', { field: 'maxRetries' });
    this.baseUrl = String(baseUrl).replace(/\/+$/, '').replace(/\/api\/v1$/, '');
    this.credentials = credentials;
    Object.assign(this, { timeoutMs, maxRetries, backoffBaseMs, backoffCapMs, maxRetryAfterMs, userAgent,
      multipartMaxBytes, chunkBytes, onRetry });
    this.fetch = fetchImpl;
    this.sleep = sleep;
    this.random = random;
  }

  url(path, query) {
    const u = new URL(this.baseUrl + API_PREFIX + path);
    for (const [k, v] of Object.entries(query || {})) {
      if (v == null) continue;
      for (const item of Array.isArray(v) ? v : [v]) u.searchParams.append(k, String(item));
    }
    return u.toString();
  }

  /** Delay before retry number `attempt` (0-based): full jitter, but never less than Retry-After. */
  backoffMs(attempt, retryAfterS = null) {
    const jitter = Math.floor(this.random() * Math.min(this.backoffCapMs, this.backoffBaseMs * 2 ** attempt));
    if (retryAfterS == null) return jitter;
    const asked = Math.ceil(retryAfterS * 1000);
    return asked > this.maxRetryAfterMs ? null : Math.max(asked, jitter);
  }

  /**
   * Send one logical request, retrying where that is safe. Returns the successful Response with
   * a `release()` that ends its timer; `requestJson` is the usual entry point.
   *
   * opts: query, json, form (() => FormData), body (() => bytes), headers, idempotencyKey,
   *       retry (true/false overrides the method rule), timeoutMs, idle (timeout resets on each
   *       chunk; for downloads), signal.
   */
  async send(method, path, opts = {}) {
    method = method.toUpperCase();
    const url = this.url(path, opts.query);
    const retryable = opts.retry ?? (SAFE_METHODS.has(method) || Boolean(opts.idempotencyKey)
      || (method === 'PUT' && !opts.precondition));
    let authRefreshed = false;
    for (let attempt = 0; ; attempt++) {
      opts.signal?.throwIfAborted();
      const headers = { 'User-Agent': this.userAgent, Accept: 'application/json', ...(opts.headers || {}) };
      headers.Authorization = await this.credentials.authorization();
      if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;
      let body;
      if (opts.json !== undefined) {
        body = JSON.stringify(opts.json);
        headers['Content-Type'] = 'application/json';
      } else if (opts.form) {
        body = await opts.form();          // rebuilt per attempt: a sent body cannot be re-read
      } else if (opts.body) {
        body = await opts.body();
      }
      const dl = deadline(opts.timeoutMs ?? this.timeoutMs, opts.signal);
      let res = null;
      let failure = null;
      try {
        res = await this.fetch(url, { method, headers, body, signal: dl.signal, redirect: 'manual' });
      } catch (e) {
        dl.clear();
        if (opts.signal?.aborted) throw opts.signal.reason ?? e;
        failure = new TransportError(`${method} ${path} 失败：${dl.timedOut ? '超时' : (e.cause?.code || e.message)}`,
          { method, url, cause: e });
      }
      if (res) {
        if (res.ok) {
          res.release = () => dl.clear();
          res.idle = () => dl.reset();
          if (!opts.consume) return res;
          try {
            // Keep body reads within the same timeout/retry budget as the request headers.
            return await opts.consume(res);
          } catch (e) {
            if (opts.signal?.aborted) throw opts.signal.reason ?? e;
            failure = new TransportError(`${method} ${path} 响应未完整读取或不是合法 JSON`, { method, url, cause: e });
          } finally {
            dl.clear();
          }
        } else {
          let text = '';
          try { text = await res.text(); } catch { text = ''; }
          dl.clear();
          failure = errorFromResponse(res.status, res.headers, text, { method, url });
          if (res.status === 401 && this.credentials.refreshable && !authRefreshed) {
            // An access token can expire between our check and the server's: fetch a new one once.
            authRefreshed = true;
            this.credentials.invalidate();
            attempt -= 1;
            continue;
          }
        }
      }
      const gateRefusal = failure.status === 429 && failure.code === 'rate_limited';
      if ((!retryable && !gateRefusal) || attempt >= this.maxRetries || !isRetryable(failure)) throw failure;
      const wait = this.backoffMs(attempt, failure.retryAfter);
      if (wait == null) throw failure;      // the server asked for longer than we are willing to wait
      this.onRetry?.({ method, path, attempt: attempt + 1, delayMs: wait, status: failure.status, code: failure.code });
      await this.sleep(wait, opts.signal);
    }
  }

  async requestJson(method, path, opts = {}) {
    return this.send(method, path, { ...opts, consume: async (res) => {
      const text = await res.text();
      const data = text ? JSON.parse(text) : null;
      if (data && typeof data === 'object' && !Array.isArray(data)) {
        Object.defineProperty(data, '$meta', { enumerable: false, value: {
          status: res.status, requestId: res.headers.get('x-request-id'),
          replayed: res.headers.get('idempotent-replayed') === 'true', location: res.headers.get('location'),
          retryAfter: parseRetryAfter(res.headers.get('retry-after')) } });
      }
      return data;
    } });
  }

  get(path, query, opts = {}) { return this.requestJson('GET', path, { ...opts, query }); }

  /** Every item of a list endpoint, following next_cursor (preferred) or next_offset. */
  async *paginate(path, query = {}, opts = {}) {
    let q = { ...query };
    const visited = new Set();
    for (;;) {
      const position = JSON.stringify([q.cursor ?? null, q.offset ?? null]);
      if (visited.has(position)) throw new APIError('分页游标没有前进', { code: 'pagination_stalled' });
      visited.add(position);
      const page = await this.get(path, q, opts);
      for (const item of page?.items || []) yield item;
      if (page?.next_cursor && page.has_more !== false) {
        q = { ...query, cursor: page.next_cursor };
        delete q.offset;
      } else if (page?.next_offset != null && !page?.next_cursor && page.has_more !== false) {
        q = { ...query, offset: page.next_offset };
      } else {
        return;
      }
    }
  }

  async collect(iterable) {
    const out = [];
    for await (const x of iterable) out.push(x);
    return out;
  }

  // ------------------------------------------------------------------ system
  whoami() { return this.get('/whoami'); }
  capabilities() { return this.get('/capabilities'); }
  usage() { return this.get('/usage'); }

  // ------------------------------------------------------------------ projects
  createProject({ name } = {}, { idempotencyKey = randomUUID() } = {}) {
    return this.requestJson('POST', '/projects', { json: name ? { name } : {}, idempotencyKey });
  }
  listProjects(query = {}) { return this.paginate('/projects', query); }
  getProject(pid) { return this.get(`/projects/${encodeURIComponent(pid)}`); }
  // Copy requests: only a delegated token (the project owner's authorization) may use these; the
  // target's approver decides in the browser. Follow a request with getCopyRequest or copy.* events.
  copyTargets() { return this.get('/copy-targets'); }
  copyManifest(pid, { sources = false, corrections = false } = {}) {
    return this.get(`/projects/${encodeURIComponent(pid)}/copy-manifest`, { sources: String(!!sources), corrections: String(!!corrections) });
  }
  requestCopy(pid, { targetWorkspaceId, name, purpose, sources = false, corrections = false } = {}, { idempotencyKey = randomUUID() } = {}) {
    if (typeof targetWorkspaceId !== 'string' || !targetWorkspaceId) throw new ValidationError('targetWorkspaceId is required', { field: 'target_workspace_id' });
    const json = { target_workspace_id: targetWorkspaceId, scope: { sources: !!sources, corrections: !!corrections } };
    if (name != null) json.name = name;
    if (purpose != null) json.purpose = purpose;
    return this.requestJson('POST', `/projects/${encodeURIComponent(pid)}/copy-requests`, { json, idempotencyKey });
  }
  listCopyRequests(pid, { status, limit = 50 } = {}) { return this.get(`/projects/${encodeURIComponent(pid)}/copy-requests`, { status, limit }); }
  getCopyRequest(cid) { return this.get(`/copy-requests/${encodeURIComponent(cid)}`); }
  withdrawCopyRequest(cid) { return this.requestJson('POST', `/copy-requests/${encodeURIComponent(cid)}/withdraw`, { retry: false }); }
  /** Rename, archive/unarchive or change output defaults; `baseSettingsRev` is the `settings_rev`
   *  you read (someone else's edit since is 409 stale_project). Not retried automatically. */
  updateProject(pid, { baseSettingsRev, name, archived, output } = {}) {
    if (!Number.isInteger(baseSettingsRev) || baseSettingsRev < 0) throw new ValidationError('baseSettingsRev must be the settings_rev you read', { field: 'base_settings_rev' });
    const json = { base_settings_rev: baseSettingsRev };
    if (name != null) json.name = name;
    if (archived != null) json.archived = archived;
    if (output != null) json.output = output;
    return this.requestJson('PATCH', `/projects/${encodeURIComponent(pid)}`, { json, retry: false });
  }
  deleteProject(pid) { return this.requestJson('DELETE', `/projects/${encodeURIComponent(pid)}`); }
  putRule(pid, rule) { return this.requestJson('PUT', `/projects/${encodeURIComponent(pid)}/rule`, { json: { rule } }); }
  bindTemplate(pid, templateId, { version = null } = {}) {
    return this.requestJson('PUT', `/projects/${encodeURIComponent(pid)}/template`,
      { json: version == null ? { template_id: templateId } : { template_id: templateId, version } });
  }
  getMappings(pid) { return this.get(`/projects/${encodeURIComponent(pid)}/mappings`); }
  putMappings(pid, mappings, { baseRev = null, note = null } = {}) {
    const json = { mappings };
    if (baseRev != null) json.base_rev = baseRev;
    if (note) json.note = note;
    return this.requestJson('PUT', `/projects/${encodeURIComponent(pid)}/mappings`, { json, precondition: baseRev != null });
  }
  getDefinition(pid) { return this.get(`/projects/${encodeURIComponent(pid)}/definition`); }
  putDefinition(pid, definition, { baseRev = null } = {}) {
    return this.requestJson('PUT', `/projects/${encodeURIComponent(pid)}/definition`,
      { json: definition, query: { base_rev: baseRev }, precondition: baseRev != null });
  }

  /** Preflight is read-only on the server, so repeating it after a timeout is harmless. */
  preflight(pid, { formats, naming, simulate } = {}) {
    const json = {};
    if (formats) json.formats = formatList(formats);
    if (naming) json.naming = naming;
    if (simulate) json.simulate = simulate;
    return this.requestJson('POST', `/projects/${encodeURIComponent(pid)}/preflight`, { json, retry: true });
  }
  preflightIssues(pid, { level, formats, naming } = {}) {
    oneOf(level, ['error', 'warning'], 'level');
    return this.paginate(`/projects/${encodeURIComponent(pid)}/preflight/issues`,
      { level, formats: formats ? formatList(formats).join(',') : undefined, naming });
  }

  // ------------------------------------------------------------------ files and templates
  listFiles(pid, query = {}) { return this.paginate(`/projects/${encodeURIComponent(pid)}/files`, query); }
  getFile(fid, opts = {}) { return this.get(`/files/${encodeURIComponent(fid)}`, undefined, opts); }
  deleteFile(fid) { return this.requestJson('DELETE', `/files/${encodeURIComponent(fid)}`); }
  /** Recognise text on a scanned PDF (default: every scanned/mixed page) or image, at most 50 pages.
   *  Synchronous on the server: waits 30 s + 6 s per page unless `timeoutMs` says otherwise, and is
   *  not retried automatically. After a timeout, the page-words route reports `source: "ocr"` for
   *  pages already recognised. */
  runSourceOcr(fid, { pages, timeoutMs } = {}) {
    if (pages != null && (!Array.isArray(pages) || pages.length > 50 || pages.some(n => !Number.isInteger(n) || n < 0))) {
      throw new ValidationError('pages must be an array of at most 50 zero-based page numbers', { field: 'pages' });
    }
    const wait = timeoutMs ?? Math.max(this.timeoutMs, 30_000 + 6_000 * (pages ? pages.length : 50));
    return this.requestJson('POST', `/files/${encodeURIComponent(fid)}/ocr`,
      { json: pages == null ? {} : { pages }, retry: false, timeoutMs: wait });
  }

  uploadSource(pid, filePath, { filename, idempotencyKey = randomUUID() } = {}) {
    return this._uploadTo(`/projects/${encodeURIComponent(pid)}/files`, filePath,
      { filename, idempotencyKey, purpose: 'source' });
  }
  uploadTemplate(filePath, { name, filename, idempotencyKey = randomUUID() } = {}) {
    return this._uploadTo('/templates', filePath, { filename, idempotencyKey, purpose: 'template', name });
  }

  async _uploadTo(route, filePath, { filename, idempotencyKey, purpose, name }) {
    const st = await stat(filePath);
    if (!st.isFile()) throw new ValidationError(`${filePath} 不是文件`, { field: 'file' });
    const fname = filename || basename(filePath);
    const sha256 = await sha256File(filePath);
    if (st.size <= this.multipartMaxBytes) {
      const type = MIME[extname(fname).toLowerCase()] || 'application/octet-stream';
      return this.requestJson('POST', route, {
        idempotencyKey,
        query: name ? { name } : undefined,
        headers: { 'X-Content-SHA256': sha256 },
        form: async () => {
          const fd = new FormData();
          fd.set('file', await fileBlob(filePath, type), fname);
          return fd;
        },
      });
    }
    // Replay the consumption first when an earlier run completed this upload.
    // resumableUpload accepts consumed sessions so the keyed POST below can return its replay.
    const up = await this.resumableUpload(filePath, { filename: fname, sha256, purpose,
      idempotencyKey: `${idempotencyKey}:upload` });
    return this.requestJson('POST', route, { idempotencyKey,
      json: name ? { upload_id: up.id, name } : { upload_id: up.id } });
  }

  /**
   * Upload a large file in Content-Range chunks read from disk. After a failed chunk it asks the
   * server how many bytes it has (`received`) and continues from there.
   */
  async resumableUpload(filePath, { filename, sha256, purpose, idempotencyKey = randomUUID(), onProgress } = {}) {
    const { size } = await stat(filePath);
    sha256 ||= await sha256File(filePath);
    const up = await this.requestJson('POST', '/uploads', { idempotencyKey,
      json: { filename: filename || basename(filePath), size, sha256, ...(purpose ? { purpose } : {}) } });
    const current = await this.get(`/uploads/${up.id}`);
    if (current.complete) return current;
    let received = Number(current.received || 0);
    const fh = await open(filePath, 'r');
    try {
      let failures = 0;
      while (received < size) {
        const len = Math.min(this.chunkBytes, size - received);
        const buf = Buffer.alloc(len);
        const { bytesRead } = await fh.read(buf, 0, len, received);
        if (bytesRead !== len) throw new IntegrityError(`读取 ${filePath} 时文件变短了`, { field: 'file' });
        try {
          const r = await this.requestJson('PUT', `/uploads/${up.id}`, { retry: false, body: () => buf,
            headers: { 'Content-Type': 'application/octet-stream',
              'Content-Range': `bytes ${received}-${received + len - 1}/${size}` } });
          received = Number(r.received);
          failures = 0;
          onProgress?.({ received, size });
        } catch (e) {
          const resumable = isRetryable(e) || e.status === 416 || e.code === 'upload_busy';
          if (!resumable || ++failures > this.maxRetries) throw e;
          const wait = this.backoffMs(failures - 1, e.retryAfter);
          if (wait == null) throw e;
          await this.sleep(wait);
          received = Number((await this.get(`/uploads/${up.id}`)).received);
        }
      }
    } finally {
      await fh.close();
    }
    // Completing twice is documented as not an error, so this POST is safe to retry.
    return this.requestJson('POST', `/uploads/${up.id}/complete`, { json: { sha256 }, retry: true });
  }

  /** Poll a source file until the server has accepted and parsed it; throws if parsing failed or the
   *  file was rejected by the malware check. `scanning` and `scan_failed` (check not run yet; the server
   *  keeps the file quarantined and retries — re-uploading would only queue the same bytes again) are
   *  waited through until `timeoutMs`; the WaitTimeoutError then carries the last record in `details`. */
  async waitForFile(fid, { timeoutMs = 120_000, pollMs = 300, maxPollMs = 3_000, signal } = {}) {
    const t0 = Date.now();
    let wait = pollMs;
    for (;;) {
      signal?.throwIfAborted();
      const remaining = timeoutMs - (Date.now() - t0);
      if (remaining <= 0) throw new WaitTimeoutError(`来源文件 ${fid} 等待超时`);
      const f = await this.getFile(fid, { signal, timeoutMs: Math.min(this.timeoutMs, remaining), retry: false });
      if (f.status === 'error') {
        throw new APIError(`来源文件 ${f.filename || fid} 解析失败：${f.error || '未知原因'}`, { code: 'file_parse_failed', details: f });
      }
      if (f.status === 'rejected') {
        throw new APIError(`来源文件 ${f.filename || fid} 未通过安全检查：${f.error || '已拒绝'}`, { code: 'file_rejected', details: f });
      }
      if (f.status !== 'parsing' && f.status !== 'scanning' && f.status !== 'scan_failed') return f;
      if (Date.now() - t0 + wait > timeoutMs) throw new WaitTimeoutError(`来源文件 ${fid} 超过 ${timeoutMs} ms 仍在解析`, { details: f });
      await this.sleep(wait, signal);
      wait = Math.min(maxPollMs, Math.ceil(wait * 1.5));
    }
  }

  listTemplates(query = {}) { return this.paginate('/templates', query); }
  getTemplate(tid) { return this.get(`/templates/${encodeURIComponent(tid)}`); }
  /** Create native DOCX/XLSX and fields from the same editor model used by the product. */
  createBuiltinTemplate({ kind, name, editor } = {}, { idempotencyKey = randomUUID() } = {}) {
    if (!kind) throw new ValidationError('kind 必须是 builtin_doc 或 builtin_sheet', { field: 'kind' });
    oneOf(kind, ['builtin_doc', 'builtin_sheet'], 'kind');
    return this.requestJson('POST', '/templates/builtin', { json: { kind, name, editor }, idempotencyKey });
  }
  /** Export the stored editor model and fields at the head or a specific version. */
  getBuiltinTemplate(tid, { version } = {}) {
    if (version != null && (!Number.isSafeInteger(version) || version < 1)) {
      throw new ValidationError('version 必须是正整数', { field: 'version' });
    }
    return this.get(`/templates/${encodeURIComponent(tid)}/builtin`, { version });
  }
  /** Save a new version; a stable key recovers a lost response without saving twice. */
  saveBuiltinTemplate(tid, { editor, name, baseVersion } = {}, { idempotencyKey = randomUUID() } = {}) {
    if (!Number.isSafeInteger(baseVersion) || baseVersion < 1) {
      throw new ValidationError('必须提供正整数 baseVersion', { field: 'base_version' });
    }
    return this.requestJson('PUT', `/templates/${encodeURIComponent(tid)}/builtin`,
      { json: { editor, name, base_version: baseVersion }, idempotencyKey, precondition: true });
  }
  getFields(tid) { return this.get(`/templates/${encodeURIComponent(tid)}/fields`); }
  /** Change one field's properties as one new version; `baseHead` is the head you read (a moved
   *  head, or a repeat of a call that succeeded, is 409 stale_template). Not retried automatically. */
  updateField(tid, fieldId, { baseHead, ...values } = {}) {
    if (!Number.isInteger(baseHead) || baseHead < 1) throw new ValidationError('baseHead must be the template head you read', { field: 'base_head' });
    return this.requestJson('PATCH', `/templates/${encodeURIComponent(tid)}/fields/${encodeURIComponent(fieldId)}`,
      { json: { base_head: baseHead, ...values }, retry: false });
  }
  /** Remove one field as one new version; with `projectId` also that project's mappings bound only to it. */
  deleteField(tid, fieldId, { baseHead, projectId, cascade = true } = {}) {
    if (!Number.isInteger(baseHead) || baseHead < 1) throw new ValidationError('baseHead must be the template head you read', { field: 'base_head' });
    const query = { base_head: baseHead };
    if (projectId) Object.assign(query, { project_id: projectId, cascade: cascade ? 'true' : 'false' });
    return this.requestJson('DELETE', `/templates/${encodeURIComponent(tid)}/fields/${encodeURIComponent(fieldId)}`,
      { query, retry: false });
  }
  createFields(tid, definition, { idempotencyKey = randomUUID() } = {}) {
    return this.requestJson('POST', `/templates/${encodeURIComponent(tid)}/fields`, { json: definition, idempotencyKey });
  }
  deleteTemplate(tid) { return this.requestJson('DELETE', `/templates/${encodeURIComponent(tid)}`); }

  // ------------------------------------------------------------------ jobs
  createJob(pid, { formats, naming, errorPolicy, layout, keepFields } = {}, { idempotencyKey = randomUUID() } = {}) {
    const json = {};
    if (formats != null) json.formats = formatList(formats);
    if (naming) json.naming = naming;
    if (errorPolicy != null) json.error_policy = oneOf(errorPolicy, ERROR_POLICIES, 'error_policy');
    if (layout != null) json.layout = oneOf(layout, LAYOUTS, 'layout');
    if (keepFields != null) json.keep_fields = Boolean(keepFields);
    return this.requestJson('POST', `/projects/${encodeURIComponent(pid)}/jobs`, { json, idempotencyKey });
  }
  getJob(jid, opts = {}) { return this.get(`/jobs/${encodeURIComponent(jid)}`, undefined, opts); }
  runs(jid, query = {}) { return this.paginate(`/jobs/${encodeURIComponent(jid)}/runs`, query); }
  listJobs(pid, query = {}) { return this.paginate(`/projects/${encodeURIComponent(pid)}/jobs`, query); }
  items(jid, { status } = {}) {
    oneOf(status, ITEM_STATUSES, 'status');
    return this.paginate(`/jobs/${encodeURIComponent(jid)}/items`, { status });
  }
  /** Cancelling an already-cancelled or finished job reports it unchanged, so retrying is safe. */
  cancelJob(jid) { return this.requestJson('POST', `/jobs/${encodeURIComponent(jid)}/cancel`, { retry: true }); }
  retryJob(jid, { includeSkipped = false, failedOnly = false } = {}, { idempotencyKey = randomUUID() } = {}) {
    return this.requestJson('POST', `/jobs/${encodeURIComponent(jid)}/retry`,
      { json: { ...(includeSkipped ? { include_skipped: true } : {}), ...(failedOnly ? { failed_only: true } : {}) }, idempotencyKey });
  }
  jobEvents(jid, { after = 0, limit } = {}, opts = {}) {
    return this.get(`/jobs/${encodeURIComponent(jid)}/events`, { after, limit }, opts);
  }

  /**
   * Poll until the job is terminal. The interval starts at pollMs and grows to maxPollMs; a
   * `poll_after_s` hint from the server wins when present. `onEvent` receives the job's events
   * (from /jobs/{jid}/events) in order, each exactly once.
   */
  async waitForJob(jid, { timeoutMs = 600_000, pollMs = 1_000, maxPollMs = 10_000, onEvent, signal } = {}) {
    const t0 = Date.now();
    const dl = deadline(timeoutMs, signal);
    const requestSignal = dl.signal;
    let wait = pollMs;
    let after = 0;
    try {
    for (;;) {
      signal?.throwIfAborted();
      const remaining = timeoutMs - (Date.now() - t0);
      if (remaining <= 0) throw new WaitTimeoutError(`任务 ${jid} 等待超时`);
      const job = await this.getJob(jid, { signal: requestSignal, timeoutMs: Math.min(this.timeoutMs, remaining) });
      if (onEvent) {
        for (;;) {
          const page = await this.jobEvents(jid, { after }, { signal: requestSignal });
          for (const ev of page.items || []) await onEvent(ev);
          if (!page.items?.length || page.next_after === after) break;
          after = page.next_after;
        }
      }
      if (isTerminal(job)) return job;
      const hint = Math.max(wait, Number(job.poll_after_s || 0) * 1000, Number(job.$meta?.retryAfter || 0) * 1000);
      if (Date.now() - t0 + hint > timeoutMs) {
        throw new WaitTimeoutError(`任务 ${jid} 在 ${Math.round(timeoutMs / 1000)} 秒内没有结束（当前 ${job.status}）`,
          { code: 'wait_timeout', details: { job } });
      }
      await this.sleep(hint, requestSignal);
      wait = Math.min(maxPollMs, Math.ceil(wait * 1.5));
    }
    } catch (e) {
      if (signal?.aborted) throw signal.reason ?? e;
      if (dl.timedOut) throw new WaitTimeoutError(`任务 ${jid} 等待超时`, { code: 'wait_timeout' });
      throw e;
    } finally {
      dl.clear();
    }
  }

  // ------------------------------------------------------------------ artifacts
  artifacts(jid, query = {}) { return this.paginate(`/jobs/${encodeURIComponent(jid)}/artifacts`, query); }

  /**
   * Stream one artifact to `outDir/<filename>` (or `destPath`). The bytes are hashed while they
   * arrive and must match X-Content-SHA256 and the manifest's sha256; otherwise nothing is left
   * at the destination. An existing file with the expected hash is kept and reported as such.
   */
  async downloadArtifact(artifact, { outDir = '.', destPath, signal } = {}) {
    const a = typeof artifact === 'string' ? { id: artifact } : artifact;
    const dest = resolve(destPath || join(outDir, safeFilename(a.filename, `${a.id}.bin`)));
    if (a.sha256) {
      try {
        if ((await sha256File(dest)) === a.sha256) return { id: a.id, path: dest, sha256: a.sha256, size: (await stat(dest)).size, reused: true };
      } catch { /* not there yet */ }
    }
    return this._download(`/artifacts/${encodeURIComponent(a.id)}/content`, dest, { expectedSha256: a.sha256, signal, id: a.id });
  }

  /** The job's ZIP (all artifacts plus the generation manifest). */
  downloadArchive(jid, destPath, { format, signal } = {}) {
    return this._download(`/jobs/${encodeURIComponent(jid)}/archive`, resolve(destPath), { query: { format }, signal });
  }
  downloadReport(jid, destPath, { signal } = {}) {
    return this._download(`/jobs/${encodeURIComponent(jid)}/report.csv`, resolve(destPath), { signal });
  }

  /** Render one native sample without creating a job; verify the binary checksum before saving. */
  downloadPreview(pid, destPath, { index, key, signature, signal } = {}) {
    if (!pid || !destPath) throw new ValidationError('preview 需要项目 ID 和输出路径');
    if (index != null && (!Number.isInteger(index) || index < 0)) throw new ValidationError('index 必须是非负整数');
    if (key != null && (typeof key !== 'string' || !key || key.length > 512)) throw new ValidationError('key 长度必须在 1–512');
    if (index != null && key != null) throw new ValidationError('index 与 key 不能同时指定');
    if (signature != null && (typeof signature !== 'string' || !signature || signature.length > 64)) throw new ValidationError('signature 长度必须在 1–64');
    const json = { ...(index != null ? { index } : {}), ...(key != null ? { key } : {}),
      ...(signature != null ? { signature } : {}) };
    return this._download(`/projects/${encodeURIComponent(pid)}/preview`, resolve(destPath),
      { method: 'POST', json, requireChecksum: true, signal });
  }

  async _download(path, dest, { expectedSha256 = null, query, signal, id = null,
    method = 'GET', json, requireChecksum = false } = {}) {
    await mkdir(resolve(dest, '..'), { recursive: true });
    for (let attempt = 0; ; attempt++) {
      const res = await this.send(method, path, { query, json, signal, retry: true, idle: true, headers: { Accept: '*/*' } });
      const tmp = `${dest}.part-${process.pid}-${randomUUID().slice(0, 8)}`;
      const hash = createHash('sha256');
      let size = 0;
      let fh = null;
      try {
        fh = await open(tmp, 'wx');
        for await (const chunk of Readable.fromWeb(res.body)) {
          res.idle();                      // the timeout is per chunk, not for the whole file
          hash.update(chunk);
          size += chunk.length;
          let offset = 0;
          while (offset < chunk.length) {
            const { bytesWritten } = await fh.write(chunk, offset, chunk.length - offset);
            if (!bytesWritten) throw new Error('写入下载文件时没有进展');
            offset += bytesWritten;
          }
        }
        await fh.sync();
        await fh.close();
        fh = null;
      } catch (e) {
        res.release();
        await fh?.close().catch(() => {});
        await rm(tmp, { force: true });
        if (signal?.aborted) throw signal.reason ?? e;
        const err = e instanceof SynwevoError ? e : new TransportError(`下载 ${path} 中断：${e.cause?.code || e.message}`, { method, cause: e });
        if (attempt >= this.maxRetries || !isRetryable(err)) throw err;
        await this.sleep(this.backoffMs(attempt), signal);
        continue;
      }
      res.release();
      const digest = hash.digest('hex');
      const header = (res.headers.get('x-content-sha256') || '').toLowerCase() || null;
      if (requireChecksum && !header) {
        await rm(tmp, { force: true });
        throw new IntegrityError('下载缺少 X-Content-SHA256', { code: 'checksum_missing' });
      }
      for (const [label, want] of [['X-Content-SHA256', header], ['清单 sha256', expectedSha256]]) {
        if (want && want !== digest) {
          await rm(tmp, { force: true });
          throw new IntegrityError(`下载内容的 SHA-256 与 ${label} 不一致（${digest} ≠ ${want}）`,
            { code: 'checksum_mismatch', details: { expected: want, actual: digest, path } });
        }
      }
      try { await rename(tmp, dest); }
      catch (e) { await rm(tmp, { force: true }); throw e; }
      return { id, path: dest, sha256: digest, size, header_sha256: header,
        verified: Boolean(header || expectedSha256), project_signature: res.headers.get('x-project-signature'),
        template_version: res.headers.get('x-template-version'), record_key: res.headers.get('x-record-key') };
    }
  }

  // ------------------------------------------------------------------ webhooks and events (admin)
  listSubscriptions() { return this.get('/webhooks/subscriptions'); }
  /** The response carries `secret` exactly once; store it, it cannot be read again. */
  createSubscription({ url, events, description } = {}) {
    // Not retried: a lost response would leave a subscription whose secret nobody saw.
    const json = { url };
    if (events) json.events = events;
    if (description) json.description = description;
    return this.requestJson('POST', '/webhooks/subscriptions', { json });
  }
  getSubscription(sid) { return this.get(`/webhooks/subscriptions/${encodeURIComponent(sid)}`); }
  updateSubscription(sid, changes, { version } = {}) {
    return this.requestJson('PATCH', `/webhooks/subscriptions/${encodeURIComponent(sid)}`,
      { json: changes, headers: version != null ? { 'If-Match': `"${version}"` } : {} });
  }
  deleteSubscription(sid) { return this.requestJson('DELETE', `/webhooks/subscriptions/${encodeURIComponent(sid)}`); }
  pauseSubscription(sid) { return this.requestJson('POST', `/webhooks/subscriptions/${encodeURIComponent(sid)}/pause`, { retry: true }); }
  resumeSubscription(sid) { return this.requestJson('POST', `/webhooks/subscriptions/${encodeURIComponent(sid)}/resume`, { retry: true }); }
  rotateSubscriptionSecret(sid, { overlapS } = {}) {
    return this.requestJson('POST', `/webhooks/subscriptions/${encodeURIComponent(sid)}/rotate-secret`,
      { json: overlapS != null ? { overlap_s: overlapS } : {} });
  }
  pingSubscription(sid) { return this.requestJson('POST', `/webhooks/subscriptions/${encodeURIComponent(sid)}/ping`); }
  deliveries(sid, { status } = {}) {
    return this.paginate(`/webhooks/subscriptions/${encodeURIComponent(sid)}/deliveries`, { status });
  }
  redeliver(sid, did) {
    return this.requestJson('POST', `/webhooks/subscriptions/${encodeURIComponent(sid)}/deliveries/${encodeURIComponent(did)}/redeliver`);
  }
  /** The workspace event feed (same ids as webhook-id), from `cursor` to the current end. */
  events({ cursor, types } = {}) {
    return this.paginate('/events', { cursor, types: Array.isArray(types) ? types.join(',') : types });
  }

  // ------------------------------------------------------------------ the main chain
  /**
   * Source files + template + field definition + project definition -> verified documents.
   *
   * Every creating call gets an Idempotency-Key. With `keyPrefix` the keys are derived from it
   * (`<prefix>:project`, `<prefix>:job`, ...), so running the same command again after a crash
   * or a timeout reuses what the first run created instead of creating it twice.
   */
  async generate({ sources, template, fields, definition, formats = ['docx'], naming, errorPolicy, layout,
    outDir = '.', projectName, templateName, keyPrefix, expectedTenant, timeoutMs = 600_000, onProgress = () => {},
    onEvent, signal, onJobCreated } = {}) {
    if (!sources?.length || !template || !fields || !definition) {
      throw new ValidationError('generate 需要 sources、template、fields 与 definition', { field: 'generate' });
    }
    const fmt = formatList(formats);
    oneOf(errorPolicy, ERROR_POLICIES, 'error_policy');
    oneOf(layout, LAYOUTS, 'layout');
    const key = (step) => (keyPrefix ? `${keyPrefix}:${step}` : randomUUID());
    const loadJson = async (x) => (typeof x === 'string' ? JSON.parse(await readFile(x, 'utf8')) : x);
    const [fieldsDef, projectDef] = await Promise.all([loadJson(fields), loadJson(definition)]);

    const me = await this.whoami();
    if (expectedTenant && me.tenant !== expectedTenant) {
      throw new PermissionDeniedError(`凭据属于工作空间 ${me.tenant}，不是预期的 ${expectedTenant}；已停止，未写入任何内容`,
        { code: 'unexpected_tenant', details: { tenant: me.tenant } });
    }
    if (!(me.scopes || []).some((s) => s === 'write' || s === 'docweave.write')) {
      throw new PermissionDeniedError('凭据没有写权限（write / docweave.write）', { code: 'insufficient_scope', details: { scopes: me.scopes } });
    }
    onProgress({ step: 'whoami', tenant: me.tenant, scopes: me.scopes });

    const project = await this.createProject({ name: projectName }, { idempotencyKey: key('project') });
    onProgress({ step: 'project', id: project.id, replayed: project.$meta?.replayed });
    const files = [];
    for (const [i, src] of sources.entries()) {
      const f = await this.uploadSource(project.id, src, { idempotencyKey: key(`source:${i}`) });
      files.push(await this.waitForFile(f.id, { signal }));
      onProgress({ step: 'source', id: f.id, filename: f.filename, status: files.at(-1).status });
    }
    const tpl = await this.uploadTemplate(template, { name: templateName, idempotencyKey: key('template') });
    onProgress({ step: 'template', id: tpl.id, name: tpl.name });
    const fieldsOut = await this.createFields(tpl.id, fieldsDef, { idempotencyKey: key('fields') });
    onProgress({ step: 'fields', count: (fieldsDef.fields || []).length, version: typeof fieldsOut?.template === 'number' ? fieldsOut.template : undefined });
    await this.bindTemplate(project.id, tpl.id);
    await this.putDefinition(project.id, projectDef);
    onProgress({ step: 'definition' });

    const pf = await this.preflight(project.id, { formats: fmt, naming });
    onProgress({ step: 'preflight', records: pf.records, valid: pf.valid, invalid: pf.invalid, expected_outputs: pf.expected_outputs });
    if ((pf.invalid > 0 && errorPolicy !== 'skip') || !pf.expected_outputs) {
      const issues = [];
      for await (const it of this.preflightIssues(project.id, { level: 'error', formats: fmt, naming })) {
        issues.push(it);
        if (issues.length >= 20) break;
      }
      throw new PreflightError(`预检未通过：${pf.records} 个样本中 ${pf.invalid} 个有错误，预计输出 ${pf.expected_outputs} 个文件`,
        { code: 'preflight_failed', details: { preflight: pf, issues, project_id: project.id } });
    }

    // Simulate all eligible samples, then download one actual native preview through the API.
    const preview = await this.preflight(project.id, { formats: fmt, naming, simulate: true });
    onProgress({ step: 'preview', simulation: preview.simulation });
    if ((preview.simulation?.failed && errorPolicy !== 'skip') || !preview.expected_outputs) {
      throw new PreflightError('试生成发现错误', { code: 'preview_failed', details: preview });
    }

    preview.download = await this.downloadPreview(project.id, join(outDir, 'preview.' + extname(template).slice(1)),
      { index: 0, signature: pf.signature, signal });

    const created = await this.createJob(project.id, { formats: fmt, naming, errorPolicy, layout },
      { idempotencyKey: key('job') });
    onJobCreated?.(created);
    onProgress({ step: 'job', id: created.id, status: created.status, replayed: created.$meta?.replayed });
    const job = await this.waitForJob(created.id, { timeoutMs, onEvent, signal });
    onProgress({ step: 'job_done', id: job.id, status: job.status, counts: job.counts });
    if (!SUCCESS_STATUSES.includes(job.status)) {
      throw new JobFailedError(`任务 ${job.id} 结束于 ${job.status}${job.error ? `：${job.error}` : ''}`,
        { code: `job_${job.status}`, details: { job } });
    }

    await mkdir(outDir, { recursive: true });
    const artifacts = [];
    for await (const a of this.artifacts(job.id)) {
      const d = await this.downloadArtifact(a, { outDir, signal });
      artifacts.push({ id: a.id, seq: a.seq, label: a.label, format: a.format, filename: a.filename, size: d.size,
        sha256: d.sha256, manifest_sha256: a.sha256, header_sha256: d.header_sha256 ?? null, path: d.path, reused: Boolean(d.reused) });
      onProgress({ step: 'artifact', filename: a.filename, sha256: d.sha256 });
    }
    return { tenant: me.tenant, project_id: project.id, template_id: tpl.id, files: files.map((f) => ({ id: f.id, filename: f.filename, format: f.format, status: f.status })),
      preview, preflight: { records: pf.records, valid: pf.valid, invalid: pf.invalid, expected_outputs: pf.expected_outputs },
      job: { id: job.id, status: job.status, counts: job.counts, run_no: job.run_no }, artifacts };
  }
}

// The directory client reuses the transport and pagination implementation, with its own fixed
// prefix. Composition keeps Docs operations out of WorkspaceClient's public interface.
class WorkspaceTransport extends Client {
  url(path, query) {
    const url = new URL(this.baseUrl + WORKSPACE_API_PREFIX + path);
    for (const [key, value] of Object.entries(query || {})) {
      if (value == null) continue;
      for (const item of Array.isArray(value) ? value : [value]) url.searchParams.append(key, String(item));
    }
    return url.toString();
  }
}

function managedRole(value, field) {
  if (!['viewer', 'editor'].includes(value)) throw new ValidationError(`${field} must be viewer or editor`, { field });
  return value;
}

function directoryId(value) {
  if (typeof value !== 'string' || !value) throw new ValidationError('A directory resource ID is required', { field: 'id' });
  return encodeURIComponent(value);
}

export class WorkspaceClient {
  #transport;
  constructor(baseUrl = WORKSPACE_BASE_URL, credentials, options = {}) {
    this.#transport = new WorkspaceTransport(baseUrl, credentials, { userAgent: `synwevo-workspace-node/${VERSION}`, ...options });
  }

  /** Only SYNWEVO_WORKSPACE_* variables are read; Docs credentials and defaults are ignored. */
  static async fromEnv(env = process.env, options = {}) {
    const baseUrl = env.SYNWEVO_WORKSPACE_BASE_URL || WORKSPACE_BASE_URL;
    const clientId = env.SYNWEVO_WORKSPACE_CLIENT_ID;
    if (!clientId) throw new ValidationError('Set SYNWEVO_WORKSPACE_CLIENT_ID for an approved directory service account', { field: 'SYNWEVO_WORKSPACE_CLIENT_ID' });
    const clientSecret = env.SYNWEVO_WORKSPACE_CLIENT_SECRET
      || (env.SYNWEVO_WORKSPACE_CLIENT_SECRET_FILE && await readSecretFile(env.SYNWEVO_WORKSPACE_CLIENT_SECRET_FILE, 'SYNWEVO_WORKSPACE_CLIENT_SECRET_FILE'));
    const credentials = new ClientCredentials({ tokenUrl: env.SYNWEVO_WORKSPACE_TOKEN_URL || String(baseUrl).replace(/\/+$/, '') + '/oidc/token',
      clientId, clientSecret, scope: env.SYNWEVO_WORKSPACE_SCOPE || 'workspace.read', resource: WORKSPACE_RESOURCE,
      fetch: options.fetch, timeoutMs: options.timeoutMs });
    return new WorkspaceClient(baseUrl, credentials, options);
  }

  get baseUrl() { return this.#transport.baseUrl; }
  whoami() { return this.#transport.get('/whoami'); }
  listMembers({ role, q, limit = 50, offset = 0 } = {}) { return this.#transport.get('/members', { role, q, limit, offset }); }
  iterMembers({ role, q, limit = 50 } = {}) { return this.#transport.paginate('/members', { role, q, limit }); }
  listInvitations({ state, limit = 50, offset = 0 } = {}) { return this.#transport.get('/invitations', { state, limit, offset }); }
  iterInvitations({ state, limit = 50 } = {}) { return this.#transport.paginate('/invitations', { state, limit }); }
  createInvitation({ email, role, canExport, expiresInDays = 7 }, { idempotencyKey = randomUUID() } = {}) {
    const json = { email, role: managedRole(role, 'role'), expires_in_days: expiresInDays };
    if (canExport != null) json.can_export = canExport;
    return this.#transport.requestJson('POST', '/invitations', { json, idempotencyKey });
  }
  resendInvitation(id, { idempotencyKey = randomUUID() } = {}) {
    return this.#transport.requestJson('POST', `/invitations/${directoryId(id)}/resend`, { json: {}, idempotencyKey });
  }
  revokeInvitation(id) { return this.#transport.requestJson('POST', `/invitations/${directoryId(id)}/revoke`, { json: {}, retry: false }); }
  changeMember(id, { role, expectedRole }) {
    return this.#transport.requestJson('PATCH', `/members/${directoryId(id)}`, {
      json: { role: managedRole(role, 'role'), expected_role: managedRole(expectedRole, 'expected_role') }, retry: false });
  }
  removeMember(id, { expectedRole, reason }) {
    return this.#transport.requestJson('POST', `/members/${directoryId(id)}/remove`, {
      json: { expected_role: managedRole(expectedRole, 'expected_role'), reason }, retry: false });
  }
}

// --------------------------------------------------------------------------- business applications
class SuiteTransport extends Client {
  url(path, query) {
    const url = new URL(this.baseUrl + SUITE_API_PREFIX + path);
    for (const [key, value] of Object.entries(query || {})) {
      if (value == null) continue;
      url.searchParams.append(key, String(value));
    }
    return url.toString();
  }
}

function suiteOperation(value) {
  if (typeof value !== 'string' || value.length > 100 || !/^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/.test(value)) {
    throw new ValidationError("operation must be an operation id such as 'forms.form.list'", { field: 'operation' });
  }
  return value;
}

function suiteKey(value) {
  if (typeof value !== 'string' || value.length < 1 || value.length > 128 || !/^[!-~]+$/.test(value)) {
    throw new ValidationError('idempotencyKey must be 1–128 visible ASCII characters (a UUID is fine)', { field: 'Idempotency-Key' });
  }
  return value;
}

/**
 * Operations of Synwevo Forms, Tables, DataPrep, Extract and Approvals, for a client a person
 * authorised. Every call acts as that person, in the one workspace they chose, with their current
 * role; the scopes the client holds are necessary, never sufficient. Independent of the Docs API.
 *
 * Every `invoke` carries an idempotency key (yours, or one generated for that call) and is retried
 * on network failures and 5xx with that same key: a change runs once however many attempts it
 * took, and a read — for which the server ignores the key — is simply read again.
 */
export class SuiteClient {
  #transport;
  #workspace = null;
  constructor(baseUrl = SUITE_BASE_URL, credentials, options = {}) {
    if (!(credentials instanceof UserToken)) {
      throw new ValidationError("SuiteClient requires a UserToken (a person's delegation) or fromEnv()", { field: 'credentials' });
    }
    this.#transport = new SuiteTransport(baseUrl, credentials, { userAgent: `synwevo-suite-node/${VERSION}`, ...options });
  }

  /**
   * Only SYNWEVO_SUITE_* variables are read: SYNWEVO_SUITE_ACCESS_TOKEN[_FILE] and/or
   * SYNWEVO_SUITE_REFRESH_TOKEN[_FILE]; with a refresh token also SYNWEVO_SUITE_CLIENT_ID,
   * optionally SYNWEVO_SUITE_CLIENT_SECRET[_FILE] and SYNWEVO_SUITE_TOKEN_URL. A refresh token
   * rotates on use: without `onRotate` the successor lives only in this process, and the value in
   * the environment is spent after the first refresh — use this from a single process only.
   */
  static async fromEnv(env = process.env, { onRotate = null, ...options } = {}) {
    const secret = async (name) => (env[name] && String(env[name]).trim())
      || (env[`${name}_FILE`] ? readSecretFile(env[`${name}_FILE`], `${name}_FILE`) : null);
    const accessToken = await secret('SYNWEVO_SUITE_ACCESS_TOKEN');
    const refreshToken = await secret('SYNWEVO_SUITE_REFRESH_TOKEN');
    if (!accessToken && !refreshToken) {
      throw new ValidationError("Set SYNWEVO_SUITE_ACCESS_TOKEN or SYNWEVO_SUITE_REFRESH_TOKEN (a person's delegation)",
        { field: 'SYNWEVO_SUITE_ACCESS_TOKEN' });
    }
    const credentials = new UserToken({ accessToken, refreshToken, clientId: env.SYNWEVO_SUITE_CLIENT_ID || null,
      clientSecret: await secret('SYNWEVO_SUITE_CLIENT_SECRET'),
      tokenUrl: refreshToken ? (env.SYNWEVO_SUITE_TOKEN_URL || SUITE_TOKEN_URL) : null, resource: SUITE_RESOURCE,
      onRotate, fetch: options.fetch, timeoutMs: options.timeoutMs });
    return new SuiteClient(env.SYNWEVO_SUITE_BASE_URL || SUITE_BASE_URL, credentials, options);
  }

  get baseUrl() { return this.#transport.baseUrl; }
  whoami(opts = {}) { return this.#transport.get('/whoami', undefined, opts); }
  /** The contract of every operation the person can use here, with `granted` per scope. */
  operations({ app } = {}, opts = {}) { return this.#transport.get('/operations', { app }, opts); }

  /**
   * Run one operation. Rejects with the typed APIError for a refusal or a business error;
   * `result.replayed` says the answer is an earlier call's (same key, same input). A `status`
   * other than succeeded / partial means the call was accepted, not finished.
   */
  async invoke(operation, input = {}, { idempotencyKey = randomUUID(), correlationId, signal, timeoutMs } = {}) {
    if (input === null || typeof input !== 'object' || Array.isArray(input)) {
      throw new ValidationError('input must be a JSON object', { field: 'input' });
    }
    const json = { input };
    if (correlationId != null) json.correlation_id = correlationId;
    const result = await this.#transport.requestJson('POST', `/ops/${suiteOperation(operation)}`,
      { json, idempotencyKey: suiteKey(idempotencyKey), signal, timeoutMs });
    result.$meta.replayed = Boolean(result.replayed);
    return result;
  }

  /** A run this client started. Its output is null (`snapshot_hidden`) once its sources are no longer readable. */
  run(runId, opts = {}) {
    if (typeof runId !== 'string' || !runId) throw new ValidationError('A run ID is required', { field: 'run_id' });
    return this.#transport.get(`/runs/${encodeURIComponent(runId)}`, undefined, opts);
  }

  /**
   * Poll a run until it settles. A run waiting for a person (an approval, a form) can take days:
   * this gives up after `timeoutMs` with WaitTimeoutError (`details` is the last state) and the
   * run carries on.
   */
  async wait(runId, { timeoutMs = 300_000, pollMs = 2_000, signal } = {}) {
    const t0 = Date.now();
    for (;;) {
      signal?.throwIfAborted();
      const state = await this.run(runId, { signal });
      if (SUITE_TERMINAL_STATUSES.includes(state.status)) return state;
      const left = timeoutMs - (Date.now() - t0);
      if (left <= 0) throw new WaitTimeoutError(`run ${runId} is still ${state.status} after ${timeoutMs} ms`, { details: state });
      await this.#transport.sleep(Math.min(pollMs, left), signal);
    }
  }

  // ----- events: what changed, to pull.
  /**
   * One page of what changed in the workspace, oldest first, for the applications this credential
   * may read. An item says where to look, never what is there. Send `next_cursor` back to
   * continue; `has_more` can be true with no items (the service stopped part-way) — ask again.
   * Without a cursor, `start: 'now'` returns no items and a cursor at the present. A cursor that
   * waited longer than the feed keeps events is refused with GoneError (`cursor_expired`).
   */
  eventsPage({ cursor, types, limit, start } = {}, opts = {}) {
    if (types !== undefined && (!Array.isArray(types) || types.some((t) => typeof t !== 'string' || !t))) {
      throw new ValidationError('types must be an array of event type names', { field: 'types' });
    }
    if (start != null && !SUITE_EVENT_STARTS.includes(start)) {
      throw new ValidationError("start must be 'oldest' or 'now'", { field: 'start' });
    }
    if (start != null && cursor != null) {
      throw new ValidationError('start cannot be combined with a cursor', { field: 'start' });
    }
    return this.#transport.get('/events', { cursor: cursor ?? undefined, types: types?.length ? types.join(',') : undefined,
      limit, start: start ?? undefined }, opts);
  }

  /**
   * Pages in order. Each page's `next_cursor` resumes right after it: store it once the page's
   * events are handled. With `follow` it keeps polling, every `intervalMs` when there is nothing new.
   */
  async *eventPages({ cursor, types, start, pageSize, follow = false, intervalMs = 5_000, signal } = {}) {
    if (follow && !(Number.isFinite(intervalMs) && intervalMs > 0)) {
      throw new ValidationError('intervalMs must be a positive number when following', { field: 'intervalMs' });
    }
    for (;;) {
      signal?.throwIfAborted();
      // `start` only while there is no cursor (null and undefined alike): once the service has
      // handed one over, the feed continues from it.
      const page = await this.eventsPage({ cursor, types, limit: pageSize, start: cursor == null ? start : undefined }, { signal });
      yield page;
      cursor = page.next_cursor ?? cursor;
      if (page.has_more) continue;
      if (!follow) return;
      await this.#transport.sleep(intervalMs, signal);
    }
  }

  /** Events in order. An event may be seen again after a crash before the cursor was stored: de-duplicate on `id`. */
  async *events(options = {}) {
    for await (const page of this.eventPages(options)) yield* page.items ?? [];
  }

  // ----- webhooks: the event feed, pushed to an https endpoint of yours and signed (Standard
  // Webhooks — verify with verifyWebhook). A subscription belongs to this client, for this person,
  // in this workspace. Calls that answer with a secret or start a send carry no idempotency key and
  // are not retried: a lost answer must not leave a secret nobody saw.
  // One subscription's path. A missing id is refused here, whatever its spelling (undefined, null,
  // ''): left out of the path it would name the collection, and answer with a list.
  #hook(sid, rest = '') {
    if (typeof sid !== 'string' || !sid) {
      throw new ValidationError('A subscription ID is required', { field: 'subscription_id' });
    }
    return `${SUITE_WEBHOOKS}/${encodeURIComponent(sid)}${rest}`;
  }
  #delivery(sid, did, rest = '') {
    if (typeof did !== 'string' || !did) throw new ValidationError('A delivery ID is required', { field: 'delivery_id' });
    return this.#hook(sid, `/deliveries/${encodeURIComponent(did)}${rest}`);
  }

  /** `events`: event types, "<app>.*" or "*". The answer's `secret` is shown this once. */
  createWebhook({ url, events, description } = {}, opts = {}) {
    if (!Array.isArray(events) || !events.length) throw new ValidationError('events must be a non-empty array', { field: 'events' });
    const json = { url, events };
    if (description != null) json.description = description;
    return this.#transport.requestJson('POST', SUITE_WEBHOOKS, { json, retry: false, ...opts });
  }
  listWebhooks(opts = {}) { return this.#transport.get(SUITE_WEBHOOKS, undefined, opts); }
  getWebhook(sid, opts = {}) { return this.#transport.get(this.#hook(sid), undefined, opts); }
  /** `version` (from the subscription you read) refuses a lost update. */
  updateWebhook(sid, changes = {}, { version, headers, ...opts } = {}) {
    // The caller's own headers go with the request; they never take the place of the guard it asked for.
    const guard = version != null ? { 'If-Match': `"${Number(version)}"` } : {};
    const own = Object.entries(headers ?? {}).filter(([name]) => version == null || name.toLowerCase() !== 'if-match');
    return this.#transport.requestJson('PATCH', this.#hook(sid),
      { json: changes, retry: false, ...opts, headers: { ...Object.fromEntries(own), ...guard } });
  }
  deleteWebhook(sid, opts = {}) { return this.#transport.requestJson('DELETE', this.#hook(sid), { retry: false, ...opts }); }
  pauseWebhook(sid, opts = {}) { return this.#transport.requestJson('POST', this.#hook(sid, '/pause'), { retry: true, ...opts }); }
  /** Delivers again from now: what happened while it was stopped is not sent afterwards. */
  resumeWebhook(sid, opts = {}) { return this.#transport.requestJson('POST', this.#hook(sid, '/resume'), { retry: true, ...opts }); }
  /** A new `secret` (shown once); the previous one keeps signing for `overlapS` seconds (default and maximum one day). */
  rotateWebhookSecret(sid, { overlapS, ...opts } = {}) {
    return this.#transport.requestJson('POST', this.#hook(sid, '/rotate-secret'),
      { json: overlapS != null ? { overlap_s: overlapS } : {}, retry: false, ...opts });
  }
  /** One signed `webhook.ping` now; resolves with how the receiver answered. */
  pingWebhook(sid, opts = {}) { return this.#transport.requestJson('POST', this.#hook(sid, '/ping'), { retry: false, ...opts }); }
  /** One page of the delivery log, newest first. */
  webhookDeliveries(sid, { status, cursor, limit } = {}, opts = {}) {
    return this.#transport.get(this.#hook(sid, '/deliveries'), { status, cursor: cursor ?? undefined, limit }, opts);
  }
  webhookDelivery(sid, did, opts = {}) {
    return this.#transport.get(this.#delivery(sid, did), undefined, opts);
  }
  /** Queues the event of an earlier delivery once more (same `webhook-id`); sent only if still readable. */
  redeliverWebhook(sid, did, opts = {}) {
    return this.#transport.requestJson('POST', this.#delivery(sid, did, '/redeliver'), { retry: false, ...opts });
  }

  // ----- files: the routes that move bytes. They name the credential's workspace in the query
  // (the service refuses any other), which is read once from whoami.
  async #where() {
    this.#workspace ??= (await this.whoami()).workspace_id;
    return { workspace_id: this.#workspace };
  }

  async #upload(path, file, filename, { idempotencyKey, keyed, signal } = {}) {
    let blob;
    if (typeof file === 'string') {
      blob = await fileBlob(file, MIME[extname(file).toLowerCase()] || 'application/octet-stream');
      filename ??= basename(file);
    } else {
      if (!filename) throw new ValidationError('filename is required when uploading bytes', { field: 'filename' });
      blob = file instanceof Blob ? file : new Blob([file], { type: MIME[extname(filename).toLowerCase()] || 'application/octet-stream' });
    }
    const query = await this.#where();
    // A keyed upload answers a repeat with the first file. Without a key a repeat would stage a
    // second import, so it is not retried.
    return this.#transport.requestJson('POST', path, { query, signal, retry: keyed,
      idempotencyKey: keyed ? suiteKey(idempotencyKey ?? randomUUID()) : undefined,
      form: () => { const form = new FormData(); form.append('file', blob, filename); return form; } });
  }

  async #download(path, query = {}, opts = {}) {
    return this.#transport.send('GET', path, { ...opts, query: { ...(await this.#where()), ...query },
      consume: async (res) => new Uint8Array(await res.arrayBuffer()) });
  }

  /** Upload a PDF, image or CSV/TSV for extract.task.create (scope extract.write). `file` is a path, bytes or a Blob. */
  extractUpload(file, { filename, idempotencyKey, signal } = {}) {
    return this.#upload('/extract/files', file, filename, { idempotencyKey, keyed: true, signal });
  }
  /** The uploaded original (extract.read and the person's export right). */
  extractDownload(fileId, opts = {}) { return this.#download(`/extract/files/${encodeURIComponent(fileId)}`, {}, opts); }
  /** A confirmed result revision (extract.read and the person's export right). */
  async extractExport(taskId, revision, opts = {}) {
    return JSON.parse(new TextDecoder().decode(await this.#download(`/extract/tasks/${encodeURIComponent(taskId)}/export`, { revision }, opts)));
  }
  /** Upload a CSV or XLSX for dataprep.dataset.import (scope dataprep.write). */
  dataprepUpload(file, { filename, idempotencyKey, signal } = {}) {
    return this.#upload('/dataprep/files', file, filename, { idempotencyKey, keyed: true, signal });
  }
  /** A dataset as a file (dataprep.read, the export right, and the read scope of where it came from). */
  dataprepExport(datasetId, { format = 'csv', ...opts } = {}) {
    return this.#download(`/dataprep/datasets/${encodeURIComponent(datasetId)}/export`, { format }, opts);
  }
  /** Every issue of a run (dataprep.read and the person's export right). */
  async dataprepRunIssues(runId, opts = {}) {
    return JSON.parse(new TextDecoder().decode(await this.#download(`/dataprep/runs/${encodeURIComponent(runId)}/issues`, {}, opts)));
  }
  /** Stage a CSV or XLSX for tables.import.apply (scope tables.write). Not retried. */
  tablesImportUpload(tableId, file, { filename, signal } = {}) {
    return this.#upload(`/tables/${encodeURIComponent(tableId)}/imports`, file, filename, { keyed: false, signal });
  }
  /** The file of an export made with tables.export.create (tables.read and the person's export right). */
  tablesExportDownload(exportId, opts = {}) { return this.#download(`/tables/exports/${encodeURIComponent(exportId)}`, {}, opts); }
  /** An attachment of a submission (forms.read and the person's export right). */
  formsFile(fileId, opts = {}) { return this.#download(`/forms/files/${encodeURIComponent(fileId)}`, {}, opts); }
}

// --------------------------------------------------------------------------- webhooks
function headerMap(headers) {
  const out = {};
  if (!headers) return out;
  const entries = typeof headers.entries === 'function' && !(headers instanceof Map) && typeof headers.get === 'function'
    ? headers.entries() : (headers instanceof Map ? headers.entries() : Object.entries(headers));
  for (const [k, v] of entries) out[String(k).toLowerCase()] = Array.isArray(v) ? v.join(', ') : String(v);
  return out;
}

function toBytes(body) {
  if (Buffer.isBuffer(body)) return body;
  if (body instanceof Uint8Array) return Buffer.from(body.buffer, body.byteOffset, body.byteLength);
  if (body instanceof ArrayBuffer) return Buffer.from(body);
  if (typeof body === 'string') return Buffer.from(body, 'utf8');
  throw new TypeError('body 必须是原始请求体（Buffer / Uint8Array / string），不要传解析后的 JSON');
}

/** Standard Webhooks key material: `whsec_<base64>` is decoded; anything else is used as UTF-8. */
export const WEBHOOK_MIN_KEY_BYTES = 16;
export function webhookKeyBytes(secret) {
  if (Buffer.isBuffer(secret) || secret instanceof Uint8Array) return Buffer.from(secret);
  const s = String(secret);
  if (s.startsWith('whsec_')) {
    const b64 = s.slice(6);
    // Strict, like the server's b64decode(validate=True): lenient decoding would silently turn a
    // mangled secret into different key bytes instead of falling back.
    if (b64.length % 4 === 0 && /^[A-Za-z0-9+/]*={0,2}$/.test(b64)) return Buffer.from(b64, 'base64');
  }
  return Buffer.from(s, 'utf8');
}

/** `v1,<base64>` for one secret — for tests and hand-signed probes of a receiver. */
export function signWebhook(id, timestamp, body, secret) {
  const mac = createHmac('sha256', webhookKeyBytes(secret)).update(`${id}.${timestamp}.`).update(toBytes(body)).digest('base64');
  return `v1,${mac}`;
}

/**
 * Verify a Standard Webhooks delivery over the *raw* body bytes. `secret` may be a list (both
 * the old and the new secret while rotating). Returns {id, timestamp, event} or throws
 * WebhookVerificationError with a code: missing_headers, invalid_timestamp,
 * timestamp_out_of_tolerance, invalid_signature.
 */
export function verifyWebhook(headers, body, secret, { toleranceS = WEBHOOK_TOLERANCE_S, nowS = Date.now() / 1000 } = {}) {
  const h = headerMap(headers);
  const id = h['webhook-id']; const ts = h['webhook-timestamp']; const sig = h['webhook-signature'];
  if (!id || !ts || !sig) throw new WebhookVerificationError('missing_headers', '缺少 webhook-id / webhook-timestamp / webhook-signature');
  if (!/^\d{1,12}$/.test(ts)) throw new WebhookVerificationError('invalid_timestamp', 'webhook-timestamp 不是 Unix 秒');
  if (Math.abs(nowS - Number(ts)) > toleranceS) {
    throw new WebhookVerificationError('timestamp_out_of_tolerance', `webhook-timestamp 与当前时间相差超过 ${toleranceS} 秒（可能是重放）`);
  }
  const raw = toBytes(body);
  const keys = (Array.isArray(secret) ? secret : [secret]).filter(Boolean).map(webhookKeyBytes);
  if (!keys.length) throw new WebhookVerificationError('no_secret', '没有提供签名密钥');
  // An empty or truncated key (an unset variable, 'whsec_' alone) would let anyone sign: refused.
  if (keys.some((k) => k.length < WEBHOOK_MIN_KEY_BYTES)) {
    throw new WebhookVerificationError('weak_secret', `签名密钥为空或过短（至少 ${WEBHOOK_MIN_KEY_BYTES} 字节）：请检查配置`);
  }
  const expected = keys.map((k) => createHmac('sha256', k).update(`${id}.${ts}.`).update(raw).digest());
  for (const part of sig.split(' ')) {
    const comma = part.indexOf(',');
    if (comma < 0 || part.slice(0, comma) !== 'v1') continue;
    const got = Buffer.from(part.slice(comma + 1), 'base64');
    for (const want of expected) {
      if (got.length === want.length && timingSafeEqual(got, want)) {
        let event = null;
        try { event = JSON.parse(raw.toString('utf8')); } catch { event = null; }
        return { id, timestamp: Number(ts), event };
      }
    }
  }
  throw new WebhookVerificationError('invalid_signature', 'webhook-signature 与请求体不匹配');
}

/** The deployment-endpoint signature: X-DocWeave-Signature `v1=<hex>` over "<ts>.<body>". */
export function verifyLegacyWebhook(headers, body, secret, { toleranceS = WEBHOOK_TOLERANCE_S, nowS = Date.now() / 1000 } = {}) {
  const h = headerMap(headers);
  const ts = h['x-docweave-timestamp']; const sig = h['x-docweave-signature'];
  if (!ts || !sig) throw new WebhookVerificationError('missing_headers', '缺少 X-DocWeave-Timestamp / X-DocWeave-Signature');
  if (!/^\d{1,12}$/.test(ts)) throw new WebhookVerificationError('invalid_timestamp', 'X-DocWeave-Timestamp 不是 Unix 秒');
  if (Math.abs(nowS - Number(ts)) > toleranceS) throw new WebhookVerificationError('timestamp_out_of_tolerance', '时间戳超出容忍范围');
  const want = Buffer.from(`v1=${createHmac('sha256', Buffer.from(String(secret), 'utf8')).update(`${ts}.`).update(toBytes(body)).digest('hex')}`);
  const got = Buffer.from(sig);
  if (got.length !== want.length || !timingSafeEqual(got, want)) throw new WebhookVerificationError('invalid_signature', '签名不匹配');
  return { id: h['x-docweave-event-id'] || null, timestamp: Number(ts), event: JSON.parse(toBytes(body).toString('utf8')) };
}

/**
 * Minimal process-local receiver: acknowledge only after onEvent succeeds (or durably enqueues).
 * Concurrent attempts and failed handlers return 503; only successful events enter the cache.
 * Use the n8n companion's persistent receipts when deduplication must survive restarts.
 */
export function createWebhookReceiver({ secret, onEvent = () => {}, onRejected = () => {}, toleranceS, maxBodyBytes = MIB } = {}) {
  const seen = new Set(); const pending = new Set();
  return createServer((req, res) => {
    if (req.method !== 'POST') { res.writeHead(405, { Allow: 'POST' }).end(); return; }
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > maxBodyBytes) { res.writeHead(413).end(); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', async () => {
      if (res.writableEnded) return;
      const body = Buffer.concat(chunks);
      let v;
      try {
        v = verifyWebhook(req.headers, body, secret, { toleranceS });
      } catch (e) {
        res.writeHead(401).end();
        onRejected({ code: e.code, message: e.message, headers: req.headers });
        return;
      }
      if (seen.has(v.id)) { res.writeHead(204).end(); return; }
      if (pending.has(v.id)) { res.writeHead(503).end(); return; }
      pending.add(v.id);
      try {
        await onEvent({ ...v, headers: headerMap(req.headers) });
        seen.add(v.id);
        res.writeHead(204).end();
      } catch {
        res.writeHead(503).end();
      } finally { pending.delete(v.id); }
    });
  });
}

// --------------------------------------------------------------------------- command line
const EXIT = { ok: 0, usage: 2, api4xx: 3, server: 4, jobFailed: 5, timeout: 6, interrupted: 130 };

function exitCodeFor(e) {
  if (e instanceof JobFailedError) return EXIT.jobFailed;
  if (e instanceof WaitTimeoutError) return EXIT.timeout;
  if (e instanceof ValidationError) return EXIT.usage;
  if (e instanceof PreflightError) return EXIT.api4xx;
  if (e instanceof TransportError || e instanceof IntegrityError || e instanceof ServerError) return EXIT.server;
  if (e instanceof APIError) return e.status >= 500 ? EXIT.server : EXIT.api4xx;
  return EXIT.server;
}

function parseArgs(argv, { booleans = [], repeated = [] } = {}) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--') { out._.push(...argv.slice(i + 1)); break; }
    if (!a.startsWith('--')) { out._.push(a); continue; }
    let [k, v] = a.slice(2).split(/=(.*)/s, 2);
    k = k.replace(/-/g, '_');
    if (booleans.includes(k)) { out[k] = v === undefined ? true : !['0', 'false', 'no'].includes(v); continue; }
    if (v === undefined) {
      if (i + 1 >= argv.length) throw new ValidationError(`--${k} 缺少取值`, { field: k });
      v = argv[++i];
    }
    if (repeated.includes(k)) (out[k] ||= []).push(v);
    else out[k] = v;
  }
  return out;
}

const USAGE = `synwevo-docs.mjs ${VERSION} — Synwevo Docs 机器接口客户端（Node.js >= 18，无依赖）

环境变量：
  SYNWEVO_BASE_URL                 服务地址，如 http://127.0.0.1:8765
  SYNWEVO_API_KEY_FILE             存放 dw_ 密钥的文件（推荐；或 SYNWEVO_API_KEY）
  SYNWEVO_CLIENT_ID / SYNWEVO_CLIENT_SECRET_FILE / SYNWEVO_TOKEN_URL   服务账户（client_credentials）
  密钥只从环境变量或文件读取，不接受命令行参数。

命令（输出为 JSON；进度写到 stderr）：
  whoami | capabilities | usage
  generate --source <文件>... --template <docx> --fields <fields.json> --definition <project.json>
           [--format docx]... [--naming <规则>] [--error-policy block|skip] [--layout off|warn|fit]
           [--out <目录>] [--project-name <名称>] [--template-name <名称>] [--key-prefix <前缀>]
           [--expected-tenant <工作空间>] [--timeout <秒>] [--cancel-on-interrupt] [--events]
  projects create --name <名称> | projects list|get|delete [ID]
  files upload <项目 ID> --file <文件> | files list <项目 ID> | files get|delete <文件 ID>
  templates upload --file <模板> | templates list|get|delete [ID]
  templates create-builtin --kind builtin_doc|builtin_sheet --file editor.json [--name <名称>]
  templates save-builtin <模板 ID> --file editor.json --base-version N [--name <名称>]
  templates get-builtin <模板 ID> [--version N]
    create-builtin / save-builtin 支持 --idempotency-key <键>
  templates bind <项目 ID> --template-id <模板 ID>
  fields get|create <模板 ID> [--file fields.json]
  rule put <项目 ID> --file rule.json | mappings get|put <项目 ID> [--file mappings.json]
  definition get|put <项目 ID> [--file project.json] [--base-rev N]
  preflight <项目 ID> [--simulate | --issues] [--format docx]
  preview <项目 ID> --out <文件> [--index N | --key <样本键>] [--signature <签名>]
  jobs submit|list <项目 ID> [--format docx] | jobs runs <任务 ID>
  report download <任务 ID> --out report.csv
  webhooks redeliver <订阅 ID> <投递 ID>
  jobs get|wait|cancel|retry|items|events <任务 ID> [--timeout <秒>] [--cancel-on-interrupt] [--failed-only]
  artifacts list <任务 ID> | artifacts download <任务 ID> --out <目录>
  archive download <任务 ID> --out <文件.zip>
  webhooks list | webhooks create --url <地址> [--event <类型>]... --secret-out <文件>
  webhooks rotate <订阅 ID> --secret-out <文件> | webhooks delete|pause|resume|ping|deliveries <订阅 ID>
  webhooks listen --port <端口> --secret-file <文件> [--host 127.0.0.1] [--max-events N]
  webhooks verify --secret-file <文件> --headers <headers.json> --body <原始请求体文件>
  events [--types job.completed,...] [--cursor <游标>]

退出码：0 成功；2 用法错误；3 接口 4xx（含预检未通过）；4 服务端 5xx / 网络 / 校验和不一致；
       5 任务以 failed / cancelled 结束；6 等待超时；130 被中断。`;

function out(obj) { process.stdout.write(`${JSON.stringify(obj, null, 2)}\n`); }
function progress(obj) { process.stderr.write(`${JSON.stringify(obj)}\n`); }

async function writeSecretOnce(path, secret) {
  // 0600 and exclusive create: a secret shown once must not land in a readable or existing file.
  await writeFile(path, `${secret}\n`, { mode: 0o600, flag: 'wx' });
}

async function withInterrupt(client, args, fn) {
  const ctl = new AbortController();
  let jobId = null;
  const onSig = () => { if (!ctl.signal.aborted) ctl.abort(Object.assign(new Error('interrupted'), { interrupted: true })); };
  process.once('SIGINT', onSig);
  process.once('SIGTERM', onSig);
  try {
    return await fn(ctl.signal, (id) => { jobId = id; });
  } catch (e) {
    if (ctl.signal.aborted) {
      if (args.cancel_on_interrupt && jobId) {
        try { progress({ step: 'cancel', job: jobId, result: (await client.cancelJob(jobId)).status }); } catch (c) { progress({ step: 'cancel_failed', error: c.message }); }
      }
      const err = new Error('已中断'); err.exitCode = EXIT.interrupted; throw err;
    }
    throw e;
  } finally {
    process.off('SIGINT', onSig);
    process.off('SIGTERM', onSig);
  }
}

export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv, { booleans: ['cancel_on_interrupt', 'events', 'help', 'include_skipped', 'failed_only', 'json', 'issues', 'simulate'],
    repeated: ['source', 'format', 'event'] });
  const [cmd, sub, id] = args._;
  if (!cmd || args.help || cmd === 'help') { process.stdout.write(`${USAGE}\n`); return cmd || args.help ? EXIT.ok : EXIT.usage; }

  if (cmd === 'webhooks' && sub === 'verify') {
    if (!args.secret_file || !args.headers || !args.body) throw new ValidationError('需要 --secret-file、--headers 与 --body', { field: 'verify' });
    const secret = await readSecretFile(args.secret_file, 'secret-file');
    const headers = JSON.parse(await readFile(args.headers, 'utf8'));
    out(verifyWebhook(headers, await readFile(args.body), secret));
    return EXIT.ok;
  }
  if (cmd === 'webhooks' && sub === 'listen') {
    if (!args.secret_file) throw new ValidationError('需要 --secret-file（订阅创建时保存的 whsec_ 密钥）', { field: 'secret_file' });
    const secret = await readSecretFile(args.secret_file, 'secret-file');
    const max = args.max_events ? Number(args.max_events) : Infinity;
    let n = 0;
    const server = createWebhookReceiver({ secret,
      onEvent: (v) => { out({ verified: true, webhook_id: v.id, type: v.event?.type, job: v.event?.job?.id ?? null, status: v.event?.job?.status ?? null }); if (++n >= max) server.close(); },
      onRejected: (r) => progress({ verified: false, code: r.code }) });
    await new Promise((ok) => server.listen(Number(args.port || 0), args.host || '127.0.0.1', ok));
    progress({ listening: `http://${args.host || '127.0.0.1'}:${server.address().port}/` });
    await new Promise((ok) => { server.on('close', ok); process.once('SIGINT', () => server.close()); });
    return EXIT.ok;
  }

  const base = process.env.SYNWEVO_BASE_URL;
  const client = new Client(base, await credentialsFromEnv(), {
    onRetry: (r) => progress({ retry: r.attempt, method: r.method, path: r.path, status: r.status, code: r.code, delay_ms: r.delayMs }) });
  const timeoutMs = args.timeout ? Number(args.timeout) * 1000 : undefined;
  if (timeoutMs !== undefined && (!Number.isFinite(timeoutMs) || timeoutMs <= 0)) {
    throw new ValidationError('--timeout 必须是正数（秒）', { field: 'timeout' });
  }

  const jsonFile = async (path) => {
    if (!path) throw new ValidationError('需要 --file <JSON 文件>', { field: 'file' });
    try { return JSON.parse(await readFile(path, 'utf8')); }
    catch (e) { throw new ValidationError(`无法读取 JSON：${e.message}`, { field: 'file' }); }
  };
  const idem = { idempotencyKey: args.idempotency_key };
  switch (`${cmd}${sub && ['projects', 'files', 'templates', 'fields', 'rule', 'mappings', 'definition', 'jobs', 'artifacts', 'archive', 'report', 'webhooks'].includes(cmd) ? ` ${sub}` : ''}`) {
    case 'preview': out(await client.downloadPreview(sub, args.out, { index: args.index == null ? undefined : Number(args.index), key: args.key, signature: args.signature })); return EXIT.ok;
    case 'whoami': out(await client.whoami()); return EXIT.ok;
    case 'capabilities': out(await client.capabilities()); return EXIT.ok;
    case 'usage': out(await client.usage()); return EXIT.ok;
    case 'projects create': out(await client.createProject({ name: args.name }, idem)); return EXIT.ok;
    case 'projects list': out(await client.collect(client.listProjects())); return EXIT.ok;
    case 'projects get': out(await client.getProject(id)); return EXIT.ok;
    case 'projects delete': out(await client.deleteProject(id)); return EXIT.ok;
    case 'files upload': out(await client.uploadSource(id, args.file, idem)); return EXIT.ok;
    case 'files list': out(await client.collect(client.listFiles(id))); return EXIT.ok;
    case 'files get': out(await client.getFile(id)); return EXIT.ok;
    case 'files delete': out(await client.deleteFile(id)); return EXIT.ok;
    case 'templates upload': out(await client.uploadTemplate(args.file, { ...idem, name: args.name })); return EXIT.ok;
    case 'templates list': out(await client.collect(client.listTemplates())); return EXIT.ok;
    case 'templates get': out(await client.getTemplate(id)); return EXIT.ok;
    case 'templates create-builtin': out(await client.createBuiltinTemplate(
      { kind: args.kind, name: args.name, editor: await jsonFile(args.file) }, idem)); return EXIT.ok;
    case 'templates save-builtin': out(await client.saveBuiltinTemplate(id,
      { editor: await jsonFile(args.file), name: args.name, baseVersion: Number(args.base_version) }, idem)); return EXIT.ok;
    case 'templates get-builtin': out(await client.getBuiltinTemplate(id,
      { version: args.version == null ? undefined : Number(args.version) })); return EXIT.ok;
    case 'templates delete': out(await client.deleteTemplate(id)); return EXIT.ok;
    case 'templates bind': out(await client.bindTemplate(id, args.template_id)); return EXIT.ok;
    case 'fields get': out(await client.getFields(id)); return EXIT.ok;
    case 'fields create': out(await client.createFields(id, await jsonFile(args.file), idem)); return EXIT.ok;
    case 'rule put': out(await client.putRule(id, await jsonFile(args.file))); return EXIT.ok;
    case 'mappings get': out(await client.getMappings(id)); return EXIT.ok;
    case 'mappings put': out(await client.putMappings(id, await jsonFile(args.file), { baseRev: args.base_rev == null ? null : Number(args.base_rev) })); return EXIT.ok;
    case 'definition get': out(await client.getDefinition(id)); return EXIT.ok;
    case 'definition put': out(await client.putDefinition(id, await jsonFile(args.file), { baseRev: args.base_rev == null ? null : Number(args.base_rev) })); return EXIT.ok;
    case 'preflight':
      out(args.issues ? await client.collect(client.preflightIssues(sub, { level: args.level, formats: args.format }))
        : await client.preflight(sub, { formats: args.format, naming: args.naming, simulate: args.simulate })); return EXIT.ok;
    case 'jobs submit': out(await client.createJob(id, { formats: args.format, naming: args.naming,
      errorPolicy: args.error_policy, layout: args.layout }, idem)); return EXIT.ok;
    case 'jobs list': out(await client.collect(client.listJobs(id))); return EXIT.ok;
    case 'jobs runs': out(await client.collect(client.runs(id))); return EXIT.ok;
    case 'report download': out(await client.downloadReport(id, args.out || `${id}.csv`)); return EXIT.ok;
    case 'webhooks redeliver': out(await client.redeliver(id, args._[3])); return EXIT.ok;
    case 'generate': {
      const result = await withInterrupt(client, args, (signal, onJob) => client.generate({
        sources: args.source, template: args.template, fields: args.fields, definition: args.definition,
        formats: args.format || ['docx'], naming: args.naming, errorPolicy: args.error_policy, layout: args.layout,
        outDir: args.out || '.', projectName: args.project_name, templateName: args.template_name, keyPrefix: args.key_prefix,
        expectedTenant: args.expected_tenant, timeoutMs, signal, onProgress: progress,
        onJobCreated: (j) => onJob(j.id), onEvent: args.events ? (ev) => progress({ event: ev.type, seq: ev.seq }) : undefined }));
      out(result);
      return EXIT.ok;
    }
    case 'jobs get': out(await client.getJob(id)); return EXIT.ok;
    case 'jobs wait': {
      const job = await withInterrupt(client, args, (signal, onJob) => { onJob(id); return client.waitForJob(id, { timeoutMs, signal }); });
      out(job);
      return SUCCESS_STATUSES.includes(job.status) ? EXIT.ok : EXIT.jobFailed;
    }
    case 'jobs cancel': out(await client.cancelJob(id)); return EXIT.ok;
    case 'jobs retry': out(await client.retryJob(id, { includeSkipped: args.include_skipped, failedOnly: args.failed_only }, { idempotencyKey: args.idempotency_key })); return EXIT.ok;
    case 'jobs items': out(await client.collect(client.items(id, { status: args.status }))); return EXIT.ok;
    case 'jobs events': out(await client.jobEvents(id, { after: Number(args.after || 0) })); return EXIT.ok;
    case 'artifacts list': out(await client.collect(client.artifacts(id))); return EXIT.ok;
    case 'artifacts download': {
      const done = [];
      for await (const a of client.artifacts(id)) done.push(await client.downloadArtifact(a, { outDir: args.out || '.' }));
      out(done);
      return EXIT.ok;
    }
    case 'archive download': out(await client.downloadArchive(id, args.out || `${id}.zip`, { format: args.format?.[0] })); return EXIT.ok;
    case 'webhooks list': out(await client.listSubscriptions()); return EXIT.ok;
    case 'webhooks create': {
      if (!args.url || !args.secret_out) throw new ValidationError('需要 --url 与 --secret-out（密钥只返回这一次，写入该文件，权限 600）', { field: 'secret_out' });
      const sub_ = await client.createSubscription({ url: args.url, events: args.event, description: args.description });
      await writeSecretOnce(args.secret_out, sub_.secret);
      const { secret: _drop, ...rest } = sub_;
      out({ ...rest, secret_file: resolve(args.secret_out) });
      return EXIT.ok;
    }
    case 'webhooks rotate': {
      if (!args.secret_out) throw new ValidationError('需要 --secret-out', { field: 'secret_out' });
      const r = await client.rotateSubscriptionSecret(id, { overlapS: args.overlap_s != null ? Number(args.overlap_s) : undefined });
      await writeSecretOnce(args.secret_out, r.secret);
      const { secret: _drop, ...rest } = r;
      out({ ...rest, secret_file: resolve(args.secret_out) });
      return EXIT.ok;
    }
    case 'webhooks delete': out(await client.deleteSubscription(id)); return EXIT.ok;
    case 'webhooks pause': out(await client.pauseSubscription(id)); return EXIT.ok;
    case 'webhooks resume': out(await client.resumeSubscription(id)); return EXIT.ok;
    case 'webhooks ping': out(await client.pingSubscription(id)); return EXIT.ok;
    case 'webhooks deliveries': out(await client.collect(client.deliveries(id, { status: args.status }))); return EXIT.ok;
    case 'events': out(await client.collect(client.events({ cursor: args.cursor, types: args.types }))); return EXIT.ok;
    default:
      throw new ValidationError(`不认识的命令：${args._.join(' ')}（--help 查看用法）`, { field: 'command' });
  }
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  main().then((code) => { process.exitCode = code; }, (e) => {
    if (e?.exitCode === EXIT.interrupted) { process.stderr.write('已中断\n'); process.exitCode = EXIT.interrupted; return; }
    process.stderr.write(`${JSON.stringify(e instanceof SynwevoError ? e.toJSON() : { error: e?.name || 'Error', message: e?.message })}\n`);
    process.exitCode = exitCodeFor(e);
  });
}
