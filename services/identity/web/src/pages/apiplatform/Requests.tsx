import { useState } from 'react';
import { useI18n } from '@synwevo/ui';
import { requestMachine, type Action, type Outcome } from './common';

export function Requests({ act, webhooks }: { act: (action: Action) => void; webhooks: boolean }) {
  const { t } = useI18n();
  const [credential, setCredential] = useState('');
  const [path, setPath] = useState('/api/v1/whoami');
  const [query, setQuery] = useState('{}');
  const [url, setUrl] = useState('');
  const [events, setEvents] = useState('job.completed,job.failed');
  const [description, setDescription] = useState('');
  const [result, setResult] = useState<Outcome | null>(null);
  const [cursor, setCursor] = useState('');
  const [selectedSid, setSelectedSid] = useState('');
  const [feed, setFeed] = useState('');
  const run = (title: string, method: string, target: string, query = {}, body?: unknown) => act({ title,
    impact: <>{t('此请求使用你明确提供的服务凭据。管理会话不会赋予业务数据访问权限。')} <code>{method} {target}</code></>,
    run: async reason => {
      const out = await requestMachine(credential, reason, method, target, query, body);
      if (!out.secret) setResult(out); else setResult(null);
      setCursor(typeof out.next_cursor === 'string' ? out.next_cursor : '');
      return out;
    } });
  const items = Array.isArray(result?.items) ? result.items as Record<string, unknown>[] : [];
  const subscription = (s: Record<string, unknown>, op: string, title: string) => {
    setFeed(''); run(title, 'POST', `/api/v1/webhooks/subscriptions/${s.id}/${op}`, {}, op === 'rotate-secret' ? { overlap_s: 0 } : undefined);
  };
  return <section className="card card-b col gap12">
    <h2>{webhooks ? t('Webhook 订阅与投递') : t('受限请求调试')}</h2>
    <p className="notice info">{t('服务凭据仅保留在当前页面内存中，切换页面会清除。不要输入管理员口令或浏览器 Cookie。')}</p>
    <label className="field">{t('服务访问令牌或访问密钥')}<input type="password" className="input" autoComplete="off" spellCheck={false} value={credential}
      onChange={e => { setCredential(e.target.value); setResult(null); setCursor(''); }} /></label>
    {webhooks ? <>
      <div className="row gap8 wrap">
        <button className="btn" disabled={!credential} onClick={() => { setFeed('subscriptions'); run(t('查看订阅'), 'GET', '/api/v1/webhooks/subscriptions'); }}>{t('查看订阅')}</button>
        <button className="btn" disabled={!credential} onClick={() => { setFeed('events'); run(t('查看事件'), 'GET', '/api/v1/events', { limit: 25 }); }}>{t('查看事件')}</button>
      </div>
      <details><summary>{t('创建订阅')}</summary><div className="col gap12" style={{ marginTop: 12 }}>
        <label className="field">{t('回调地址')}<input className="input" type="url" value={url} maxLength={2048} onChange={e => setUrl(e.target.value)} /></label>
        <label className="field">{t('事件类型（逗号分隔）')}<input className="input" value={events} maxLength={500} onChange={e => setEvents(e.target.value)} /></label>
        <label className="field">{t('订阅说明')}<input className="input" value={description} maxLength={500} onChange={e => setDescription(e.target.value)} /></label>
        <button className="btn primary" disabled={!credential || !url.trim() || !events.trim()} onClick={() => { setFeed(''); run(t('创建订阅'), 'POST', '/api/v1/webhooks/subscriptions', {}, { url, description, events: events.split(',').map(s => s.trim()).filter(Boolean) }); }}>{t('创建订阅')}</button>
      </div></details>
      {feed === 'subscriptions' && items.map(s => <article className="api-platform-record col gap8" key={String(s.id)}>
        <strong>{String(s.description || s.id)}</strong><code>{String(s.url)}</code><code>{String(s.status)} · {String(s.id)}</code>
        <div className="row gap8 wrap">
          <button className="btn" onClick={() => { setSelectedSid(String(s.id)); setFeed('deliveries'); run(t('查看投递'), 'GET', `/api/v1/webhooks/subscriptions/${s.id}/deliveries`, { limit: 25 }); }}>{t('查看投递')}</button>
          <button className="btn" onClick={() => subscription(s, 'pause', t('暂停订阅'))}>{t('暂停订阅')}</button>
          <button className="btn" onClick={() => subscription(s, 'resume', t('恢复订阅'))}>{t('恢复订阅')}</button>
          <button className="btn" onClick={() => subscription(s, 'rotate-secret', t('轮换签名密钥'))}>{t('轮换签名密钥')}</button>
          <button className="btn danger" onClick={() => { setFeed(''); run(t('删除订阅'), 'DELETE', `/api/v1/webhooks/subscriptions/${s.id}`); }}>{t('删除订阅')}</button>
        </div>
        <details><summary>{t('修改订阅')}</summary><WebhookEdit item={s} save={body => { setFeed(''); run(t('修改订阅'), 'PATCH', `/api/v1/webhooks/subscriptions/${s.id}`, {}, body); }} /></details>
      </article>)}
      {feed === 'deliveries' && items.map(item => <article className="api-platform-record row gap8 wrap" key={String(item.id)}>
        <code>{String(item.id)}</code><span>{String(item.status)}</span><span>{t('尝试次数')} · {String(item.attempts)}</span><span>{String(item.last_code ?? '')}</span>
        <button className="btn" onClick={() => { setFeed(''); run(t('重新投递'), 'POST', `/api/v1/webhooks/subscriptions/${selectedSid}/deliveries/${item.id}/redeliver`); }}>{t('重新投递')}</button>
      </article>)}
      {cursor && <button className="btn" onClick={() => run(t('下一页'), 'GET', feed === 'events' ? '/api/v1/events' : `/api/v1/webhooks/subscriptions/${selectedSid}/deliveries`, { limit: 25, cursor })}>{t('下一页')}</button>}
    </> : <>
      <p className="muted">{t('调试只接受允许的只读机器接口，不接受任意 URL、本地路径、文件下载或 Cookie。')}</p>
      <label className="field">{t('接口路径')}<input className="input" value={path} maxLength={300} onChange={e => { setPath(e.target.value); setResult(null); }} /></label>
      <label className="field">{t('查询参数（JSON）')}<textarea className="input" value={query} maxLength={4000} onChange={e => setQuery(e.target.value)} spellCheck={false} /></label>
      <button className="btn primary" disabled={!credential || !path} onClick={() => act({ title: t('执行只读请求'), impact: <>{t('请求结果仅由此服务凭据的实时权限决定。')} <code>{path}</code></>,
        run: async reason => { const out = await requestMachine(credential, reason, 'GET', path, JSON.parse(query)); setResult(out); return {}; } })}>{t('执行只读请求')}</button>
    </>}
    {result && <><h3>{t('实际响应')}</h3><pre className="api-platform-json" tabIndex={0}>{JSON.stringify(result, null, 2)}</pre></>}
  </section>;
}

function WebhookEdit({ item, save }: { item: Record<string, unknown>; save: (body: unknown) => void }) {
  const { t } = useI18n();
  const [url, setUrl] = useState(String(item.url));
  const [description, setDescription] = useState(String(item.description || ''));
  const [events, setEvents] = useState(Array.isArray(item.events) ? item.events.join(',') : '');
  return <div className="col gap8">
    <label className="field">{t('回调地址')}<input className="input" type="url" value={url} onChange={e => setUrl(e.target.value)} /></label>
    <label className="field">{t('订阅说明')}<input className="input" value={description} onChange={e => setDescription(e.target.value)} /></label>
    <label className="field">{t('事件类型（逗号分隔）')}<input className="input" value={events} onChange={e => setEvents(e.target.value)} /></label>
    <button className="btn" onClick={() => save({ version: item.version, url, description, events: events.split(',').map(x => x.trim()).filter(Boolean) })}>{t('保存修改')}</button>
  </div>;
}
