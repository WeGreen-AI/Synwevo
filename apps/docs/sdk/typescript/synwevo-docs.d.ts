// Type declarations for synwevo-docs.mjs (Synwevo Docs machine-API client, Node.js >= 18).
//
// They describe the module as it is: every export, the option objects it reads, and the shapes the
// /api/v1 routes return (docs/integration/api.md, docs/integration/openapi.yaml and the route code).
// Where the server documents an object only loosely, the known fields are listed and the rest is
// `Record<string, unknown>`. The server may add fields at any time (api.md §5); these types list the
// ones it returns today. Keep this file in step with synwevo-docs.mjs.
//
// Several methods validate their arguments before sending and throw a ValidationError
// synchronously (they are not `async`): updateProject, runSourceOcr, updateField, deleteField,
// createBuiltinTemplate, getBuiltinTemplate, saveBuiltinTemplate, preflight, preflightIssues,
// createJob, items and downloadPreview. Everything else reports failures through the promise.

/// <reference types="node" />

import type { PathLike } from 'node:fs';
import type { IncomingHttpHeaders, Server } from 'node:http';

// ============================================================================ constants
export declare const VERSION: string;
export declare const API_PREFIX: '/api/v1';
export declare const WORKSPACE_BASE_URL: 'https://accounts.synwevo.com';
export declare const WORKSPACE_RESOURCE: 'urn:synwevo:platform:workspace';
export declare const WORKSPACE_API_PREFIX: '/api/workspace/v1';
export declare const TERMINAL_STATUSES: readonly ['completed', 'completed_with_errors', 'failed', 'cancelled'];
export declare const SUCCESS_STATUSES: readonly ['completed', 'completed_with_errors'];
export declare const ERROR_POLICIES: readonly ['block', 'skip'];
export declare const LAYOUTS: readonly ['off', 'warn', 'fit'];
export declare const ITEM_STATUSES: readonly ['pending', 'running', 'success', 'failed', 'skipped', 'cancelled'];
/** Seconds a webhook timestamp may differ from the local clock. */
export declare const WEBHOOK_TOLERANCE_S: 300;

/** JSON payloads with no client-side coercion or format-specific schema. */
export type JSONValue = null | boolean | number | string | JSONValue[] | { [key: string]: JSONValue };

// ============================================================================ enumerations
export type TerminalJobStatus = (typeof TERMINAL_STATUSES)[number];
export type SuccessJobStatus = (typeof SUCCESS_STATUSES)[number];
/** queued → running → (completed | completed_with_errors | failed | cancelled); cancelling while a cancel is pending. */
export type JobStatus = 'queued' | 'running' | 'cancelling' | TerminalJobStatus;
export type JobPhase = 'planning' | 'generating';
export type ErrorPolicy = (typeof ERROR_POLICIES)[number];
export type Layout = (typeof LAYOUTS)[number];
export type ItemStatus = (typeof ITEM_STATUSES)[number];
/**
 * `scanning` and `scan_failed` are quarantine (the server retries the check; do not re-upload),
 * `parsing` is in progress, `ready` / `needs_ocr` are usable, `error` / `unsupported` are not
 * readable, `rejected` failed the malware check.
 */
export type SourceFileStatus =
  | 'scanning' | 'scan_failed' | 'parsing' | 'ready' | 'needs_ocr' | 'error' | 'unsupported' | 'rejected';
/** What `waitForFile` resolves with: every other status either keeps it polling or throws. */
export type SettledSourceFileStatus = 'ready' | 'needs_ocr' | 'unsupported';
export type ScanStatus =
  | 'pending' | 'clean' | 'infected' | 'encrypted' | 'error' | 'too_large' | 'not_configured' | 'unscanned';
export type RecordMode = 'per_row' | 'per_file' | 'per_group' | 'per_block';
/** The values the template service writes to `templates.kind` (the column itself is free text). */
export type TemplateKind = 'uploaded' | 'builtin_doc' | 'builtin_sheet';
export type BuiltinTemplateKind = 'builtin_doc' | 'builtin_sheet';
/** Field kinds accepted by a `docweave.template-fields` v1 definition. */
export type FieldKind =
  | 'text' | 'cell' | 'region' | 'repeat' | 'repeat_block' | 'block_column' | 'pdf_box' | 'pdf_repeat' | 'pdf_field';
export type UploadStatus = 'open' | 'complete' | 'consumed' | 'expired' | 'aborted';
export type UploadPurpose = 'source' | 'template';
export type JobEventType =
  | 'job.created' | 'job.started' | 'job.planned' | 'job.cancel_requested' | 'job.retry_requested'
  | 'job.recovered' | 'job.completed' | 'job.completed_with_errors' | 'job.failed' | 'job.cancelled';
export type FileEventType = 'file.ready' | 'file.needs_ocr' | 'file.rejected' | 'file.failed';
/** Copy request status changes, sent to the source (personal) workspace. */
export type CopyEventType =
  | 'copy.requested' | 'copy.approved' | 'copy.rejected' | 'copy.withdrawn' | 'copy.expired'
  | 'copy.done' | 'copy.failed' | 'copy.stale';
/** Types in the workspace event feed (`GET /events`). */
export type EventType = JobEventType | FileEventType | CopyEventType;
/** Types a webhook delivery can carry: the feed's plus the test ping. */
export type WebhookEventType = EventType | 'webhook.ping';
/** A subscription filter: everything, a family, or one type. */
export type WebhookEventFilter = '*' | 'job.*' | 'file.*' | 'copy.*' | EventType;
export type SubscriptionStatus = 'active' | 'paused' | 'disabled';
export type DeliveryStatus = 'pending' | 'delivered' | 'failed' | 'dropped';
export type WebhookVerificationErrorCode =
  | 'missing_headers' | 'invalid_timestamp' | 'timestamp_out_of_tolerance' | 'invalid_signature' | 'no_secret';

// ============================================================================ errors
export interface SynwevoErrorOptions {
  /** HTTP status; 0 when there was no HTTP response. */
  status?: number;
  code?: string | null;
  requestId?: string | null;
  field?: string | null;
  details?: Record<string, unknown> | null;
  /** Seconds the server asked to wait (Retry-After, or the RateLimit `t=` reset on 429/503). */
  retryAfter?: number | null;
  method?: string | null;
  url?: string | null;
  cause?: unknown;
}

export interface SynwevoErrorJSON {
  error: string;
  status: number;
  code: string | null;
  message: string;
  field: string | null;
  request_id: string | null;
  details: Record<string, unknown> | null;
  retry_after: number | null;
}

export declare class SynwevoError extends Error {
  constructor(message: string, options?: SynwevoErrorOptions);
  /** HTTP status; 0 when there was no HTTP response (transport, validation, integrity, waits). */
  status: number;
  /** Machine-readable code: the server's error code, or one the client sets (e.g. `wait_timeout`). */
  code: string | null;
  requestId: string | null;
  /** The request field or header the error is about (a JSON pointer is turned into `a.b.0`). */
  field: string | null;
  details: Record<string, unknown> | null;
  retryAfter: number | null;
  method: string | null;
  url: string | null;
  toJSON(): SynwevoErrorJSON;
}
/** No usable response: connection failure, timeout, or a body that was cut off or not JSON. */
export declare class TransportError extends SynwevoError {}
/** A non-2xx response (the class says which status family). */
export declare class APIError extends SynwevoError {}
export declare class BadRequestError extends APIError {}
export declare class AuthenticationError extends APIError {}
export declare class PermissionDeniedError extends APIError {}
export declare class NotFoundError extends APIError {}
export declare class ConflictError extends APIError {}
export declare class GoneError extends APIError {}
export declare class PayloadTooLargeError extends APIError {}
export declare class RangeNotSatisfiableError extends APIError {}
export declare class RateLimitError extends APIError {}
export declare class ServerError extends APIError {}
/** The bytes received are not the bytes the server (or the manifest) described. */
export declare class IntegrityError extends SynwevoError {}
/** Rejected before sending: the server documents these values and would refuse them too. */
export declare class ValidationError extends SynwevoError {}
export declare class WaitTimeoutError extends SynwevoError {}
/** A job reached a terminal status other than completed / completed_with_errors. */
export declare class JobFailedError extends SynwevoError {}
/** Preflight found problems that would block the job (error_policy=block). */
export declare class PreflightError extends SynwevoError {}
export declare class WebhookVerificationError extends Error {
  constructor(code: WebhookVerificationErrorCode, message: string);
  name: 'WebhookVerificationError';
  code: WebhookVerificationErrorCode;
}

// ============================================================================ helpers
/** Anything with `get(name)` (a fetch `Headers`), or a plain header object. */
export type HeaderSource =
  | { get(name: string): string | null | undefined }
  | Readonly<Record<string, string | null | undefined>>;

