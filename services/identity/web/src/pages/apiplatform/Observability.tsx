import { useMemo, useState } from 'react';
import { get } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { useDebounced, useLoad } from '../../lib/hooks';
import { BASE, DataState, Pager, usePage } from './common';
import { contractText, localizeContract } from './api-platform-contract.messages';

type Call = { id: string; at: number; method: string; route: string; status: number; duration_ms: number; request_id: string;
  credential_id: string | null; client_id: string | null; credential_kind: string | null; workspace_id: string; error_code: string | null };
type Operation = { path: string; method: string; summary?: string; description?: string; security?: Record<string, string[]>[]; parameters?: unknown[]; requestBody?: unknown; responses?: unknown };

export function Calls() {
  const { t, locale } = useI18n();
  const [filters, setFilters] = useState({ workspace: '', request: '', credential: '', code: '', status: '' });
  const [errors, setErrors] = useState(false);
  const q = useDebounced(filters);
  const params = new URLSearchParams({ workspace_id: q.workspace.trim(), request_id: q.request.trim(),
    credential_id: q.credential.trim(), error_code: q.code.trim(), status: q.status, errors_only: String(errors) });
  const load = usePage<Call>(`${BASE}/calls?${params}`);
  const columns = [t('时间'), t('接口'), t('状态'), t('耗时'), t('调用方'), t('工作空间 ID'), t('请求编号')];
  const set = (key: keyof typeof filters) => (e: { target: { value: string } }) => setFilters({ ...filters, [key]: e.target.value });
  return <section className="card card-b col gap12"><h2>{t('实际调用记录')}</h2>
    <p className="muted">{t('仅保留最近 30 天的调用元数据，不记录请求内容、令牌或文档数据。')}</p>
    <div className="row gap12 wrap">
      <label className="field">{t('请求编号')}<input className="input" value={filters.request} maxLength={128} spellCheck={false} onChange={set('request')} /></label>
      <label className="field">{t('按工作空间 ID 筛选')}<input className="input" value={filters.workspace} maxLength={100} onChange={set('workspace')} /></label>
      <label className="field">{t('凭据或客户端 ID')}<input className="input" value={filters.credential} maxLength={200} spellCheck={false} onChange={set('credential')} /></label>
      <label className="field">{t('错误代码')}<input className="input" value={filters.code} maxLength={64} spellCheck={false} onChange={set('code')} /></label>
      <label className="field">{t('状态')}<select className="input" value={filters.status} onChange={set('status')}>
        <option value="">{t('全部')}</option>{['2xx', '3xx', '4xx', '429', '5xx'].map(v => <option key={v} value={v}>{v}</option>)}</select></label>
    </div>
    <label><input type="checkbox" checked={errors} onChange={e => setErrors(e.target.checked)} /> {t('仅显示失败调用')}</label>
    <button className="btn" onClick={load.refresh}>{t('刷新')}</button>
    <DataState loading={load.loading} error={load.error} empty={!load.data?.items.length} retry={load.reload} />
    {!load.loading && !load.error && <div className="api-platform-table api-platform-cards"><table><thead><tr>
      {columns.map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>
      {load.data?.items.map(row => <tr key={row.id}><td data-label={columns[0]}>{new Date(row.at * 1000).toLocaleString(locale)}</td>
        <td data-label={columns[1]}><code>{row.method} {row.route}</code></td>
        <td data-label={columns[2]}><span>{row.status}{row.error_code && <> · <code>{row.error_code}</code></>}</span></td>
        <td data-label={columns[3]}>{t('{count} 毫秒', { count: Math.round(row.duration_ms) })}</td>
        <td data-label={columns[4]}><code>{row.credential_id || row.client_id || '—'}</code></td>
        <td data-label={columns[5]}><code>{row.workspace_id}</code></td><td data-label={columns[6]}><code>{row.request_id}</code></td></tr>)}
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
      .map(([method, operation]) => ({ ...operation, path, method: method.toUpperCase(),
        scopes: [...new Set(operation.security?.flatMap(scheme => Object.values(scheme).flat()) || [])] })))
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
      {!!row.scopes.length && <p>{t('所需权限')} · <code>{row.scopes.join(', ')}</code></p>}
      <details><summary>{t('请求与响应结构')}</summary><pre className="api-platform-json">{JSON.stringify(localizeContract({ security: row.security, parameters: row.parameters, requestBody: row.requestBody, responses: row.responses }, t), null, 2)}</pre></details>
    </article>)}
    <Pager page={page} move={offset => setPosition({ query: q, offset })} busy={load.loading} />
  </section>;
}
