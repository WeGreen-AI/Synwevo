import { api } from '@synwevo/auth-client';
import { newKey, op, read } from '../../shared/ops';
import { MAX_CONTEXTS, safeContextPath } from './context';
import type { AgentFile, FileCapabilities, FilePage, FileReference, SelectedFile } from './types';
import type { FileText } from './files.messages';

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const timestamp = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim();
export function fileError(code: string): Error & { code: string } { return Object.assign(new Error(code), { code }); }
function invalid(): never { throw fileError('invalid_file_response'); }
export function fileCapabilities(value: unknown): FileCapabilities {
  if (!object(value) || typeof value.configured !== 'boolean' || value.ocr !== false
    || !Array.isArray(value.formats) || !value.formats.every((f) => typeof f === 'string' && /^[a-z0-9]+$/i.test(f))
    || !['max_bytes', 'max_files', 'max_storage_bytes', 'max_context_chars', 'unattached_retention_seconds'].every((k) => integer(value[k]) && Number(value[k]) > 0)
    || !(value.scanner === null || object(value.scanner))) return invalid();
  return value as unknown as FileCapabilities;
}
export function fileRecord(value: unknown, expected?: string): AgentFile {
  if (!object(value) || !text(value.id) || (expected !== undefined && value.id !== expected) || typeof value.filename !== 'string'
    || !['receiving', 'queued', 'parsing', 'ready', 'failed', 'cancelled', 'deleted'].includes(String(value.status))
    || !['size', 'attempts'].every((k) => integer(value[k]))
    || !['created_at', 'updated_at'].every((k) => timestamp(value[k]))
    || !(value.error_code === null || typeof value.error_code === 'string') || typeof value.scan_status !== 'string'
    || !(value.download_path === null || typeof value.download_path === 'string')) return invalid();
  if (value.status === 'ready') {
    if (!object(value.reference) || value.reference.file_id !== value.id || typeof value.reference.snapshot !== 'string'
      || !/^[a-f0-9]{64}$/i.test(value.reference.snapshot)) return invalid();
  } else if (value.reference !== null) return invalid();
  if ('extraction' in value && value.extraction !== null) {
    const extraction = value.extraction;
    if (value.status !== 'ready' || !object(extraction) || !Array.isArray(extraction.lines) || !Array.isArray(extraction.tables)
      || !extraction.lines.every((line) => object(line) && typeof line.text === 'string' && object(line.source))
      || !extraction.tables.every((table) => object(table) && typeof table.name === 'string' && object(table.source)
        && Array.isArray(table.rows) && table.rows.every((row) => Array.isArray(row) && row.every((cell) => typeof cell === 'string'))
        && (table.row_numbers === undefined || (Array.isArray(table.row_numbers) && table.row_numbers.length === table.rows.length && table.row_numbers.every(integer))))) return invalid();
  }
  return { ...value, download_path: safeFileDownload(value.download_path, value.id) } as unknown as AgentFile;
}
export function safeFileDownload(path: unknown, id?: string): string | null {
  const safe = safeContextPath(path);
  if (!safe || !/^\/api\/suite\/agent\/files\/[A-Za-z0-9_-]+\/download$/.test(safe)) return null;
  return id === undefined || safe === `/api/suite/agent/files/${id}/download` ? safe : null;
}
export function filePage(value: unknown, offset: number): FilePage {
  if (!object(value) || !Array.isArray(value.files) || value.files.length > 20
    || !(value.next_offset === null || (integer(value.next_offset) && value.next_offset > offset))
    || (value.next_offset !== null && !value.files.length)) return invalid();
  return { files: value.files.map((file) => fileRecord(file)), next_offset: value.next_offset as number | null };
}
export const processingFile = (file: AgentFile) => ['receiving', 'queued', 'parsing'].includes(file.status);
export const canRetryFile = (file: AgentFile) => ['failed', 'cancelled'].includes(file.status) && file.attempts < 3 && file.error_code !== 'agent_file_expired';
export function selectFile(file: AgentFile): SelectedFile {
  if (file.error_code === 'agent_file_expired') throw fileError('agent_file_expired');
  if (file.status !== 'ready' || !file.reference) throw fileError('agent_file_not_ready');
  return { title: file.filename, reference: { file_id: file.id, snapshot: file.reference.snapshot } };
}
export function addFile(selected: SelectedFile[], file: SelectedFile, businessCount: number): SelectedFile[] {
  if (selected.length + businessCount >= MAX_CONTEXTS || selected.some((s) => s.reference.file_id === file.reference.file_id)) return selected;
  return [...selected, file];
}
export function fileReferences(selected: SelectedFile[], businessCount: number): FileReference[] | undefined {
  if (selected.length + businessCount > MAX_CONTEXTS) throw fileError('invalid_context');
  if (selected.some((file) => !text(file.reference.file_id) || !/^[a-f0-9]{64}$/i.test(file.reference.snapshot))) throw fileError('agent_file_not_ready');
  return selected.length ? selected.map(({ reference: { file_id, snapshot } }) => ({ file_id, snapshot })) : undefined;
}
export function checkUpload(file: File, caps: FileCapabilities): void {
  if (!caps.configured) throw fileError('agent_file_not_configured');
  if (!file.size || file.size > caps.max_bytes) throw fileError('agent_file_limit');
  if (!caps.formats.some((f) => f.toLowerCase() === file.name.split('.').pop()?.toLowerCase())) throw fileError('context_format_unsupported');
}
/** Locations are supplied by strict ingest: text line, PDF page/line, CSV/TSV sheet.
 * Unknown/invalid metadata is never rendered as schema or inferred from array positions. */
