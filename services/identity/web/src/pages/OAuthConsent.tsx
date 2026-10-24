import { useEffect, useId, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { get, post } from '@synwevo/auth-client';
import { Brand, PageState, useI18n } from '@synwevo/ui';
import { SCOPE_TEXT } from './oauth/scopes';
import { errorText } from './account/common';
import { roleLabel } from '../lib/labels';
import './account/account.css';

/** One of the person's live workspaces the client could act in (provider_routes.consent_request). */
export type WorkspaceChoice = { id: string; name: string; role: string | null; consented: boolean };
export type Request = {
  id: string;
  /** `registration` is how the account service knows this client: registered by an operator, or
   *  self-described by a Client ID Metadata Document (`cimd`) whose address is its id. */
  client: { id: string; name: string; app_id: string; registration?: 'operator' | 'cimd'; client_uri?: string | null };
  scopes: string[]; already_granted: string[]; identity_scopes: string[];
  workspace: { id: string; name: string } | null;
  /** Null when the request already names the workspace; a list when the person chooses (one token, one workspace). */
  workspace_choices?: WorkspaceChoice[] | null;
  redirect_host: string; expires_at: number;
};

/** The host of an address, for naming where a self-described client publishes its document; null
 *  when there is no address or it does not parse (then nothing is named). */
export function hostOf(url: string | null | undefined): string | null {
  if (!url) return null;
  try { return new URL(url).hostname || null; } catch { return null; }
}

/** The consent step of an authorization (API-4): who is asking, for what, in which workspace, and
 *  where the answer goes. Only this page's buttons decide; the client never sees the choice before
 *  the browser is sent back to it. A client that follows only the OAuth protocol (an MCP client)
 *  cannot name a workspace, so when the person has several the page asks them to choose one, and
 *  sends that choice with the approval; a client the platform knows only from its own metadata
 *  document is shown as such, with the host it comes from and the fact that nobody verified it. */
export default function OAuthConsent() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const rid = params.get('request') || '';
  const [req, setReq] = useState<Request | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<string | null>(null);
  const hintId = useId();
  useEffect(() => { document.title = `${t('授权应用')} · Synwevo Account`; }, [t]);
  useEffect(() => {
    if (!rid) { setError(t('授权请求不存在')); return; }
    get<Request>(`/api/auth/oauth-consent/${encodeURIComponent(rid)}`).then(setReq, e => setError(errorText(e, t)));
  }, [rid, t]);
  const choices = req?.workspace_choices?.length ? req.workspace_choices : null;
  // Only an id from the offered list is ever sent; a stale selection after a re-read is none.
  const selected = choices?.some(w => w.id === chosen) ? chosen : null;
  async function decide(decision: 'approve' | 'deny') {
    if (decision === 'approve' && choices && !selected) return;
    setBusy(true); setError(null);
    try {
      const body = decision === 'approve' && selected ? { workspace_id: selected } : {};
      const out = await post<{ redirect: string }>(`/api/auth/oauth-consent/${encodeURIComponent(rid)}/${decision}`, body);
      window.location.assign(out.redirect);
    } catch (e) { setError(errorText(e, t)); setBusy(false); }
  }
  if (error && !req) return <PageState as="main"><h1>{t('无法继续授权')}</h1><p role="alert">{error}</p><a href="/account">{t('账户与安全')}</a></PageState>;
  if (!req) return <PageState as="main"><p role="status">{t('加载中…')}</p></PageState>;
  const fresh = req.scopes.filter(scope => !req.already_granted.includes(scope));
  const selfDescribed = req.client.registration === 'cimd';
  const clientHost = selfDescribed ? hostOf(req.client.client_uri) ?? hostOf(req.client.id) : null;
  const approveBlocked = !!choices && !selected;
  return <main className="consent-page col gap16">
    <Brand />
    <section className="card card-b col gap16" aria-labelledby="consent-title">
      <h1 id="consent-title">{t('「{name}」请求代表你访问 Synwevo', { name: req.client.name })}</h1>
      {selfDescribed && <div className="notice col gap4" role="note">
        <b>{t('自描述客户端')}</b>
        <span>{clientHost
          ? t('这个客户端用它自己发布在 {host} 的文档描述自己。Synwevo 读取并检查了这份文档，但没有核实是谁在运营它。', { host: clientHost })
          : t('这个客户端用它自己发布的文档描述自己。Synwevo 读取并检查了这份文档，但没有核实是谁在运营它。')}</span>
      </div>}
      {choices ? <fieldset className="col gap8" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <legend className="secttl">{t('请选择这个客户端可以使用的工作空间')}</legend>
        <p className="tiny muted">{t('它得到的令牌只在你选择的工作空间内有效；换一个工作空间需要再授权一次。')}</p>
        <div className="picker">
          {choices.map(w => <label key={w.id}>
            <input type="radio" name="consent-workspace" value={w.id} required checked={selected === w.id} disabled={busy} onChange={() => setChosen(w.id)} />
            <span>{w.name}<span className="dim"> {t(roleLabel(w.role))}</span>{w.consented && <> <span className="badge">{t('已授权过')}</span></>}</span>
          </label>)}
        </div>
      </fieldset>
        : <p className="muted">{t('工作空间')} · <strong>{req.workspace?.name || t('未指定')}</strong></p>}
      <div className="col gap8"><h2 className="secttl">{t('它将可以')}</h2>
        <ul className="col gap8">{req.scopes.map(scope => <li key={scope}>{SCOPE_TEXT[scope] ? t(SCOPE_TEXT[scope]) : scope} <code className="tiny">{scope}</code>
          {req.already_granted.includes(scope) && <span className="badge">{t('已授权过')}</span>}</li>)}</ul>
        {req.identity_scopes.length > 0 && <p className="tiny muted">{t('并且')}{req.identity_scopes.map(s => SCOPE_TEXT[s] ? t(SCOPE_TEXT[s]) : s).join(t('、'))}</p>}
      </div>
      <p className="notice info">{t('批准后你会回到 {host}。你可以随时在「账户与安全 → 已授权的应用」中撤回，撤回会立即让它的令牌失效。', { host: req.redirect_host })}</p>
      {fresh.length > 0 && req.already_granted.length > 0 && <p className="tiny muted">{t('其中新的权限：{list}', { list: fresh.join(', ') })}</p>}
      {error && <p className="notice err" role="alert">{error}</p>}
      <div className="row gap8 wrap">
        <button className="btn primary" disabled={busy || approveBlocked} aria-describedby={approveBlocked ? hintId : undefined}
          onClick={() => void decide('approve')}>{busy ? t('处理中…') : t('批准')}</button>
        <button className="btn" disabled={busy} onClick={() => void decide('deny')}>{t('拒绝')}</button>
        {approveBlocked && <span className="tiny muted" id={hintId}>{t('请先选择一个工作空间')}</span>}
      </div>
    </section>
  </main>;
}
