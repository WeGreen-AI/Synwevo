import { useEffect, useId, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import { ApiError, post } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { ActionDialog } from './ActionDialog';
import { useAdminData } from './data';
import { errText } from './labels';
import { useUnsavedChanges } from '../lib/hooks';
import { emptyMarkupForm, localInput, validateMarkup, type Band, type BandRates, type MarkupForm, type MarkupProblem, type MarkupSpec,
  type RateKey } from './model-price-bands';
import './model-price-bands.messages';

/** Sale price = a published cost price x an administrator's markup, published as a new immutable
 *  sale price version. Publishing changes no route; the model provider tab adopts it. */
type Environment = 'test' | 'live' | null;
type Unit = { code: string; scale: number };
type RateTable = Partial<Record<Band, BandRates>>;
type CostRecord = { id: string; status: 'draft' | 'published'; price_id: string | null;
  spec: { name: string; purpose: 'sale' | 'cost'; model: string; unit: Unit; off_peak?: BandRates | null } };
type PricePage = { environment: Environment; items: CostRecord[] };
export type MarkupVersion = { price_id: string; model: string; spec: MarkupSpec; unit: Unit; sale_rates: RateTable; actor_id: string;
  reason: string; operation_id: string; created_at: number };
type MarkupList = { environment: Environment; items: MarkupVersion[] };
type Example = { cost: string; sale: string };
type Plan = { spec: MarkupSpec; model: string; unit: Unit; cost_rates: RateTable; sale_rates: RateTable; example_tokens: 1000;
  examples: Partial<Record<Band, Record<RateKey, Example>>>; replaces_price_id: string | null; preview_token: string; expires_at: number;
  risk: 'high'; environment: Environment };
type Publication = { operation_id: string; status: 'succeeded'; markup: MarkupVersion };
type Attempt = { environment: Environment; body: { spec: MarkupSpec; preview_token: string; idempotency_key: string; reason: string } };
const API = '/api/admin/billing/prices';
const RATE_KEYS: RateKey[] = ['input_uncached', 'input_cached', 'output'];
const RATE_NAMES: Record<RateKey, string> = { input_uncached: '未缓存输入', input_cached: '缓存输入', output: '输出' };
export const BAND_NAMES: Record<Band, string> = { flat: '统一费率', peak: '高峰', off_peak: '低谷' };
const BAND_ORDER: Band[] = ['flat', 'peak', 'off_peak'];
const PROBLEMS: Record<MarkupProblem, string> = {
  name: '名称须为 1–80 个字符，不能包含控制字符。',
  cost_price_id: '请选择一个已发布的成本价。',
  markup: '倍率须为 1 至 100，最多四位小数，例如 1.5。',
  off_peak_markup: '低谷倍率须为 1 至 100，最多四位小数；只有分时成本价可以单独设置。留空表示与高峰相同。',
  terms_ref: '条款引用须以英文字母或数字开头，共 1–200 位，仅限英文字母、数字及 . _ : / -，不能包含查询参数。',
  effective: '请输入有效的本地日期与时间，精确到秒。',
};
const grid: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 12 };
const fieldset: CSSProperties = { margin: 0, padding: 0, border: 0, minWidth: 0 };
const uncertainResponse = (err: unknown) => !(err instanceof ApiError) || err.upstream || err.status >= 500 || err.status === 408 || err.status === 0;
const localError = (message: string) => new ApiError(400, 'model_price_markup_form', message);
const sameSpec = (a: MarkupSpec, b: MarkupSpec) => (['name', 'cost_price_id', 'markup', 'off_peak_markup', 'terms_ref', 'effective_from'] as const)
  .every(key => a[key] === b[key]);
const bands = (table: RateTable) => BAND_ORDER.filter(band => table[band]);
const validTable = (table: unknown): table is RateTable => !!table && typeof table === 'object' &&
  Object.entries(table as RateTable).every(([band, rates]) => BAND_ORDER.includes(band as Band) && !!rates &&
    RATE_KEYS.every(key => typeof rates[key] === 'string'));
export function validMarkupVersion(value: unknown): value is MarkupVersion {
  const v = value as MarkupVersion;
  return !!v && typeof v.price_id === 'string' && typeof v.model === 'string' && !!v.spec && typeof v.spec.markup === 'string' &&
    !!v.unit && typeof v.unit.code === 'string' && validTable(v.sale_rates) && Number.isSafeInteger(v.created_at);
}

