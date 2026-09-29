import { useCallback, useEffect, useState } from 'react';
import { get, post } from '@synwevo/auth-client';
import { useDialog, useI18n, useToast } from '@synwevo/ui';
import { SCOPE_TEXT } from '../oauth/scopes';
import { ErrorNotice, errorText, timeText } from './common';

type Connected = { id: string; client: { id: string; name: string | null; app_id: string | null };
  workspace: { id: string; name: string | null } | null; scopes: string[]; source: 'consent' | 'legacy';
  granted_at: number; last_issued_at: number | null };

/** Integrations this person let act for them (API-4): what each may do, where, since when — and a
 *  way to take it back that revokes the integration's tokens at once. */
export default function ConnectedAppsSection({ timezone }: { timezone: string }) {
  const { t, locale } = useI18n();
  const { confirm } = useDialog();
  const toast = useToast();
  const [items, setItems] = useState<Connected[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    setError(null);
    try { setItems((await get<{ items: Connected[] }>('/api/auth/connected-apps')).items); }
    catch (e) { setError(errorText(e, t)); }
  }, [t]);
  useEffect(() => { void load(); }, [load]);
  async function revoke(row: Connected) {
    const name = row.client.name || row.client.id;
    if (!await confirm(t('撤回「{name}」的授权', { name }), t('它的访问令牌立即失效，之后再使用需要你重新同意。已经交给它的数据无法收回。'), t('撤回授权'), true)) return;
    setBusy(row.id); setError(null);
    try {
      await post(`/api/auth/connected-apps/${row.id}/revoke`, {});
      toast(t('已撤回「{name}」的授权', { name }), 'ok');
      await load();
    } catch (e) { setError(errorText(e, t)); } finally { setBusy(null); }
  }
  return <section id="connected-apps" className="card card-b col gap12 account-section">
    <h2 className="secttl">{t('已授权的应用')}</h2>
    <p className="muted">{t('你同意代表你访问 Synwevo 的集成。登录不等于授权：每个应用在第一次请求这些权限时都会先问你。')}</p>
    <ErrorNotice error={error} retry={load} />
    {items === null && !error && <p role="status">{t('加载中…')}</p>}
    {items?.length === 0 && <p className="muted">{t('你还没有授权任何应用。')}</p>}
    {items?.map(row => <article key={row.id} className="col gap8 connected-app">
      <div className="row gap8 wrap"><strong>{row.client.name || row.client.id}</strong>
        {row.source === 'legacy' && <span className="chip" title={t('这个授权早于同意页面，当时没有单独询问你。')}>{t('旧授权')}</span>}</div>
      <span className="tiny muted">{t('工作空间')} · {row.workspace?.name || row.workspace?.id || t('未指定')}
        {' · '}{t('授权于 {date}', { date: timeText(row.granted_at, locale, timezone) })}
        {row.last_issued_at ? <>{' · '}{t('最近一次取得令牌 {date}', { date: timeText(row.last_issued_at, locale, timezone) })}</> : null}</span>
      <ul className="col gap4">{row.scopes.map(scope => <li key={scope}>{SCOPE_TEXT[scope] ? t(SCOPE_TEXT[scope]) : scope} <code className="tiny">{scope}</code></li>)}</ul>
      <div><button className="btn danger" disabled={busy === row.id} onClick={() => void revoke(row)}>{busy === row.id ? t('处理中…') : t('撤回授权')}</button></div>
    </article>)}
  </section>;
}
