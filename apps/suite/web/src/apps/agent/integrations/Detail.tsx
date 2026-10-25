/** /agent/integrations/:cid — one connection: its health and server facts, the tools with their
 *  grants and the person's own narrowing, the resources and prompts it offers, every call made
 *  through it, and whether this person's Agent uses it. Everything shown is what the server
 *  returned; management buttons are disabled with the reason when the person does not manage it,
 *  and the actions a platform stop refuses are disabled with that reason. A connection pointed at
 *  another address says what has to happen before an Agent uses it again. A write the server
 *  executed without returning its result re-reads the connection. */
import { useEffect, useId, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useDialog } from '@synwevo/ui';
import { read } from '../../../shared/ops';
import { Empty, Loading } from '../../../shared/states';
import type { IntegrationText } from './integrations.messages';
import {
  grantView, isReadOnly, mcpErrorKey, needsToken, oauthOutcome, personalModes, platformStopped, pointedElsewhere, repointSteps,
  safeAuthorizationUrl, slugifyAlias, toolHints, KIND_TEXT, ORIGIN_TEXT,
  type Binding, type CallRecord, type ConnectionDetail, type ConnectionSummary, type GrantMode, type OAuthStart, type OAuthOutcome,
  type PersonalMode, type Prompt, type RenderedPrompt, type Resource, type ResourcePage, type Tool,
} from './model';
import {
  CallStatusBadge, ConnectionStatusBadge, CopyButton, ErrorNotice, Fact, Facts, HealthBadge, OwnerText, useRead, useText, useWrite,
  WriteFeedback, type Write,
} from './shared';

export const TABS = ['overview', 'tools', 'resources', 'prompts', 'calls', 'mine'] as const;
export type Tab = typeof TABS[number];
const TAB_TEXT: Record<Tab, IntegrationText> = {
  overview: 'tabOverview', tools: 'tabTools', resources: 'tabResources', prompts: 'tabPrompts', calls: 'tabCalls', mine: 'tabMine',
};
export function tabFromSearch(search: URLSearchParams): Tab {
  const value = search.get('tab');
  return (TABS as readonly string[]).includes(value ?? '') ? value as Tab : 'overview';
}

const GRANT_TEXT: Record<GrantMode, IntegrationText> = { off: 'modeOff', confirm: 'modeConfirm', auto: 'modeAuto' };
const PERSONAL_TEXT: Record<'' | PersonalMode, IntegrationText> = { '': 'personalFollow', confirm: 'personalConfirm', off: 'personalOff' };

/** A refusal code the server recorded, in the centre's words, with the code itself beside it. */
function ErrorLine({ code }: { code: string }) {
  const { m } = useText();
  // The centre's own sentence for the code; the message the server recorded may be an external
  // server's prose, so it is never rendered.
  return <span><span>{m(mcpErrorKey(code) ?? 'error')}</span> <code className="muted">{code}</code></span>;
}

function JsonBlock({ value, label }: { value: unknown; label: string }) {
  let text: string;
  try { text = JSON.stringify(value, null, 2) ?? ''; } catch { text = ''; }
  return <pre className="mi-pre" tabIndex={0} aria-label={label}>{text}</pre>;
}

/** The merge of a write's output into the detail already on the page. */
type Update = (next: ConnectionDetail) => void;
/** Re-reads the connection: after a write the server executed but whose result it cannot return. */
type Reload = () => void;
const noReload: Reload = () => {};
function withSummary(detail: ConnectionDetail, summary: ConnectionSummary): ConnectionDetail {
  return { ...detail, ...summary, tools: detail.tools, resources: detail.resources, prompts: detail.prompts, instructions: detail.instructions, calls: detail.calls };
}
export function withBinding(detail: ConnectionDetail, binding: Binding): ConnectionDetail {
  const modes = binding.tool_modes ?? {};
  return { ...detail, binding, tools: detail.tools.map((tool) => ({ ...tool, personal_mode: modes[tool.name] ?? null })) };
}

// ---------------------------------------------------------------- overview

