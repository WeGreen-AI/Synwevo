import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Modal, useI18n, type Params } from '@synwevo/ui';
import { read } from '../../shared/ops';
import { AgentFailure } from './errors';
import { MAX_CONTEXTS, contextErrorKey, contextKey, contextPage, resolvedContext, safeContextPath } from './context';
import { contextCopy, type ContextText } from './context.messages';
import type { ContextItem, ContextKind, ContextPage, MessageContext, SelectedContext, Workspace } from './types';

export function useContextText() {
  const { t } = useI18n();
  return (key: ContextText, params?: Params) => t(contextCopy[key][0], params);
}
export function ContextFailure({ error, retry, fallback = false }: { error: unknown; retry?: () => void; fallback?: boolean }) {
  const c = useContextText();
  const key = contextErrorKey(error);
  if (!key && !fallback) return <AgentFailure error={error} onRetry={retry} />;
  return <div className="notice err" role="alert"><p>{c(key ?? 'failed')}</p>
    {retry && <button type="button" className="btn" onClick={retry}>{c('retry')}</button>}</div>;
}

/** Render only the server's current safe labels, never reconstruct references from history. */
export function ContextSources({ contexts, hidden = false }: { contexts?: MessageContext[]; hidden?: boolean }) {
  const c = useContextText();
  if (hidden || !contexts?.length) return null;
  return <section className="ag-context-sources" aria-label={c('sources')}>
    <p className="muted small">{c('sources')}</p><ul className="ag-context-list">
      {contexts.map((source, i) => {
        const path = safeContextPath(source.open_path);
        return <li key={i} className="ag-context-source">
          {path ? <Link to={path}>{source.title}</Link> : <span>{source.title}</span>}
          <small className="muted">{source.workspace_name}</small>
        </li>;
      })}
    </ul>
  </section>;
}

export function ContextChips({ selected, onRemove, disabled = false }: {
  selected: SelectedContext[]; onRemove: (key: string) => void; disabled?: boolean;
}) {
  const c = useContextText();
  if (!selected.length) return null;
  return <div className="ag-context-selection">
    <p className="muted small" role="status">{c('selected', { count: selected.length })}</p>
    <ul className="ag-context-list">{selected.map((item) => <li className="ag-context-chip" key={contextKey(item.reference)}>
      <span><strong>{item.title}</strong><small>{item.workspace_name} · {c(item.reference.kind)}</small></span>
      <button type="button" className="btn ghost" disabled={disabled} aria-label={c('remove', { title: item.title })}
        onClick={() => onRemove(contextKey(item.reference))}><span aria-hidden="true">×</span></button>
    </li>)}</ul>
    <p className="muted small">{c('noReplay')}</p>
  </div>;
}

interface PickerProps {
  spaces: Workspace[]; selected: SelectedContext[]; onChoose: (item: SelectedContext) => void;
  onRemove: (key: string) => void; disabled: boolean;
}
export default function ContextPicker({ disabled, ...props }: PickerProps) {
  const c = useContextText();
  const [open, setOpen] = useState(false);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  return <>
    <button type="button" className="btn ghost sm ag-context-open" disabled={disabled} aria-haspopup="dialog"
      onClick={() => setOpen(true)}>{c('open')}</button>
    {open && !disabled && <ContextDialog {...props} onClose={() => setOpen(false)} />}
  </>;
}

