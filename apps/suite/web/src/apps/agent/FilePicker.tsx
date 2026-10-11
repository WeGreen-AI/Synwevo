import { useEffect, useId, useRef, useState } from 'react';
import { Modal, useI18n, type Params } from '@synwevo/ui';
import { MAX_CONTEXTS } from './context';
import { fileCopy, type FileText } from './files.messages';
import { canRetryFile, fileErrorKey, fileRowLabel, fileSourceLabel, processingFile, scannerLabel, selectFile, type FileClient, type UploadAttempt } from './files';
import type { AgentFile, FileCapabilities, FilePage, SelectedFile } from './types';

export function useFileText() {
  const { t } = useI18n();
  return (key: FileText, params?: Params) => t(fileCopy[key][0], params);
}
export function FileFailure({ error }: { error: unknown }) {
  const f = useFileText();
  return <p className="notice err" role="alert">{f(fileErrorKey(error) ?? 'loadFailed')}</p>;
}
export function FileChips({ selected, onRemove, disabled }: { selected: SelectedFile[]; onRemove: (id: string) => void; disabled: boolean }) {
  const f = useFileText();
  if (!selected.length) return null;
  return <div className="ag-context-selection">
    <p className="muted small">{f('selected', { count: selected.length })}</p>
    <ul className="ag-context-list">{selected.map((file) => <li className="ag-context-chip" key={file.reference.file_id}>
      <span><strong>{file.title}</strong><small>{f('open')}</small></span>
      <button type="button" className="btn ghost" disabled={disabled} aria-label={f('remove', { title: file.title })}
        onClick={() => onRemove(file.reference.file_id)}><span aria-hidden="true">×</span></button>
    </li>)}</ul><p className="muted small">{f('history')}</p>
  </div>;
}
interface Props {
  client: FileClient; selected: SelectedFile[]; businessCount: number; disabled: boolean;
  onChoose: (file: SelectedFile) => void; onRemove: (id: string) => void;
  onRecord: (file: Pick<AgentFile, 'id' | 'status'> & Partial<AgentFile>) => void;
  onDeleted: () => Promise<void>;
}
export default function FilePicker({ disabled, ...props }: Props) {
  const f = useFileText();
  const [open, setOpen] = useState(false);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  return <>
    <button type="button" className="btn ghost sm ag-context-open" disabled={disabled} aria-haspopup="dialog"
      onClick={() => setOpen(true)}>{f('open')}</button>
    {open && !disabled && <FileDialog {...props} onClose={() => setOpen(false)} />}
  </>;
}
export function FilePreview({ file }: { file: AgentFile }) {
  const f = useFileText();
  if (file.status !== 'ready' || !file.extraction) return null;
  return <section className="ag-file-preview" aria-label={f('preview')}>
    <h3>{file.filename}</h3><p>{f('preview')}</p>
    {file.extraction.lines?.map((line, i) => <div key={i}>
      <small className="muted">{fileSourceLabel(line.source, f)}</small><pre>{line.text}</pre>
    </div>)}
    {file.extraction.tables?.map((table, i) => <div key={i}>
      <p>{table.name}</p><small className="muted">{fileSourceLabel(table.source, f)}</small>
      <div className="ag-file-table" tabIndex={0} role="region" aria-label={table.name || f('preview')}><table>
        <tbody>{table.rows.map((row, j) => <tr key={j}>
          <th scope="row">{fileRowLabel(table.row_numbers?.[j], f)}</th>{row.map((cell, k) => <td key={k}>{String(cell ?? '')}</td>)}
        </tr>)}</tbody>
      </table></div>
    </div>)}
  </section>;
}