/** Rates per million tokens, one column per band: "cost → sale" when both are given. */
export function RateTableView({ unit, cost, sale, caption }: { unit: Unit; cost?: RateTable; sale: RateTable; caption: string }) {
  const { t } = useI18n();
  return <div className="tbl-wrap"><table className="tbl">
    <caption className="small" style={{ textAlign: 'left' }}>{caption}</caption>
    <thead><tr><th scope="col">{t('Token 类型')}</th>{bands(sale).map(band => <th key={band} scope="col">{t(BAND_NAMES[band])}</th>)}</tr></thead>
    <tbody>{RATE_KEYS.map(key => <tr key={key}><th scope="row">{t(RATE_NAMES[key])}</th>
      {bands(sale).map(band => <td key={band}>{cost?.[band]
        ? t('{cost} → {sale} {code}', { cost: cost[band]![key], sale: sale[band]![key], code: unit.code })
        : t('{amount} {code}', { amount: sale[band]![key], code: unit.code })}</td>)}</tr>)}</tbody>
  </table></div>;
}

export function ModelPriceMarkups({ manage, onDenied }: { manage: boolean; onDenied: () => void }) {
  const { t, locale } = useI18n();
  const id = useId();
  const listing = useAdminData<MarkupList>(`${API}/markups`, onDenied);
  const prices = useAdminData<PricePage>(`${API}?limit=100`, onDenied);
  const environment = listing.data?.environment;
  const costs = (prices.data?.items || []).filter(item => item.status === 'published' && item.price_id && item.spec.purpose === 'cost');
  const markups = (listing.data?.items || []).filter(validMarkupVersion);
  const [form, setForm] = useState<MarkupForm>(emptyMarkupForm);
  const [tried, setTried] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<'preview' | 'publish' | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [result, setResult] = useState<Publication | null>(null);
  const [now, setNow] = useState(Date.now());
  const flight = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!plan) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [plan]);
  const selected = costs.find(item => item.price_id === form.cost_price_id) || null;
  const banded = !!selected?.spec.off_peak;
  const validation = validateMarkup(form, banded);
  const canWrite = manage && (environment === 'test' || environment === 'live') && !listing.loading && !listing.err;
  const frozen = !!busy || dialogOpen || uncertain;
  const dirty = Object.values(form).some(value => value !== '');
  const expired = !!plan && now >= plan.expires_at * 1000;
  const changed = !!plan && (!validation.spec || !sameSpec(validation.spec, plan.spec));
  useUnsavedChanges((dirty && !result) || uncertain || !!busy);

  const environmentName = (env: Environment | undefined) => t(env === 'live' ? '正式环境' : env === 'test' ? '测试环境' : '未配置');
  const time = (seconds: number) => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'long' }).format(new Date(seconds * 1000));
  function fail(err: unknown) {
    setError(err);
    if (err instanceof ApiError && !err.upstream && ['platform_permission_denied', 'platform_admin_required'].includes(err.code)) onDenied();
  }
  function edit(key: keyof MarkupForm, value: string) {
    if (frozen || !canWrite) return;
    const next = { ...form, [key]: value };
    // A flat cost price has no off-peak rates to mark up separately.
    if (key === 'cost_price_id' && !costs.find(item => item.price_id === value)?.spec.off_peak) next.off_peak_markup = '';
    if (key === 'cost_price_id' && !form.effective) next.effective = localInput(Math.floor(Date.now() / 1000));
    setForm(next); setPlan(null); setAttempt(null); setResult(null);
  }
  async function preview(event: FormEvent) {
    event.preventDefault(); setTried(true);
    if (!canWrite || frozen || flight.current) return;
    if (!validation.spec) { document.getElementById(`${id}-${validation.problems[0]}`)?.focus(); return; }
    flight.current = true; setBusy('preview'); setError(null); setConfirmed(false); setPlan(null); setAttempt(null);
    const spec = validation.spec;
    try {
      const out = await post<Plan>(`${API}/markups/preview`, { spec });
      if (!alive.current) return;
      if (!out?.spec || !sameSpec(out.spec, spec) || out.environment !== environment || out.risk !== 'high' || !out.preview_token ||
        typeof out.expires_at !== 'number' || out.expires_at * 1000 <= Date.now() || !validTable(out.cost_rates) || !validTable(out.sale_rates) ||
        out.example_tokens !== 1000 || !out.examples) {
        throw localError('加价预览与当前输入不一致或已过期，请重新预览。');
      }
      setPlan(out); setNow(Date.now()); setDialogOpen(true);
    } catch (err) { if (alive.current) fail(err); } finally { flight.current = false; if (alive.current) setBusy(null); }
  }
  async function publish(reason: string): Promise<Publication> {
    if (flight.current || !plan || !canWrite || !confirmed) throw localError('请先核对有效的加价预览和发布影响。');
    let original = attempt;
    if (!uncertain && (changed || Date.now() >= plan.expires_at * 1000)) throw localError('加价预览已失效，请关闭并重新预览。');
    if (!original) {
      if (Array.from(reason.trim()).length < 8 || Array.from(reason.trim()).length > 500) throw localError('发布理由须为 8–500 个字符。');
      original = { environment, body: { spec: plan.spec, preview_token: plan.preview_token, idempotency_key: crypto.randomUUID(), reason: reason.trim() } };
      setAttempt(original);
    }
    if (original.environment !== environment) throw localError('环境已变化，不能在另一环境重试原请求。');
    flight.current = true; setBusy('publish'); setError(null);
    try {
      const out = await post<Publication>(`${API}/markups/publish`, original.body);
      if (out?.status !== 'succeeded' || !out.operation_id || !validMarkupVersion(out.markup) || !sameSpec(out.markup.spec, original.body.spec)) {
        throw new Error('Invalid markup publication response');
      }
      return out;
    } catch (err) {
      if (alive.current) {
        if (uncertain || uncertainResponse(err)) setUncertain(true);
        else if (!(err instanceof ApiError && err.code === 'reauth_required')) { setAttempt(null); setPlan(null); setDialogOpen(false); }
        fail(err);
      }
      throw err;
    } finally { flight.current = false; if (alive.current) setBusy(null); }
  }
  function completed(out: Publication) {
    if (!alive.current) return;
    setUncertain(false); setAttempt(null); setResult(out); setPlan(null); setDialogOpen(false); setConfirmed(false);
    setForm(emptyMarkupForm()); setTried(false); listing.reload(); prices.reload();
  }
  function field(key: Exclude<MarkupProblem, 'cost_price_id'>, label: string, options: { maxLength: number; decimal?: boolean; type?: 'datetime-local'; help?: string }) {
    const problem = tried && validation.problems.includes(key);
    return <label className="field" htmlFor={`${id}-${key}`} key={key} style={{ minWidth: 0 }}>
      <span>{t(label)}</span>
      <input id={`${id}-${key}`} className="input" type={options.type || 'text'} inputMode={options.decimal ? 'decimal' : undefined}
        step={options.type ? 1 : undefined} maxLength={options.maxLength} value={form[key]} onChange={event => edit(key, event.target.value)}
        aria-invalid={problem} aria-describedby={[options.help && `${id}-${key}-help`, problem && `${id}-${key}-error`].filter(Boolean).join(' ') || undefined}
        style={{ width: '100%', minWidth: 0 }} />
      {options.help && <span id={`${id}-${key}-help`} className="small muted">{t(options.help)}</span>}
      {problem && <span id={`${id}-${key}-error`} className="small">{t(PROBLEMS[key])}</span>}
    </label>;
  }

  return <section className="card card-b col gap12" aria-labelledby={`${id}-title`} aria-busy={listing.loading} style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
    <h3 id={`${id}-title`}>{t('售价加价（成本价 × 倍率）')}</h3>
    <p className="small">{t('售价由已发布的成本价乘以管理员设定的倍率生成，按相同时段计价；可为低谷时段单独设定倍率。倍率不低于 1，不会低于成本销售。')}</p>
    <p className="small">{t('发布只保存一个不可修改的新售价版本，不改变任何路线。要让用户按新售价付费，请在「模型提供方」中让路线采用该售价；采用后，用户尚未使用的报价与持续同意会失效并要求重新确认。')}</p>
    <p className="notice info">{t('加价环境：{environment}', { environment: environmentName(environment) })}</p>
    {listing.err && <p className="notice err" role="alert">{t(listing.err)}</p>}
    {environment === null && <p className="notice warn">{t('计费环境尚未配置，加价发布已禁用。')}</p>}
    {!manage && <p className="notice">{t('你可以查看加价版本，不能发布。')}</p>}
    {error != null && <div className="notice err" role="alert"><p>{t('加价请求未完成。你的输入已保留，请核对下方状态。')}</p><p>{errText(error)}</p></div>}
    {result && <p className="notice ok" role="status">{t('已发布售价版本 {price}（模型 {model}），操作编号 {id}。尚未被任何路线采用。', { price: result.markup.price_id, model: result.markup.model, id: result.operation_id })}</p>}
    {uncertain && attempt && <div className="notice warn col gap8" role="alert">
      <p>{t('发布结果尚不确定，输入已锁定。请保留此页面，仅重试原请求；刷新记录不能确认原请求失败。')}</p>
      <p className="small">{t('原请求编号：{id}', { id: attempt.body.idempotency_key })}</p>
      <div><button type="button" className="btn" disabled={!canWrite || !!busy || dialogOpen} onClick={() => { setConfirmed(false); setDialogOpen(true); }}>{t('核对并重试原请求')}</button></div>
    </div>}

    {manage && <form className="col gap12" onSubmit={preview} noValidate aria-labelledby={`${id}-editor`}>
      <h4 id={`${id}-editor`}>{t('发布新的加价售价')}</h4>
      {prices.err && <p className="notice err" role="alert">{t(prices.err)}</p>}
      {!prices.loading && !prices.err && !costs.length && <p className="notice">{t('最近 100 条价格记录中没有已发布的成本价。请先在上方发布成本价。')}</p>}
      <fieldset className="col gap12" style={fieldset} disabled={!canWrite || frozen}>
        <legend className="small">{t('所有字段均须填写；低谷倍率可留空')}</legend>
        <label className="field" htmlFor={`${id}-cost_price_id`} style={{ minWidth: 0 }}><span>{t('成本价（加价基础）')}</span>
          <select id={`${id}-cost_price_id`} className="input" value={form.cost_price_id} onChange={event => edit('cost_price_id', event.target.value)}
            aria-invalid={tried && validation.problems.includes('cost_price_id')} style={{ width: '100%', minWidth: 0 }}>
            <option value="">{t('请选择已发布的成本价')}</option>
            {costs.map(item => <option key={item.price_id!} value={item.price_id!}>{t('{name} · {model} · {kind} · {id}', { name: item.spec.name,
              model: item.spec.model, kind: t(item.spec.off_peak ? '分时' : '统一费率'), id: item.price_id! })}</option>)}
          </select>
          {tried && validation.problems.includes('cost_price_id') && <span className="small">{t(PROBLEMS.cost_price_id)}</span>}
        </label>
        <div style={grid}>
          {field('name', '售价名称', { maxLength: 80 })}
          {field('markup', banded ? '高峰倍率' : '倍率', { maxLength: 8, decimal: true, help: '例如 1.5 表示售价为成本价的 1.5 倍。' })}
          {banded && field('off_peak_markup', '低谷倍率（可选）', { maxLength: 8, decimal: true, help: '留空则低谷与高峰使用同一倍率；低谷售价不能高于高峰售价。' })}
          {field('terms_ref', '条款引用标识', { maxLength: 200 })}
          {field('effective', '生效日期与时间（本地）', { maxLength: 19, type: 'datetime-local' })}
        </div>
      </fieldset>
      {tried && !validation.spec && <p className="notice err" role="alert">{t('请修正已标出的加价字段，再预览。')}</p>}
      <div><button type="submit" className="btn primary" disabled={!canWrite || frozen}>{t('预览加价与示例费用')}</button></div>
    </form>}

    <h4>{t('已发布的加价售价')}</h4>
    {listing.loading ? <p role="status">{t('正在读取加价版本…')}</p> : !listing.err && (markups.length ? markups.map(item =>
      <details key={item.price_id} className="notice">
        <summary>{t('{model} · ×{markup} · {time}', { model: item.model, markup: item.spec.markup, time: time(item.created_at) })}</summary>
        <div className="col gap8 small" style={{ paddingTop: 8 }}>
          <p>{t('售价版本：{price}；成本价：{cost}', { price: item.price_id, cost: item.spec.cost_price_id })}</p>
          <p>{item.spec.off_peak_markup ? t('高峰倍率 ×{markup}，低谷倍率 ×{off}', { markup: item.spec.markup, off: item.spec.off_peak_markup })
            : t('倍率 ×{markup}', { markup: item.spec.markup })}</p>
          <RateTableView unit={item.unit} sale={item.sale_rates} caption={t('售价（每百万 token，{code}）', { code: item.unit.code })} />
          <p>{t('条款引用：{ref}', { ref: item.spec.terms_ref })}</p>
          <p className="muted">{t('发布于 {time} · 操作者 {actor}', { time: time(item.created_at), actor: item.actor_id })}</p>
          <p style={{ whiteSpace: 'pre-wrap' }}>{t('发布理由：{reason}', { reason: item.reason })}</p>
        </div>
      </details>) : <p className="muted">{t('尚未发布加价售价。')}</p>)}

    {dialogOpen && plan && <ActionDialog<Publication> title={t('确认发布加价售价')} confirmLabel={t(uncertain ? '重试原请求' : '发布售价版本')}
      reason={attempt ? 'none' : 'required'} reauthScope="admin" size="wide"
      ready={canWrite && confirmed && !busy && (uncertain || (!expired && !changed))}
      impact={<div className="col gap12">
        <p><b>{t('高风险操作 · {environment}', { environment: environmentName(plan.environment) })}</b></p>
        <p>{t('模型：{model} · 成本价：{cost}', { model: plan.model, cost: plan.spec.cost_price_id })}</p>
        <p>{plan.spec.off_peak_markup ? t('高峰倍率 ×{markup}，低谷倍率 ×{off}', { markup: plan.spec.markup, off: plan.spec.off_peak_markup })
          : t('倍率 ×{markup}', { markup: plan.spec.markup })}</p>
        <RateTableView unit={plan.unit} cost={plan.cost_rates} sale={plan.sale_rates} caption={t('成本价 → 售价（每百万 token，{code}）', { code: plan.unit.code })} />
        <div className="tbl-wrap"><table className="tbl">
          <caption className="small" style={{ textAlign: 'left' }}>{t('示例：每 {tokens} 个 token 的成本与售价（{code}）', { tokens: plan.example_tokens, code: plan.unit.code })}</caption>
          <thead><tr><th scope="col">{t('Token 类型')}</th>{BAND_ORDER.filter(band => plan.examples[band]).map(band => <th key={band} scope="col">{t(BAND_NAMES[band])}</th>)}</tr></thead>
          <tbody>{RATE_KEYS.map(key => <tr key={key}><th scope="row">{t(RATE_NAMES[key])}</th>
            {BAND_ORDER.filter(band => plan.examples[band]).map(band => <td key={band}>{t('成本 {cost}，售价 {sale}', plan.examples[band]![key])}</td>)}</tr>)}</tbody>
        </table></div>
        <p>{plan.replaces_price_id ? t('此模型最近一次加价售价为 {price}；它仍保留，路线采用哪一个由「模型提供方」决定。', { price: plan.replaces_price_id })
          : t('这是此模型的第一个加价售价。')}</p>
        <p>{t('发布不改变任何路线、报价或已结算费用。路线采用新售价后，用户尚未使用的报价与持续同意会失效并要求重新确认。')}</p>
        <p className="small">{t('服务器确认预览有效至 {time}。', { time: time(plan.expires_at) })}</p>
        {attempt && <p className="small" style={{ whiteSpace: 'pre-wrap' }}>{t('原发布理由：{reason}', { reason: attempt.body.reason })}</p>}
        {uncertain && <p className="notice warn">{t('重试将发送完全相同的原请求、理由和请求编号，即使原预览已经到期。不会创建另一笔发布。')}</p>}
        {!uncertain && (expired || changed) && <p className="notice warn" role="alert">{t('加价预览已失效，请关闭并重新预览。')}</p>}
      </div>}
      run={publish} onDone={completed} onClose={() => { if (flight.current) return; setDialogOpen(false); setConfirmed(false); if (!uncertain) { setPlan(null); setAttempt(null); } }}>
      <label className="row gap8" style={{ alignItems: 'flex-start' }}><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />
        <span>{t('我已核对成本价、倍率、各时段售价与示例费用。')}</span></label>
    </ActionDialog>}
  </section>;
}
