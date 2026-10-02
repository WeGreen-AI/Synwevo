import { useEffect, useId, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import { ApiError, post } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { ActionDialog } from './ActionDialog';
import { useAdminData } from './data';
import { errText } from './labels';
import { useUnsavedChanges } from '../lib/hooks';
import { MODEL_PROVIDER_API as API, routeTextKeys, routeLabels, emptyProviderForm, providerForm, copyProviderRoute,
  validateProviderForm, validProviderState, sameProviderVersion, sameProviderSpec, validProviderHistory,
  readProviderPreview, createProviderAttempt, validProviderPublication, clearProviderCredential, providerFailureMode,
  type ProviderSpec, type ProviderState, type ProviderHistory, type ProviderForm, type ProviderRequest,
  type ProviderPreview, type ProviderPublication, type ProviderAttempt, type ProviderEnvironment } from './model-provider';
import './model-provider.messages';

export interface ModelProviderTabProps { canRead: boolean; canManage: boolean; onDenied: () => void }
const grid: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 16 };
const fieldset: CSSProperties = { border: 0, padding: 0, margin: 0, minWidth: 0 };
const localError = (message: string) => new ApiError(400, 'model_provider_form', message);
const sameForm = (a: ProviderForm, b: ProviderForm) => Object.keys(a).every(key => a[key as keyof ProviderForm] === b[key as keyof ProviderForm]);
const credentialLabels = { keep: '保留当前凭据', replace: '设置或轮换密钥', revoke: '撤销密钥' };

function ProviderDetails({ spec }: { spec: ProviderSpec | null }) {
  const { t, locale } = useI18n();
  if (!spec) return <p className="muted">{t('尚未配置模型提供方。')}</p>;
  return <dl className="col gap8 small" style={{ margin: 0 }}>
    <div><dt>{t('路线启用状态')}</dt><dd style={{ margin: 0 }}>{t(spec.enabled ? '启用（调用仍受其他条件限制）' : '停用')}</dd></div>
    {!spec.route ? <div><dt>{t('路线')}</dt><dd style={{ margin: 0 }}>{t('未配置')}</dd></div> : <>
      {routeTextKeys.map(key => <div key={key}><dt>{t(routeLabels[key])}</dt><dd style={{ margin: 0 }}>{spec.route![key]}</dd></div>)}
      {(['input_token_ceiling', 'output_token_ceiling'] as const).map(key => <div key={key}><dt>{t(routeLabels[key])}</dt><dd style={{ margin: 0 }}>{spec.route![key]}</dd></div>)}
      <div><dt>{t(routeLabels.valid_until)}</dt><dd style={{ margin: 0 }}>{new Intl.DateTimeFormat(locale, {
        dateStyle: 'medium', timeStyle: 'long', timeZone: 'UTC',
      }).format(new Date(spec.route.valid_until * 1000))}</dd><dd style={{ margin: 0 }}>{t('UNIX 秒：{seconds}', { seconds: spec.route.valid_until })}</dd></div>
    </>}
  </dl>;
}

/** The coordinator supplies effective grants. A denied reader mounts no HTTP hooks. */
export function ModelProviderTab(props: ModelProviderTabProps) {
  const { t } = useI18n();
  if (!props.canRead) return <p className="notice" role="note">{t('你没有查看模型提供方配置的权限。')}</p>;
  return <ModelProviderContent {...props} />;
}