/** Retry-After as seconds: delta-seconds or an HTTP-date (RFC 9110 §10.2.3); null if absent or invalid. */
export declare function parseRetryAfter(value: string | number | null | undefined, nowMs?: number): number | null;
/**
 * The `t` (seconds until quota is back) of an IETF RateLimit header, e.g. `"key";r=0;t=12`. With
 * several policies the longest reset among those with nothing left decides; when every policy
 * has budget left, the first item's reset is returned. null when there is no `t`.
 */
export declare function parseRateLimitReset(value: string | null | undefined): number | null;
/** The typed error for a non-2xx response (the error envelope, RFC 9457 problem+json or OAuth). */
export declare function errorFromResponse(
  status: number,
  headers: HeaderSource | null | undefined,
  text: string | null | undefined,
  options?: { method?: string | null; url?: string | null },
): APIError;
/** Hex SHA-256 of a file, streamed. */
export declare function sha256File(path: PathLike): Promise<string>;
/** A file name the server gave us, made safe to use inside a local directory. */
export declare function safeFilename(name: string | null | undefined, fallback?: string): string;
/**
 * The server's `terminal` flag when an object carries one, otherwise whether `status` is one of
 * TERMINAL_STATUSES. (Job responses from /api/v1 carry no `terminal` field today.)
 */
export declare function isTerminal(job: { readonly status?: string | null; readonly terminal?: boolean | null } | null | undefined): boolean;

// ============================================================================ credentials
/** What the Client needs from a credential; ApiKey and ClientCredentials implement it. */
export interface Credentials {
  /** The Authorization header value, e.g. `Bearer …`. */
  authorization(): Promise<string>;
  /** Forget a cached token (called once after a 401 when `refreshable`). */
  invalidate(): void;
  readonly refreshable: boolean;
}

/** A `dw_…` API key issued with `python -m synwevo_docs.cli apikey create`. */
export declare class ApiKey implements Credentials {
  /** @throws ValidationError when the key is empty. */
  constructor(key: string);
  /** The trimmed key; not enumerable, so it is not printed or serialised. */
  readonly key: string;
  authorization(): Promise<string>;
  invalidate(): void;
  readonly refreshable: false;
  toJSON(): { type: 'api_key'; key: '***' };
}

export interface ClientCredentialsOptions {
  tokenUrl: string;
  clientId: string;
  clientSecret: string;
  /** Default `docweave.read docweave.write`; null or '' sends no scope. */
  scope?: string | null;
  /** RFC 8707 resource, e.g. `urn:synwevo:app:docweave`. */
  resource?: string | null;
  fetch?: FetchLike;
  /** Token request timeout. Default 30 000. */
  timeoutMs?: number;
  /** Fetch a new token this many seconds before the current one expires. Default 60. */
  refreshMarginS?: number;
}

/**
 * OAuth 2.0 client_credentials for a service account (client_secret_post): fetches an access
 * token, caches it, and fetches a new one shortly before it expires (or once after a 401).
 */
export declare class ClientCredentials implements Credentials {
  /** @throws ValidationError without tokenUrl, clientId and clientSecret. */
  constructor(options: ClientCredentialsOptions);
  tokenUrl: string;
  clientId: string;
  /** Not enumerable. */
  readonly clientSecret: string;
  scope: string | null;
  resource: string | null;
  fetch: FetchLike;
  timeoutMs: number;
  refreshMarginS: number;
  readonly refreshable: true;
  invalidate(): void;
  authorization(): Promise<string>;
  toJSON(): { type: 'client_credentials'; token_url: string; client_id: string; scope: string | null };
}

/**
 * Credentials from the environment (the variables the Python SDK and CLI read):
 * SYNWEVO_API_KEY or SYNWEVO_API_KEY_FILE, or SYNWEVO_CLIENT_ID + SYNWEVO_CLIENT_SECRET_FILE (or
 * SYNWEVO_CLIENT_SECRET) + SYNWEVO_TOKEN_URL [+ SYNWEVO_SCOPE, SYNWEVO_RESOURCE].
 * Rejects with ValidationError when none is set or the set is incomplete.
 */
export declare function credentialsFromEnv(
  env?: Readonly<Record<string, string | undefined>>,
): Promise<ApiKey | ClientCredentials>;

// ============================================================================ transport
/** The subset of `fetch` the client calls: a URL string and an init object. */
export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;
export type QueryValue = string | number | boolean;
/** Query parameters; null / undefined are left out, arrays repeat the key. */
export type QueryParams = Readonly<Record<string, QueryValue | readonly QueryValue[] | null | undefined>>;

export interface RetryInfo {
  method: string;
  path: string;
  /** 1-based number of the retry about to happen. */
  attempt: number;
  delayMs: number;
  /** 0 for a transport failure. */
  status: number;
  code: string | null;
}

export interface ClientOptions {
  /** Per-request timeout. Positive integer, default 30 000. */
  timeoutMs?: number;
  /** Retries of one logical call. Non-negative integer, default 4. */
  maxRetries?: number;
  /** Default 500. */
  backoffBaseMs?: number;
  /** Default 30 000. */
  backoffCapMs?: number;
  /** A Retry-After longer than this is not waited for: the error is thrown. Default 120 000. */
  maxRetryAfterMs?: number;
  userAgent?: string;
  /** Default `globalThis.fetch`. */
  fetch?: FetchLike;
  /** Default: node:timers/promises setTimeout (abortable). */
  sleep?: (ms: number, signal?: AbortSignal) => Promise<unknown>;
  /** Jitter source in [0, 1). Default Math.random. */
  random?: () => number;
  /** Uploads up to this size are sent as one multipart request; larger ones are resumable. Default 16 MiB. */
  multipartMaxBytes?: number;
  /** Resumable upload chunk size. Positive integer, default 8 MiB. */
  chunkBytes?: number;
  onRetry?: ((info: RetryInfo) => void) | null;
}

/** Options every request accepts. */
export interface RequestOptions {
  signal?: AbortSignal;
  /** Overrides the client's timeoutMs for this call. */
  timeoutMs?: number;
  /** true / false overrides the method rule (GET/HEAD, keyed POSTs and unconditional PUTs retry). */
  retry?: boolean;
  headers?: Readonly<Record<string, string>>;
}

export interface SendOptions extends RequestOptions {
  query?: QueryParams;
  /** Sent as application/json when not undefined. */
  json?: unknown;
  /** Rebuilt for every attempt: a sent body cannot be re-read. */
  form?: () => FormData | Promise<FormData>;
  body?: () => NonNullable<RequestInit['body']> | Promise<NonNullable<RequestInit['body']>>;
  /** Sent as Idempotency-Key and reused on every retry; also makes a POST retryable. */
  idempotencyKey?: string;
  /** A PUT carrying a concurrency precondition is not retried automatically. */
  precondition?: boolean;
  /** Accepted for downloads; every successful response gets `idle()` whether or not it is set. */
  idle?: boolean;
}

/** A successful Response from `send`, holding the request's timer until `release()`. */
export type SentResponse = Response & {
  /** End the request's timer. Call it once the body has been read (or abandoned). */
  release(): void;
  /** Restart the timeout: an idle timeout for streamed bodies. */
  idle(): void;
};

/** Attached (not enumerable) to every JSON object `requestJson` returns. */
export interface ResponseMeta {
  status: number;
  requestId: string | null;
  /** The server replayed an earlier response for the same Idempotency-Key. */
  replayed: boolean;
  location: string | null;
  retryAfter: number | null;
}
export type WithMeta<T> = T & { readonly $meta: ResponseMeta };

/** One page of a list route. Offset routes set next_offset; cursor routes set next_cursor / has_more. */
export interface Page<T> {
  items: T[];
  total?: number;
  offset?: number | null;
  limit?: number;
  next_offset?: number | null;
  next_cursor?: string | null;
  has_more?: boolean;
}

/** offset or cursor (not both); limit defaults to 50, at most 200 (artifacts and items differ). */
export interface ListQuery {
  limit?: number;
  offset?: number;
  cursor?: string;
}

// ============================================================================ API objects: system
export interface WhoAmI {
  key_id: string;
  name: string;
  tenant: string;
  scopes: string[];
}

export interface Capabilities {
  api_version: string;
  rows: Record<string, unknown>[];
  environment: {
    libreoffice: string | null;
    tesseract: string | null;
    ocr_languages: string[];
    pdf_engine: string;
    malware_scan: Record<string, unknown>;
    [key: string]: unknown;
  };
  limits: { max_file_mb: number; max_pages: number; max_cells: number; max_records_per_job: number };
  [key: string]: unknown;
}