/** Mounted only while visible. Every request and real-status poll is aborted on close/navigation. */
export function FileDialog({ client, selected, businessCount, onChoose, onRemove, onRecord, onDeleted, onClose }: Omit<Props, 'disabled'> & { onClose: () => void }) {
  const f = useFileText();
  const id = useId();
  const [caps, setCaps] = useState<FileCapabilities>();
  const [page, setPage] = useState<FilePage>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>();
  const [pollError, setPollError] = useState<unknown>();
  const [pollTick, setPollTick] = useState(0);
  const [action, setAction] = useState<string | null>(null);
  const [preview, setPreview] = useState<AgentFile>();
  const [deleting, setDeleting] = useState<AgentFile>();
  const [local, setLocal] = useState<File>();
  const [attempt, setAttempt] = useState<UploadAttempt | undefined>(() => client.pending());
  const [uploadState, setUploadState] = useState(() => client.pending()?.state);
  const [preparing, setPreparing] = useState(false);
  const lifetime = useRef<AbortController | null>(null);
  const listRequest = useRef<AbortController | null>(null);
  const actionRequest = useRef<AbortController | null>(null);
  const uploadRequest = useRef<AbortController | null>(null);
  const total = selected.length + businessCount;
  const busy = loading || !!action || preparing || uploadState === 'uploading';
  const processing = (page?.files ?? []).filter(processingFile).map((file) => file.id).join(',');
  function stopUpload() {
    uploadRequest.current?.abort(); uploadRequest.current = null;
    if (attempt?.state === 'uploading') attempt.state = 'unknown';
    setUploadState(attempt?.state);
  }
  function cancelRequests() {
    lifetime.current?.abort(); listRequest.current?.abort(); actionRequest.current?.abort(); uploadRequest.current?.abort();
    const pending = client.pending(); if (pending?.state === 'uploading') pending.state = 'unknown';
  }
  function close() { cancelRequests(); onClose(); }
  function update(file: AgentFile) {
    setPage((old) => old ? { ...old, files: old.files.some((f) => f.id === file.id)
      ? old.files.map((f) => f.id === file.id ? file : f) : [file, ...old.files] } : { files: [file], next_offset: null });
    setPreview((old) => old?.id === file.id ? file : old); onRecord(file);
  }
  async function load(offset = 0) {
    listRequest.current?.abort();
    const request = new AbortController(); listRequest.current = request;
    setLoading(true); setError(undefined); setPollError(undefined);
    try {
      const [capabilities, next] = await Promise.all([client.capabilities(request.signal), client.list(offset, request.signal)]);
      if (request.signal.aborted) return;
      setCaps(capabilities);
      setPage((old) => ({ ...next, files: offset && old ? [...old.files, ...next.files.filter((file) => !old.files.some((prior) => prior.id === file.id))] : next.files }));
      next.files.forEach(onRecord);
    } catch (error) { if (!request.signal.aborted) { setError(error); setPollError(error); } }
    finally { if (!request.signal.aborted) { setLoading(false); listRequest.current = null; } }
  }
  useEffect(() => {
    lifetime.current = new AbortController(); void load();
    return cancelRequests;
  }, [client]);
  useEffect(() => {
    if (!processing || pollError || busy || loading) return;
    const request = new AbortController();
    const signal = lifetime.current?.signal;
    const abort = () => request.abort();
    signal?.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(async () => {
      if (request.signal.aborted || signal?.aborted) return;
      try {
        const files = await Promise.all(processing.split(',').map((fid) => client.get(fid, request.signal)));
        if (request.signal.aborted || lifetime.current?.signal.aborted) return;
        files.forEach(update); setPollTick((n) => n + 1);
      } catch (error) { if (!request.signal.aborted) setPollError(error); }
    }, 1500);
    return () => { clearTimeout(timer); request.abort(); signal?.removeEventListener('abort', abort); };
  }, [client, processing, pollTick, pollError, busy, loading]);
  async function upload() {
    if (!caps || busy || (!local && !attempt)) return;
    const request = new AbortController(); uploadRequest.current = request;
    setError(undefined); setPreparing(true);
    try {
      const next = local ? await client.prepare(local, caps) : attempt!;
      if (request.signal.aborted) return;
      setAttempt(next); setLocal(undefined); setPreparing(false); setUploadState('uploading');
      const file = await client.upload(next, request.signal);
      if (request.signal.aborted) return;
      setUploadState('received'); update(file);
    } catch (error) {
      if (!request.signal.aborted) { setError(error); setAttempt(client.pending()); setUploadState(client.pending()?.state); }
    } finally { if (!request.signal.aborted) { setPreparing(false); uploadRequest.current = null; } }
  }
  async function inspect(file: AgentFile, choose: boolean) {
    if (actionRequest.current || busy || (choose && total >= MAX_CONTEXTS)) return;
    const request = new AbortController(); actionRequest.current = request; setAction(file.id); setError(undefined);
    try {
      const current = await client.get(file.id, request.signal);
      if (request.signal.aborted) return;
      update(current);
      if (choose) onChoose(selectFile(current)); else setPreview(current);
    } catch (error) { if (!request.signal.aborted) setError(error); }
    finally { if (!request.signal.aborted) { setAction(null); actionRequest.current = null; } }
  }
  async function mutate(kind: 'retry' | 'cancel' | 'delete', file: AgentFile) {
    if (actionRequest.current || busy) return;
    const request = new AbortController(); actionRequest.current = request; setAction(file.id); setError(undefined);
    try {
      const next = await client.mutate(kind, file.id, request.signal);
      if (request.signal.aborted) return;
      if (next.status === 'deleted') {
        onRecord(next); onRemove(file.id); setDeleting(undefined); setPreview(undefined);
        setPage((old) => old && { ...old, files: old.files.filter((f) => f.id !== file.id) });
        setAttempt(client.pending()); setUploadState(client.pending()?.state);
        await onDeleted(); // Refresh transcript: deleted-source history must not remain visibly available.
        if (!request.signal.aborted) void load(0); // deletion shifts offset pagination
      } else update(next as AgentFile);
    } catch (error) { if (!request.signal.aborted) setError(error); }
    finally { if (!request.signal.aborted) { setAction(null); actionRequest.current = null; } }
  }
  return <Modal title={f('open')} onClose={close} describedBy={`${id}-intro`}
    footer={<button type="button" className="btn primary" onClick={close}>{f('done')}</button>}>
    <div className="ag-context-picker ag-file-picker">
      <p id={`${id}-intro`}>{f('intro')}</p><p className="muted small">{f('history')}</p>
      {caps && <>
        {!caps.configured && <p className="notice">{f('notConfigured')}</p>}
        <p className="muted small">{f('limits', { formats: caps.formats.join(', ') || '—', bytes: caps.max_bytes,
          count: caps.max_files, storage: caps.max_storage_bytes, chars: caps.max_context_chars })}</p>
        <p className="muted small">{f('retention', { hours: caps.unattached_retention_seconds / 3600 })}</p>
        <p className="muted small">{f('encoding')}</p>{caps.formats.some((x) => ['docx', 'xlsx'].includes(x.toLowerCase())) && <p className="muted small">{f('office')}</p>}<p className="notice">{f('noOcr')}</p><p className="muted small">{f(scannerLabel(caps.scanner))}</p>
      </>}
      <label htmlFor={`${id}-file`}>{f('chooseUpload')}
        <input id={`${id}-file`} className="input" type="file" disabled={!caps?.configured || busy}
          accept={caps?.formats.map((format) => `.${format}`).join(',')}
          onChange={(e) => { const file = e.target.files?.[0]; if (file) { setLocal(file); setError(undefined); } e.target.value = ''; }} />
      </label>
      {(local || attempt) && <div className="ag-file-upload">
        <p>{local?.name ?? attempt?.file.name}</p>
        {preparing && <p role="status">{f('preparing')}</p>}
        {uploadState === 'uploading' && <p role="status">{f('uploading')}</p>}
        {!local && uploadState === 'unknown' && <p className="notice" role="status">{f('unknown')}</p>}
        {!local && uploadState === 'received' && <p role="status">{f('accepted')}</p>}
        {(local || uploadState === 'unknown' || uploadState === 'prepared') && <button type="button" className="btn" disabled={busy || !caps?.configured}
          onClick={() => void upload()}>{f(local || uploadState === 'prepared' ? 'upload' : 'retryUpload')}</button>}
        {uploadState === 'uploading' && <button type="button" className="btn" onClick={stopUpload}>{f('cancelUpload')}</button>}
      </div>}
      <p role="status" className="muted small">{f('combined', { count: total })}</p>
      <FileChips selected={selected} onRemove={onRemove} disabled={busy} />
      <button type="button" className="btn" disabled={loading || busy} onClick={() => void load(0)}>{f('refresh')}</button>
      {loading && <p role="status">{f('loading')}</p>}{action && <p role="status">{f('reading')}</p>}
      {error != null && <FileFailure error={error} />}{pollError != null && pollError !== error && <FileFailure error={pollError} />}
      {page && !page.files.length && !loading && <p>{f('empty')}</p>}
      <ul className="ag-context-results" aria-busy={loading}>{page?.files.map((file) => {
        const chosen = selected.some((s) => s.reference.file_id === file.id);
        return <li className="ag-file-row" key={file.id}>
          <strong>{file.filename}</strong><p role="status">{f(file.status)}</p>
          <p className="muted small">{f('details', { size: file.size, attempts: file.attempts })}</p>
          <p className="muted small">{f(scannerLabel(caps?.scanner ?? null, file.scan_status))}</p>
          {file.error_code && <FileFailure error={{ code: file.error_code }} />}
          <div className="ag-file-actions">
            <button type="button" className="btn" disabled={busy || chosen || total >= MAX_CONTEXTS || (file.status !== 'ready' || file.error_code === 'agent_file_expired')}
              aria-label={f('choose', { title: file.filename })} onClick={() => void inspect(file, true)}>{f(chosen ? 'chosen' : 'choose', { title: file.filename })}</button>
            {file.status === 'ready' && file.error_code !== 'agent_file_expired' && <button type="button" className="btn ghost" disabled={busy}
              onClick={() => void inspect(file, false)}>{f('inspect', { title: file.filename })}</button>}
            {file.download_path && file.error_code !== 'agent_file_expired' && <a className="btn ghost" href={file.download_path} download>{f('download')}</a>}
            {canRetryFile(file) && <button type="button" className="btn" disabled={busy} onClick={() => void mutate('retry', file)}>{f('retry')}</button>}
            {processingFile(file) && <button type="button" className="btn" disabled={busy} onClick={() => void mutate('cancel', file)}>{f('cancel')}</button>}
            <button type="button" className="btn ghost" disabled={busy} onClick={() => { setDeleting(file); setError(undefined); }}>{f('delete')}</button>
          </div>
          {deleting?.id === file.id && <div className="notice err" role="group" aria-labelledby={`${id}-delete`}>
            <p id={`${id}-delete`}>{f('deleteImpact', { title: file.filename })}</p><div className="ag-file-actions">
              <button type="button" className="btn" disabled={busy} onClick={() => setDeleting(undefined)}>{f('keep')}</button>
              <button type="button" className="btn danger" disabled={busy} onClick={() => void mutate('delete', file)}>{f('delete')}</button>
            </div>
          </div>}
        </li>;
      })}</ul>
      {page?.next_offset != null && <button type="button" className="btn" disabled={loading || busy}
        onClick={() => void load(page.next_offset!)}>{f('more')}</button>}
      {preview && <FilePreview file={preview} />}
    </div>
  </Modal>;
}