export function OverviewTab({ detail, update, onDeleted, reload = noReload, onDeleteUnverified = reload }: {
  detail: ConnectionDetail; update: Update; onDeleted: () => void; reload?: Reload;
  /** The delete was executed but its result cannot be read: the page goes to the re-read list. */
  onDeleteUnverified?: () => void;
}) {
  const { m, date } = useText();
  const dialog = useDialog();
  const write = useWrite();
  const [confirming, setConfirming] = useState(false);
  const [tested, setTested] = useState<ConnectionDetail | null>(null);
  const [badAuthUrl, setBadAuthUrl] = useState(false);
  const tokenWrite = useWrite();
  const [token, setToken] = useState('');
  const [tokenIssue, setTokenIssue] = useState(false);
  const manage = detail.manage;
  // Refused while the platform's stop holds (connections._platform_stop): enabling, testing,
  // refreshing, authenticating and giving a credential. Disabling and deleting stay open.
  const stopped = platformStopped(detail);
  const repointed = !stopped && pointedElsewhere(detail);
  const askToken = manage && !stopped && needsToken(detail);
  const locked = write.locked || tokenWrite.locked || confirming;
  const ref = { connection_id: detail.id };
  const server = detail.server_info?.title || detail.server_info?.name || null;
  const serverText = server ? (detail.server_info?.version ? `${server} ${detail.server_info.version}` : server) : m('none');
  const refreshed = (next: ConnectionDetail) => { setTested(null); update(next); };

  function test(operation: 'agent.mcp.connection.test' | 'agent.mcp.connection.refresh') {
    setTested(null);
    write.start<ConnectionDetail>(operation, ref, (next) => { update(next); setTested(next); }, reload);
  }
  function toggle() {
    write.start<ConnectionSummary>(detail.status === 'disabled' ? 'agent.mcp.connection.enable' : 'agent.mcp.connection.disable', ref,
      (summary) => refreshed(withSummary(detail, summary)), reload);
  }
  async function revoke() {
    setConfirming(true);
    try {
      if (!await dialog.confirm(m('revokeTitle'), m('revokeBody'), m('revoke'), true)) return;
      write.start<ConnectionSummary>('agent.mcp.credential.revoke', ref, (summary) => refreshed(withSummary(detail, summary)), reload);
    } finally { setConfirming(false); }
  }
  async function remove() {
    setConfirming(true);
    try {
      if (!await dialog.confirm(m('deleteTitle'), m('deleteBody', { name: detail.name }), m('remove'), true)) return;
      write.start<{ deleted: string }>('agent.mcp.connection.delete', ref, onDeleted, onDeleteUnverified);
    } finally { setConfirming(false); }
  }
  function authenticate() {
    setBadAuthUrl(false);
    write.start<OAuthStart>('agent.mcp.oauth.start', ref, (out) => {
      const target = safeAuthorizationUrl(out.authorization_url);
      if (!target) return setBadAuthUrl(true);
      window.location.assign(target);
    }, reload);
  }
  function saveToken(e: FormEvent) {
    e.preventDefault();
    if (locked) return;
    const credential = token.trim();
    setTokenIssue(!credential);
    if (!credential) return;
    // The token is sealed on the server; this page forgets it as soon as the server has answered.
    tokenWrite.start<ConnectionSummary>('agent.mcp.connection.update', { ...ref, credential }, (summary) => {
      setToken('');
      refreshed(withSummary(detail, summary));
    }, () => { setToken(''); reload(); });
  }

  return <div className="col gap16">
    <section className="card"><div className="card-b">
      <Facts>
        <Fact label={m('health')}><span className="row wrap"><HealthBadge health={detail.health} /><ConnectionStatusBadge status={detail.status} stopped={stopped} />
          {detail.health_checked_at != null && <span className="muted small">{m('lastChecked')} {date(detail.health_checked_at)}</span>}</span></Fact>
        {stopped && detail.platform_disabled_at != null && <Fact label={m('platformStoppedAt')}>{date(detail.platform_disabled_at)}</Fact>}
        {detail.last_ok_at != null && <Fact label={m('lastOk')}>{date(detail.last_ok_at)}</Fact>}
        {detail.last_error && <Fact label={m('lastError')}><ErrorLine code={detail.last_error.code} /></Fact>}
        <Fact label={m('circuit')}>{detail.circuit_open_until ? <span className="mi-err">{m('circuitOpen', { time: date(detail.circuit_open_until) })}</span> : m('circuitClosed')}</Fact>
        <Fact label={m('reviewOwner')}><OwnerText summary={detail} /></Fact>
        <Fact label={m('url')}><code className="mi-break">{detail.url}</code></Fact>
        {detail.description && <Fact label={m('descriptionShort')}>{detail.description}</Fact>}
        <Fact label={m('serverInfo')}>{serverText}</Fact>
        <Fact label={m('protocol')}>{detail.protocol_version ?? m('none')}</Fact>
        <Fact label={m('discoveredAt')}>{detail.discovered_at != null ? date(detail.discovered_at) : m('none')}</Fact>
        <Fact label={m('reviewAuth')}>{m(detail.auth_kind === 'none' ? 'authNone' : detail.auth_kind === 'bearer' ? 'authBearer' : 'authOAuth')}</Fact>
        {detail.auth_kind !== 'none' && <Fact label={m('credential')}>{!detail.has_credential ? m('credentialNone')
          : detail.credential_hint && detail.credential_hint !== 'oauth' ? m('credentialEnding', { hint: detail.credential_hint.replace(/^…/, '') }) : m('credentialSet')}</Fact>}
        {detail.auth_kind === 'oauth' && <Fact label={m('oauthClient')}>{detail.oauth_client
          ? <span>{m(detail.oauth_client.kind === 'preregistered' ? 'oauthPreregistered' : detail.oauth_client.kind === 'cimd' ? 'oauthCimd' : 'oauthDynamic')} · <code className="mi-break">{detail.oauth_client.client_id}</code></span>
          : m('oauthNotYet')}</Fact>}
        <Fact label={m('createdAt')}>{date(detail.created_at)}</Fact>
      </Facts>
      {detail.instructions && <details>
        <summary>{m('instructions')}</summary>
        <div className="mi-expand col gap4">
          <p className="muted small">{m('instructionsNote')}</p>
          <pre className="mi-pre" tabIndex={0}>{detail.instructions}</pre>
        </div>
      </details>}
    </div></section>
    <section className="card"><div className="card-b">
      {stopped ? <p className="notice warn" role="status">{m('platformStopped')}</p>
        : detail.status === 'disabled' && <p className="notice warn">{m('disabledHint')}</p>}
      {repointed && <div className="notice warn col" role="status">
        <p>{m('repointed')}</p>
        <p>{m('repointedNext')}</p>
        <ol className="mi-list-plain">{repointSteps(detail).map((key) => <li key={key}>{m(key)}</li>)}</ol>
      </div>}
      {!stopped && !repointed && detail.auth_kind === 'oauth' && !detail.has_credential && <p className="notice info">{m('authFirst')}</p>}
      {askToken && <form className="col gap4" onSubmit={saveToken} noValidate>
        {!repointed && <p className="notice info">{m('tokenMissing')}</p>}
        <label className="field"><span>{m('tokenLabel')}</span>
          <input className="input" type="password" autoComplete="new-password" spellCheck={false} autoCapitalize="none" value={token} maxLength={4096}
            disabled={locked} aria-describedby={`mi-token-${detail.id}`} onChange={(e) => { setToken(e.target.value); setTokenIssue(false); tokenWrite.dismiss(); }} /></label>
        <p id={`mi-token-${detail.id}`} className="muted small">{m('tokenHint')}</p>
        {tokenIssue && <p className="notice err" role="alert">{m('tokenRequired')}</p>}
        <div><button type="submit" className="btn primary" disabled={locked}>{m('saveToken')}</button></div>
      </form>}
      <WriteFeedback write={tokenWrite} savedText={m('tokenSaved')} />
      <div className="row wrap mi-actions">
        {detail.auth_kind === 'oauth' && <button type="button" className="btn primary" disabled={!manage || locked || stopped} onClick={authenticate}>{write.busy && write.operation === 'agent.mcp.oauth.start' ? m('authenticating') : m('authenticate')}</button>}
        <button type="button" className="btn" disabled={!manage || locked || stopped} onClick={() => test('agent.mcp.connection.test')}>{write.busy && write.operation === 'agent.mcp.connection.test' ? m('testing') : m('testConnection')}</button>
        <button type="button" className="btn" disabled={!manage || locked || stopped} onClick={() => test('agent.mcp.connection.refresh')}>{m('refreshCaps')}</button>
        <button type="button" className="btn" disabled={!manage || locked || (stopped && detail.status === 'disabled')} onClick={toggle}>{m(detail.status === 'disabled' ? 'enable' : 'disable')}</button>
        {detail.auth_kind !== 'none' && <button type="button" className="btn" disabled={!manage || locked || !detail.has_credential} onClick={() => void revoke()}>{m('revoke')}</button>}
        <span className="spacer" />
        <button type="button" className="btn danger" disabled={!manage || locked} onClick={() => void remove()}>{m('remove')}</button>
      </div>
      {!manage && <p className="muted small">{m('manageOnly')}</p>}
      {badAuthUrl && <p className="notice err" role="alert">{m('mcp_oauth_failed')}</p>}
      {tested && <p className="notice ok" role="status">{m('testPassed', { tools: m('toolsCount', { n: tested.counts.tools }),
        resources: m('resourcesCount', { n: tested.counts.resources }), prompts: m('promptsCount', { n: tested.counts.prompts }) })}</p>}
      <WriteFeedback write={write} />
    </div></section>
  </div>;
}