export interface Usage {
  workspace: string;
  active_jobs: number;
  queued_jobs: number;
  running_jobs: number;
  cancelling_jobs: number;
  planning_jobs: number;
  pending_items: number;
  limits: {
    max_active_jobs: number;
    max_records_per_job: number;
    sync_plan_max: number;
    requests_per_minute: number;
    workspace_requests_per_minute: number;
    policy_sources: Record<string, unknown>;
    max_file_mb: number;
    max_pages: number;
    max_cells: number;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

// ============================================================================ API objects: projects
export interface RecordRule {
  mode: RecordMode;
  table?: Record<string, unknown>;
  group?: Record<string, unknown>;
  block?: Record<string, unknown>;
  [key: string]: unknown;
}

/** Output defaults of a project. */
export interface ProjectOutput {
  formats?: string[];
  naming?: string;
  error_policy?: ErrorPolicy;
  layout?: Layout;
  [key: string]: unknown;
}

/** `{type, sources, target: {binding}, transforms?, columns?}` (api.md §3.4). */
export interface Mapping {
  id?: string;
  type: 'single' | 'region' | 'repeat';
  sources: Record<string, unknown>[];
  target: { binding: string; [key: string]: unknown };
  transforms?: unknown[];
  columns?: unknown[];
  [key: string]: unknown;
}

export interface Mappings {
  rev: number;
  mappings: Mapping[];
}

/** The template a project is bound to, as GET /projects/{pid} reports it. */
export interface ProjectTemplateBinding {
  id: string;
  /** null when the bound template was deleted (`missing`). */
  name: string | null;
  format: string | null;
  /** The pinned version, else the head. */
  version: number | null;
  head: number | null;
  pinned: boolean;
  /** Number of fields in that version. */
  fields: number;
  /** The template was deleted. */
  missing?: true;
  /** A rescan found the template infected; its contents are refused (410) everywhere. */
  rejected?: true;
  scan?: ScanVerdict;
}

/** GET /projects/{pid} (also returned by updateProject and bindTemplate). */
export interface Project {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  /** `{}` until a rule is saved. */
  rule: Partial<RecordRule>;
  output: ProjectOutput;
  mapping_rev: number;
  /** Pass back as `baseSettingsRev` to updateProject. */
  settings_rev: number;
  archived_at: number | null;
  template: ProjectTemplateBinding | null;
  files: SourceFile[];
  /** Number of mappings. */
  mappings: number;
}

/** POST /projects answers with the stored project row, not the GET view. */
export interface CreatedProject {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  [key: string]: unknown;
}

/** Job counts as stored (webhooks, events, list rows): item statuses plus totals. */
export interface RawJobCounts {
  total?: number | null;
  planned?: number | null;
  unplanned?: number | null;
  files?: number | null;
  documents?: number | null;
  pending?: number | null;
  running?: number | null;
  success?: number | null;
  failed?: number | null;
  skipped?: number | null;
  cancelled?: number | null;
}

/** An item of listProjects (archived projects are not listed). */
export interface ProjectListItem {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  template_id: string | null;
  record_rule: Partial<RecordRule>;
  step: string | null;
  origin: string | null;
  archived_at: number | null;
  /** Number of source files. */
  sources: number;
  last_job: { id: string; status: JobStatus; counts: RawJobCounts; created_at: number } | null;
  template_name: string | null;
}

export interface UpdateProjectInput {
  /** The `settings_rev` you read; a newer one is 409 stale_project. Required. */
  baseSettingsRev: number;
  name?: string | null;
  archived?: boolean | null;
  output?: ProjectOutput | null;
}

export interface FileError {
  file_id: string;
  file: string;
  code: string;
  message: string;
  [key: string]: unknown;
}

export interface RuleResult {
  rule: RecordRule;
  /** Records the rule finds in the current sources. */
  records: number;
  file_errors: FileError[];
  warnings: unknown[];
}

/** `docweave.project` v1 as GET /projects/{pid}/definition exports it. */
export interface ProjectDefinition {
  schema: 'docweave.project';
  version: 1;
  project: { id: string; name: string };
  rule: Partial<RecordRule>;
  mappings: Mapping[];
  output: ProjectOutput;
  template?: {
    id: string;
    name: string;
    format: string;
    version: number;
    pinned: boolean;
    fields: { id: string; name: string; kind: string; binding: string | null }[];
  };
}

/** A `docweave.project` v1 document to import (an exported one is accepted as is). */
export interface ProjectDefinitionInput {
  schema: 'docweave.project';
  version: 1;
  project?: { id?: string; name?: string };
  template?: {
    id?: string;
    version?: number;
    pinned?: boolean;
    fields?: { id: string; [key: string]: unknown }[];
    [key: string]: unknown;
  } | null;
  rule?: Partial<RecordRule> | null;
  mappings?: Mapping[];
  output?: ProjectOutput;
}

export interface Issue {
  level?: string;
  code?: string;
  message?: string;
  [key: string]: unknown;
}

export interface PreflightSimulation {
  checked: number;
  failed: number;
  /** Samples beyond the simulated ones were not covered. */
  limited: boolean;
  layout: unknown;
  limit: number;
  max: number;
  stopped: string | null;
}

export interface PreflightFormat {
  format: string;
  label: string;
  available: boolean;
  [key: string]: unknown;
}

export interface PreflightSample {
  record: string;
  index: number;
  key: string;
  file_name: string | null;
  where: unknown;
  issues: Issue[];
}

/** POST /projects/{pid}/preflight. */
export interface Preflight {
  input_files: number;
  ready_files: number;
  file_errors: FileError[];
  records: number;
  valid: number;
  invalid: number;
  excluded: { blank: number; filtered: number; empty_key: number; [reason: string]: number };
  /** Number of warnings. */
  warnings: number;
  record_warnings: unknown[];
  issues_by_code: Record<string, number>;
  /** The first 50 invalid samples with up to 3 issues each. */
  invalid_samples: PreflightSample[];
  formats: PreflightFormat[];
  expected_outputs: number;
  expected_if_block: number;
  naming_examples: string[];
  static_issues: Issue[];
  tokens: string[];
  /** Pass to downloadPreview to pin the inputs. */
  signature: string;
  template_version: number;
  template_pinned: boolean;
  issues_total: { error: number; warning: number };
  sources_parsing: number;
  /** Present when simulate was requested. */
  simulation?: PreflightSimulation;
}

/** An item of preflightIssues; mapping-level issues come first with no sample (seq/label/key null). */
export interface PreflightIssue {
  seq: number | null;
  label: string | null;
  key: string | null;
  file_name: string | null;
  where: unknown;
  slot: string | null;
  level: 'error' | 'warning';
  code: string;
  message: string;
}

// ============================================================================ API objects: files and uploads
export interface ScanVerdict {
  status: ScanStatus;
  signature?: string | null;
  engine?: string | null;
  reason?: string | null;
  checked?: boolean | null;
  sha256?: string | null;
  checked_at?: number | null;
  attempts?: number | null;
  retry_at?: number | null;
}

/** A source file (GET /files/{fid}, list items, upload responses). */
export interface SourceFile {
  id: string;
  project_id: string;
  filename: string;
  ord: number;
  size: number;
  format: string | null;
  family: string | null;
  status: SourceFileStatus;
  error: string | null;
  notes: string[];
  structure: Record<string, unknown>;
  version: number;
  representative: boolean;
  created_at: number;
  updated_at: number;
  options: Record<string, unknown>;
  ocr_pages: number[];
  corrections: Record<string, { value: string; confirmed: boolean }>;
  sha256: string;
  scan: ScanVerdict;
}

/** POST /files/{fid}/ocr. */
export interface SourceOcrResult {
  /** Pages recognised by this call. */
  recognized: number[];
  /** Every page with stored OCR. */
  pages: number[];
  words: number;
  low_conf: number;
  /** The source's status afterwards (`needs_ocr` becomes `ready`). */
  status: SourceFileStatus;
  version: number;
}

export interface RunSourceOcrOptions {
  /** Zero-based pages, at most 50. Omitted: every scanned/mixed PDF page, or the whole image. */
  pages?: readonly number[] | null;
  /** Default: max(client timeout, 30 s + 6 s per page (50 pages when omitted)). */
  timeoutMs?: number;
}

/** A resumable upload session (/uploads). */
export interface Upload {
  id: string;
  filename: string;
  size: number;
  sha256: string | null;
  purpose: UploadPurpose | null;
  /** Bytes the server has. */
  received: number;
  status: UploadStatus;
  created_at: number;
  updated_at: number;
  expires_at: number;
  /** status is complete or consumed. */
  complete: boolean;
  upload_url: string;
}

export interface UploadOptions {
  /** Name sent to the server; default the file's basename. */
  filename?: string;
  /** Default: a new UUID per call. Keep it stable to recover a lost response without a second upload. */
  idempotencyKey?: string;
}

export interface ResumableUploadOptions {
  filename?: string;
  /** Hex SHA-256; computed from the file when omitted. */
  sha256?: string;
  purpose?: UploadPurpose;
  idempotencyKey?: string;
  onProgress?: (progress: { received: number; size: number }) => void;
}

export interface WaitForFileOptions {
  /** Default 120 000. */
  timeoutMs?: number;
  /** First poll interval; grows ×1.5 up to maxPollMs. Default 300. */
  pollMs?: number;
  /** Default 3 000. */
  maxPollMs?: number;
  signal?: AbortSignal;
}

// ============================================================================ API objects: templates
/** One field of a template version. The keys besides id/name/kind depend on the kind and format. */
export interface TemplateField {
  id: string;
  name: string;
  kind: string;
  binding?: string;
  required?: boolean;
  data_type?: 'auto' | 'text' | 'number' | 'date';
  format?: string | null;
  default?: unknown;
  [key: string]: unknown;
}

export interface TemplateVersionInfo {
  version_no: number;
  parent_no: number | null;
  note: string | null;
  created_at: number;
}

export interface ExportFormat {
  format: string;
  label: string;
  available: boolean;
  native?: boolean;
  [key: string]: unknown;
}

/** An item of listTemplates. */
export interface TemplateListItem {
  id: string;
  name: string;
  kind: TemplateKind | null;
  format: string;
  original_name: string | null;
  head: number;
  created_at: number;
  updated_at: number;
  info: Record<string, unknown> | null;
  scan: ScanVerdict;
  /** Fields in the head version. */
  slot_count: number;
}

/** GET /templates/{tid}. */
export interface Template {
  id: string;
  name: string;
  format: string;
  kind: TemplateKind | null;
  /** Pass back as `baseHead` to updateField / deleteField. */
  head: number;
  /** The head version's number. */
  version: number;
  fields: TemplateField[];
  /** The last 50 versions. */
  versions: TemplateVersionInfo[];
  exports: ExportFormat[];
  info: Record<string, unknown> | null;
}

/** The full detail uploadTemplate, createBuiltinTemplate and saveBuiltinTemplate answer with. */
export interface TemplateDetail {
  id: string;
  name: string;
  kind: TemplateKind | null;
  format: string;
  original_name: string | null;
  head: number;
  created_at: number;
  updated_at: number;
  info: Record<string, unknown> | null;
  scan: ScanVerdict;
  version: {
    no: number;
    slots: TemplateField[];
    note: string | null;
    editor: Record<string, unknown> | null;
    created_at: number;
  };
  can_undo: boolean;
  can_redo: boolean;
  versions: TemplateVersionInfo[];
  exports: ExportFormat[];
}

/** GET /templates/{tid}/builtin. */
export interface BuiltinTemplate {
  id: string;
  name: string;
  kind: BuiltinTemplateKind;
  head: number;
  version: number;
  /** The product editor model (docs/integration/editor-models.md). */
  editor: Record<string, unknown> | null;
  fields: TemplateField[];
}

/** A field as `docweave.template-fields` v1 exports it; keys with no value are left out. */
export interface ExportedField {
  id: string;
  name: string;
  kind: string;
  binding?: string;
  required?: boolean;
  data_type?: string;
  format?: string;
  default?: unknown;
  locator?: Record<string, unknown>;
  columns?: unknown[];
}

/** GET /templates/{tid}/fields. */
export interface TemplateFieldsDocument {
  schema: 'docweave.template-fields';
  version: 1;
  template: { id: string; name: string; format: string; version: number };
  fields: ExportedField[];
}

/** One field to create; the other keys depend on the kind (api.md §3.3). Unknown keys are refused. */
export interface FieldSpec {
  kind: FieldKind;
  name?: string;
  [key: string]: unknown;
}

/** A `docweave.template-fields` v1 definition for createFields (at most the server's per-call cap). */
export interface TemplateFieldsInput {
  schema: 'docweave.template-fields';
  version: 1;
  template?: Record<string, unknown>;
  fields: FieldSpec[];
}

/** POST /templates/{tid}/fields: all fields as one new version. */
export interface CreatedFields {
  /** The template's new version number. */
  template: number;
  created: { id: string; name: string; kind: string }[];
}

/** The properties PATCH /templates/{tid}/fields/{field_id} accepts; anything else is 400 invalid. */
export interface FieldChanges {
  name?: string;
  required?: boolean;
  data_type?: 'auto' | 'text' | 'number' | 'date';
  format?: string | null;
  default?: unknown;
  font_size?: number;
  min_font_size?: number;
  align?: 'left' | 'center' | 'right';
  valign?: 'top' | 'middle' | 'bottom';
  /** Table repeats: remove_row / blank_row / placeholder / remove_table / error; blocks: remove / placeholder / error; PDF areas: blank / placeholder / error. */
  empty_policy?: string;
  placeholder?: string;
  repeat_header?: boolean;
  keep_header_when_empty?: boolean;
  binding?: string;
  /** XLSX only (replace / insert_after / insert_before); ignored for other formats. */
  mode?: string;
  /** Rename or re-align columns by key. */
  columns?: { key: string; name?: string; align?: 'left' | 'center' | 'right' }[];
  /** PPTX: warn / error / continue; others: error / shrink / continue. */
  overflow?: 'warn' | 'error' | 'continue' | 'shrink';
  /** Clamped to 0.3–1. */
  min_row_scale?: number;
  /** `#RRGGBB`. */
  color?: string;
  separator?: 'none' | 'blank' | 'page_break';
  /** The key of a block column to remove. */
  remove_column?: string;
  shrink?: boolean;
  /** Clamped to 4–72. */
  shrink_min?: number;
}

export type UpdateFieldInput = { /** The template `head` you read; a newer head is 409 stale_template. */ baseHead: number } & FieldChanges;

export interface DeleteFieldOptions {
  /** The template `head` you read. Required. */
  baseHead: number;
  /** Also remove this project's mappings bound only to the field. */
  projectId?: string | null;
  /** With projectId: remove those mappings. Default true. */
  cascade?: boolean;
}

/** PATCH / DELETE of one field: the template after its new version. */
export interface FieldEditResult {
  id: string;
  name: string;
  format: string;
  kind: TemplateKind | null;
  head: number;
  version: number;
  fields: TemplateField[];
}

export interface CreateBuiltinTemplateInput {
  kind: BuiltinTemplateKind;
  name?: string;
  editor: Record<string, unknown>;
}

export interface SaveBuiltinTemplateInput {
  editor: Record<string, unknown>;
  name?: string;
  /** The version the editor model was read at. Positive integer, required. */
  baseVersion: number;
}

// ============================================================================ API objects: jobs
export interface JobCounts {
  samples: number | null;
  /** Successful samples. */
  documents: number | null;
  /** Output files (successful samples × formats). */
  files: number | null;
  failed: number | null;
  skipped: number | null;
  cancelled: number | null;
  pending: number | null;
  running: number | null;
  /** While phase is `planning`: samples with a plan so far, and those without. */
  planned: number | null;
  unplanned: number | null;
}

/** Stored options: historical rows can be sparse or explicitly null. */
export interface JobOptions {
  formats?: string[] | null;
  error_policy?: string | null;
  naming?: string | null;
  keep_fields?: boolean | null;
  /** Missing on jobs created before layout checks existed. */
  layout?: string | null;
  [key: string]: unknown;
}

/** GET /jobs/{jid} (also createJob, retryJob, cancelJob). */
export interface Job {
  id: string;
  project_id: string;
  status: JobStatus;
  run_no: number | null;
  created_at: number | null;
  started_at: number | null;
  finished_at: number | null;
  template: { id: string | null; name: string | null; format: string | null; version_no: number | null } | null;
  options: JobOptions | null;
  error: string | null;
  /** Machine-readable reason for `failed`, e.g. `has_errors`. */
  error_code: string | null;
  cancel_requested: boolean;
  phase: JobPhase;
  /** Why the platform stopped the job, e.g. `authorization_revoked:credential_revoked`. */
  halt_reason: string | null;
  /** Who submitted it, as a secret-free label. */
  principal: string | null;
  /** X-Request-ID of the submitting request. */
  request_id: string | null;
  counts: JobCounts;
  /** Set on a create/retry answered from an earlier request with the same Idempotency-Key. */
  idempotent_replay?: boolean;
}

export interface CancelledJob extends Job {
  cancel: { accepted: boolean; reason: 'already_terminal' | 'already_requested' | null };
}

/** An item of listJobs. Its `counts` are the stored counts, not the Job view's. */
export interface JobListItem {
  id: string;
  status: JobStatus;
  created_at: number | null;
  started_at: number | null;
  finished_at: number | null;
  counts: RawJobCounts | null;
  options: JobOptions | null;
  run_no: number | null;
  phase: JobPhase;
  halt_reason: string | null;
  error_code: string | null;
  principal: string | null;
}

/** An item of runs(): the first run and every retry. */
export interface JobRun {
  run_no: number;
  trigger: string;
  requested_by: string | null;
  /** Items in this run; null while a large batch is still being planned. */
  items: number | null;
  created_at: number | null;
  started_at: number | null;
  finished_at: number | null;
  status: string | null;
  requested_by_principal: string | null;
}

export interface ItemArtifact {
  id: string;
  item_id: string;
  format: string | null;
  filename: string | null;
  sha256: string | null;
  size: number | null;
  verification: JSONValue;
  run_no: number | null;
}

/** An item of items(): one sample's result. */
export interface JobItem {
  id: string;
  seq: number | null;
  record_key: string | null;
  label: string | null;
  status: ItemStatus;
  issues: JSONValue;
  attempts: number | null;
  file_base: string | null;
  started_at: number | null;
  finished_at: number | null;
  run_no: number | null;
  artifacts: ItemArtifact[];
}

/** Data of a job event; which keys are present depends on the type. */
export interface JobEventData {
  status?: JobStatus;
  run_no?: number;
  counts?: RawJobCounts;
  phase?: JobPhase;
  samples?: number;
  skipped?: number | null;
  formats?: string[];
  request_id?: string | null;
  halt_reason?: string;
  [key: string]: unknown;
}

/** An item of jobEvents(): `seq` increases within the job. */
export interface JobEvent {
  seq: number;
  id: string;
  type: string;
  version: number;
  job_id: string;
  occurred_at: number;
  data: JSONValue;
}

export interface JobEventsPage {
  items: JobEvent[];
  /** Pass as `after` to the next call. */
  next_after: number;
}

export interface CreateJobInput {
  /** Array or comma-separated names (e.g. `docx,pdf`); default: the template's native format. */
  formats?: string | readonly string[];
  naming?: string;
  errorPolicy?: ErrorPolicy;
  layout?: Layout;
  keepFields?: boolean;
}

export interface WaitForJobOptions {
  /** Default 600 000. */
  timeoutMs?: number;
  /** First poll interval; grows ×1.5 up to maxPollMs. Default 1 000. */
  pollMs?: number;
  /** Default 10 000. */
  maxPollMs?: number;
  /** Receives the job's events in order, each exactly once (awaited). */
  onEvent?: (event: JobEvent) => unknown;
  signal?: AbortSignal;
}

// ============================================================================ API objects: artifacts and downloads
/** An item of artifacts(): the manifest row of one output file. */
export interface Artifact {
  id: string;
  item_id: string;
  format: string | null;
  filename: string | null;
  size: number | null;
  /** Content SHA-256; downloads are checked against it. */
  sha256: string | null;
  created_at: number | null;
  run_no: number | null;
  /** The sample's sequence number and label. */
  seq: number | null;
  label: string | null;
}

/** What downloadArtifact needs: an id, plus the manifest's filename and sha256 when known. */
export interface ArtifactRef {
  readonly id: string;
  readonly filename?: string | null;
  readonly sha256?: string | null;
}

export interface DownloadResult {
  /** The artifact id; null for archives, reports and previews. */
  id: string | null;
  /** Absolute path of the saved file. */
  path: string;
  sha256: string;
  size: number;
  /** X-Content-SHA256 as sent (lower-case), null when absent. */
  header_sha256: string | null;
  /** A header or manifest checksum was compared. ZIP and CSV downloads have none: false. */
  verified: boolean;
  project_signature: string | null;
  template_version: string | null;
  /** X-Record-Key as sent (URL-encoded). */
  record_key: string | null;
  reused?: undefined;
}

/** A file already at the destination with the expected SHA-256 is kept and reported like this. */
export interface ReusedDownload {
  id: string;
  path: string;
  sha256: string;
  size: number;
  reused: true;
}

export interface DownloadArtifactOptions {
  /** Default '.'; the file is saved as outDir/<safe filename>. */
  outDir?: string;
  /** Overrides outDir. */
  destPath?: string;
  signal?: AbortSignal;
}

export interface PreviewCommonOptions {
  /** The preflight signature the preview must match (409 stale otherwise). */
  signature?: string;
  signal?: AbortSignal;
}
/** One sample by zero-based `index` or by `key`, not both. */
export type PreviewOptions =
  | (PreviewCommonOptions & { index?: number; key?: undefined })
  | (PreviewCommonOptions & { key?: string; index?: undefined });

// ============================================================================ API objects: webhooks and events
export interface Subscription {
  id: string;
  url: string;
  description: string;
  events: string[];
  status: SubscriptionStatus;
  disabled_reason: string | null;
  created_at: number;
  updated_at: number;
  /** Also sent as ETag; pass to updateSubscription as `version`. */
  version: number;
  authorized_by: string | null;
  consecutive_failures: number;
  last_success_at: number | null;
  last_failure_at: number | null;
  previous_secret_expires_at: number | null;
}

/** Only createSubscription and rotateSubscriptionSecret return the signing secret, once. */
export interface SubscriptionWithSecret extends Subscription {
  secret: string;
}

export interface CreateSubscriptionInput {
  url: string;
  /** Default on the server: the four terminal job events. */
  events?: readonly WebhookEventFilter[];
  description?: string;
}

export interface SubscriptionChanges {
  url?: string;
  description?: string | null;
  events?: readonly WebhookEventFilter[];
}

export interface Delivery {
  id: string;
  /** The event id, sent as webhook-id. */
  webhook_id: string;
  event_type: string | null;
  status: DeliveryStatus;
  attempts: number;
  last_code: number | null;
  last_error: string | null;
  duration_ms: number | null;
  created_at: number | null;
  delivered_at: number | null;
  next_attempt_at: number | null;
  redelivery: boolean;
  redelivery_of: string | null;
}

/** Data of a source-file event. */
export interface FileEventData {
  file_id: string;
  project_id: string;
  status: SourceFileStatus;
  /** The scan verdict's status (e.g. clean, not_configured, infected, encrypted). */
  reason: string | null;
  version: number;
}

export interface JobFeedEvent {
  id: string;
  type: JobEventType;
  version: number;
  occurred_at: number;
  job_id: string;
  project_id: string | null;
  data: JobEventData;
}

export interface FileFeedEvent {
  id: string;
  type: FileEventType;
  version: number;
  occurred_at: number;
  job_id: null;
  project_id: string;
  file_id: string;
  data: FileEventData;
}

export type CopyStatus = 'pending' | 'approved' | 'rejected' | 'withdrawn' | 'expired' | 'running' | 'done' | 'failed' | 'stale';

/** Ids and status only: the manifest and the decision's reason come from getCopyRequest. */
export interface CopyEventData {
  copy_id: string;
  source_project_id: string;
  target_workspace_id: string;
  status: CopyStatus;
  version: number;
}

export interface CopyFeedEvent {
  id: string;
  type: CopyEventType;
  version: number;
  occurred_at: number;
  job_id: null;
  project_id: string;
  copy_id: string;
  data: CopyEventData;
}

/** An item of events(): the workspace feed; narrow on `type`. `id` equals the webhook-id of the same event. */
export type FeedEvent = JobFeedEvent | FileFeedEvent | CopyFeedEvent;

/** The job summary a job webhook carries. The deployment endpoint's payload adds tenant and times. */
export interface WebhookJobSummary {
  id: string;
  project_id: string;
  status: JobStatus;
  counts: RawJobCounts;
  run_no: number;
  tenant?: string;
  created_at?: number;
  finished_at?: number | null;
}

export interface JobWebhookEvent {
  id: string;
  type: JobEventType;
  version: number;
  occurred_at: number;
  tenant: string;
  job: WebhookJobSummary;
  data: JobEventData;
}

export interface FileWebhookEvent {
  id: string;
  type: FileEventType;
  version: number;
  occurred_at: number;
  tenant: string;
  job: null;
  data: FileEventData;
}

export interface PingWebhookEvent {
  id: string;
  type: 'webhook.ping';
  version: number;
  occurred_at: number;
  /** null for the deployment endpoint's test ping. */
  tenant: string | null;
  job: null;
  /** subscription_id for a subscription ping; empty for the deployment endpoint's test. */
  data: { subscription_id?: string };
}

export interface CopyWebhookEvent {
  id: string;
  type: CopyEventType;
  version: number;
  occurred_at: number;
  tenant: string;
  job: null;
  data: CopyEventData;
}

/** The JSON body of a delivery; narrow on `type`. */
export type WebhookEvent = JobWebhookEvent | FileWebhookEvent | CopyWebhookEvent | PingWebhookEvent;

export interface CopyTarget {
  id: string;
  name: string;
  role: 'editor' | 'admin' | 'owner';
  organization_id: string;
  organization_name: string | null;
}

export interface CopyPerson { user_id: string; name: string }

/** A copy request as its requester sees it (the source workspace id is never echoed). */
export interface CopyRequest {
  id: string;
  source_project_id: string;
  target_organization_id: string;
  target_workspace_id: string;
  name: string;
  purpose: string | null;
  scope: { sources: boolean; corrections: boolean };
  manifest: Record<string, unknown>;
  digest: string;
  status: CopyStatus;
  requester: CopyPerson | null;
  decider: CopyPerson | null;
  decided_at: number | null;
  decision_reason: string | null;
  self_approved: boolean;
  approval_expires_at: number | null;
  executed_at: number | null;
  /** When done: the new project's id in the target workspace and what was copied. */
  result: ({ project_id: string } & Record<string, unknown>) | null;
  error: string | null;
  version: number;
  created_at: number;
  updated_at: number;
  expires_at: number;
}

export interface RequestCopyInput {
  targetWorkspaceId: string;
  name?: string;
  purpose?: string;
  sources?: boolean;
  corrections?: boolean;
}

/** Raw body bytes exactly as received (never re-serialised JSON). */
export type WebhookBody = Uint8Array | ArrayBuffer | string;
/** `whsec_<base64>` is decoded; any other string is used as UTF-8; bytes as is. */
export type WebhookSecret = string | Uint8Array;
/** Fetch Headers, a Map, or a plain object such as Node's IncomingHttpHeaders (any case). */
export type WebhookHeaders =
  | Headers
  | Map<string, string | readonly string[]>
  | Readonly<Record<string, string | readonly string[] | undefined>>;

export interface VerifiedWebhook<E = WebhookEvent> {
  id: string;
  timestamp: number;
  /** The parsed body; null when the (correctly signed) body is not JSON. */
  event: E | null;
}

export interface LegacyVerifiedWebhook<E = WebhookEvent> {
  /** X-DocWeave-Event-Id, when sent. */
  id: string | null;
  timestamp: number;
  event: E;
}

export interface VerifyWebhookOptions {
  /** Default WEBHOOK_TOLERANCE_S. */
  toleranceS?: number;
  /** The current time in Unix seconds (for tests). */
  nowS?: number;
}

export interface ReceivedWebhook extends VerifiedWebhook<WebhookEvent> {
  /** The request headers, lower-cased. */
  headers: Record<string, string>;
}

export interface WebhookReceiverOptions {
  /** One secret, or several while rotating. */
  secret: WebhookSecret | readonly WebhookSecret[];
  /** Acknowledged (204) only after this resolves; a throw answers 503 so the sender retries. */
  onEvent?: (delivery: ReceivedWebhook) => unknown;
  onRejected?: (rejection: { code: WebhookVerificationErrorCode; message: string; headers: IncomingHttpHeaders }) => void;
  toleranceS?: number;
  /** Larger bodies are answered 413. Default 1 MiB. */
  maxBodyBytes?: number;
}

/** Standard Webhooks key material as bytes. */
export declare function webhookKeyBytes(secret: WebhookSecret): Buffer;
/** `v1,<base64>` for one secret — for tests and hand-signed probes of a receiver. */
export declare function signWebhook(id: string, timestamp: string | number, body: WebhookBody, secret: WebhookSecret): `v1,${string}`;
/**
 * Verify a Standard Webhooks delivery over the *raw* body bytes. `secret` may be a list (old
 * and new while rotating).
 * @throws WebhookVerificationError (missing_headers, invalid_timestamp, timestamp_out_of_tolerance,
 *   invalid_signature, no_secret)
 * @throws TypeError when `body` is not raw bytes or a string.
 */
export declare function verifyWebhook<E = WebhookEvent>(
  headers: WebhookHeaders,
  body: WebhookBody,
  secret: WebhookSecret | readonly WebhookSecret[],
  options?: VerifyWebhookOptions,
): VerifiedWebhook<E>;
/**
 * The deployment-endpoint signature: X-DocWeave-Signature `v1=<hex>` over "<ts>.<body>".
 * @throws WebhookVerificationError; SyntaxError when the signed body is not JSON.
 */
export declare function verifyLegacyWebhook<E = WebhookEvent>(
  headers: WebhookHeaders,
  body: WebhookBody,
  secret: string,
  options?: VerifyWebhookOptions,
): LegacyVerifiedWebhook<E>;
/**
 * Minimal process-local receiver (not yet listening): verifies, deduplicates by webhook-id in
 * memory and acknowledges only after `onEvent` succeeds. Call `listen()` on the result.
 */
export declare function createWebhookReceiver(options: WebhookReceiverOptions): Server;

// ============================================================================ generate
export interface GenerateOptions {
  /** Source file paths, in order. */
  sources: readonly string[];
  /** Template file path. */
  template: string;
  /** A `docweave.template-fields` document, or the path of its JSON file. */
  fields: TemplateFieldsInput | string;
  /** A `docweave.project` document, or the path of its JSON file. */
  definition: ProjectDefinitionInput | string;
  /** Default ['docx']. */
  formats?: string | readonly string[];
  naming?: string;
  errorPolicy?: ErrorPolicy;
  layout?: Layout;
  /** Where the preview and the artifacts are saved. Default '.'. */
  outDir?: string;
  projectName?: string;
  templateName?: string;
  /** Derive every Idempotency-Key from this (`<prefix>:project`, …) so a rerun reuses what exists. */
  keyPrefix?: string;
  /** Stop before writing anything when the credential belongs to another workspace. */
  expectedTenant?: string;
  /** For waitForJob. Default 600 000. */
  timeoutMs?: number;
  onProgress?: (progress: GenerateProgress) => void;
  onEvent?: (event: JobEvent) => unknown;
  signal?: AbortSignal;
  onJobCreated?: (job: WithMeta<Job>) => void;
}

export type GenerateProgress =
  | { step: 'whoami'; tenant: string; scopes: string[] }
  | { step: 'project'; id: string; replayed: boolean | undefined }
  | { step: 'source'; id: string; filename: string; status: SettledSourceFileStatus }
  | { step: 'template'; id: string; name: string }
  | { step: 'fields'; count: number; version: number | undefined }
  | { step: 'definition' }
  | { step: 'preflight'; records: number; valid: number; invalid: number; expected_outputs: number }
  | { step: 'preview'; simulation: PreflightSimulation | undefined }
  | { step: 'job'; id: string; status: JobStatus; replayed: boolean | undefined }
  | { step: 'job_done'; id: string; status: TerminalJobStatus; counts: JobCounts }
  | { step: 'artifact'; filename: string; sha256: string };

export interface GeneratedArtifact {
  id: string;
  seq: number;
  label: string | null;
  format: string;
  filename: string;
  size: number;
  /** SHA-256 of the saved bytes. */
  sha256: string;
  manifest_sha256: string;
  header_sha256: string | null;
  path: string;
  reused: boolean;
}

export interface GenerateResult {
  tenant: string;
  project_id: string;
  template_id: string;
  files: { id: string; filename: string; format: string | null; status: SettledSourceFileStatus }[];
  /** The simulated preflight, with the downloaded native preview. */
  preview: WithMeta<Preflight> & { download: DownloadResult };
  preflight: { records: number; valid: number; invalid: number; expected_outputs: number };
  job: { id: string; status: SuccessJobStatus; counts: JobCounts; run_no: number };
  artifacts: GeneratedArtifact[];
}

// ============================================================================ client
export interface IdempotencyOptions {
  /** Default: a new UUID per call. Supply a stable key to recover across process restarts. */
  idempotencyKey?: string;
}

export type DirectoryManagedRole = 'viewer' | 'editor';
export type DirectoryMemberRole = DirectoryManagedRole | 'admin' | 'owner';
export type DirectoryInvitationState = 'pending' | 'accepted' | 'declined' | 'revoked' | 'expired';
export interface DirectoryWhoami {
  workspace: { id: string; name: string | null; kind: string | null };
  client_id: string;
  name: string | null;
  scopes: string[];
}
export interface DirectoryMember {
  user_id: string;
  email: string;
  display_name: string | null;
  role: DirectoryMemberRole;
  account_status: string;
  joined_at: number;
}
export interface DirectoryPage<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
  next_offset: number | null;
}
export interface DirectoryMemberPage extends DirectoryPage<DirectoryMember> { owner_count: number }
export interface DirectoryInvitation {
  id: string;
  email: string;
  role: DirectoryMemberRole;
  state: DirectoryInvitationState;
  created_at: number;
  expires_at: number;
  invited_via: string | null;
}
export interface DirectoryInvitationCreated extends DirectoryInvitation { can_export: boolean; mail: 'queued' }
export interface DirectoryMemberChanged {
  user_id: string;
  role: DirectoryManagedRole;
  can_export: boolean;
  previous_role: DirectoryManagedRole;
  journaled: boolean | null;
}
export interface DirectoryMemberRemoved { user_id: string; removed: boolean; journaled: boolean }
export interface DirectoryInvitationRevoked { id: string; state: 'revoked'; journaled: boolean }
export interface DirectoryInvitationInput {
  email: string;
  role: DirectoryManagedRole;
  canExport?: boolean;
  expiresInDays?: number;
}
export interface DirectoryMemberQuery { role?: DirectoryMemberRole; q?: string; limit?: number; offset?: number }
export interface DirectoryInvitationQuery { state?: DirectoryInvitationState; limit?: number; offset?: number }
export type WorkspaceClientOptions = Omit<ClientOptions, 'multipartMaxBytes' | 'chunkBytes'>;

/** Independent directory client. No Docs operations, Docs default scope or API key environment fallback. */
export declare class WorkspaceClient {
  /** Passing undefined for baseUrl selects https://accounts.synwevo.com. */
  constructor(baseUrl: string | URL | undefined, credentials: Credentials, options?: WorkspaceClientOptions);
  /** Only SYNWEVO_WORKSPACE_* variables; workspace.read by default, fixed workspace resource. */
  static fromEnv(env?: Record<string, string | undefined>, options?: WorkspaceClientOptions): Promise<WorkspaceClient>;
  readonly baseUrl: string;
  whoami(): Promise<WithMeta<DirectoryWhoami>>;
  listMembers(query?: DirectoryMemberQuery): Promise<WithMeta<DirectoryMemberPage>>;
  iterMembers(query?: Omit<DirectoryMemberQuery, 'offset'>): AsyncGenerator<DirectoryMember, void, unknown>;
  listInvitations(query?: DirectoryInvitationQuery): Promise<WithMeta<DirectoryPage<DirectoryInvitation>>>;
  iterInvitations(query?: Omit<DirectoryInvitationQuery, 'offset'>): AsyncGenerator<DirectoryInvitation, void, unknown>;
  createInvitation(input: DirectoryInvitationInput, options?: IdempotencyOptions): Promise<WithMeta<DirectoryInvitationCreated>>;
  resendInvitation(id: string, options?: IdempotencyOptions): Promise<WithMeta<DirectoryInvitationCreated>>;
  revokeInvitation(id: string): Promise<WithMeta<DirectoryInvitationRevoked>>;
  /** expectedRole must be the role most recently read; stale state is 409 role_changed. */
  changeMember(id: string, input: { role: DirectoryManagedRole; expectedRole: DirectoryManagedRole }): Promise<WithMeta<DirectoryMemberChanged>>;
  removeMember(id: string, input: { expectedRole: DirectoryManagedRole; reason: string }): Promise<WithMeta<DirectoryMemberRemoved>>;
}

export declare class Client {
  /**
   * @param baseUrl e.g. http://127.0.0.1:8765 (a trailing /api/v1 is removed)
   * @throws ValidationError for a missing baseUrl, unusable credentials or invalid numeric options.
   */
  constructor(baseUrl: string | URL, credentials: Credentials, options?: ClientOptions);

