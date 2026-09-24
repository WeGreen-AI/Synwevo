import { useMemo, useState } from 'react';
import { get } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { useDebounced, useLoad } from '../../lib/hooks';
import { BASE, DataState, Pager, usePage } from './common';
import { contractText, localizeContract } from './api-platform-contract.messages';

type Call = { id: string; at: number; method: string; route: string; status: number; duration_ms: number; request_id: string; credential_id: string; workspace_id: string };
type Operation = { path: string; method: string; summary?: string; description?: string; security?: Record<string, string[]>[]; parameters?: unknown[]; requestBody?: unknown; responses?: unknown };

export function Calls() {
  const { t, locale } = useI18n();
  const [workspace, setWorkspace] = useState('');
  const [errors, setErrors] = useState(false);
  const query = useDebounced(workspace);
  const load = usePage<Call>(`${BASE}/calls?workspace_id=${encodeURIComponent(query)}&errors_only=${errors}`);
  return <section className="card card-b col gap12"><h2>{t('实际调用记录')}</h2>
    <p className="muted">{t('仅保留最近 30 天的调用元数据，不记录请求内容、令牌或文档数据。')}</p>
    <label className="field">{t('按工作空间 ID 筛选')}<input className="input" value={workspace} onChange={e => setWorkspace(e.target.value)} /></label>
    <label><input type="checkbox" checked={errors} onChange={e => setErrors(e.target.checked)} /> {t('仅显示失败调用')}</label>
    <button className="btn" onClick={load.refresh}>{t('刷新')}</button>
    <DataState loading={load.loading} error={load.error} empty={!load.data?.items.length} retry={load.reload} />
    {!load.loading && !load.error && <div className="api-platform-table"><table><thead><tr>
      {[t('时间'), t('接口'), t('状态'), t('耗时'), t('工作空间 ID'), t('请求编号')].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>
      {load.data?.items.map(row => <tr key={row.id}><td>{new Date(row.at * 1000).toLocaleString(locale)}</td><td><code>{row.method} {row.route}</code></td><td>{row.status}</td><td>{t('{count} 毫秒', { count: Math.round(row.duration_ms) })}</td><td><code>{row.workspace_id}</code></td><td><code>{row.request_id}</code></td></tr>)}
    </tbody></table></div>}
    <Pager page={load.data} move={load.move} busy={load.loading} />
  </section>;
}

export function Operations() {
  const { t } = useI18n();
  const [search, setSearch] = useState('');
  const q = useDebounced(search);
  const [position, setPosition] = useState({ query: '', offset: 0 });
  const load = useLoad<{ paths: Record<string, Record<string, Omit<Operation, 'path' | 'method'>>> }>(BASE + '/openapi',
    signal => get(BASE + '/openapi', signal));
  // Search the actual contract in the selected language as well as its source text.
  // Fetching once also keeps page boundaries stable while the language changes.
  const items = useMemo(() => Object.entries(load.data?.paths || {}).flatMap(([path, methods]) =>
    Object.entries(methods).filter(([method]) => ['get', 'post', 'put', 'patch', 'delete'].includes(method))
      .map(([method, operation]) => ({ ...operation, path, method: method.toUpperCase() })))
    .filter(row => `${JSON.stringify(row)} ${JSON.stringify(localizeContract(row, t))}`.toLocaleLowerCase().includes(q.toLocaleLowerCase())), [load.data, q, t]);
  const offset = Math.min(position.query === q ? position.offset : 0, Math.max(0, Math.ceil(items.length / 25) - 1) * 25);
  const page = { items: items.slice(offset, offset + 25), total: items.length, offset, limit: 25,
    next_offset: offset + 25 < items.length ? offset + 25 : null };
  return <section className="card card-b col gap12"><h2>{t('接口契约')}</h2>
    <label className="field">{t('搜索路径、说明或权限')}<input className="input" type="search" value={search} maxLength={100} onChange={e => setSearch(e.target.value)} /></label>
    <a className="btn" href={BASE + '/openapi'} target="_blank" rel="noreferrer">{t('查看 OpenAPI JSON')}</a>
    <DataState loading={load.loading} error={load.error} empty={!items.length} retry={load.reload} />
    {!load.loading && !load.error && page.items.map(row => <article className="api-platform-record col gap8" key={row.method + row.path}>
      <code>{row.method} {row.path}</code><strong>{contractText(row.summary || '', t)}</strong>
      {row.description && <p>{contractText(row.description, t)}</p>}
      {!!row.security?.some(scheme => scheme.platformToken?.length) && <p>{t('所需权限')} · <code>{row.security.flatMap(scheme => scheme.platformToken || []).join(', ')}</code></p>}
      <details><summary>{t('请求与响应结构')}</summary><pre className="api-platform-json">{JSON.stringify(localizeContract({ security: row.security, parameters: row.parameters, requestBody: row.requestBody, responses: row.responses }, t), null, 2)}</pre></details>
    </article>)}
    <Pager page={page} move={offset => setPosition({ query: q, offset })} busy={load.loading} />
  </section>;
}