// ---------------------------------------------------------------- tools

export function ToolEntry({ tool, detail, write, update, reload = noReload }: { tool: Tool; detail: ConnectionDetail; write: Write; update: Update; reload?: Reload }) {
  const { m, date } = useText();
  const id = useId();
  const manage = detail.manage;
  const bound = detail.binding.bound;
  const readOnly = isReadOnly(tool);
  const locked = write.locked;
  const grant = grantView(tool);
  // Its schema was not kept (connections.UNUSABLE): it can only be off.
  const unusable = tool.unusable ? tool.unusable.code : null;
  function setGrant(mode: GrantMode) {
    write.start<ConnectionDetail>('agent.mcp.grant.set', { connection_id: detail.id, tool: tool.name, mode }, update, reload);
  }
  function setPersonal(value: '' | PersonalMode) {
    const tool_modes = personalModes(detail.tools, { tool: tool.name, mode: value || null });
    write.start<Binding>('agent.mcp.binding.set', { connection_id: detail.id, tool_modes }, (binding) => update(withBinding(detail, binding)), reload);
  }
  return <article className={`mi-entry${tool.removed ? ' removed' : ''}`} aria-labelledby={`${id}-name`}>
    <div className="row wrap">
      <h3 id={`${id}-name`}>{tool.title || tool.name}</h3>
      {tool.title && tool.title !== tool.name && <code className="muted">{tool.name}</code>}
      {tool.removed && <span className="badge">{m('removedTool')}</span>}
      {unusable && !tool.removed && <span className="badge warn">{m('toolUnusable')}</span>}
      {grant.suspended && <span className="badge warn">{m('stale')}</span>}
    </div>
    {tool.description && <p className="small">{tool.description}</p>}
    <div className="mi-hints" aria-label={m('toolHints')}>{toolHints(tool.annotations).map((key) => <span key={key} className={`badge ${key === 'hintReadOnly' ? 'ok' : key === 'hintDestructive' ? 'err' : ''}`}>{m(key)}</span>)}</div>
    <div className="mi-entry-controls">
      <label className="field"><span>{m('grantLabel')}</span>
        <select className="input" value={grant.value} disabled={!manage || tool.removed || locked} onChange={(e) => setGrant(e.target.value as GrantMode)}>
          {grant.suspended && <option value="" disabled>{m('staleSelect')}</option>}
          {(['off', 'confirm', 'auto'] as GrantMode[]).map((mode) => <option key={mode} value={mode} disabled={(mode === 'auto' && !readOnly) || (mode !== 'off' && !!unusable)}>{m(GRANT_TEXT[mode])}</option>)}
        </select></label>
      {bound && <label className="field"><span>{m('personalLabel')}</span>
        <select className="input" value={tool.personal_mode ?? ''} disabled={tool.removed || locked} onChange={(e) => setPersonal(e.target.value as '' | PersonalMode)}>
          {(['', 'confirm', 'off'] as const).map((mode) => <option key={mode} value={mode}>{m(PERSONAL_TEXT[mode])}</option>)}
        </select></label>}
    </div>
    {unusable && !tool.removed && <p className="notice warn">{m(mcpErrorKey(unusable) ?? 'mcp_tool_unusable')}</p>}
    {grant.suspended && !unusable && <div className="notice warn mi-notice-row">
      <p>{m('staleNotice', { mode: m(GRANT_TEXT[grant.suspended]) })}</p>
      <button type="button" className="btn sm" disabled={!manage || locked || !grant.reconfirm}
        onClick={() => grant.suspended && setGrant(grant.suspended)}>{m('reconfirm', { mode: m(GRANT_TEXT[grant.suspended]) })}</button>
    </div>}
    <p className="muted small">{!readOnly ? m('autoDisabledHint') : ''}{!readOnly && bound ? ' ' : ''}{bound ? m('personalHint') : ''}</p>
    {tool.grant.granted_at != null && tool.grant.mode !== 'off' && <p className="muted small">{m('grantedAt', { time: date(tool.grant.granted_at) })}</p>}
    {!unusable && <details>
      <summary>{m('schemaToggle')}</summary>
      <div className="mi-expand"><JsonBlock value={tool.input_schema} label={m('schemaToggle')} /></div>
    </details>}
  </article>;
}