  /** Without trailing slash or /api/v1. */
  baseUrl: string;
  credentials: Credentials;
  timeoutMs: number;
  maxRetries: number;
  backoffBaseMs: number;
  backoffCapMs: number;
  maxRetryAfterMs: number;
  userAgent: string;
  multipartMaxBytes: number;
  chunkBytes: number;
  onRetry: ((info: RetryInfo) => void) | null;
  fetch: FetchLike;
  sleep: (ms: number, signal?: AbortSignal) => Promise<unknown>;
  random: () => number;

  /** The absolute URL of an /api/v1 path. */
  url(path: string, query?: QueryParams): string;
  /** Delay before retry `attempt` (0-based): full jitter, never less than Retry-After; null when that exceeds maxRetryAfterMs. */
  backoffMs(attempt: number, retryAfterS?: number | null): number | null;
  /**
   * Send one logical request, retrying where that is safe. With `consume`, resolves with what it
   * returns (read inside the same timeout/retry budget); otherwise with the Response, whose
   * `release()` must be called once its body is read.
   */
  send<T>(method: string, path: string, opts: SendOptions & { consume: (res: SentResponse) => T | Promise<T> }): Promise<T>;
  send(method: string, path: string, opts?: SendOptions): Promise<SentResponse>;
  /** Parsed JSON (null for an empty body); objects carry a non-enumerable `$meta`. */
  requestJson<T = unknown>(method: string, path: string, opts?: SendOptions): Promise<T>;
  get<T = unknown>(path: string, query?: QueryParams, opts?: RequestOptions): Promise<T>;
  /** Every item of a list route, following next_cursor (preferred) or next_offset. */
  paginate<T = unknown>(path: string, query?: QueryParams, opts?: RequestOptions): AsyncGenerator<T, void, undefined>;
  collect<T>(iterable: AsyncIterable<T> | Iterable<T>): Promise<T[]>;