function ModelProviderContent({ canManage, onDenied }: ModelProviderTabProps) {
  const { t, locale } = useI18n();
  const id = useId();
  const current = useAdminData<ProviderState>(API, onDenied);
  const [cursors, setCursors] = useState<(number | null)[]>([null]);
  const cursor = cursors[cursors.length - 1];
  const history = useAdminData<ProviderHistory>(`${API}/history?limit=20${cursor === null ? '' : `&before_revision=${cursor}`}`, onDenied);
  const state = validProviderState(current.data) ? current.data : null;
  const environment = state?.environment;
  const [base, setBase] = useState<ProviderState | null>(null);
  const [form, setForm] = useState<ProviderForm>(emptyProviderForm);
  const [saved, setSaved] = useState<ProviderForm>(emptyProviderForm);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<'preview' | 'publish' | null>(null);
  const [tried, setTried] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [plan, setPlan] = useState<ProviderPreview | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const uncertainRef = useRef(false);
  const [result, setResult] = useState<ProviderPublication | null>(null);
  const [copy, setCopy] = useState<ProviderState | null>(null);
  const [now, setNow] = useState(Date.now());
  const [, keyChanged] = useState(0);
  const keyInput = useRef<HTMLInputElement>(null);
  const requestRef = useRef<ProviderRequest | null>(null);
  const attemptRef = useRef<ProviderAttempt | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const errorSummary = useRef<HTMLDivElement>(null);
  const flight = useRef(false);
  const alive = useRef(true);
  // The raw key lives only in this input and the exact in-memory request. Never in UI state,
  // logs, URLs, storage, server-state rendering or preview text.
  function clearRequests() {
    clearProviderCredential(requestRef.current); requestRef.current = null;
    clearProviderCredential(attemptRef.current?.body ?? null); attemptRef.current = null;
  }
  function clearSecrets() { clearRequests(); if (keyInput.current) keyInput.current.value = ''; }
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; clearSecrets(); };
  }, []);
  // Losing the manage grant disables confirmation but must not destroy an unresolved
  // request. Losing the read grant unmounts this content and runs the cleanup above.
  useEffect(() => { if (!canManage) setCopy(null); }, [canManage]);
  useEffect(() => { if (error && !dialogOpen) errorSummary.current?.focus(); }, [error, dialogOpen]);
  const canWrite = canManage && !!state && (environment === 'test' || environment === 'live') && !current.loading && !current.err;
  const changedBase = !!base && !!state && !sameProviderVersion(base, state);
  const changedEnvironment = !!plan && plan.environment !== environment;
  const frozen = !!busy || dialogOpen || uncertain || !!copy;
  const dirty = !sameForm(form, saved) || !!keyInput.current?.value;
  const validation = validateProviderForm(form, keyInput.current?.value || '');
  const expired = !!plan && now >= plan.expires_at * 1000;
  const historyValid = validProviderHistory(history.data, cursor, environment);
  const attempt = attemptRef.current;
  useUnsavedChanges(dirty || uncertain || !!busy);
  useEffect(() => {
    if (!base && state && !current.loading && !current.err) {
      setBase(state); const next = providerForm(state.spec); setForm(next); setSaved(next);
    }
  }, [base, state, current.loading, current.err]);
  useEffect(() => {
    if (!plan) return;
    setNow(Date.now()); const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [plan]);
  const environmentName = (env: ProviderEnvironment | undefined) => t(env === 'live' ? '正式环境' : env === 'test' ? '测试环境' : env === null ? '未配置' : '正在读取环境…');
  const time = (seconds: number) => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'long', timeZone: 'UTC' }).format(new Date(seconds * 1000));
  const credentialStatus = (configured: boolean) => t(configured ? '已配置（仅状态）' : '未配置');
  function fail(err: unknown) {
    setError(err);
    if (err instanceof ApiError && !err.upstream) {
      if (err.status === 409) setConflict(true);
      if (err.status === 404 || ['platform_permission_denied', 'platform_admin_required'].includes(err.code)) onDenied();
    }
  }
  function refresh() { current.reload(); history.reload(); }
  function edit<K extends keyof ProviderForm>(key: K, value: ProviderForm[K]) {
    if (!canWrite || frozen || flight.current) return;
    setForm(previous => ({ ...previous, [key]: value })); setPlan(null); clearRequests(); setResult(null); setError(null);
  }
  function adoptLatest() {
    if (!state || !canWrite || frozen || flight.current || (base?.environment === state.environment && state.revision < base.revision)) return;
    setBase(state); setSaved(providerForm(state.spec)); setConflict(false); setPlan(null); clearRequests(); setError(null);
    heading.current?.focus();
  }
  function applyCopy(item: ProviderState) {
    if (!canWrite || flight.current || uncertainRef.current || !item.spec || item.environment !== environment) return;
    setForm(previous => copyProviderRoute(previous, item.spec!)); setPlan(null); clearRequests(); setTried(false); setError(null); setResult(null); setCopy(null);
    requestAnimationFrame(() => heading.current?.focus());
  }
  async function preview(event: FormEvent) {
    event.preventDefault(); setTried(true);
    if (!canWrite || !base || frozen || flight.current || conflict || changedBase) return;
    const checked = validateProviderForm(form, keyInput.current?.value || '');
    if (!checked.spec || !checked.credential) { document.getElementById(`${id}-${Object.keys(checked.errors)[0]}`)?.focus(); return; }
    flight.current = true; setBusy('preview'); setError(null); setConfirmed(false); setPlan(null); clearRequests();
    const request = { expected_revision: base.revision, spec: checked.spec, credential: checked.credential };
    requestRef.current = request;
    try {
      const out = await post<unknown>(`${API}/preview`, request);
      if (!alive.current) return;
      const next = readProviderPreview(out, base, request);
      if (!next) throw localError('提供方预览与当前版本或输入不一致，或已过期。请重新读取并预览。');
      setPlan(next); setDialogOpen(true); setNow(Date.now());
    } catch (err) { if (alive.current) { clearRequests(); fail(err); } }
    finally { flight.current = false; if (alive.current) setBusy(null); }
  }
  async function publish(reason: string): Promise<ProviderPublication> {
    if (!alive.current || flight.current || !plan || !canWrite || !confirmed || changedEnvironment) throw localError('请先核对提供方预览及发布影响。');
    if (!uncertainRef.current && (changedBase || conflict || Date.now() >= plan.expires_at * 1000)) throw localError('提供方预览已失效，请关闭并重新预览。');
    let original = attemptRef.current;
    if (!original) {
      if (!requestRef.current) throw localError('提供方预览已失效，请关闭并重新预览。');
      if (Array.from(reason.trim()).length < 8 || Array.from(reason.trim()).length > 500) throw localError('发布理由须为 8–500 个字符。');
      original = createProviderAttempt(plan, requestRef.current, reason, crypto.randomUUID());
      attemptRef.current = original;
    }
    if (original.environment !== environment) throw localError('环境已变化，不能在另一环境重试原请求。');
    flight.current = true; setBusy('publish'); setError(null);
    try {
      const out = await post<unknown>(`${API}/publish`, original.body);
      if (!validProviderPublication(out, original)) throw new Error('Invalid provider publication response');
      return out;
    } catch (err) {
      if (alive.current) {
        const mode = providerFailureMode(uncertainRef.current, { application: err instanceof ApiError && !err.upstream,
          status: err instanceof ApiError ? err.status : undefined, code: err instanceof ApiError ? err.code : undefined });
        if (mode === 'uncertain') { uncertainRef.current = true; setUncertain(true); }
        else if (mode === 'refused') { clearRequests(); setPlan(null); setDialogOpen(false); }
        if (mode !== 'reauth') fail(err);
      }
      throw err;
    } finally { flight.current = false; if (alive.current) setBusy(null); }
  }
  function completed(out: ProviderPublication) {
    if (!alive.current) return;
    clearSecrets(); uncertainRef.current = false; setUncertain(false); setResult(out); setBase(out.state);
    const next = providerForm(out.state.spec); setForm(next); setSaved(next); setTried(false); setConflict(false);
    setPlan(null); setDialogOpen(false); setConfirmed(false); setCursors([null]); refresh();
    requestAnimationFrame(() => heading.current?.focus());
  }
  function details(item: ProviderState) {
    return <div className="col gap8">
      <p>{t('版本 {revision} · {environment}', { revision: item.revision, environment: environmentName(item.environment) })}</p>
      <p className="small">{t('提供方：{provider} · 固定成本账户：{account}', { provider: item.provider, account: item.account_ref })}</p>
      <p>{t('凭据：{status} · 凭据版本 {revision}', { status: credentialStatus(item.credential_configured), revision: item.credential_revision })}</p>
      <ProviderDetails spec={item.spec} />
      {item.created_at !== null && <p className="small muted">{t('发布于 {time} · 操作者 {actor}', { time: time(item.created_at), actor: item.actor_id || '—' })}</p>}
      {item.reason !== null && <p className="small" style={{ whiteSpace: 'pre-wrap' }}>{t('发布理由：{reason}', { reason: item.reason })}</p>}
      {item.operation_id && <p className="small muted">{t('操作编号：{id}', { id: item.operation_id })}</p>}
    </div>;
  }
  function field(key: typeof routeTextKeys[number] | 'input_token_ceiling' | 'output_token_ceiling' | 'valid_until') {
    const problem = tried ? validation.errors[key] : undefined;
    const numeric = key === 'input_token_ceiling' || key === 'output_token_ceiling';
    return <label key={key} className="field" htmlFor={`${id}-${key}`} style={{ minWidth: 0 }}>
      <span>{t(routeLabels[key])}</span>
      <input id={`${id}-${key}`} className="input" type={key === 'valid_until' ? 'datetime-local' : 'text'}
        step={key === 'valid_until' ? 1 : undefined} inputMode={numeric ? 'numeric' : undefined} required value={form[key]}
        onChange={event => edit(key, event.target.value)} aria-invalid={!!problem}
        aria-describedby={`${id}-route-help${problem ? ` ${id}-${key}-error` : ''}`} style={{ minWidth: 0, width: '100%' }} />
      {problem && <span className="small" id={`${id}-${key}-error`}>{t(problem)}</span>}
    </label>;
  }
  return <div className="col gap16" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
    <section className="card card-b col gap12" aria-labelledby={`${id}-title`}>
      <div className="row wrap gap12"><h2 id={`${id}-title`} className="grow">{t('模型提供方')}</h2>
        <button type="button" className="btn" disabled={current.loading || history.loading || !!busy || dialogOpen} onClick={refresh}>{t('刷新配置与历史')}</button></div>
      <p className="notice info">{t('提供方配置环境：{environment}', { environment: environmentName(environment) })}</p>
      <p>{t('管理平台模型提供方的凭据和路线。模型、账户、证据及价格须先在实际业务中核实，再填写对应引用。')}</p>
      <p className="small muted">{t('配置状态不代表已连接或已通过模型验证。本页不会发起模型探测、模型调用或支付。')}</p>
      <p className="small muted">{t('密钥轮换或撤销不会重置固定成本账户 platform-deepseek，也不会清除历史费用或待确认用量。')}</p>
      {!canManage && <p className="notice">{t('你可以查看提供方配置和历史，不能修改或发布。')}</p>}
      {state?.environment === null && <p className="notice warn">{t('计费环境尚未配置，提供方编辑与发布已禁用。')}</p>}
      {current.err && <p className="notice err" role="alert">{t(current.err)}</p>}
      {!current.loading && !current.err && !state && <p className="notice err" role="alert">{t('无法确认服务器的提供方配置，请刷新后重试。')}</p>}
      {error != null && <div ref={errorSummary} tabIndex={-1} className="notice err" role="alert"><p>{t('提供方请求未完成。你的输入已保留，请核对当前状态。')}</p><p>{errText(error)}</p></div>}
      {result && <p className="notice ok" role="status">{t('提供方配置版本 {revision} 已发布，操作编号 {id}。', { revision: result.state.revision, id: result.operation_id })}</p>}
      {uncertain && <div className="notice warn col gap8" role="alert">
        <p>{t('发布结果尚不确定，输入已锁定。请保留此页面，仅重试原请求；刷新记录不能确认原请求失败。')}</p>
        {attempt && <p className="small">{t('原请求编号：{id}', { id: attempt.body.idempotency_key })}</p>}
        {changedEnvironment && <p>{t('环境已变化，不能在另一环境重试原请求。')}</p>}
        <div><button type="button" className="btn" disabled={!canWrite || !attempt || !plan || !!busy || dialogOpen || changedEnvironment}
          onClick={() => { setConfirmed(false); setDialogOpen(true); }}>{t('核对并重试原请求')}</button></div>
      </div>}
    </section>
    <section className="card card-b col gap12" aria-labelledby={`${id}-current`} aria-busy={current.loading}>
      <h3 id={`${id}-current`}>{t('当前服务器版本')}</h3>
      {current.loading ? <p role="status">{t('正在读取提供方配置…')}</p> : !current.err && state && details(state)}
    </section>
    {canManage && base && <section className="card card-b col gap12" aria-labelledby={`${id}-editor`}>
      <h3 id={`${id}-editor`} ref={heading} tabIndex={-1}>{t('编辑下一版本')}</h3>
      <p className="small">{t('以配置版本 {revision} 和凭据版本 {credential} 为基础；路线切换与该版本绑定。', { revision: base.revision, credential: base.credential_revision })}</p>
      {(changedBase || conflict) && <div className="notice warn col gap8" role="alert">
        <p>{t('服务器版本或发布条件已变化。输入已保留；请刷新并比较当前版本，再继续。')}</p>
        <div className="row wrap gap8"><button type="button" className="btn" disabled={current.loading || !!busy || dialogOpen} onClick={current.reload}>{t('读取最新提供方配置')}</button>
          <button type="button" className="btn" disabled={!canWrite || frozen || (state?.environment === base.environment && state.revision < base.revision)} onClick={adoptLatest}>{t('保留输入，采用当前版本继续')}</button></div>
      </div>}
      <form className="col gap16" onSubmit={preview} noValidate>
        <fieldset className="col gap12" style={fieldset} disabled={!canWrite || frozen}>
          <legend>{t('凭据操作')}</legend>
          <label className="field" htmlFor={`${id}-credential_action`}><span>{t('选择凭据操作')}</span>
            <select id={`${id}-credential_action`} className="input" value={form.credential_action} onChange={event => edit('credential_action', event.target.value as ProviderForm['credential_action'])}>
              {(['keep', 'replace', 'revoke'] as const).map(action => <option key={action} value={action}>{t(credentialLabels[action])}</option>)}
            </select></label>
          {form.credential_action === 'replace' && <label className="field" htmlFor={`${id}-api_key`}>
            <span>{t('新提供方密钥')}</span>
            <input ref={keyInput} id={`${id}-api_key`} className="input" type="password" autoComplete="new-password" autoCapitalize="none" spellCheck={false} required
              onChange={() => { if (!canWrite || frozen || flight.current) return; keyChanged(value => value + 1); setPlan(null); clearRequests(); setResult(null); }}
              aria-invalid={tried && !!validation.errors.api_key} aria-describedby={`${id}-key-help${tried && validation.errors.api_key ? ` ${id}-api_key-error` : ''}`} />
            <span id={`${id}-key-help`} className="small muted">{t('密钥仅留在本页内存，用于本次预览、发布及原请求重试。成功或离开页面后清理，不会回显已有密钥。')}</span>
            {tried && validation.errors.api_key && <span className="small" id={`${id}-api_key-error`}>{t(validation.errors.api_key)}</span>}
          </label>}
          {form.credential_action === 'revoke' && <p className="notice warn">{t('撤销后该凭据不可用于新的模型调用；历史密钥不能恢复。若路线启用，请同时停用路线。')}</p>}
        </fieldset>
        <fieldset className="col gap12" style={fieldset} disabled={!canWrite || frozen}>
          <legend>{t('路线配置')}</legend>
          <label className="row gap8"><input id={`${id}-enabled`} type="checkbox" checked={form.enabled} onChange={event => edit('enabled', event.target.checked)} />
            <span>{t('启用路线')}</span></label>
          <label className="field" htmlFor={`${id}-route_mode`}><span>{t('路线')}</span>
            <select id={`${id}-route_mode`} className="input" value={form.route_mode} onChange={event => edit('route_mode', event.target.value as ProviderForm['route_mode'])}
              aria-invalid={tried && !!validation.errors.route_mode} aria-describedby={tried && validation.errors.route_mode ? `${id}-route_mode-error` : undefined}>
              <option value="none">{t('不配置路线')}</option><option value="route">{t('填写已核实的路线')}</option></select>
            {tried && validation.errors.route_mode && <span className="small" id={`${id}-route_mode-error`}>{t(validation.errors.route_mode)}</span>}
          </label>
          <p className="small muted" id={`${id}-route-help`}>{t('填写真实模型、路线、证据及已发布价格的版本 ID；本页不提供默认批准项。有效期按 UTC 输入，精确到秒。')}</p>
          {form.route_mode === 'route' && <div style={grid}>{routeTextKeys.map(field)}{field('input_token_ceiling')}{field('output_token_ceiling')}{field('valid_until')}</div>}
          <p className="small muted">{t('启用仍须满足凭据、核实有效期、价格、成本策略及业务授权条件；停用不撤销已发送的调用或既有费用。')}</p>
        </fieldset>
        {tried && !validation.spec && <p className="notice err" role="alert">{t('请修正已标出的提供方字段，再预览变更。')}</p>}
        <div><button type="submit" className="btn primary" disabled={!canWrite || frozen || changedBase || conflict ||
          (validation.spec !== null && sameProviderSpec(validation.spec, base.spec) && form.credential_action === 'keep')}>{t('预览提供方变更')}</button></div>
        {dirty && <p className="small muted">{t('输入尚未发布，当前生效版本保持不变。')}</p>}
      </form>
    </section>}
    <section className="card card-b col gap12" aria-labelledby={`${id}-history`} aria-busy={history.loading}>
      <h3 id={`${id}-history`}>{t('提供方发布历史')}</h3>
      <p className="small muted">{t('历史不可修改。复制仅将路线及启用状态填入编辑器，须作为新版本预览和发布；不恢复历史密钥，也不改变当前选择的凭据操作。')}</p>
      {history.err && <p className="notice err" role="alert">{t(history.err)}</p>}
      {history.loading ? <p role="status">{t('正在读取发布历史…')}</p> : !history.err && (historyValid ? <>
        {!history.data!.items.length && <p className="muted">{t('此页没有已发布的提供方配置。')}</p>}
        {history.data!.items.map(item => <details key={`${item.environment}:${item.revision}`} className="notice">
          <summary>{t('查看提供方版本 {revision} · {time}', { revision: item.revision, time: item.created_at === null ? '—' : time(item.created_at) })}</summary>
          <div className="col gap12" style={{ paddingTop: 12 }}>{details(item)}
            {canManage && <div><button type="button" className="btn" disabled={!canWrite || frozen || !item.spec || item.environment !== environment}
              onClick={() => { if (dirty) setCopy(item); else applyCopy(item); }}>{t('复制路线到编辑器')}</button></div>}
          </div>
        </details>)}
      </> : <p className="notice err" role="alert">{t('无法确认提供方历史记录，请刷新后重试。')}</p>)}
      <nav className="row wrap gap8" aria-label={t('提供方历史分页')}>
        <button type="button" className="btn" disabled={history.loading || cursors.length === 1 || !!busy || dialogOpen} onClick={() => setCursors(values => values.slice(0, -1))}>{t('较新一页')}</button>
        <span className="small">{t('第 {page} 页，每页最多 20 条', { page: cursors.length })}</span>
        <button type="button" className="btn" disabled={history.loading || !!history.err || !historyValid || history.data?.next_before_revision == null || !!busy || dialogOpen}
          onClick={() => { const next = history.data?.next_before_revision; if (next != null) setCursors(values => [...values, next]); }}>{t('较旧一页')}</button>
        {cursors.length > 1 && <button type="button" className="btn" disabled={history.loading || !!busy || dialogOpen} onClick={() => setCursors([null])}>{t('返回最新版本')}</button>}
      </nav>
    </section>
    {busy && <p role="status" className="small muted">{t('正在等待服务器响应…')}</p>}
    {copy && <ActionDialog title={t('替换尚未发布的路线输入？')} confirmLabel={t('替换路线输入')} reason="none"
      impact={t('历史路线及启用状态会替换当前路线输入。凭据操作和新密钥输入保持不变，历史密钥不会恢复。')}
      run={async () => copy} onDone={applyCopy} onClose={() => setCopy(null)} />}
    {dialogOpen && plan && <ActionDialog<ProviderPublication> title={t('确认发布提供方配置')} confirmLabel={t(uncertain ? '重试原请求' : '发布提供方配置')}
      reason={attempt ? 'none' : 'required'} reauthScope="admin" size="wide"
      ready={canWrite && confirmed && !busy && !changedEnvironment && (uncertain || (!expired && !changedBase && !conflict))}
      impact={<div className="col gap12">
        <p><b>{t('高风险操作 · {environment}', { environment: environmentName(plan.environment) })}</b></p>
        {!canManage && <p className="notice warn" role="alert">{t('管理权限已变化，发布与重试已禁用。尚未确认结果的原请求仍保留在本页内存。')}</p>}
        <p>{t('提供方：{provider} · 固定成本账户：{account}', { provider: plan.before.provider, account: plan.before.account_ref })}</p>
        <div style={grid}><div className="col gap8"><h4>{t('变更前：版本 {revision}', { revision: plan.before.revision })}</h4>
          <ProviderDetails spec={plan.before.spec} /><p>{t('凭据：{status} · 凭据版本 {revision}', { status: credentialStatus(plan.before.credential_configured), revision: plan.before.credential_revision })}</p></div>
          <div className="col gap8"><h4>{t('准备发布的新设置')}</h4><ProviderDetails spec={plan.after} />
            <p>{t('凭据操作：{action}', { action: t(credentialLabels[plan.credential.action]) })}</p>
            <p>{t('凭据：{status} · 凭据版本 {revision}', { status: credentialStatus(plan.credential_configured), revision: plan.credential_revision })}</p></div></div>
        <p>{t('请核对全部路线、证据、价格版本、上限和有效期。发布需要近期管理员身份验证；轮换不会重置固定成本账户，历史密钥无法恢复。')}</p>
        <p className="small">{t('服务器确认预览有效至 {time}。', { time: time(plan.expires_at) })}</p>
        {attempt && <><p className="small" style={{ whiteSpace: 'pre-wrap' }}>{t('原发布理由：{reason}', { reason: attempt.body.reason })}</p><p className="small">{t('原请求编号：{id}', { id: attempt.body.idempotency_key })}</p></>}
        {uncertain && <p className="notice warn">{t('重试会使用完全相同的密钥、请求体、理由及请求编号，即使原预览已到期。不会改用新请求。')}</p>}
        {!uncertain && (expired || changedBase || conflict) && <p className="notice warn" role="alert">{t('提供方预览已失效，请关闭并重新预览。')}</p>}
        {changedEnvironment && <p className="notice warn" role="alert">{t('环境已变化，不能在另一环境重试原请求。')}</p>}
      </div>}
      run={publish} onDone={completed} onClose={() => { if (flight.current) return; setDialogOpen(false); setConfirmed(false); if (!uncertainRef.current) { setPlan(null); clearRequests(); } }}>
      <label className="row gap8" style={{ alignItems: 'flex-start' }}><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />
        <span>{t('我已核对环境、配置前后差异、凭据操作和发布影响。')}</span></label>
    </ActionDialog>}
  </div>;
}