export function ToolsTab({ detail, update, reload }: { detail: ConnectionDetail; update: Update; reload?: Reload }) {
  const { m } = useText();
  const write = useWrite();
  if (!detail.tools.length) return <Empty title={m('toolsEmpty')} />;
  return <div className="col gap12">
    <p className="muted">{m('toolsIntro')}</p>
    {!detail.manage && <p className="muted small">{m('toolsManagerNote')}</p>}
    {!detail.binding.bound && <p className="muted small">{m('noBinding')}</p>}
    <div className="suite-list">{detail.tools.map((tool) => <ToolEntry key={tool.name} tool={tool} detail={detail} write={write} update={update} reload={reload} />)}</div>
    <WriteFeedback write={write} />
  </div>;
}

// ---------------------------------------------------------------- resources

export function ResourcesTab({ detail, reload }: { detail: ConnectionDetail; reload?: Reload }) {
  const { m, duration } = useText();
  const write = useWrite();
  const [page, setPage] = useState<ResourcePage | null>(null);
  const stopped = platformStopped(detail);
  if (!detail.resources.length) return <Empty title={m('resourcesEmpty')} />;
  function open(resource: Resource) {
    setPage(null);
    write.start<ResourcePage>('agent.mcp.resource.read_page', { connection_id: detail.id, uri: resource.uri }, setPage, reload);
  }
  return <div className="col gap12">
    <p className="muted">{m('resourcesHint')}</p>
    {stopped && <p className="notice warn" role="status">{m('platformStoppedUse')}</p>}
    <div className="suite-list">{detail.resources.map((resource) => <article key={resource.uri} className="mi-entry">
      <div className="row wrap"><h3>{resource.title || resource.name || resource.uri}</h3>
        {resource.mime_type && <span className="badge">{resource.mime_type}</span>}
        {resource.size != null && <span className="muted small">{m('bytes', { n: resource.size })}</span>}</div>
      <code className="mi-break small">{resource.uri}</code>
      {resource.description && <p className="small">{resource.description}</p>}
      <div><button type="button" className="btn sm" disabled={write.locked || stopped} onClick={() => open(resource)}>
        {write.busy && write.input?.uri === resource.uri ? m('reading') : m('read')}</button></div>
      {page?.uri === resource.uri && <section className="col gap4" aria-label={m('preview')}>
        <div className="mi-preview-head"><strong>{m('preview')}</strong><span className="muted small">{m('bytes', { n: page.bytes })} · {duration(page.duration_ms)}</span>
          {page.text != null && <CopyButton text={page.text} />}</div>
        {page.truncated && <p className="notice warn">{m('truncated')}</p>}
        {page.text != null ? <pre className="mi-pre" tabIndex={0}>{page.text}</pre> : <p className="muted">{m('noText')}</p>}
      </section>}
    </article>)}</div>
    <WriteFeedback write={write} />
  </div>;
}

