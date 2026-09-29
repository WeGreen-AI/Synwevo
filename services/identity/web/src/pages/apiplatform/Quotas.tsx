import { useEffect, useState, type FormEvent } from 'react';
import { ApiError, get, post, put } from '@synwevo/auth-client';
import { Modal, useI18n } from '@synwevo/ui';
import { useLoad } from '../../lib/hooks';
import { BASE, DataState, ErrorNotice, Pager, type Action, type Page } from './common';

type Field = 'credential_rpm' | 'workspace_rpm' | 'active_jobs';
type Source = 'credential' | 'workspace' | 'platform' | 'deployment';
type Effective = Record<Field, { value: number; source: Source }>;
type Policy = { scope_kind: string; scope_id: string; version: number; updated_at: number; updated_by: string; reason: string } & Record<Field, number | null>;
type Overview = { window_s: number; fields: Field[]; layer_fields: Record<string, Field[]>;
  ranges: Record<Field, { min: number; max: number }>; deployment_defaults: Record<Field, number>;
  platform: { policy: Policy | null; effective: Effective }; overrides: Page<Policy> };
type Usage = { limit: number; remaining: number; window_s: number; source: Source; measured_at: number };
type View = { workspace_id: string; credential: string | null; effective: Effective;
  policies: Record<'credential' | 'workspace' | 'platform', Policy | null>;
  usage: { key?: Usage; workspace?: Usage; active_jobs: { now: number; measured_at: number } } };
type Preview = { version: number; before: Effective; after: Effective; workspace_id: string | null;
  impact: { stops_running_work: boolean; active_jobs_now?: number; over_new_limit?: number; workspaces_with_own_override?: number } };
type History = { id: string; version: number; at: number; actor_id: string; reason: string;
  before: Record<Field, number | null>; after: Record<Field, number | null> };
type Target = { kind: 'platform' | 'workspace' | 'credential'; id: string };

const scopePath = (t: Target) => t.kind === 'platform' ? 'platform' : `${t.kind}/${encodeURIComponent(t.id)}`;

function useLabels() {
  const { t } = useI18n();
  const field: Record<Field, string> = { credential_rpm: t('每个凭据每分钟请求数'), workspace_rpm: t('工作空间每分钟请求总数'),
    active_jobs: t('同时进行的生成任务') };
  const source: Record<Source, string> = { credential: t('凭据设置'), workspace: t('工作空间设置'),
    platform: t('平台设置'), deployment: t('部署默认值') };
  return { field, source };
}

/** Request rate and job capacity: what applies, where it comes from, how much is in use, and a
 *  versioned change that the next request in every process obeys (API-1). */
