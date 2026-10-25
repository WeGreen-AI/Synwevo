/** /agent/integrations — every connection this person owns or may use, with the platform's
 *  decision on outbound MCP. The search filters what was read; nothing is invented. */
import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Empty, Loading } from '../../../shared/states';
import { platformStopped, type ConnectionSummary, type Policy } from './model';
import { ConnectionStatusBadge, ErrorNotice, HealthBadge, OwnerText, useRead, useText } from './shared';

export function matches(item: ConnectionSummary, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return [item.name, item.url, item.host ?? '', item.description ?? '', item.owner_name ?? ''].some((v) => v.toLowerCase().includes(needle));
}

export function ConnectionRow({ item }: { item: ConnectionSummary }) {
  const { m, date } = useText();
  // A platform stop keeps every Agent from using the connection, whatever the person's binding says.
  const stopped = platformStopped(item);
  return <Link className="suite-row mi-row" to={`/agent/integrations/${item.id}`}>
    <div className="grow col gap4">
      <div className="row wrap">
        <span className="title">{item.name}</span>
        <HealthBadge health={item.health} />
        <ConnectionStatusBadge status={item.status} stopped={stopped} />
        {item.binding.bound && item.binding.enabled && !stopped && <span className="badge info">{m('bound')}</span>}
      </div>
      <div className="sub"><OwnerText summary={item} /> · {item.host ?? item.url}</div>
      {stopped && <p className="small mi-err">{m('platformStoppedRow')}</p>}
      <div className="row wrap small muted mi-row-facts">
        <span>{m('toolsCount', { n: item.counts.tools })}</span>
        <span>{m('grantedCount', { n: item.counts.granted })}</span>
        <span>{item.last_call_at ? m('lastActivity', { time: date(item.last_call_at) }) : m('neverCalled')}</span>
      </div>
    </div>
    <span className="mi-chevron" aria-hidden="true">›</span>
  </Link>;
}

/** A create or delete the server executed without returning its result, named for the notice. */
export interface Unverified { kind: 'create' | 'delete'; name: string }
export function unverifiedOf(state: unknown): Unverified | null {
  const value = (state as { unverified?: { kind?: unknown; name?: unknown } } | null)?.unverified;
  if (!value || (value.kind !== 'create' && value.kind !== 'delete') || typeof value.name !== 'string') return null;
  return { kind: value.kind, name: value.name };
}

/** The page without its data loading: tests render it with fixtures. */
export function ListView({ items, policy, q, setQ, loading, error, reload, deleted, unverified }: {
  items: ConnectionSummary[] | null; policy: Policy | null; q: string; setQ: (q: string) => void;
  loading: boolean; error: unknown; reload: () => void;
  /** The name of a connection deleted just before arriving here. */
  deleted?: string | null;
  /** A create or delete whose result the server could not return; this list was read after it. */
  unverified?: Unverified | null;
}) {
  const { m } = useText();
  const outbound = policy?.outbound_enabled !== false;
  const shown = items?.filter((item) => matches(item, q)) ?? null;
  return <div className="mi-page col gap16">
    <header className="suite-head">
      <div><h1>{m('title')}</h1><p className="lede">{m('intro')}</p></div>
      <div className="actions">
        <Link className="btn" to="/agent/integrations/clients">{m('clientsLink')}</Link>
        {outbound && policy && <Link className="btn primary" to="/agent/integrations/new">{m('add')}</Link>}
      </div>
    </header>
    {deleted && <p className="notice ok" role="status">{m('deleted')} {deleted}</p>}
    {unverified && <p className="notice warn" role="status">{m(unverified.kind === 'create' ? 'createUnverified' : 'deleteUnverified', { name: unverified.name })}</p>}
    {policy && !outbound && <p className="notice warn" role="status">{m('outboundClosed')}</p>}
    <div className="row wrap mi-toolbar">
      <label className="grow"><span className="sr-only">{m('search')}</span>
        <input type="search" className="input" placeholder={m('search')} value={q} onChange={(e) => setQ(e.target.value)} /></label>
      <button type="button" className="btn ghost sm" disabled={loading} onClick={reload}>{m('refresh')}</button>
    </div>
    {loading && !items ? <Loading /> : error != null ? <ErrorNotice error={error} retry={reload} />
      : !items || !shown ? null
      : items.length === 0 ? <Empty title={m('empty')} action={outbound && policy ? <Link className="btn primary" to="/agent/integrations/new">{m('add')}</Link> : undefined}>{m('emptyHint')}</Empty>
      : shown.length === 0 ? <Empty title={m('noMatch')} />
      : <div className="suite-list">{shown.map((item) => <ConnectionRow key={item.id} item={item} />)}</div>}
  </div>;
}

export default function IntegrationsList() {
  const [q, setQ] = useState('');
  const state = useLocation().state as { deleted?: unknown } | null;
  const deleted = typeof state?.deleted === 'string' ? state.deleted : null;
  const unverified = unverifiedOf(state);
  const policy = useRead<Policy>('agent.mcp.policy.get');
  const list = useRead<{ items: ConnectionSummary[] }>('agent.mcp.connection.list');
  return <ListView items={list.data?.items ?? null} policy={policy.data} q={q} setQ={setQ}
    loading={list.loading || policy.loading} error={list.error ?? policy.error}
    reload={() => { list.reload(); policy.reload(); }} deleted={deleted} unverified={unverified} />;
}