export function fileSourceLabel(source: unknown, f: (key: FileText, params?: Record<string, string | number>) => string): string {
  if (!object(source)) return f('sourceUnavailable');
  const positive = (value: unknown): value is number => integer(value) && value > 0;
  if ('page' in source) {
    if (!positive(source.page) || ('line' in source && !positive(source.line))) return f('sourceUnavailable');
    return positive(source.line) ? f('sourcePageLine', { page: source.page, line: source.line }) : f('sourcePage', { page: source.page });
  }
  if (positive(source.line)) return f('sourceLine', { line: source.line });
  if (text(source.sheet)) return f('sourceTable', { name: source.sheet });
  return f('sourceUnavailable');
}
export function fileRowLabel(row: unknown, f: (key: FileText, params?: Record<string, string | number>) => string): string {
  return integer(row) && row > 0 ? f('sourceRow', { row }) : f('sourceUnavailable');
}
export function scannerLabel(scanner: FileCapabilities['scanner'], scan?: string): FileText {
  if (!scanner || scanner.status === 'off' || scanner.mode === 'off' || scanner.configured === false) return 'scanOff';
  if (scanner.status === 'down') return 'scanDown';
  if (scan === 'clean') return scanner.status === 'ok' ? 'scanClean' : 'scanPending';
  if (scan === 'not_configured') return 'scanOff';
  if (scan === 'infected') return 'scanInfected';
  if (scan === 'encrypted') return 'scanEncrypted';
  if (scan === 'error' || scan === 'too_large') return 'scanFailed';
  return scan === undefined && scanner.status === 'ok' ? 'scanAvailable' : 'scanPending';
}
export function fileErrorKey(error: unknown): FileText | null {
  if (!object(error) || error.upstream) return null;
  const code = error.code === 'idempotent_failure' && object(error.detail) ? error.detail.code : error.code;
  return ({ agent_file_expired: 'expired', agent_file_not_found: 'notFound', agent_file_not_ready: 'notReady', agent_file_changed: 'changed', agent_file_limit: 'limit',
    agent_file_quota: 'quota', agent_file_busy: 'busy', agent_file_not_configured: 'notConfigured', agent_file_retry_unavailable: 'retryUnavailable',
    context_limit_exceeded: 'contextLimit', context_parse_error: 'contextParse', context_needs_ocr: 'needsOcr',
    context_ocr_unsupported: 'ocrUnsupported', context_encrypted: 'scanEncrypted', context_format_unsupported: 'unsupported', context_incomplete: 'incomplete', parse_timeout: 'parseTimeout', parse_failed: 'parseFailed',
    parse_interrupted: 'parseInterrupted', upload_interrupted: 'uploadInterrupted', invalid_file_response: 'invalidResponse',
    malware_detected: 'scanInfected', file_quarantined: 'scanInfected', file_blocked: 'scanInfected', malware_scan_failed: 'scanFailed',
    scan_unavailable: 'scanDown', scan_limit: 'scanFailed', unchecked_file: 'scanFailed', file_too_large: 'limit',
    scanner_unavailable: 'scanDown', malware_scanner_unavailable: 'scanDown', scan_failed: 'scanFailed', file_unavailable: 'notFound',
    encrypted_file: 'scanEncrypted', invalid_filename: 'invalidName', idempotency_conflict: 'uploadConflict',
  } as Record<string, FileText>)[String(code)] ?? null;
}