// ---------------------------------------------------------------- prompts

export function PromptEntry({ prompt, detail, write, rendered, onRendered, reload }: {
  prompt: Prompt; detail: ConnectionDetail; write: Write; rendered: RenderedPrompt | null; onRendered: (value: RenderedPrompt) => void; reload?: Reload;
}) {
  const { m, duration } = useText();
  const id = useId();
  const [values, setValues] = useState<Record<string, string>>({});
  const missing = prompt.arguments.filter((a) => a.required && !(values[a.name] ?? '').trim());
  const stopped = platformStopped(detail);
  function submit(e: FormEvent) {
    e.preventDefault();
    if (write.locked || stopped || missing.length) return;
    const args: Record<string, string> = {};
    for (const a of prompt.arguments) if (values[a.name] !== undefined && values[a.name] !== '') args[a.name] = values[a.name];
    write.start<RenderedPrompt>('agent.mcp.prompt.render', { connection_id: detail.id, name: prompt.name, arguments: args }, onRendered, reload);
  }
  return <article className="mi-entry" aria-labelledby={`${id}-name`}>
    <div className="row wrap"><h3 id={`${id}-name`}>{prompt.title || prompt.name}</h3>
      {prompt.title && prompt.title !== prompt.name && <code className="muted">{prompt.name}</code>}</div>
    {prompt.description && <p className="small">{prompt.description}</p>}
    <form className="col gap4" onSubmit={submit} noValidate>
      {prompt.arguments.length ? <fieldset className="mi-fieldset col gap4" disabled={write.locked}>
        <legend>{m('arguments')}</legend>
        {prompt.arguments.map((a) => <label key={a.name} className="field"><span>{a.name}{a.required ? ` · ${m('requiredArg')}` : ''}{a.description ? ` — ${a.description}` : ''}</span>
          <input className="input" value={values[a.name] ?? ''} maxLength={4000} autoComplete="off" onChange={(e) => setValues((old) => ({ ...old, [a.name]: e.target.value }))} /></label>)}
      </fieldset> : <p className="muted small">{m('argsEmpty')}</p>}
      <div><button type="submit" className="btn sm" disabled={write.locked || stopped || !!missing.length}>{write.busy && write.input?.name === prompt.name ? m('rendering') : m('render')}</button></div>
    </form>
    {rendered && <section className="col gap4" aria-label={m('preview')}>
      <div className="mi-preview-head"><strong>{m('preview')}</strong><span className="muted small">{duration(rendered.duration_ms)}</span>
        <CopyButton text={rendered.text} label={m('copyPrompt')} /></div>
      <p className="muted small">{m('copyNote')}</p>
      {rendered.truncated && <p className="notice warn">{m('truncated')}</p>}
      <pre className="mi-pre" tabIndex={0}>{rendered.text}</pre>
    </section>}
  </article>;
}