/** This dialog mounts only while open. Closing, navigation and scope changes abort both reads. */
export function ContextDialog({ spaces, selected, onChoose, onRemove, onClose }: Omit<PickerProps, 'disabled'> & { onClose: () => void }) {
  const c = useContextText();
  const id = useId();
  const [workspace, setWorkspace] = useState(spaces[0]?.workspace_id ?? '');
  const [kind, setKind] = useState<ContextKind>('form');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState<ContextPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<{ error: unknown; offset: number } | null>(null);
  const [resolving, setResolving] = useState<string | null>(null);
  const [resolveFailure, setResolveFailure] = useState<{ error: unknown; item: ContextItem } | null>(null);
  const listRequest = useRef<AbortController | null>(null);
  const resolveRequest = useRef<AbortController | null>(null);
  const spacesKey = JSON.stringify(spaces.map((ws) => ws.workspace_id));
  function cancel() {
    listRequest.current?.abort(); resolveRequest.current?.abort();
    listRequest.current = null; resolveRequest.current = null;
  }
  function reset() {
    cancel(); setPage(null); setLoading(false); setFailure(null); setResolving(null); setResolveFailure(null);
  }
  function close() { cancel(); onClose(); }
  async function load(offset: number) {
    if (!workspace || !spaces.some((ws) => ws.workspace_id === workspace)) return;
    listRequest.current?.abort();
    const request = new AbortController(); listRequest.current = request;
    setLoading(true); setFailure(null);
    try {
      const value = await read<unknown>('agent.context.list', { workspace_id: workspace, kind, ...(search ? { q: search } : {}), offset }, null, request.signal);
      if (request.signal.aborted) return;
      const next = contextPage(value, workspace, kind, offset);
      setPage((old) => ({ ...next, limited: next.limited || (offset > 0 && !!old?.limited),
        items: offset && old ? [...old.items, ...next.items.filter((item) => !old.items.some((prior) => contextKey(prior) === contextKey(item)))] : next.items }));
    } catch (error) { if (!request.signal.aborted) setFailure({ error, offset }); }
    finally { if (!request.signal.aborted) { setLoading(false); listRequest.current = null; } }
  }
  useEffect(() => {
    reset();
    if (!spaces.some((ws) => ws.workspace_id === workspace)) setWorkspace(spaces[0]?.workspace_id ?? '');
    else void load(0);
    return cancel;
  }, [workspace, kind, search, spacesKey]);
  async function choose(item: ContextItem) {
    if (resolveRequest.current || selected.length >= MAX_CONTEXTS || selected.some((prior) => contextKey(prior.reference) === contextKey(item))) return;
    const request = new AbortController(); resolveRequest.current = request;
    setResolving(contextKey(item)); setResolveFailure(null);
    try {
      const value = await read<unknown>('agent.context.resolve', { workspace_id: item.workspace_id, kind: item.kind, resource_id: item.resource_id }, null, request.signal);
      if (!request.signal.aborted) onChoose(resolvedContext(value, item));
    } catch (error) { if (!request.signal.aborted) setResolveFailure({ error, item }); }
    finally { if (!request.signal.aborted) { setResolving(null); resolveRequest.current = null; } }
  }
  return <Modal title={c('open')} onClose={close} describedBy={`${id}-intro`}
    footer={<button type="button" className="btn primary" onClick={close}>{c('done')}</button>}>
    <div className="ag-context-picker">
      <p id={`${id}-intro`}>{c('intro')}</p>
      <p className="muted small">{c('formHint')}</p><p className="muted small">{c('tableHint')}</p>
      <ContextChips selected={selected} onRemove={onRemove} />
      {selected.length >= MAX_CONTEXTS && <p className="notice" role="status">{c('max')}</p>}
      {!spaces.length ? <p className="notice">{c('noSpaces')}</p> : <>
        <div className="ag-context-filters">
          <label htmlFor={`${id}-workspace`}>{c('workspace')}<select id={`${id}-workspace`} className="input" value={workspace}
            onChange={(e) => { reset(); setWorkspace(e.target.value); }}>
            {spaces.map((ws) => <option key={ws.workspace_id} value={ws.workspace_id}>{ws.name}</option>)}
          </select></label>
          <label htmlFor={`${id}-kind`}>{c('kind')}<select id={`${id}-kind`} className="input" value={kind}
            onChange={(e) => { reset(); setKind(e.target.value as ContextKind); }}>
            <option value="form">{c('form')}</option><option value="table">{c('table')}</option>
          </select></label>
        </div>
        <form className="ag-context-search" onSubmit={(e) => {
          e.preventDefault(); reset(); if (query.trim() === search) void load(0); else setSearch(query.trim());
        }}>
          <label htmlFor={`${id}-query`}>{c('query')}<input id={`${id}-query`} className="input" type="search" maxLength={100}
            value={query} onChange={(e) => setQuery(e.target.value)} /></label>
          <button type="submit" className="btn">{c('search')}</button>
        </form>
        {page?.limited && <p className="notice" role="status">{c('limited')}</p>}
        {loading && <p role="status">{c('loading')}</p>}
        {resolving && <p role="status">{c('resolving')}</p>}
        {failure && <ContextFailure error={failure.error} fallback retry={() => void load(failure.offset)} />}
        {resolveFailure && <ContextFailure error={resolveFailure.error} fallback retry={() => void choose(resolveFailure.item)} />}
        {page && !page.items.length && !loading && !failure && <p>{c('empty')}</p>}
        <ul className="ag-context-results" aria-busy={loading}>
          {page?.items.map((item) => {
            const chosen = selected.some((prior) => contextKey(prior.reference) === contextKey(item));
            return <li key={contextKey(item)}><button type="button" className="ag-context-result"
              disabled={chosen || !!resolving || selected.length >= MAX_CONTEXTS} aria-label={c('choose', { title: item.title })}
              onClick={() => void choose(item)}><span>{item.title}</span><small className="muted">{chosen ? c('chosen') : c(item.kind)}</small></button></li>;
          })}
        </ul>
        {page?.next_offset != null && !failure && <button type="button" className="btn" disabled={loading}
          onClick={() => void load(page.next_offset!)}>{c('more')}</button>}
      </>}
    </div>
  </Modal>;
}
