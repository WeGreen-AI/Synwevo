import { useState } from 'react';
import { post } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { useDebounced } from '../../lib/hooks';
import { BASE, DataState, Pager, usePage, type Action } from './common';

type Job = { id: string; tenant: string; project_id: string; status: string; phase: string; created_at: number;
  started_at: number | null; finished_at: number | null; origin: string; request_id: string | null; halt_reason: string | null;
  counts: { samples: number; documents: number; failed: number; pending: number; running: number } };
type JobTotals = { queued: number; running: number; cancelling: number; oldest_queued_at: number | null };

/** Generation jobs across workspaces (API-6): metadata only — no project or template names, no
 *  sample data — and a protective cancel for a runaway job, with the workspace's own cancel rules. */
export function JobsPanel({ act, revision }: { act: (a: Action) => void; revision: number }) {
  const { t, locale } = useI18n();
  const [workspace, setWorkspace] = useState('');
  const [status, setStatus] = useState('active');
  const [origin, setOrigin] = useState('');
  const q = useDebounced(workspace);
  const load = usePage<Job>(`${BASE}/jobs?${new URLSearchParams({ workspace_id: q.trim(), status, origin, revision: String(revision) })}`);
  const totals = (load.data as unknown as { totals?: JobTotals } | null)?.totals;
  const when = (at: number | null) => at ? new Date(at * 1000).toLocaleString(locale) : '—';
  const labels: Record<string, string> = { queued: t('排队中'), running: t('进行中'), cancelling: t('正在取消'), completed: t('已完成'),
    completed_with_errors: t('部分完成'), failed: t('失败'), cancelled: t('已取消') };
  const columns = [t('任务'), t('工作空间 ID'), t('状态'), t('样本'), t('来源'), t('提交时间'), t('请求编号'), t('操作')];
  const cancel = (row: Job) => act({ title: t('停止任务'),
    impact: <>{t('排队中的任务立即取消；进行中的任务不再开始新样本，正在生成的样本完成后保留结果。该工作空间会收到与自行取消相同的事件。')} <code>{row.id}</code></>,
    run: reason => post(`${BASE}/jobs/${row.id}/cancel`, { reason }) });
  return <section className="card card-b col gap12"><h2>{t('生成任务')}</h2>
    <p className="muted">{t('跨工作空间的任务运行情况，只含元数据，不显示项目名称、模板或样本内容。平台只能停止失控的任务，重试由工作空间自己决定。')}</p>
    {totals && <div className="api-platform-grid">
      <Tile label={t('排队中')} value={String(totals.queued)} /><Tile label={t('进行中')} value={String(totals.running)} />
      <Tile label={t('正在取消')} value={String(totals.cancelling)} /><Tile label={t('最早排队')} value={when(totals.oldest_queued_at)} /></div>}
    <div className="row gap12 wrap">
      <label className="field">{t('按工作空间 ID 筛选')}<input className="input" value={workspace} maxLength={100} onChange={e => setWorkspace(e.target.value)} /></label>
      <label className="field">{t('状态')}<select className="input" value={status} onChange={e => setStatus(e.target.value)}>
        <option value="active">{t('未结束')}</option><option value="">{t('全部')}</option>
        {Object.entries(labels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label className="field">{t('来源')}<select className="input" value={origin} onChange={e => setOrigin(e.target.value)}>
        <option value="">{t('全部')}</option><option value="api">API</option><option value="ui">{t('网页')}</option></select></label>
    </div>
    <DataState loading={load.loading} error={load.error} empty={!load.data?.items.length} retry={load.reload} />
    {!load.loading && !load.error && !!load.data?.items.length && <div className="api-platform-table api-platform-cards"><table>
      <thead><tr>{columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>{load.data.items.map(row => <tr key={row.id}>
        <td data-label={columns[0]}><code>{row.id}</code></td><td data-label={columns[1]}><code>{row.tenant}</code></td>
        <td data-label={columns[2]}><span><span className="chip">{labels[row.status] || row.status}</span>{row.halt_reason && <> <code className="tiny">{row.halt_reason}</code></>}</span></td>
        <td data-label={columns[3]}>{t('{done}/{all} 成功 · {failed} 失败', { done: row.counts.documents, all: row.counts.samples, failed: row.counts.failed })}</td>
        <td data-label={columns[4]}>{row.origin === 'ui' ? t('网页') : 'API'}</td>
        <td data-label={columns[5]}>{when(row.created_at)}</td>
        <td data-label={columns[6]}>{row.request_id ? <code className="tiny">{row.request_id}</code> : '—'}</td>
        <td data-label={columns[7]}>{['queued', 'running'].includes(row.status) ? <button className="btn sm" onClick={() => cancel(row)}>{t('停止')}</button> : '—'}</td>
      </tr>)}</tbody></table></div>}
    <Pager page={load.data} move={load.move} busy={load.loading} />
  </section>;
}

type Audit = { seq: number; id: string; at: number; actor_kind: string; actor_id: string | null; actor_email: string | null;
  workspace_id: string | null; action: string; object_kind: string | null; object_id: string | null; result: string;
  request_id: string | null; meta: Record<string, unknown> };

/** The API platform's own trail (API-6): console changes, OAuth consent and client use, machine
 *  credentials' self-service — refusals included. Read-only. */
export function AuditPanel() {
  const { t, locale } = useI18n();
  const [filters, setFilters] = useState({ action: '', result: '', workspace: '', request: '' });
  const q = useDebounced(filters);
  const load = usePage<Audit>(`${BASE}/audit?${new URLSearchParams({ action: q.action.trim(), result: q.result,
    workspace_id: q.workspace.trim(), request_id: q.request.trim() })}`);
  const set = (key: keyof typeof filters) => (e: { target: { value: string } }) => setFilters({ ...filters, [key]: e.target.value });
  const results: Record<string, string> = { ok: t('成功'), denied: t('拒绝'), error: t('出错') };
  const columns = [t('时间'), t('操作'), t('结果'), t('操作者'), t('对象'), t('工作空间 ID'), t('理由与详情')];
  return <section className="card card-b col gap12"><h2>{t('API 平台审计')}</h2>
    <p className="muted">{t('API 管理台的修改、OAuth 同意与客户端使用、机器凭据的自助操作，包括被拒绝与出错的尝试。只读。')}</p>
    <div className="row gap12 wrap">
      <label className="field">{t('操作（完整名称）')}<input className="input" value={filters.action} maxLength={80} spellCheck={false} placeholder="api_platform.quota.change" onChange={set('action')} /></label>
      <label className="field">{t('结果')}<select className="input" value={filters.result} onChange={set('result')}>
        <option value="">{t('全部')}</option>{Object.entries(results).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label className="field">{t('按工作空间 ID 筛选')}<input className="input" value={filters.workspace} maxLength={100} onChange={set('workspace')} /></label>
      <label className="field">{t('请求编号')}<input className="input" value={filters.request} maxLength={128} spellCheck={false} onChange={set('request')} /></label>
    </div>
    <DataState loading={load.loading} error={load.error} empty={!load.data?.items.length} retry={load.reload} />
    {!load.loading && !load.error && !!load.data?.items.length && <div className="api-platform-table api-platform-cards"><table>
      <thead><tr>{columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>{load.data.items.map(row => <tr key={row.id}>
        <td data-label={columns[0]}>{new Date(row.at * 1000).toLocaleString(locale)}</td>
        <td data-label={columns[1]}><code>{row.action}</code></td>
        <td data-label={columns[2]}><span className="chip">{results[row.result] || row.result}</span></td>
        <td data-label={columns[3]}>{row.actor_email || (row.actor_id ? <code className="tiny">{row.actor_id}</code> : row.actor_kind)}</td>
        <td data-label={columns[4]}>{row.object_id ? <code className="tiny">{row.object_id}</code> : '—'}</td>
        <td data-label={columns[5]}>{row.workspace_id ? <code className="tiny">{row.workspace_id}</code> : '—'}</td>
        <td data-label={columns[6]}><span>{typeof row.meta.reason === 'string' ? row.meta.reason : ''}{typeof row.meta.code === 'string' && <> <code className="tiny">{row.meta.code}</code></>}</span></td>
      </tr>)}</tbody></table></div>}
    <Pager page={load.data} move={load.move} busy={load.loading} />
  </section>;
}

function Tile({ label, value }: { label: string; value: string }) {
  return <div className="card card-b col gap8"><span className="muted">{label}</span><strong>{value}</strong></div>;
}
