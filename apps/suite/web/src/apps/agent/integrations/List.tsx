/** /agent/integrations — every connection this person owns or may use, with the platform's
 *  decision on outbound MCP. The search filters what was read; nothing is invented. */
import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Empty, Loading } from '../../../shared/states';
import type { ConnectionSummary, Policy } from './model';
import { ConnectionStatusBadge, ErrorNotice, HealthBadge, OwnerText, useRead, useText } from './shared';

export function matches(item: ConnectionSummary, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return [item.name, item.url, item.host ?? '', item.description ?? '', item.owner_name ?? ''].some((v) => v.toLowerCase().includes(needle));
}

export function ConnectionRow({ item }: { item: ConnectionSummary }) {
  const { m, date } = useText();
  return <Link className="suite-row mi-row" to={`/agent/integrations/${item.id}`}>
    <div className="grow col gap4">
      <div className="row wrap">
        <span className="title">{item.name}</span>
        <HealthBadge health={item.health} />
        <ConnectionStatusBadge status={item.status} />
        {item.binding.bound && item.binding.enabled && <span className="badge info">{m('bound')}</span>}
      </div>
      <div className="sub"><OwnerText summary={item} /> · {item.host ?? item.url}</div>
      <div className="row wrap small muted mi-row-facts">
        <span>{m('toolsCount', { n: item.counts.tools })}</span>
        <span>{m('grantedCount', { n: item.counts.granted })}</span>
        <span>{item.last_call_at ? m('lastActivity', { time: date(item.last_call_at) }) : m('neverCalled')}</span>
      </div>
    </div>
    <span className="mi-chevron" aria-hidden="true">›</span>
  </Link>;
}

/** The page without its data loading: tests render it with fixtures. */
export function ListView({ items, policy, q, setQ, loading, error, reload, deleted }: {
  items: ConnectionSummary[] | null; policy: Policy | null; q: string; setQ: (q: string) => void;
  loading: boolean; error: unknown; reload: () => void;
  /** The name of a connection deleted just before arriving here. */
  deleted?: string | null;
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
  const policy = useRead<Policy>('agent.mcp.policy.get');
  const list = useRead<{ items: ConnectionSummary[] }>('agent.mcp.connection.list');
  return <ListView items={list.data?.items ?? null} policy={policy.data} q={q} setQ={setQ}
    loading={list.loading || policy.loading} error={list.error ?? policy.error}
    reload={() => { list.reload(); policy.reload(); }} deleted={deleted} />;
}