  // ---------------------------------------------------------------- system
  whoami(): Promise<WithMeta<WhoAmI>>;
  capabilities(): Promise<WithMeta<Capabilities>>;
  usage(): Promise<WithMeta<Usage>>;

  // ---------------------------------------------------------------- projects
  createProject(body?: { name?: string }, options?: IdempotencyOptions): Promise<WithMeta<CreatedProject>>;
  listProjects(query?: ListQuery): AsyncGenerator<ProjectListItem, void, undefined>;
  getProject(pid: string): Promise<WithMeta<Project>>;
  /**
   * Rename, archive/unarchive or change output defaults; `baseSettingsRev` is the `settings_rev`
   * you read (someone else's edit since is 409 stale_project). Not retried automatically.
   * @throws ValidationError (synchronously) when baseSettingsRev is not a non-negative integer.
   */
  updateProject(pid: string, input: UpdateProjectInput): Promise<WithMeta<Project>>;
  /** Delegated tokens only (403 delegation_required otherwise). */
  copyTargets(): Promise<WithMeta<{ items: CopyTarget[]; source_is_personal: boolean }>>;
  copyManifest(pid: string, options?: { sources?: boolean; corrections?: boolean }): Promise<WithMeta<{ manifest: Record<string, unknown>; digest: string }>>;
  /** @throws ValidationError synchronously without targetWorkspaceId. */
  requestCopy(pid: string, input: RequestCopyInput, options?: IdempotencyOptions): Promise<WithMeta<CopyRequest>>;
  /** Newest requests first; limit is 1–100 (default 50). This endpoint has no cursor. */
  listCopyRequests(pid: string, options?: { status?: CopyStatus; limit?: number }): Promise<WithMeta<{ items: CopyRequest[] }>>;
  getCopyRequest(cid: string): Promise<WithMeta<CopyRequest>>;
  withdrawCopyRequest(cid: string): Promise<WithMeta<CopyRequest>>;
  deleteProject(pid: string): Promise<WithMeta<{ deleted: string }>>;
  putRule(pid: string, rule: RecordRule): Promise<WithMeta<RuleResult>>;
  /** Bind a template; `version` pins that version, null or omitted follows the head. */
  bindTemplate(pid: string, templateId: string, options?: { version?: number | null }): Promise<WithMeta<Project>>;
  getMappings(pid: string): Promise<WithMeta<Mappings>>;
  /** With `baseRev`, a newer revision is 409 stale (and the call is not retried). */
  putMappings(pid: string, mappings: readonly Mapping[], options?: { baseRev?: number | null; note?: string | null }): Promise<WithMeta<Mappings>>;
  getDefinition(pid: string): Promise<WithMeta<ProjectDefinition>>;
  putDefinition(pid: string, definition: ProjectDefinitionInput, options?: { baseRev?: number | null }): Promise<WithMeta<ProjectDefinition>>;
  /** Read-only on the server (simulate needs write), so it is retried after a timeout. */
  preflight(pid: string, options?: { formats?: string | readonly string[]; naming?: string; simulate?: boolean | number }): Promise<WithMeta<Preflight>>;
  preflightIssues(pid: string, options?: { level?: 'error' | 'warning'; formats?: string | readonly string[]; naming?: string }): AsyncGenerator<PreflightIssue, void, undefined>;