export type UploadAttempt = { file: File; key: string; state: 'prepared' | 'uploading' | 'unknown' | 'received'; result?: AgentFile };
/** One owner in AgentApp survives dialog/route changes. No data or keys are persisted to disk.
 * Hash bytes as well as name: same-size replacements must never reuse an uncertain upload key. */
export function createFileClient() {
  const uploads = new Map<string, UploadAttempt>();
  const mutations = new Map<string, string>();
  const inFlight = new WeakMap<UploadAttempt, AbortSignal>();
  let latest: UploadAttempt | undefined;
  let generation = 0;
  return {
    pending: () => latest,
    reset: () => { generation++; uploads.clear(); mutations.clear(); latest = undefined; },
    async prepare(file: File, caps: FileCapabilities): Promise<UploadAttempt> {
      const current = generation;
      checkUpload(file, caps);
      const hash = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
      const digest = Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, '0')).join('');
      if (current !== generation) throw fileError('upload_interrupted');
      const signature = JSON.stringify([file.name, digest]);
      const prior = uploads.get(signature);
      // After a known response, explicitly choosing the file again starts a fresh upload intent.
      // This permits re-uploading expired files or files with no remaining parse attempts.
      latest = prior && prior.state !== 'received' ? prior : { file, key: newKey(), state: 'prepared' };
      uploads.set(signature, latest);
      return latest;
    },
    async upload(attempt: UploadAttempt, signal: AbortSignal): Promise<AgentFile> {
      if (attempt.state === 'received' && attempt.result) return attempt.result;
      if (attempt.state === 'uploading') throw fileError('agent_file_busy');
      const form = new FormData(); form.append('file', attempt.file);
      attempt.state = 'uploading'; inFlight.set(attempt, signal);
      try {
        const result = fileRecord(await api('/api/suite/agent/files', { method: 'POST', body: form, signal,
          workspaceId: null, headers: { 'Idempotency-Key': attempt.key } }));
        if (signal.aborted) throw fileError('upload_interrupted');
        attempt.result = result; attempt.state = 'received';
        for (const [signature, value] of uploads) if (value === attempt) uploads.delete(signature);
        return result;
      } catch (error) { if (inFlight.get(attempt) === signal) attempt.state = 'unknown'; throw error; }
    },
    capabilities: async (signal: AbortSignal) => fileCapabilities(await api('/api/suite/agent/files/capabilities', { signal, workspaceId: null })),
    list: async (offset: number, signal: AbortSignal) => filePage(await read('agent.file.list', { offset }, null, signal), offset),
    get: async (id: string, signal: AbortSignal) => fileRecord(await read('agent.file.get', { file_id: id }, null, signal), id),
    async mutate(action: 'retry' | 'cancel' | 'delete', id: string, signal: AbortSignal): Promise<AgentFile | { id: string; status: 'deleted' }> {
      const signature = JSON.stringify([action, id]);
      const key = mutations.get(signature) ?? newKey(); mutations.set(signature, key);
      const result = await op(`agent.file.${action}`, { file_id: id }, { key, workspace: null, signal });
      const value: unknown = result.output;
      if (action === 'delete') {
        if (!object(value) || value.id !== id || value.status !== 'deleted') return invalid();
        mutations.delete(signature);
        // A deliberate new upload after deletion is a new intent, even for identical bytes.
        for (const [signature, attempt] of uploads) if (attempt.result?.id === id) uploads.delete(signature);
        if (latest?.result?.id === id) latest = undefined;
        return { id, status: 'deleted' };
      }
      const file = fileRecord(value, id); mutations.delete(signature); return file;
    },
  };
}
export type FileClient = ReturnType<typeof createFileClient>;