export function PromptsTab({ detail, reload }: { detail: ConnectionDetail; reload?: Reload }) {
  const { m } = useText();
  const write = useWrite();
  const [rendered, setRendered] = useState<RenderedPrompt | null>(null);
  if (!detail.prompts.length) return <Empty title={m('promptsEmpty')} />;
  return <div className="col gap12">
    <p className="muted">{m('promptsHint')}</p>
    {platformStopped(detail) && <p className="notice warn" role="status">{m('platformStoppedUse')}</p>}
    <div className="suite-list">{detail.prompts.map((prompt) => <PromptEntry key={prompt.name} prompt={prompt} detail={detail} write={write}
      rendered={rendered?.name === prompt.name ? rendered : null} onRendered={setRendered} reload={reload} />)}</div>
    <WriteFeedback write={write} />
  </div>;
}

// ---------------------------------------------------------------- calls

export function CallEntry({ call }: { call: CallRecord }) {
  const { m, date, duration } = useText();
  const origin = ORIGIN_TEXT[call.origin];
  const kind = KIND_TEXT[call.kind];
  const own = call.input_preview != null || call.result_preview != null;
  return <article className="mi-entry">
    <div className="mi-call">
      <div className="col gap4">
        <div className="row wrap"><strong>{kind ? m(kind) : call.kind}</strong>{call.name && <code className="mi-break">{call.name}</code>}<CallStatusBadge status={call.status} /></div>
        <div className="mi-call-facts">
          <span>{origin ? m(origin) : call.origin}</span>
          <span>{m('startedAt')} {date(call.started_at)}</span>
          {call.duration_ms != null && <span>{duration(call.duration_ms)}</span>}
          {call.request_id && <span>{m('requestId', { id: call.request_id })}</span>}
        </div>
        {call.error && <p className="small mi-err"><ErrorLine code={call.error.code} /></p>}
      </div>
    </div>
    {own && <details>
      <summary>{m('inputPreview')} · {m('resultPreview')}</summary>
      <div className="mi-expand col gap4">
        {call.input_preview != null && <><strong className="small">{m('inputPreview')}</strong><pre className="mi-pre" tabIndex={0}>{call.input_preview}</pre></>}
        {call.result_preview != null && <><strong className="small">{m('resultPreview')}</strong><pre className="mi-pre" tabIndex={0}>{call.result_preview}</pre></>}
      </div>
    </details>}
  </article>;
}

const PAGE = 50;
export function CallsTab({ detail }: { detail: ConnectionDetail }) {
  const { m } = useText();
  const [items, setItems] = useState<CallRecord[]>(detail.calls);
  const [more, setMore] = useState(detail.calls.length >= 20);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function load(before: number | null) {
    setLoading(true); setError(null);
    try {
      const out = await read<{ items: CallRecord[] }>('agent.mcp.calls', { connection_id: detail.id, limit: PAGE, ...(before != null ? { before } : {}) }, null);
      setItems((old) => before == null ? out.items : [...old, ...out.items.filter((c) => !old.some((o) => o.id === c.id))]);
      setMore(out.items.length >= PAGE);
    } catch (e) { setError(e); } finally { setLoading(false); }
  }
  return <div className="col gap12">
    <div className="row wrap"><p className="muted grow">{m('callsHint')}</p>
      <button type="button" className="btn ghost sm" disabled={loading} onClick={() => void load(null)}>{m('refresh')}</button></div>
    {error != null && <ErrorNotice error={error} retry={() => void load(null)} />}
    {!items.length ? (loading ? <Loading /> : <Empty title={m('callsEmpty')} />) : <div className="suite-list">{items.map((call) => <CallEntry key={call.id} call={call} />)}</div>}
    {loading && !!items.length && <Loading />}
    {more && !!items.length && <div><button type="button" className="btn" disabled={loading} onClick={() => void load(items[items.length - 1].started_at)}>{m('loadMore')}</button></div>}
  </div>;
}

