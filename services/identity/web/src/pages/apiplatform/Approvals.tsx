import { useState } from 'react';
import { post } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { BASE, DataState, Pager, usePage, type Action, type Approval } from './common';

export function ApprovalsPanel({ act, scopes, resource, revision, owner = false }: {
  act: (action: Action) => void; scopes: string[]; resource: string; revision: number; owner?: boolean;
}) {
  const { t, locale } = useI18n();
  const [name, setName] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [kind, setKind] = useState('service_account');
  const [selected, setSelected] = useState<string[]>([]);
  const [days, setDays] = useState(30);
  const [status, setStatus] = useState('');
  const source = owner ? '/api/auth/api-approvals' : BASE + '/approvals';
  const load = usePage<Approval>(`${source}?status=${status}&revision=${revision}`);
  const labels: Record<string, string> = { pending: t('待审批'), approved: t('已批准'), rejected: t('已拒绝'), revoked: t('已撤回') };
  return <div className="col gap16">
    {!owner && <section className="card card-b col gap12">
      <h2>{t('申请工作空间所有者授权')}</h2>
      <p className="muted">{t('审批只覆盖指定工作空间与 API 资源，有效期结束或所有者资格失效会停止凭据使用。')}</p>
      <div className="api-platform-grid">
        <label className="field">{t('凭据名称')}<input className="input" maxLength={100} value={name} onChange={e => setName(e.target.value)} /></label>
        <label className="field">{t('工作空间 ID')}<input className="input" maxLength={100} value={workspace} onChange={e => setWorkspace(e.target.value)} /></label>
        <label className="field">{t('凭据类型')}<select className="input" value={kind} onChange={e => setKind(e.target.value)}><option value="service_account">{t('服务客户端')}</option><option value="api_key">{t('访问密钥')}</option></select></label>
        <label className="field">{t('有效天数')}<input className="input" type="number" min={1} max={365} value={days} onChange={e => setDays(Number(e.target.value))} /></label>
      </div>
      <fieldset><legend>{t('申请权限')}</legend><div className="row gap12 wrap">{scopes.map(scope => <label key={scope}><input type="checkbox" checked={selected.includes(scope)} onChange={e => setSelected(e.target.checked ? [...selected, scope] : selected.filter(x => x !== scope))} /> <code>{scope}</code></label>)}</div></fieldset>
      <p className="muted">{t('API 资源')} · <code>{resource}</code></p>
      <p className="muted">{t('访问密钥的管理权限包含读写权限；申请管理权限时必须选择全部权限。')}</p>
      <button className="btn primary" disabled={!name.trim() || !workspace.trim() || !selected.length || !Number.isInteger(days) || days < 1 || days > 365}
        onClick={() => act({ title: t('提交审批申请'), impact: t('所有者需要在账户的授权审批页明确批准，管理员才能签发凭据。'),
          run: reason => post(BASE + '/approvals', { reason, name: name.trim(), workspace_id: workspace.trim(), credential_kind: kind, scopes: selected, resources: [resource], expires_in_days: days }) })}>{t('提交审批申请')}</button>
    </section>}
    <section className="card card-b col gap12">
      <div className="row gap12 wrap"><h2>{owner ? t('工作空间授权审批') : t('审批记录')}</h2><span className="spacer" />
        {!owner && <label className="field">{t('状态')}<select className="input" value={status} onChange={e => setStatus(e.target.value)}><option value="">{t('全部')}</option>{Object.entries(labels).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>}
        <button className="btn" onClick={load.refresh}>{t('刷新')}</button></div>
      {owner && <p className="notice info">{t('你只在此批准或撤回自己的工作空间授权。平台控制台仍仅向平台管理员开放。')}</p>}
      <DataState loading={load.loading} error={load.error} empty={!load.data?.items.length} retry={load.reload} />
      {!load.loading && !load.error && load.data?.items.map(row => <ApprovalRow key={row.id} row={row} owner={owner} act={act} locale={locale} labels={labels} />)}
      <Pager page={load.data} move={load.move} busy={load.loading} />
    </section>
  </div>;
}

function ApprovalRow({ row, owner, act, locale, labels }: { row: Approval; owner: boolean; act: (action: Action) => void; locale: string; labels: Record<string, string> }) {
  const { t } = useI18n();
  const [selected, setSelected] = useState(row.requested_scopes);
  const live = row.expires_at > Date.now() / 1000;
  const base = owner ? '/api/auth/api-approvals' : BASE + '/approvals';
  return <article className="api-platform-record col gap8">
    <div className="row gap8 wrap"><strong>{row.name}</strong><span className="chip">{labels[row.status] || row.status}</span>
      {!live && <span className="chip">{t('已过期')}</span>}{row.consumed_at && <span className="chip">{t('已签发')}</span>}</div>
    <code>{row.id} · {row.workspace_id}</code>
    <p>{t('API 资源')} · <code>{row.requested_resources.join(', ')}</code></p>
    <p>{t('到期时间')} · {new Date(row.expires_at * 1000).toLocaleString(locale)}</p>
    <p>{row.reason}</p>
    <div className="row gap8 wrap">{row.requested_scopes.map(scope => owner && row.status === 'pending' ?
      <label key={scope}><input type="checkbox" checked={selected.includes(scope)} onChange={e => setSelected(e.target.checked ? [...selected, scope] : selected.filter(x => x !== scope))} /> <code>{scope}</code></label> : <code className="chip" key={scope}>{scope}</code>)}</div>
    {row.approved_scopes.length > 0 && <p>{t('批准权限')} · <code>{row.approved_scopes.join(', ')}</code></p>}
    <div className="row gap8 wrap">
      {owner && row.status === 'pending' && live && <>
        <button className="btn primary" disabled={!selected.length} onClick={() => act({ title: t('批准授权'), impact: <>{t('你批准的权限将允许管理员签发绑定此工作空间的机器凭据。')} <code>{row.workspace_id}</code></>, run: reason => post(`${base}/${row.id}/approve`, { reason, scopes: selected, resources: row.requested_resources }) })}>{t('批准授权')}</button>
        <button className="btn" onClick={() => act({ title: t('拒绝申请'), impact: row.name, run: reason => post(`${base}/${row.id}/reject`, { reason }) })}>{t('拒绝申请')}</button>
      </>}
      {!owner && row.status === 'approved' && !row.consumed_at && live && <button className="btn primary" onClick={() => act({ title: t('签发凭据'), impact: t('审批仅可使用一次。返回的密钥只显示一次，请准备安全保存。'), run: reason => post(`${base}/${row.id}/issue`, { reason }) })}>{t('签发凭据')}</button>}
      {['pending', 'approved'].includes(row.status) && <button className="btn danger" onClick={() => act({ title: t('撤回授权'), impact: t('撤回会立即停止已绑定凭据及其延迟任务，无法恢复该审批。'), run: reason => post(`${base}/${row.id}/revoke`, { reason }) })}>{t('撤回授权')}</button>}
    </div>
  </article>;
}
