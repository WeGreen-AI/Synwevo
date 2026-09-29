import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { get, post } from '@synwevo/auth-client';
import { Brand, PageState, useI18n } from '@synwevo/ui';
import { SCOPE_TEXT } from './oauth/scopes';
import { errorText } from './account/common';
import './account/account.css';

type Request = { id: string; client: { id: string; name: string; app_id: string }; scopes: string[];
  already_granted: string[]; identity_scopes: string[]; workspace: { id: string; name: string } | null;
  redirect_host: string; expires_at: number };

/** The consent step of an authorization (API-4): who is asking, for what, in which workspace, and
 *  where the answer goes. Only this page's buttons decide; the client never sees the choice before
 *  the browser is sent back to it. */
export default function OAuthConsent() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const rid = params.get('request') || '';
  const [req, setReq] = useState<Request | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { document.title = `${t('授权应用')} · Synwevo Account`; }, [t]);
  useEffect(() => {
    if (!rid) { setError(t('授权请求不存在')); return; }
    get<Request>(`/api/auth/oauth-consent/${encodeURIComponent(rid)}`).then(setReq, e => setError(errorText(e, t)));
  }, [rid, t]);
  async function decide(decision: 'approve' | 'deny') {
    setBusy(true); setError(null);
    try {
      const out = await post<{ redirect: string }>(`/api/auth/oauth-consent/${encodeURIComponent(rid)}/${decision}`, {});
      window.location.assign(out.redirect);
    } catch (e) { setError(errorText(e, t)); setBusy(false); }
  }
  if (error && !req) return <PageState as="main"><h1>{t('无法继续授权')}</h1><p role="alert">{error}</p><a href="/account">{t('账户与安全')}</a></PageState>;
  if (!req) return <PageState as="main"><p role="status">{t('加载中…')}</p></PageState>;
  const fresh = req.scopes.filter(scope => !req.already_granted.includes(scope));
  return <main className="consent-page col gap16">
    <Brand />
    <section className="card card-b col gap16" aria-labelledby="consent-title">
      <h1 id="consent-title">{t('「{name}」请求代表你访问 Synwevo', { name: req.client.name })}</h1>
      <p className="muted">{t('工作空间')} · <strong>{req.workspace?.name || t('未指定')}</strong></p>
      <div className="col gap8"><h2 className="secttl">{t('它将可以')}</h2>
        <ul className="col gap8">{req.scopes.map(scope => <li key={scope}>{SCOPE_TEXT[scope] ? t(SCOPE_TEXT[scope]) : scope} <code className="tiny">{scope}</code>
          {req.already_granted.includes(scope) && <span className="chip">{t('已授权过')}</span>}</li>)}</ul>
        {req.identity_scopes.length > 0 && <p className="tiny muted">{t('并且')}{req.identity_scopes.map(s => SCOPE_TEXT[s] ? t(SCOPE_TEXT[s]) : s).join(t('、'))}</p>}
      </div>
      <p className="notice info">{t('批准后你会回到 {host}。你可以随时在「账户与安全 → 已授权的应用」中撤回，撤回会立即让它的令牌失效。', { host: req.redirect_host })}</p>
      {fresh.length > 0 && req.already_granted.length > 0 && <p className="tiny muted">{t('其中新的权限：{list}', { list: fresh.join(', ') })}</p>}
      {error && <p className="notice err" role="alert">{error}</p>}
      <div className="row gap8 wrap">
        <button className="btn primary" disabled={busy} onClick={() => void decide('approve')}>{busy ? t('处理中…') : t('批准')}</button>
        <button className="btn" disabled={busy} onClick={() => void decide('deny')}>{t('拒绝')}</button>
      </div>
    </section>
  </main>;
}