export function Quotas({ act, revision }: { act: (a: Action) => void; revision: number }) {
  const { t, locale } = useI18n();
  const labels = useLabels();
  const [offset, setOffset] = useState(0);
  const url = `${BASE}/quotas?limit=25&offset=${offset}&revision=${revision}`;
  const load = useLoad<Overview>(url, signal => get(url, signal));
  const [workspace, setWorkspace] = useState('');
  const [credential, setCredential] = useState('');
  const [view, setView] = useState<View | null>(null);
  const [viewError, setViewError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Target | null>(null);
  const [history, setHistory] = useState<Target | null>(null);

  async function lookup(event?: FormEvent) {
    event?.preventDefault();
    const q = new URLSearchParams();
    if (workspace.trim()) q.set('workspace_id', workspace.trim());
    if (credential.trim()) q.set('credential', credential.trim());
    setBusy(true); setViewError(null);
    try { setView(await get<View>(`${BASE}/quotas/effective?${q}`)); }
    catch (e) { setView(null); setViewError(e); }
    finally { setBusy(false); }
  }
  // A saved change is shown against fresh numbers, not the ones read before it.
  useEffect(() => { if (view) void lookup(); }, [revision]);  // eslint-disable-line react-hooks/exhaustive-deps

  const when = (at: number) => new Date(at * 1000).toLocaleString(locale);
  const data = load.data;
  return <section className="col gap16">
    <div className="card card-b col gap12"><h2>{t('流量与配额')}</h2>
      <p className="muted">{t('请求速率按凭据与工作空间分别计算，所有服务进程共用同一份计数；修改后下一次请求即按新值执行。同时进行的生成任务同样约束网页与 API 提交。')}</p>
      <DataState loading={load.loading} error={load.error} empty={false} retry={load.reload} />
      {data && !load.error && <>
        <div className="api-platform-table api-platform-cards"><table><caption className="muted">{t('平台默认（未单独设置的工作空间与凭据按此执行）')}</caption>
          <thead><tr><th>{t('限制')}</th><th>{t('部署默认值')}</th><th>{t('平台设置')}</th><th>{t('当前生效')}</th></tr></thead>
          <tbody>{data.fields.map(f => <tr key={f}><td data-label={t('限制')}>{labels.field[f]}</td><td data-label={t('部署默认值')}>{data.deployment_defaults[f]}</td>
            <td data-label={t('平台设置')}>{data.platform.policy?.[f] ?? t('继承')}</td>
            <td data-label={t('当前生效')}><span><strong>{data.platform.effective[f].value}</strong> <span className="chip">{labels.source[data.platform.effective[f].source]}</span></span></td></tr>)}
          </tbody></table></div>
        <div className="row gap8 wrap">
          <button className="btn" onClick={() => setEditing({ kind: 'platform', id: '' })}>{t('修改平台策略')}</button>
          <button className="btn" onClick={() => setHistory({ kind: 'platform', id: '' })}>{t('变更历史')}</button>
        </div>
      </>}
    </div>

    <form className="card card-b col gap12" onSubmit={lookup}><h2>{t('查看工作空间或凭据')}</h2>
      <div className="row gap12 wrap">
        <label className="field">{t('工作空间 ID')}<input className="input" value={workspace} maxLength={100} onChange={e => setWorkspace(e.target.value)} /></label>
        <label className="field">{t('凭据（可选，如 api_key:… 或 service_account:…）')}<input className="input" value={credential} maxLength={200} spellCheck={false} onChange={e => setCredential(e.target.value)} /></label>
      </div>
      <div className="row gap8"><button className="btn primary" type="submit" disabled={busy || (!workspace.trim() && !credential.trim())}>{busy ? t('加载中…') : t('查看生效值')}</button></div>
      <ErrorNotice error={viewError} />
      {view && <div className="col gap12" aria-live="polite">
        <p className="muted">{t('工作空间')} · <code>{view.workspace_id}</code>{view.credential && <> · {t('凭据')} · <code>{view.credential}</code></>}</p>
        <div className="api-platform-table api-platform-cards"><table><thead><tr><th>{t('限制')}</th><th>{t('当前生效')}</th><th>{t('来源')}</th><th>{t('当前用量')}</th></tr></thead>
          <tbody>
            {(['credential_rpm', 'workspace_rpm', 'active_jobs'] as Field[]).map(f => {
              const u = f === 'credential_rpm' ? view.usage.key : f === 'workspace_rpm' ? view.usage.workspace : null;
              return <tr key={f}><td data-label={t('限制')}>{labels.field[f]}</td><td data-label={t('当前生效')}><strong>{view.effective[f].value}</strong></td>
                <td data-label={t('来源')}><span className="chip">{labels.source[view.effective[f].source]}</span></td>
                <td data-label={t('当前用量')}>{f === 'active_jobs' ? t('{now} 个进行中（{at}）', { now: view.usage.active_jobs.now, at: when(view.usage.active_jobs.measured_at) })
                  : u ? t('本分钟剩余 {n} / {limit}（{at}）', { n: u.remaining, limit: u.limit, at: when(u.measured_at) })
                  : t('未选择凭据')}</td></tr>;
            })}
          </tbody></table></div>
        <div className="row gap8 wrap">
          <button className="btn" onClick={() => setEditing({ kind: 'workspace', id: view.workspace_id })}>{t('修改工作空间策略')}</button>
          {view.credential && <button className="btn" onClick={() => setEditing({ kind: 'credential', id: view.credential! })}>{t('修改凭据策略')}</button>}
          <button className="btn" onClick={() => setHistory({ kind: 'workspace', id: view.workspace_id })}>{t('工作空间变更历史')}</button>
        </div>
      </div>}
    </form>

    {data && <div className="card card-b col gap12"><h2>{t('已单独设置的策略')}</h2>
      {!data.overrides.items.length ? <p className="muted">{t('没有记录。')}</p> : data.overrides.items.map(row =>
        <article key={`${row.scope_kind}:${row.scope_id}`} className="api-platform-record col gap8">
          <div className="row gap8 wrap"><span className="chip">{labels.source[row.scope_kind as Source]}</span><code>{row.scope_id || t('平台')}</code>
            <span className="muted">{t('版本 {n}', { n: row.version })} · {when(row.updated_at)}</span></div>
          <p>{data.fields.filter(f => row[f] != null).map(f => `${labels.field[f]} ${row[f]}`).join(' · ')}</p>
          <p className="muted">{t('理由')} · {row.reason}</p>
          <div className="row gap8 wrap">
            <button className="btn sm" onClick={() => setEditing({ kind: row.scope_kind as Target['kind'], id: row.scope_id })}>{t('修改')}</button>
            <button className="btn sm" onClick={() => setHistory({ kind: row.scope_kind as Target['kind'], id: row.scope_id })}>{t('变更历史')}</button>
          </div>
        </article>)}
      <Pager page={data.overrides} move={setOffset} busy={load.loading} />
    </div>}

    {editing && data && <PolicyEditor target={editing} overview={data} close={() => setEditing(null)}
      proceed={action => { setEditing(null); act(action); }} />}
    {history && <PolicyHistory target={history} close={() => setHistory(null)} />}
  </section>;
}

function PolicyEditor({ target, overview, close, proceed }: { target: Target; overview: Overview; close: () => void; proceed: (a: Action) => void }) {
  const { t } = useI18n();
  const labels = useLabels();
  const fields = overview.layer_fields[target.kind];
  const [current, setCurrent] = useState<Policy | null | undefined>(undefined);
  const [values, setValues] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  // The version the change is based on is the one read here; a newer one makes the save fail
  // with the current values rather than overwrite them.
  async function read() {
    setError(null); setPreview(null);
    try {
      const policy = target.kind === 'platform' ? (await get<Overview>(`${BASE}/quotas?limit=1`)).platform.policy
        : (await get<View>(`${BASE}/quotas/effective?${target.kind === 'workspace' ? 'workspace_id' : 'credential'}=${encodeURIComponent(target.id)}`)).policies[target.kind];
      setCurrent(policy);
      setValues(Object.fromEntries(fields.map(f => [f, policy?.[f] == null ? '' : String(policy[f])])));
    } catch (e) { setError(e); setCurrent(null); }
  }
  useEffect(() => { void read(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const body = () => ({ base_version: current?.version ?? 0,
    ...Object.fromEntries(fields.map(f => [f, values[f]?.trim() ? Number(values[f]) : null])) });
  const invalid = fields.some(f => { const raw = values[f]?.trim(); if (!raw) return false;
    const n = Number(raw); const r = overview.ranges[f]; return !Number.isInteger(n) || n < r.min || n > r.max; });

  async function check(event: FormEvent) {
    event.preventDefault();
    if (busy || invalid) return;
    setBusy(true); setError(null);
    try { setPreview(await post<Preview>(`${BASE}/quotas/preview/${scopePath(target)}`, body())); }
    catch (e) { setPreview(null); setError(e); }
    finally { setBusy(false); }
  }
  const stale = error instanceof ApiError && error.code === 'stale_policy';
  const title = target.kind === 'platform' ? t('修改平台策略') : target.kind === 'workspace' ? t('修改工作空间策略') : t('修改凭据策略');
  return <Modal title={title} onClose={() => { if (!busy) close(); }} size="wide"
    footer={<><button className="btn" disabled={busy} onClick={close}>{t('取消')}</button>
      {preview ? <button className="btn primary" onClick={() => {
        const payload = body();
        proceed({ title, impact: <PreviewTable preview={preview} fields={overview.fields} />,
          run: reason => put(`${BASE}/quotas/${scopePath(target)}`, { reason, ...payload }) });
      }}>{t('继续保存')}</button>
        : <button form="quota-editor" type="submit" className="btn primary" disabled={busy || invalid || current === undefined}>{busy ? t('处理中…') : t('预览影响')}</button>}</>}>
    <form id="quota-editor" className="col gap12" onSubmit={check}>
      <p className="muted">{target.kind === 'platform' ? t('平台') : <code>{target.id}</code>} · {t('版本 {n}', { n: current?.version ?? 0 })}</p>
      <p className="notice info">{t('留空表示继承上一层（凭据 → 工作空间 → 平台 → 部署默认值）。降低上限不会停止进行中的任务，只影响之后的请求与提交。')}</p>
      {fields.map(f => <label key={f} className="field">{labels.field[f]} <span className="muted">（{overview.ranges[f].min}–{overview.ranges[f].max}）</span>
        <input className="input" inputMode="numeric" value={values[f] ?? ''} disabled={busy || current === undefined}
          onChange={e => { setValues({ ...values, [f]: e.target.value }); setPreview(null); }} /></label>)}
      {invalid && <p role="alert">{t('请输入范围内的整数，或留空表示继承。')}</p>}
      <ErrorNotice error={error} retry={stale ? read : undefined} />
      {preview && <PreviewTable preview={preview} fields={overview.fields} />}
    </form>
  </Modal>;
}

function PreviewTable({ preview, fields }: { preview: Preview; fields: Field[] }) {
  const { t } = useI18n();
  const labels = useLabels();
  const cell = (e: { value: number; source: Source }) => `${e.value}（${labels.source[e.source]}）`;
  return <div className="col gap8">
    <div className="api-platform-table"><table><thead><tr><th>{t('限制')}</th><th>{t('修改前')}</th><th>{t('修改后')}</th></tr></thead>
      <tbody>{fields.map(f => <tr key={f}><td>{labels.field[f]}</td><td>{cell(preview.before[f])}</td>
        <td><strong>{cell(preview.after[f])}</strong></td></tr>)}</tbody></table></div>
    {preview.impact.active_jobs_now != null && <p>{t('当前有 {n} 个任务在排队或进行中；其中 {over} 个超出新上限，它们会继续完成，之后的提交需等待。',
      { n: preview.impact.active_jobs_now, over: preview.impact.over_new_limit ?? 0 })}</p>}
    {preview.impact.workspaces_with_own_override != null && <p>{t('{n} 个工作空间有自己的设置，不受平台修改影响。', { n: preview.impact.workspaces_with_own_override })}</p>}
    <p className="muted">{t('保存后下一次请求即生效，不需要重启。')}</p>
  </div>;
}

function PolicyHistory({ target, close }: { target: Target; close: () => void }) {
  const { t, locale } = useI18n();
  const labels = useLabels();
  const [offset, setOffset] = useState(0);
  const url = `${BASE}/quotas/history/${scopePath(target)}?limit=25&offset=${offset}`;
  const load = useLoad<Page<History>>(url, signal => get(url, signal));
  const fmt = (v: Record<Field, number | null>) => (Object.keys(labels.field) as Field[])
    .filter(f => v[f] != null).map(f => `${labels.field[f]} ${v[f]}`).join(' · ') || t('全部继承');
  return <Modal title={t('变更历史')} onClose={close} size="wide" footer={<button className="btn" onClick={close}>{t('关闭')}</button>}>
    <div className="col gap12">
      <p className="muted">{target.kind === 'platform' ? t('平台') : <code>{target.id}</code>}</p>
      <DataState loading={load.loading} error={load.error} empty={!load.data?.items.length} retry={load.reload} />
      {load.data?.items.map(row => <article key={row.id} className="api-platform-record col gap8">
        <div className="row gap8 wrap"><strong>{t('版本 {n}', { n: row.version })}</strong><span className="muted">{new Date(row.at * 1000).toLocaleString(locale)}</span><code>{row.actor_id}</code></div>
        <p>{t('修改前')} · {fmt(row.before)}</p><p>{t('修改后')} · {fmt(row.after)}</p>
        <p className="muted">{t('理由')} · {row.reason}</p>
      </article>)}
      <Pager page={load.data} move={setOffset} busy={load.loading} />
    </div>
  </Modal>;
}
