/** /agent/integrations/new — three steps: who owns the connection and where it is, how it
 *  authenticates, then create it (one idempotency key per attempt), test it and show what the
 *  server really offered. Secrets live in this form's state only until the create succeeds. */
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useSession } from '@synwevo/auth-client';
import { Loading } from '../../../shared/states';
import { integrationCopy, type IntegrationText } from './integrations.messages';
import { manageableWorkspaces, safeAuthorizationUrl, type AuthKind, type ConnectionDetail, type ConnectionSummary, type OAuthStart, type Policy, type SessionWorkspace } from './model';
import { ErrorNotice, HealthBadge, OwnerText, useRead, useText, useWrite, WriteFeedback } from './shared';

const STEPS: IntegrationText[] = ['step1', 'step2', 'step3'];
const AUTH_TEXT: Record<AuthKind, IntegrationText> = { none: 'authNone', bearer: 'authBearer', oauth: 'authOAuth' };

export function validUrl(value: string): boolean {
  const text = value.trim();
  if (!text || /\s/.test(text) || text.length > 2048) return false;
  try {
    const url = new URL(text);
    return (url.protocol === 'https:' || url.protocol === 'http:') && !!url.hostname && !url.hash && !url.username && !url.password;
  } catch { return false; }
}

export function Stepper({ step }: { step: number }) {
  const { m } = useText();
  return <ol className="mi-steps" aria-label={m('stepOf', { n: step })}>
    {STEPS.map((key, i) => <li key={key} className={i + 1 === step ? 'on' : i + 1 < step ? 'done' : ''} aria-current={i + 1 === step ? 'step' : undefined}>
      <span className="mi-step-n" aria-hidden="true">{i + 1}</span><span>{m(key)}</span>
    </li>)}
  </ol>;
}

/** What a successful test discovered: server, protocol, counts. */
export function DiscoverySummary({ detail }: { detail: ConnectionDetail | ConnectionSummary }) {
  const { m } = useText();
  const name = detail.server_info?.title || detail.server_info?.name || m('serverUnnamed');
  const version = detail.server_info?.version ? `${name} ${detail.server_info.version}` : name;
  return <div className="notice ok col" role="status">
    <p><strong>{m('testOk')}</strong> · <HealthBadge health={detail.health} /></p>
    <p>{m('serverLine', { name: version, version: detail.protocol_version ?? '—' })}</p>
    <p>{m('toolsCount', { n: detail.counts.tools })} · {m('resourcesCount', { n: detail.counts.resources })} · {m('promptsCount', { n: detail.counts.prompts })}</p>
  </div>;
}

