import { useState } from 'react';
import { post } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { useDebounced } from '../../lib/hooks';
import { BASE, DataState, Pager, usePage, type Action } from './common';

type Sub = { id: string; tenant: string; status: 'active' | 'paused' | 'disabled'; disabled_reason: string | null;
  consecutive_failures: number; last_success_at: number | null; last_failure_at: number | null; receiver: string;
  events: string[]; pending: number; failed_7d: number; delivered_7d: number; last_error: string | null };
type Totals = { pending: number; failing: number; disabled: number; next_attempt_at: number | null };

/** Delivery health across workspaces (API-6): metadata only — receiver origin, status, failures,
 *  backlog — and a protective pause. Payloads, receiver paths and secrets are never shown; resuming,
 *  redelivering and editing belong to the workspace's own credential (the tools below). */
export function WebhookOps({ act, revision }: { act: (a: Action) => void; revision: number }) {
  const { t, locale } = useI18n();
  const [workspace, setWorkspace] = useState('');
  const [status, setStatus] = useState('');
  const [failing, setFailing] = useState(false);
  const q = useDebounced(workspace);
  const load = usePage<Sub>(`${BASE}/webhooks?${new URLSearchParams({ workspace_id: q.trim(), status, failing: String(failing), revision: String(revision) })}`);
  const totals = (load.data as unknown as { totals?: Totals } | null)?.totals;
  const when = (at: number | null) => at ? new Date(at * 1000).toLocaleString(locale) : '—';
  const label: Record<Sub['status'], string> = { active: t('投递中'), paused: t('已暂停'), disabled: t('已停用') };
  const columns = [t('工作空间 ID'), t('接收方'), t('状态'), t('连续失败'), t('排队中'), t('近 7 天'), t('最近成功'), t('最近错误'), t('操作')];
  const pause = (row: Sub) => act({ title: t('暂停 Webhook 订阅'),
    impact: <>{t('排队中的 {n} 次投递会被丢弃，之后的事件不再发送，直到该工作空间自己恢复订阅。', { n: row.pending })} <code>{row.id}</code></>,
    run: reason => post(`${BASE}/webhooks/${row.id}/pause`, { reason }) });
  return <section className="card card-b col gap12"><h2>{t('投递运行概况')}</h2>
    <p className="muted">{t('跨工作空间的订阅健康状况，只含元数据：不显示事件内容、接收地址的路径或签名密钥。平台可暂停失败或异常的接收方，恢复与重投由工作空间自己完成。')}</p>
    {totals && <div className="api-platform-grid">
      <Tile label={t('排队中的投递')} value={String(totals.pending)} />
      <Tile label={t('持续失败的订阅')} value={String(totals.failing)} />
      <Tile label={t('已停用的订阅')} value={String(totals.disabled)} />
      <Tile label={t('下一次重试')} value={when(totals.next_attempt_at)} /></div>}
    <div className="row gap12 wrap">
      <label className="field">{t('按工作空间 ID 筛选')}<input className="input" value={workspace} maxLength={100} onChange={e => setWorkspace(e.target.value)} /></label>
      <label className="field">{t('状态')}<select className="input" value={status} onChange={e => setStatus(e.target.value)}>
        <option value="">{t('全部')}</option><option value="active">{label.active}</option><option value="paused">{label.paused}</option><option value="disabled">{label.disabled}</option></select></label>
    </div>
    <label><input type="checkbox" checked={failing} onChange={e => setFailing(e.target.checked)} /> {t('只看正在失败的订阅')}</label>
    <DataState loading={load.loading} error={load.error} empty={!load.data?.items.length} retry={load.reload} />
    {!load.loading && !load.error && !!load.data?.items.length && <div className="api-platform-table api-platform-cards"><table>
      <thead><tr>{columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>{load.data.items.map(row => <tr key={row.id}>
        <td data-label={columns[0]}><code>{row.tenant}</code></td>
        <td data-label={columns[1]}><span><code>{row.receiver}</code><br /><span className="tiny muted">{row.events.join(', ')}</span></span></td>
        <td data-label={columns[2]}><span><span className="chip">{label[row.status]}</span>{row.disabled_reason && <> <code className="tiny">{row.disabled_reason}</code></>}</span></td>
        <td data-label={columns[3]}>{row.consecutive_failures}</td>
        <td data-label={columns[4]}>{row.pending}</td>
        <td data-label={columns[5]}>{t('成功 {ok} · 失败 {bad}', { ok: row.delivered_7d, bad: row.failed_7d })}</td>
        <td data-label={columns[6]}>{when(row.last_success_at)}</td>
        <td data-label={columns[7]}>{row.last_error ? <code className="tiny">{row.last_error}</code> : '—'}</td>
        <td data-label={columns[8]}>{row.status === 'active' ? <button className="btn sm" onClick={() => pause(row)}>{t('暂停')}</button> : '—'}</td>
      </tr>)}</tbody></table></div>}
    <Pager page={load.data} move={load.move} busy={load.loading} />
  </section>;
}

function Tile({ label, value }: { label: string; value: string }) {
  return <div className="card card-b col gap8"><span className="muted">{label}</span><strong>{value}</strong></div>;
}