  // ---------------------------------------------------------------- files and templates
  listFiles(pid: string, query?: ListQuery): AsyncGenerator<SourceFile, void, undefined>;
  getFile(fid: string, options?: RequestOptions): Promise<WithMeta<SourceFile>>;
  deleteFile(fid: string): Promise<WithMeta<{ deleted: string }>>;
  /**
   * Recognise text on a scanned PDF or image (at most 50 pages). Synchronous on the server and
   * not retried automatically; after a timeout, the page-words route reports `source: "ocr"` for
   * pages already recognised.
   * @throws ValidationError (synchronously) unless pages is an array of at most 50 non-negative integers.
   */
  runSourceOcr(fid: string, options?: RunSourceOcrOptions): Promise<WithMeta<SourceOcrResult>>;
  /** Multipart up to multipartMaxBytes, resumable above; the SHA-256 is sent and checked. */
  uploadSource(pid: string, filePath: string, options?: UploadOptions): Promise<WithMeta<SourceFile>>;
  uploadTemplate(filePath: string, options?: UploadOptions & { name?: string }): Promise<WithMeta<TemplateDetail>>;
  /** Upload in Content-Range chunks read from disk, resuming from the server's `received`. */
  resumableUpload(filePath: string, options?: ResumableUploadOptions): Promise<WithMeta<Upload>>;
  /**
   * Poll until the file is accepted and parsed. Rejects with APIError `file_parse_failed`
   * (status error) or `file_rejected` (malware check), or WaitTimeoutError (the last record in
   * `details` when it was still scanning / scan_failed / parsing).
   */
  waitForFile(fid: string, options?: WaitForFileOptions): Promise<WithMeta<SourceFile & { status: SettledSourceFileStatus }>>;
  listTemplates(query?: ListQuery): AsyncGenerator<TemplateListItem, void, undefined>;
  getTemplate(tid: string): Promise<WithMeta<Template>>;
  /** @throws ValidationError (synchronously) for another kind. */
  createBuiltinTemplate(input: CreateBuiltinTemplateInput, options?: IdempotencyOptions): Promise<WithMeta<TemplateDetail>>;
  /** @throws ValidationError (synchronously) for a version that is not a positive integer. */
  getBuiltinTemplate(tid: string, options?: { version?: number }): Promise<WithMeta<BuiltinTemplate>>;
  /** @throws ValidationError (synchronously) without a positive integer baseVersion. */
  saveBuiltinTemplate(tid: string, input: SaveBuiltinTemplateInput, options?: IdempotencyOptions): Promise<WithMeta<TemplateDetail>>;
  getFields(tid: string): Promise<WithMeta<TemplateFieldsDocument>>;
  /**
   * Change one field's properties as one new version; `baseHead` is the head you read (a moved
   * head, or a repeat of a call that succeeded, is 409 stale_template). Not retried automatically.
   * @throws ValidationError (synchronously) when baseHead is not a positive integer.
   */
  updateField(tid: string, fieldId: string, input: UpdateFieldInput): Promise<WithMeta<FieldEditResult>>;
  /**
   * Remove one field as one new version; with `projectId` also that project's mappings bound only
   * to it. Not retried automatically. Built-in editor templates answer 409 builtin_fields.
   * @throws ValidationError (synchronously) when baseHead is not a positive integer.
   */
  deleteField(tid: string, fieldId: string, options: DeleteFieldOptions): Promise<WithMeta<FieldEditResult & { removed_mappings: string[] }>>;
  createFields(tid: string, definition: TemplateFieldsInput, options?: IdempotencyOptions): Promise<WithMeta<CreatedFields>>;
  deleteTemplate(tid: string): Promise<WithMeta<{ deleted: string }>>;

