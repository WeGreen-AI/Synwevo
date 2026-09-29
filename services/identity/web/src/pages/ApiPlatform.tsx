import { useEffect, useState } from 'react';
import { get, useSession } from '@synwevo/auth-client';
import { PageState, useI18n } from '@synwevo/ui';
import { Topbar } from '../components/Topbar';
import AdminStepUp from '../components/AdminStepUp';
import { useLoad } from '../lib/hooks';
import { ApprovalsPanel } from './apiplatform/Approvals';
import { Credentials } from './apiplatform/Credentials';
import { Requests } from './apiplatform/Requests';
import { Calls, Operations } from './apiplatform/Observability';
import { Quotas } from './apiplatform/Quotas';
import { WebhookOps } from './apiplatform/WebhookOps';
import { BASE, Confirm, DataState, Secret, type Action, type Outcome } from './apiplatform/common';
import './api-platform.messages';
import './apiplatform/api-platform.css';

type Overview = { permission: string; adapter_available: boolean; resource: string; scopes: string[];
  pending_approvals: number; calls: { calls: number; errors: number; mean_duration_ms: number | null };
  webhooks: { pending: number; failing: number } | null;
  sdk_links: { name: string; url: string }[] };

export default function ApiPlatform() {
  const { t } = useI18n();
  const { me } = useSession();
  const [tab, setTab] = useState('approvals');
  const [revision, setRevision] = useState(0);
  const [action, setAction] = useState<Action | null>(null);
  const [secret, setSecret] = useState<Outcome | null>(null);
  const allowed = me?.platform_role === 'admin';
  const key = allowed ? `${BASE}/overview?revision=${revision}` : null;
  const load = useLoad<Overview>(key, signal => get(key!, signal));
  useEffect(() => { document.title = `${t('API 管理平台')} · Synwevo Account`; }, [t]);
  if (!allowed) return <PageState as="main"><h1>{t('页面不存在')}</h1><a href="/account">{t('账户与安全')}</a></PageState>;
  if (me?.admin_locked) return <div className="app"><Topbar title={t('API 管理平台')} /><main className="api-platform-main"><div className="acct-page col gap16"><AdminStepUp compact /></div></main></div>;
  // The outcome of an action is the refreshed list (and, for a new secret, the one-time dialog);
  // no generic "done" banner on top of it.
  const done = (out: Outcome) => {
    setAction(null); setRevision(n => n + 1);
    if (out.key || out.client_secret || out.secret) setSecret(out);
  };
  const act = (action: Action) => setAction(action);
  const tabs: [string, string][] = [['approvals', t('授权审批')], ['credentials', t('服务凭据')],
    ['quotas', t('流量与配额')], ['webhooks', t('Webhook')], ['operations', t('接口契约')], ['calls', t('调用记录')], ['debug', t('请求调试')]];
  return <div className="app">
    <Topbar title={t('API 管理平台')} />
    <main className="api-platform-main"><div className="acct-page col gap16">
      <p className="notice info">{t('管理权限不授予文档数据访问；凭据须经工作空间所有者批准。')}</p>
      <DataState loading={load.loading} error={load.error} empty={false} retry={load.reload} />
      {load.data && !load.error && <>
        <section className="api-platform-grid" aria-label={t('真实调用统计')}>
          <Stat label={t('最近 30 天调用')} value={t('{count} 次', { count: load.data.calls.calls })} />
          <Stat label={t('失败调用')} value={t('{count} 次', { count: load.data.calls.errors })} />
          <Stat label={t('平均耗时')} value={load.data.calls.mean_duration_ms === null ? t('暂无调用') : t('{count} 毫秒', { count: Math.round(load.data.calls.mean_duration_ms) })} />
          <Stat label={t('待审批')} value={t('{count} 条', { count: load.data.pending_approvals })} />
          {load.data.webhooks && <Stat label={t('事件积压')} value={t('{count} 次投递', { count: load.data.webhooks.pending })} />}
          {load.data.webhooks && <Stat label={t('持续失败的订阅')} value={t('{count} 个', { count: load.data.webhooks.failing })} />}
        </section>
        {!load.data.adapter_available && <p className="notice warn">{t('此部署未连接 Synwevo Docs，接口契约、Webhook 和请求调试暂不可用。')}</p>}
        <nav className="row gap8 wrap" aria-label={t('API 管理导航')}>{tabs.map(([id, label]) => <button key={id} className={`btn ${tab === id ? 'primary' : ''}`} aria-current={tab === id ? 'page' : undefined}
          onClick={() => setTab(id)}>{label}</button>)}</nav>
        {tab === 'approvals' && <ApprovalsPanel act={act} scopes={load.data.scopes} resource={load.data.resource} revision={revision} />}
        {tab === 'credentials' && <Credentials act={act} revision={revision} />}
        {tab === 'quotas' && <Quotas act={act} revision={revision} />}
        {tab === 'webhooks' && <><WebhookOps act={act} revision={revision} /><Requests key="webhooks" act={act} webhooks /></>}
        {tab === 'operations' && <Operations />}
        {tab === 'calls' && <Calls />}
        {tab === 'debug' && <Requests key="debug" act={act} webhooks={false} />}
        <footer className="row gap12 wrap"><strong>{t('SDK 与集成文档')}</strong>{load.data.sdk_links.map(link => <a key={link.url} href={link.url} target="_blank" rel="noreferrer">{t(link.name)}</a>)}</footer>
      </>}
    </div></main>
    {action && <Confirm action={action} close={() => setAction(null)} done={done} />}
    {secret && <Secret result={secret} close={() => setSecret(null)} />}
  </div>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="card card-b col gap8"><span className="muted">{label}</span><strong style={{ fontSize: 24 }}>{value}</strong></div>;
}

/** Owner consent is account UI, never a route into the administrator console. */
export function OwnerApiApprovals() {
  const { t } = useI18n();
  const [revision, setRevision] = useState(0);
  const [action, setAction] = useState<Action | null>(null);
  useEffect(() => { document.title = `${t('工作空间授权审批')} · Synwevo Account`; }, [t]);
  return <div className="app"><Topbar title={t('工作空间授权审批')} />
    <main className="api-platform-main"><div className="acct-page"><ApprovalsPanel owner act={setAction} scopes={[]} resource="" revision={revision} /></div></main>
    {action && <Confirm action={action} close={() => setAction(null)} done={() => { setAction(null); setRevision(n => n + 1); }} />}
  </div>;
}