/** The form itself; `workspaces` are the ones the person administers (from the session). */
export function NewConnectionForm({ workspaces }: { workspaces: SessionWorkspace[] }) {
  const { m } = useText();
  const create = useWrite();
  const test = useWrite();
  const oauth = useWrite();
  const [step, setStep] = useState(1);
  const [ownerKind, setOwnerKind] = useState<'user' | 'workspace'>('user');
  const [workspaceId, setWorkspaceId] = useState(workspaces[0]?.id ?? '');
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [authKind, setAuthKind] = useState<AuthKind>('none');
  const [token, setToken] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [created, setCreated] = useState<ConnectionSummary | null>(null);
  const [tested, setTested] = useState<ConnectionDetail | null>(null);
  const [badAuthUrl, setBadAuthUrl] = useState(false);
  const [issue, setIssue] = useState<IntegrationText | null>(null);
  const locked = create.locked || test.locked || oauth.locked;
  const workspace = workspaces.find((ws) => ws.id === workspaceId) ?? null;

  function next(e: FormEvent) {
    e.preventDefault();
    setIssue(null);
    if (step === 1) {
      if (!name.trim() || !url.trim() || (ownerKind === 'workspace' && !workspace)) return setIssue('requiredFields');
      if (!validUrl(url)) return setIssue('mcp_url_invalid');
    }
    if (step === 2 && authKind === 'bearer' && !token.trim()) return setIssue('tokenRequired');
    setStep((s) => Math.min(3, s + 1));
  }
  function runTest(connection: ConnectionSummary) {
    test.start<ConnectionDetail>('agent.mcp.connection.test', { connection_id: connection.id }, setTested);
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    if (locked || created) return;
    setIssue(null);
    const input: Record<string, unknown> = {
      owner_kind: ownerKind, ...(ownerKind === 'workspace' ? { workspace_id: workspaceId } : {}),
      name: name.trim(), url: url.trim(), description: description.trim(), auth_kind: authKind,
      ...(authKind === 'bearer' ? { credential: token.trim() } : {}),
      ...(authKind === 'oauth' && clientId.trim() ? { oauth_client: { client_id: clientId.trim(), ...(clientSecret.trim() ? { client_secret: clientSecret.trim() } : {}) } } : {}),
    };
    create.start<ConnectionSummary>('agent.mcp.connection.create', input, (summary) => {
      // The secrets have been sealed on the server; nothing on this page needs them any more.
      setToken(''); setClientSecret('');
      setCreated(summary);
      if (summary.auth_kind !== 'oauth') runTest(summary);
    });
  }
  function authenticate(connection: ConnectionSummary) {
    setBadAuthUrl(false);
    oauth.start<OAuthStart>('agent.mcp.oauth.start', { connection_id: connection.id }, (out) => {
      const target = safeAuthorizationUrl(out.authorization_url);
      if (!target) return setBadAuthUrl(true);
      window.location.assign(target);
    });
  }

  if (created) {
    return <div className="col gap16">
      <Stepper step={3} />
      <section className="card" aria-live="polite"><div className="card-b col gap12">
        <p className="notice ok" role="status">{m('created')}</p>
        <div className="row wrap"><strong>{created.name}</strong><HealthBadge health={(tested ?? created).health} /><span className="muted small"><OwnerText summary={created} /> · {created.host ?? created.url}</span></div>
        {created.auth_kind === 'oauth' ? <>
          <p className="muted">{m('authFirst')}</p>
          <div className="row wrap">
            <button type="button" className="btn primary" disabled={oauth.locked} onClick={() => authenticate(created)}>{oauth.busy ? m('authenticating') : m('authenticate')}</button>
            <Link className="btn" to={`/agent/integrations/${created.id}`}>{m('openDetail')}</Link>
          </div>
          {badAuthUrl && <p className="notice err" role="alert">{m('mcp_oauth_failed')}</p>}
          <WriteFeedback write={oauth} />
        </> : <>
          {test.busy && <p role="status"><Loading label={m('testing')} /></p>}
          {tested && <DiscoverySummary detail={tested} />}
          {test.error != null && <div className="notice err col" role="alert"><strong>{m('testFailed')}</strong><ErrorNotice error={test.error} /></div>}
          {!test.busy && !test.pending && <div className="row wrap">
            <Link className="btn primary" to={`/agent/integrations/${created.id}`}>{m('openDetail')}</Link>
            {!tested && <button type="button" className="btn" disabled={test.locked} onClick={() => runTest(created)}>{m('testConnection')}</button>}
          </div>}
          {test.pending && !test.busy && <WriteFeedback write={test} />}
        </>}
      </div></section>
    </div>;
  }

  return <form className="col gap16" onSubmit={step === 3 ? submit : next} noValidate>
    <Stepper step={step} />
    <section className="card"><div className="card-b col gap12">
      {step === 1 && <>
        <fieldset className="mi-fieldset col gap4" disabled={locked}>
          <legend>{m('ownerLabel')}</legend>
          <label className="check"><input type="radio" name="owner" value="user" checked={ownerKind === 'user'} onChange={() => setOwnerKind('user')} /><span>{m('ownerPersonal')}</span></label>
          <label className="check"><input type="radio" name="owner" value="workspace" checked={ownerKind === 'workspace'} disabled={!workspaces.length} onChange={() => setOwnerKind('workspace')} /><span>{m('ownerWorkspace')}</span></label>
          {!workspaces.length && <p className="muted small">{m('noManagedWorkspaces')}</p>}
        </fieldset>
        {ownerKind === 'workspace' && !!workspaces.length && <label className="field"><span>{m('workspaceLabel')}</span>
          <select className="input" value={workspaceId} disabled={locked} onChange={(e) => setWorkspaceId(e.target.value)}>
            {workspaces.map((ws) => <option key={ws.id} value={ws.id}>{ws.name}</option>)}
          </select></label>}
        <label className="field"><span>{m('name')}</span>
          <input className="input" value={name} maxLength={80} required disabled={locked} autoComplete="off" onChange={(e) => setName(e.target.value)} /></label>
        <label className="field"><span>{m('url')}</span>
          <input className="input" type="url" inputMode="url" value={url} maxLength={2048} required disabled={locked} autoComplete="off" spellCheck={false}
            aria-describedby="mi-url-hint" onChange={(e) => setUrl(e.target.value)} /></label>
        <p id="mi-url-hint" className="muted small">{m('urlHint')}</p>
        <label className="field"><span>{m('description')}</span>
          <textarea className="input mi-textarea" rows={2} maxLength={500} value={description} disabled={locked} onChange={(e) => setDescription(e.target.value)} /></label>
      </>}
      {step === 2 && <>
        <fieldset className="mi-fieldset col gap4" disabled={locked}>
          <legend>{m('authLabel')}</legend>
          {(['none', 'bearer', 'oauth'] as AuthKind[]).map((kind) => <label className="check" key={kind}>
            <input type="radio" name="auth" value={kind} checked={authKind === kind} onChange={() => setAuthKind(kind)} /><span>{m(AUTH_TEXT[kind])}</span></label>)}
        </fieldset>
        {authKind === 'bearer' && <>
          <label className="field"><span>{m('tokenLabel')}</span>
            <input className="input" type="password" autoComplete="new-password" spellCheck={false} autoCapitalize="none" value={token} maxLength={4096}
              disabled={locked} aria-describedby="mi-token-hint" onChange={(e) => setToken(e.target.value)} /></label>
          <p id="mi-token-hint" className="muted small">{m('tokenHint')}</p>
        </>}
        {authKind === 'oauth' && <>
          <label className="field"><span>{m('oauthClientId')}</span>
            <input className="input" value={clientId} maxLength={512} disabled={locked} autoComplete="off" spellCheck={false} onChange={(e) => setClientId(e.target.value)} /></label>
          <label className="field"><span>{m('oauthClientSecret')}</span>
            <input className="input" type="password" autoComplete="new-password" spellCheck={false} value={clientSecret} maxLength={4096} disabled={locked || !clientId.trim()} onChange={(e) => setClientSecret(e.target.value)} /></label>
          <p className="muted small">{m('oauthHint')}</p>
        </>}
      </>}
      {step === 3 && <>
        <dl className="mi-facts">
          <div><dt>{m('reviewOwner')}</dt><dd>{ownerKind === 'workspace' ? m('workspaceOwner', { name: workspace?.name ?? '' }) : m('personal')}</dd></div>
          <div><dt>{m('name')}</dt><dd>{name.trim()}</dd></div>
          <div><dt>{m('url')}</dt><dd><code className="mi-break">{url.trim()}</code></dd></div>
          {description.trim() && <div><dt>{m('description')}</dt><dd>{description.trim()}</dd></div>}
          <div><dt>{m('reviewAuth')}</dt><dd>{m(AUTH_TEXT[authKind])}{authKind === 'oauth' && clientId.trim() ? ` · ${m('oauthPreregistered')}` : ''}</dd></div>
        </dl>
        <WriteFeedback write={create} />
      </>}
      {issue && <p className="notice err" role="alert">{m(issue)}</p>}
      <div className="row wrap mi-actions">
        {step > 1 && <button type="button" className="btn" disabled={locked} onClick={() => { setIssue(null); setStep((s) => s - 1); }}>{m('prev')}</button>}
        <span className="spacer" />
        <Link className="btn ghost" to="/agent/integrations">{m('cancel')}</Link>
        {step < 3 ? <button type="submit" className="btn primary" disabled={locked}>{m('next')}</button>
          : <button type="submit" className="btn primary" disabled={locked}>{create.busy ? m('creating') : m('create')}</button>}
      </div>
    </div></section>
  </form>;
}

export default function NewConnection() {
  const { m } = useText();
  const { me } = useSession();
  const policy = useRead<Policy>('agent.mcp.policy.get');
  const workspaces = manageableWorkspaces(me?.workspaces);
  return <div className="mi-page col gap16">
    <header className="suite-head"><div>
      <p className="small"><Link to="/agent/integrations">‹ {m('back')}</Link></p>
      <h1>{m('newTitle')}</h1>
    </div></header>
    {policy.loading ? <Loading /> : policy.error != null ? <ErrorNotice error={policy.error} retry={policy.reload} />
      : policy.data && !policy.data.outbound_enabled ? <p className="notice warn" role="status">{m('outboundClosed')}</p>
      : <NewConnectionForm workspaces={workspaces} />}
  </div>;
}

export const authKindText = (kind: AuthKind) => integrationCopy[AUTH_TEXT[kind]][0];