  // ---------------------------------------------------------------- jobs
  /** Submit a job (202). @throws ValidationError (synchronously) for invalid formats, errorPolicy or layout. */
  createJob(pid: string, input?: CreateJobInput, options?: IdempotencyOptions): Promise<WithMeta<Job>>;
  getJob(jid: string, options?: RequestOptions): Promise<WithMeta<Job>>;
  runs(jid: string, query?: ListQuery): AsyncGenerator<JobRun, void, undefined>;
  listJobs(pid: string, query?: ListQuery): AsyncGenerator<JobListItem, void, undefined>;
  /** @throws ValidationError (synchronously) for an unknown status. */
  items(jid: string, options?: { status?: ItemStatus }): AsyncGenerator<JobItem, void, undefined>;
  /** Cancelling a cancelled or finished job reports it unchanged, so retrying is safe. */
  cancelJob(jid: string): Promise<WithMeta<CancelledJob>>;
  /** Default: failed and cancelled items. `failedOnly` and `includeSkipped` exclude each other. */
  retryJob(jid: string, input?: { includeSkipped?: boolean; failedOnly?: boolean }, options?: IdempotencyOptions): Promise<WithMeta<Job>>;
  jobEvents(jid: string, query?: { after?: number; limit?: number }, options?: RequestOptions): Promise<WithMeta<JobEventsPage>>;
  /**
   * Poll until the job is terminal. A Retry-After header (or a `poll_after_s` field, which the
   * server does not send today) lengthens the interval.
   * Resolves with the terminal job whatever its status; rejects with WaitTimeoutError.
   */
  waitForJob(jid: string, options?: WaitForJobOptions): Promise<WithMeta<Job & { status: TerminalJobStatus }>>;