// ---------------------------------------------------------------- my agent

export function MineTab({ detail, update, reload }: { detail: ConnectionDetail; update: Update; reload?: Reload }) {
  const { m } = useText();
  const write = useWrite();
  const binding = detail.binding;
  const [enabled, setEnabled] = useState(binding.bound ? !!binding.enabled : false);
  const [alias, setAlias] = useState(binding.alias ?? detail.slug);
  const [resources, setResources] = useState(!!binding.resources_enabled);
  const slug = slugifyAlias(alias);
  const changed = !binding.bound || enabled !== !!binding.enabled || resources !== !!binding.resources_enabled || (slug && slug !== binding.alias);
  function submit(e: FormEvent) {
    e.preventDefault();
    if (write.locked || !slug) return;
    write.start<Binding>('agent.mcp.binding.set', { connection_id: detail.id, enabled, resources_enabled: resources, alias: slug }, (next) => {
      update(withBinding(detail, next));
      setEnabled(!!next.enabled); setResources(!!next.resources_enabled); setAlias(next.alias ?? slug);
    }, reload);
  }
  return <form className="col gap12" onSubmit={submit} noValidate>
    <p className="muted">{m('mineIntro')}</p>
    <div className="row wrap">{binding.bound ? (binding.enabled ? <span className="badge info">{m('bound')}</span> : <span className="badge">{m('boundDisabled')}</span>) : <span className="badge">{m('notBound')}</span>}
      {(detail.status === 'disabled' || platformStopped(detail)) && <ConnectionStatusBadge status={detail.status} stopped={platformStopped(detail)} />}</div>
    {platformStopped(detail) && <p className="notice warn" role="status">{m('platformStoppedAgent')}</p>}
    <label className="check"><input type="checkbox" checked={enabled} disabled={write.locked} onChange={(e) => { setEnabled(e.target.checked); write.dismiss(); }} /><span>{m('enabled')}</span></label>
    <label className="field"><span>{m('alias')}</span>
      <input className="input" value={alias} maxLength={24} autoComplete="off" spellCheck={false} disabled={write.locked} aria-describedby="mi-alias-hint"
        onChange={(e) => { setAlias(e.target.value); write.dismiss(); }} /></label>
    <p id="mi-alias-hint" className="muted small">{m('aliasHint')}{slug && slug !== alias ? ` ${m('aliasPreview', { alias: slug })}` : ''}</p>
    {!slug && <p className="notice err" role="alert">{m('aliasInvalid')}</p>}
    <label className="check"><input type="checkbox" checked={resources} disabled={write.locked} onChange={(e) => { setResources(e.target.checked); write.dismiss(); }} /><span>{m('resourcesEnabled')}</span></label>
    <div className="row wrap"><button type="submit" className="btn primary" disabled={write.locked || !slug || !changed}>{m('save')}</button>
      <Link className="btn ghost" to="/agent/settings">{m('agentSettings')}</Link></div>
    <WriteFeedback write={write} />
  </form>;
}

// ---------------------------------------------------------------- the page

