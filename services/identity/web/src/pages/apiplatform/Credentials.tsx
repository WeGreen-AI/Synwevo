import { useState } from 'react';
import { post } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { BASE, DataState, Pager, usePage, type Action } from './common';
import { useDebounced } from '../../lib/hooks';

type Credential = { kind: string; id: string; name: string; workspace_id: string; scopes: string; created_at: number;
  expires_at: number | null; disabled_at: number | null; approval_id: string | null; status: string; approval_error?: string;
  approval_expires_at?: number; last_used_at: number | null };

/** The overlap in which the old secret keeps working after a rotation, chosen per rotation and
 *  bounded by the server (24 hours). Zero ends the old secret at once. */
function Overlap({ onChange }: { onChange: (seconds: number) => void }) {
  const { t } = useI18n();
  const [value, setValue] = useState(0);
  const options: [number, string][] = [[0, t('立即失效')], [600, t('10 分钟')], [3600, t('1 小时')], [86400, t('24 小时')]];
  return <label className="field">{t('旧密钥继续有效的时间')}
    <select className="input" value={value} onChange={e => { const v = Number(e.target.value); setValue(v); onChange(v); }}>
      {options.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select></label>;
}

export function Credentials({ act, revision }: { act: (a: Action) => void; revision: number }) {
  const { t, locale } = useI18n();
  const [workspace, setWorkspace] = useState('');
  const query = useDebounced(workspace);
  const load = usePage<Credential>(`${BASE}/credentials?workspace_id=${encodeURIComponent(query)}&revision=${revision}`);
  // `rotated` is what the server reports for a key replaced by rotation (synwevo_core.auth.key_status).
  const status: Record<string, string> = { active: t('有效'), disabled: t('已停用'), revoked: t('已撤销'),
    expired: t('已过期'), grace: t('轮换重叠期'), rotated: t('已被替换'), approval_inactive: t('审批当前无效') };
  const action = (row: Credential, operation: string, title: string) => {
    const overlap = { seconds: 0 };
    act({ title,
      impact: <div className="col gap8">{operation === 'rotate'
        ? <><span>{t('新密钥只显示一次。重叠期内新旧密钥都可调用，到期后旧密钥失效；选择「立即失效」会马上停止旧密钥。')}</span><Overlap onChange={v => { overlap.seconds = v; }} /></>
        : <span>{t('操作将影响此凭据及其现有集成。')}</span>} <code>{row.id}</code></div>,
      run: reason => post(`${BASE}/credentials/${row.kind}/${row.id}/${operation}`,
        operation === 'rotate' ? { reason, grace_seconds: overlap.seconds } : { reason }) });
  };
  return <section className="card card-b col gap12"><h2>{t('服务凭据')}</h2>
    <label className="field">{t('按工作空间 ID 筛选')}<input className="input" value={workspace} maxLength={100} onChange={e => setWorkspace(e.target.value)} /></label>
    <p className="notice info">{t('历史凭据按原有授权继续使用；没有绑定审批的历史凭据不能在这里轮换。')}</p>
    <DataState loading={load.loading} error={load.error} empty={!load.data?.items.length} retry={load.reload} />
    {!load.loading && !load.error && load.data?.items.map(row => <article key={`${row.kind}:${row.id}`} className="api-platform-record col gap8">
      <div className="row gap8 wrap"><strong>{row.name}</strong><span className="chip">{row.kind === 'api_key' ? t('访问密钥') : t('服务客户端')}</span>
        <span className="chip">{status[row.status] || row.status}</span></div>
      <code>{row.id} · {row.workspace_id}</code><code>{row.scopes}</code>
      <p>{row.approval_id ? `${t('审批记录')} · ${row.approval_id}` : t('历史凭据')}</p>
      {(row.expires_at || row.approval_expires_at) && <p>{t('到期时间')} · {new Date((row.expires_at || row.approval_expires_at!) * 1000).toLocaleString(locale)}</p>}
      <p className="muted">{t('最近使用')} · {row.last_used_at ? new Date(row.last_used_at * 1000).toLocaleString(locale) : t('暂无使用记录')}</p>
      {row.approval_error && <p role="status">{t('审批当前无效')} · <code>{row.approval_error}</code></p>}
      <div className="row gap8 wrap">
        {row.status === 'active' && row.approval_id && !row.approval_error && <button className="btn" onClick={() => action(row, 'rotate', t('轮换密钥'))}>{t('轮换密钥')}</button>}
        {!row.disabled_at && <button className="btn danger" onClick={() => action(row, 'disable', t('停用凭据'))}>{t('停用凭据')}</button>}
        {row.kind === 'service_account' && !row.disabled_at && <button className="btn" onClick={() => action(row, 'revoke-tokens', t('撤销访问令牌'))}>{t('撤销访问令牌')}</button>}
        {row.kind === 'service_account' && !!row.disabled_at && row.approval_id && !row.approval_error && <button className="btn" onClick={() => action(row, 'enable', t('重新启用'))}>{t('重新启用')}</button>}
      </div>
    </article>)}
    <Pager page={load.data} move={load.move} busy={load.loading} />
  </section>;
}