  // ---------------------------------------------------------------- artifacts
  artifacts(jid: string, query?: ListQuery): AsyncGenerator<Artifact, void, undefined>;
  /**
   * Stream one artifact to `outDir/<filename>` (or `destPath`), checking X-Content-SHA256 and the
   * manifest's sha256; on a mismatch nothing is left at the destination (IntegrityError).
   */
  downloadArtifact(artifact: string | ArtifactRef, options?: DownloadArtifactOptions): Promise<DownloadResult | ReusedDownload>;
  /** The job's ZIP (all artifacts plus the generation manifest); `verified` is false (no checksum header). */
  downloadArchive(jid: string, destPath: string, options?: { format?: string; signal?: AbortSignal }): Promise<DownloadResult>;
  /** The job's CSV report; `verified` is false (no checksum header). */
  downloadReport(jid: string, destPath: string, options?: { signal?: AbortSignal }): Promise<DownloadResult>;
  /**
   * Render one native sample without creating a job; the X-Content-SHA256 header is required.
   * @throws ValidationError (synchronously) for a missing pid/destPath or invalid index, key or signature.
   */
  downloadPreview(pid: string, destPath: string, options?: PreviewOptions): Promise<DownloadResult>;

  // ---------------------------------------------------------------- webhooks and events (admin scope)
  listSubscriptions(): Promise<WithMeta<{ items: Subscription[]; limit: number }>>;
  /** The response carries `secret` exactly once. Not retried: a lost response would hide the secret. */
  createSubscription(input: CreateSubscriptionInput): Promise<WithMeta<SubscriptionWithSecret>>;
  getSubscription(sid: string): Promise<WithMeta<Subscription>>;
  /** `version` is sent as If-Match; a newer version is 409 version_conflict. */
  updateSubscription(sid: string, changes: SubscriptionChanges, options?: { version?: number | string }): Promise<WithMeta<Subscription>>;
  deleteSubscription(sid: string): Promise<WithMeta<{ deleted: string }>>;
  pauseSubscription(sid: string): Promise<WithMeta<Subscription>>;
  resumeSubscription(sid: string): Promise<WithMeta<Subscription>>;
  /** New secret, returned once; the old one keeps signing for `overlapS` seconds (0–86 400, default 86 400). */
  rotateSubscriptionSecret(sid: string, options?: { overlapS?: number }): Promise<WithMeta<SubscriptionWithSecret>>;
  /** One signed webhook.ping, one attempt; resolves with its delivery record. */
  pingSubscription(sid: string): Promise<WithMeta<Delivery>>;
  deliveries(sid: string, options?: { status?: DeliveryStatus }): AsyncGenerator<Delivery, void, undefined>;
  redeliver(sid: string, did: string): Promise<WithMeta<Delivery>>;
  /** The workspace event feed from `cursor` to the current end (read scope). */
  events(options?: { cursor?: string; types?: string | readonly EventType[] }): AsyncGenerator<FeedEvent, void, undefined>;

  // ---------------------------------------------------------------- the main chain
  /**
   * Source files + template + field definition + project definition → verified documents:
   * whoami, project, uploads, fields, binding, definition, preflight, simulated preview, one
   * native preview download, job, wait, and every artifact downloaded and checked.
   * @throws PreflightError, JobFailedError, PermissionDeniedError (unexpected_tenant / insufficient_scope), …
   */
  generate(options: GenerateOptions): Promise<GenerateResult>;
}

// ============================================================================ command line
/**
 * The CLI (`node synwevo-docs.mjs …`). Resolves with 0, 2 (usage) or 5 (a waited job failed);
 * other failures reject (the script's own entry point maps them to exit codes 2–6 and 130).
 */
export declare function main(argv?: readonly string[]): Promise<number>;