export function DetailView({ detail, tab, setTab, update, onDeleted, reload, onDeleteUnverified, authNotice, dismissAuth }: {
  detail: ConnectionDetail; tab: Tab; setTab: (tab: Tab) => void; update: Update; onDeleted: () => void;
  reload?: Reload; onDeleteUnverified?: () => void;
  authNotice?: OAuthOutcome; dismissAuth?: () => void;
}) {
  const { m } = useText();
  const id = useId();
  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    const index = TABS.indexOf(tab);
    const next = e.key === 'ArrowRight' ? TABS[(index + 1) % TABS.length] : e.key === 'ArrowLeft' ? TABS[(index + TABS.length - 1) % TABS.length]
      : e.key === 'Home' ? TABS[0] : e.key === 'End' ? TABS[TABS.length - 1] : null;
    if (!next) return;
    e.preventDefault(); setTab(next);
    document.getElementById(`${id}-tab-${next}`)?.focus();
  }
  const panels: Record<Tab, ReactNode> = {
    overview: <OverviewTab detail={detail} update={update} onDeleted={onDeleted} reload={reload} onDeleteUnverified={onDeleteUnverified ?? reload} />,
    tools: <ToolsTab detail={detail} update={update} reload={reload} />,
    resources: <ResourcesTab detail={detail} reload={reload} />,
    prompts: <PromptsTab detail={detail} reload={reload} />,
    calls: <CallsTab detail={detail} />,
    mine: <MineTab detail={detail} update={update} reload={reload} />,
  };
  const count = (key: Tab) => key === 'tools' ? detail.counts.tools : key === 'resources' ? detail.counts.resources : key === 'prompts' ? detail.counts.prompts : null;
  return <>
    {authNotice && <div className={`notice ${authNotice.ok ? 'ok' : 'err'} mi-notice-row`} role={authNotice.ok ? 'status' : 'alert'}>
      <p>{authNotice.ok ? m('authOk') : m('authError', { reason: m(mcpErrorKey(authNotice.code) ?? 'mcp_oauth_failed') })}{!authNotice.ok && <> <code className="muted">{authNotice.code}</code></>}</p>
      {dismissAuth && <button type="button" className="btn sm" onClick={dismissAuth}>{m('dismiss')}</button>}
    </div>}
    <div className="tabs mi-tabs" role="tablist" aria-label={m('tabsLabel')} onKeyDown={onKey}>
      {TABS.map((key) => <button key={key} type="button" role="tab" id={`${id}-tab-${key}`} className={key === tab ? 'on' : ''}
        aria-selected={key === tab} aria-controls={`${id}-panel-${key}`} tabIndex={key === tab ? 0 : -1} onClick={() => setTab(key)}>
        {m(TAB_TEXT[key])}{count(key) != null ? ` (${count(key)})` : ''}</button>)}
    </div>
    <div id={`${id}-panel-${tab}`} role="tabpanel" aria-labelledby={`${id}-tab-${tab}`} className="mi-tab-panel">{panels[tab]}</div>
  </>;
}

export default function ConnectionDetailPage({ cid }: { cid: string }) {
  const { m } = useText();
  const location = useLocation();
  const navigate = useNavigate();
  const search = new URLSearchParams(location.search);
  const [tab, setTabState] = useState<Tab>(() => tabFromSearch(search));
  const [authNotice, setAuthNotice] = useState<OAuthOutcome>(() => oauthOutcome(search));
  const resource = useRead<ConnectionDetail>('agent.mcp.connection.get', { connection_id: cid });
  // The callback's ?auth=… is read once and taken out of the address, so a reload or a shared
  // link does not announce an outcome again.
  useEffect(() => {
    const current = new URLSearchParams(location.search);
    if (!current.has('auth')) return;
    current.delete('auth'); current.delete('code');
    navigate({ pathname: location.pathname, search: current.toString() ? `?${current}` : '' }, { replace: true });
  }, [location.pathname, location.search, navigate]);
  function setTab(next: Tab) {
    setTabState(next);
    const current = new URLSearchParams(location.search);
    current.delete('auth'); current.delete('code');
    if (next === 'overview') current.delete('tab'); else current.set('tab', next);
    navigate({ pathname: location.pathname, search: current.toString() ? `?${current}` : '' }, { replace: true });
  }
  const detail = resource.data;
  return <div className="mi-page col gap16">
    <header className="suite-head">
      <div>
        <p className="small"><Link to="/agent/integrations">‹ {m('back')}</Link></p>
        <h1>{detail?.name ?? m('title')}</h1>
        {detail && <p className="lede"><OwnerText summary={detail} /> · {detail.host ?? detail.url}</p>}
      </div>
      {detail && <div className="actions"><HealthBadge health={detail.health} /><ConnectionStatusBadge status={detail.status} stopped={platformStopped(detail)} />
        {detail.binding.bound && detail.binding.enabled && !platformStopped(detail) && <span className="badge info">{m('bound')}</span>}</div>}
    </header>
    {/* A failed re-read keeps the last detail on the page, so the failure is said beside it. */}
    {detail && resource.error != null && <ErrorNotice error={resource.error} retry={resource.reload} />}
    {resource.loading && !detail ? <Loading /> : resource.error != null && !detail ? <ErrorNotice error={resource.error} retry={resource.reload} />
      : detail && <DetailView detail={detail} tab={tab} setTab={setTab} update={resource.setData} authNotice={authNotice} dismissAuth={() => setAuthNotice(null)}
        onDeleted={() => navigate('/agent/integrations', { replace: true, state: { deleted: detail.name } })} reload={resource.reload}
        onDeleteUnverified={() => navigate('/agent/integrations', { replace: true, state: { unverified: { kind: 'delete', name: detail.name } } })} />}
  </div>;
}
