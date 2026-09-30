import { useEffect, useId, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { ApiError, get, patch, post } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { permitted, type Held } from "./access";
import { useAdminData } from "./data";
import { errText } from "./labels";
import { useUnsavedChanges } from "../lib/hooks";
import "./model-prices.messages";

type Environment = "test" | "live" | null;
export type PriceSpec = {
  name: string; purpose: "sale" | "cost"; unit: { code: string; scale: number };
  provider: "deepseek"; model: string; input_uncached: string; input_cached: string; output: string;
  terms_ref: string; effective_from: number; previous_price_id: string | null;
};
export type PriceDraft = {
  id: string; revision: number; status: "draft" | "published"; price_id: string | null;
  created_at: number; updated_at: number; actor_id: string; spec: PriceSpec;
};
type PricePage = { environment: Environment; items: PriceDraft[]; next_before: string | null };
type Plan = { draft: PriceDraft; preview_token: string; expires_at: number; risk: "high"; environment: Environment };
type Publication = { operation_id: string; status: "succeeded"; draft: PriceDraft };
type Calculation = {
  unit: PriceSpec["unit"]; exact: { numerator: string; denominator: string }; display_amount: string;
  charge_units_if_no_prior_remainder: string; per_tokens: 1000000;
};
type Form = {
  name: string; purpose: "" | PriceSpec["purpose"]; code: string; scale: string; provider: "" | "deepseek";
  model: string; input_uncached: string; input_cached: string; output: string; terms_ref: string;
  effective: string; previous_price_id: string;
};
type Counts = Pick<Form, "input_uncached" | "input_cached" | "output">;
type CreateAttempt = { environment: "test" | "live"; body: { spec: PriceSpec; idempotency_key: string } };
type PublishAttempt = {
  id: string; body: { expected_revision: number; preview_token: string; idempotency_key: string; reason: string };
};
type Choice = { kind: "new" } | { kind: "select" | "copy"; item: PriceDraft };
const API = "/api/admin/billing/prices";
const rateKeys = ["input_uncached", "input_cached", "output"] as const;
const rateNames = { input_uncached: "未缓存输入", input_cached: "缓存输入", output: "输出" };
const grid: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", gap: 16 };
const fieldset: CSSProperties = { border: 0, padding: 0, margin: 0, minWidth: 0 };
const emptyForm = (): Form => ({ name: "", purpose: "", code: "", scale: "", provider: "", model: "",
  input_uncached: "", input_cached: "", output: "", terms_ref: "", effective: "", previous_price_id: "" });
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const endpoint = (id: string, action = "") => `${API}/drafts/${encodeURIComponent(id)}${action}`;
// Only bounded counts, scale and timestamps become numbers. Monetary values never do.
const decimal = (value: string) => /^(0|[1-9][0-9]{0,8})(\.[0-9]{1,9})?$/.test(value);
const countValid = (value: string) => /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) <= 1_000_000_000;
const uncertain = (error: unknown) => !(error instanceof ApiError) || error.upstream || error.status >= 500 || error.status === 408 || error.status === 0;
const localError = (message: string) => new ApiError(400, "model_price_form", message);
const timeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
function localInput(seconds: number): string {
  const date = new Date(seconds * 1000);
  if (!Number.isFinite(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${String(date.getFullYear()).padStart(4, "0")}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
function timestamp(value: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) return null;
  const seconds = new Date(value).getTime() / 1000;
  // Reject impossible calendar dates and local times skipped by daylight saving time.
  return Number.isSafeInteger(seconds) && localInput(seconds) === (value.length === 16 ? `${value}:00` : value) ? seconds : null;
}
// Explicit order avoids treating JSON key order as a change to the price.
function editable(spec: PriceSpec): Form {
  return { name: spec.name, purpose: spec.purpose, code: spec.unit.code, scale: String(spec.unit.scale),
    provider: spec.provider, model: spec.model, input_uncached: spec.input_uncached, input_cached: spec.input_cached,
    output: spec.output, terms_ref: spec.terms_ref, effective: localInput(spec.effective_from), previous_price_id: spec.previous_price_id || "" };
}
function validated(form: Form, source: PriceSpec | null): { spec: PriceSpec | null; errors: Partial<Record<keyof Form, string>> } {
  const errors: Partial<Record<keyof Form, string>> = {};
  if (!form.name.trim() || Array.from(form.name).length > 80 || /[\u0000-\u001f\u007f]/.test(form.name)) errors.name = "名称须为 1–80 个字符，不能包含控制字符。";
  if (!form.purpose) errors.purpose = "请选择销售价或成本价。";
  if (!/^[A-Z][A-Z0-9_]{1,15}$/.test(form.code)) errors.code = "单位代码须以大写字母开头，共 2–16 位，仅限大写字母、数字或下划线。";
  if (!/^\d+$/.test(form.scale) || Number(form.scale) > 12) errors.scale = "单位精度须为 0–12 的整数。";
  if (form.provider !== "deepseek") errors.provider = "请选择模型提供方。";
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$/.test(form.model)) errors.model = "模型标识须以英文字母或数字开头，共 1–100 位，仅限英文字母、数字及 . _ : -。";
  for (const key of rateKeys) if (!decimal(form[key])) errors[key] = "费率须为非负十进制数，整数最多 9 位、小数最多 9 位；不能有多余前导零、指数或千位分隔符。";
  if (!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/.test(form.terms_ref)) errors.terms_ref = "条款引用须以英文字母或数字开头，共 1–200 位，仅限英文字母、数字及 . _ : / -，不能包含查询参数。";
  if (form.previous_price_id && !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(form.previous_price_id)) errors.previous_price_id = "前一价格版本须以英文字母或数字开头，共 1–200 位，仅限英文字母、数字及 . _ : -。";
  // Preserve an unchanged server timestamp even if it is the later occurrence of a
  // repeated local time. Editing a different field must not move its effective instant.
  const effective = source && form.effective === localInput(source.effective_from) ? source.effective_from : timestamp(form.effective);
  if (effective === null) errors.effective = "请输入有效的本地日期与时间，精确到秒。";
  else if (effective < 0 || effective > 253402300799) errors.effective = "生效时间须位于 UTC 1970-01-01 至 9999-12-31 之间。";
  if (Object.keys(errors).length) return { spec: null, errors };
  return { errors, spec: { name: form.name.trim(), purpose: form.purpose as PriceSpec["purpose"],
    unit: { code: form.code, scale: Number(form.scale) }, provider: "deepseek", model: form.model,
    input_uncached: form.input_uncached, input_cached: form.input_cached, output: form.output,
    terms_ref: form.terms_ref, effective_from: effective!, previous_price_id: form.previous_price_id || null } };
}
const unitFraction = (scale: number) => scale === 0 ? "1" : `0.${"0".repeat(scale - 1)}1`;

function PriceDetails({ spec }: { spec: PriceSpec }) {
  const { t, locale } = useI18n();
  const date = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "long" }).format(new Date(spec.effective_from * 1000));
  return <div className="col gap8 small" style={{ overflowWrap: "anywhere", minWidth: 0 }}>
    <b>{spec.name}</b>
    <p>{t("{purpose} · 提供方：{provider} · 模型：{model}", { purpose: t(spec.purpose === "sale" ? "销售价" : "成本价"), provider: spec.provider, model: spec.model })}</p>
    <p>{t("单位：{code}；精度：{scale}；钱包整数 1 = {fraction} {code}。", { ...spec.unit, fraction: unitFraction(spec.unit.scale) })}</p>
    <ul>{rateKeys.map(key => <li key={key}>{t("{kind}：每百万 token 为 {amount} {code}", { kind: t(rateNames[key]), amount: spec[key], code: spec.unit.code })}</li>)}</ul>
    <p>{t("生效时间：{time}（本地时区 {zone}）；Unix 秒：{seconds}", { time: date, zone: timeZone(), seconds: String(spec.effective_from) })}</p>
    <p>{t("条款引用：{ref}", { ref: spec.terms_ref })}</p>
    <p>{t("前一价格版本：{id}", { id: spec.previous_price_id || t("无前一版本") })}</p>
  </div>;
}

/** Do not mount the loader when the caller cannot read platform configuration. */
export function ModelPricesTab({ held, onDenied }: { held: Held; onDenied: () => void }) {
  const { t } = useI18n();
  if (permitted(held, "platform.config.view").disabled) return <p className="notice" role="note">{t("你的平台角色不能查看模型价格。")}</p>;
  return <ModelPricesContent held={held} onDenied={onDenied} />;
}

function ModelPricesContent({ held, onDenied }: { held: Held; onDenied: () => void }) {
  const { t, locale } = useI18n();
  const id = useId();
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const before = cursors[cursors.length - 1];
  const list = useAdminData<PricePage>(`${API}?limit=20${before === null ? "" : `&before=${encodeURIComponent(before)}`}`, onDenied);
  const [base, setBase] = useState<PriceDraft | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [savedForm, setSavedForm] = useState<Form | null>(null);
  const [sourceSpec, setSourceSpec] = useState<PriceSpec | null>(null);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<"save" | "preview" | "calculate" | "compare" | "publish" | null>(null);
  const flight = useRef(false);
  const comparison = useRef<AbortController | null>(null);
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<PriceDraft | null>(null);
  const [createAttempt, setCreateAttempt] = useState<CreateAttempt | null>(null);
  const [publishAttempt, setPublishAttempt] = useState<PublishAttempt | null>(null);
  const [publishUncertain, setPublishUncertain] = useState(false);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planInvalid, setPlanInvalid] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [result, setResult] = useState<Publication | null>(null);
  const [saved, setSaved] = useState<PriceDraft | null>(null);
  const [counts, setCounts] = useState<Counts>({ input_uncached: "", input_cached: "", output: "" });
  const [calculation, setCalculation] = useState<{ result: Calculation; id: string; revision: number; counts: Counts } | null>(null);
  const [tried, setTried] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const manage = !permitted(held, "platform.config.manage").disabled;
  const environment = list.data?.environment;
  const canWrite = manage && (environment === "test" || environment === "live") && !list.err && !list.loading;
  const pending = !!createAttempt || publishUncertain;
  const dirty = !!form && !same(form, savedForm);
  const validation = form ? validated(form, sourceSpec) : { spec: null, errors: {} };
  const editableDraft = !!form && base?.status !== "published";
  const expired = !!plan && now >= plan.expires_at * 1000;
  const changedEnvironment = !!plan && plan.environment !== environment;
  const createEnvironmentChanged = !!createAttempt && createAttempt.environment !== environment;
  const frozen = !!busy || pending || dialogOpen;
  const current = !!base && !dirty && !conflict && !pending;
  useUnsavedChanges(dirty || pending || !!busy);
  useEffect(() => () => comparison.current?.abort(), []);

  useEffect(() => {
    if (!plan) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [plan]);
  useEffect(() => {
    if (!base || list.loading || list.err) return;
    const item = list.data?.items.find(item => item.id === base.id);
    if (item && (item.revision > base.revision || (item.revision === base.revision && item.status === "published" && base.status === "draft"))) {
      setLatest(item); setConflict(true); setPlanInvalid(true); setCalculation(null);
    }
  }, [list.data, list.loading, list.err, base]);

  const formatTime = (seconds: number) => new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "long" }).format(new Date(seconds * 1000));
  const environmentName = (value: Environment | undefined) => t(value === "test" ? "测试环境" : value === "live" ? "正式环境" : value === null ? "未配置" : "正在读取环境…");
  function accept(item: PriceDraft) {
    setBase(item); setSourceSpec(item.spec); const next = editable(item.spec); setForm(next); setSavedForm(next);
    setConflict(false); setLatest(null); setPlan(null); setPlanInvalid(false); setCalculation(null); setTried(false);
  }
  function fail(err: unknown) {
    setError(err);
    if (err instanceof ApiError && !err.upstream) {
      if (base && ["price_revision_conflict", "price_published"].includes(err.code)) {
        setConflict(true); setPlanInvalid(true); setCalculation(null);
        const server = err.detail?.current as PriceDraft | undefined;
        setLatest(server?.id === base.id && server.spec && Number.isInteger(server.revision) ? server : null);
      }
      if (err.code === "platform_permission_denied" || err.code === "platform_admin_required") onDenied();
    }
  }
  function choose(next: Choice) {
    if (flight.current || pending) return;
    if (dirty) { setChoice(next); return; }
    applyChoice(next);
  }
  function applyChoice(next: Choice) {
    setChoice(null); setError(null); setSaved(null); setResult(null); setConflict(false); setLatest(null);
    setPlan(null); setPlanInvalid(false); setPublishAttempt(null); setCalculation(null); setTried(false);
    if (next.kind === "select") accept(next.item);
    else {
      const nextForm = next.kind === "copy" ? { ...editable(next.item.spec), previous_price_id: next.item.price_id || "" } : emptyForm();
      setBase(null); setSourceSpec(next.kind === "copy" ? next.item.spec : null); setForm(nextForm); setSavedForm(emptyForm());
    }
    requestAnimationFrame(() => heading.current?.focus());
  }
  function edit(key: keyof Form, value: string) {
    if (!form || frozen || !canWrite || base?.status === "published") return;
    setForm({ ...form, [key]: value }); setPlan(null); setPlanInvalid(false); setCalculation(null); setSaved(null);
  }
  function refresh() { list.reload(); }
  async function save(event: FormEvent) {
    event.preventDefault(); setTried(true);
    if (flight.current || !canWrite || !editableDraft || conflict || publishUncertain || dialogOpen || createEnvironmentChanged) return;
    if (!createAttempt && !validation.spec) {
      document.getElementById(`${id}-${Object.keys(validation.errors)[0]}`)?.focus();
      return;
    }
    flight.current = true; setBusy("save"); setError(null);
    let attempt = createAttempt;
    try {
      let item: PriceDraft;
      if (base) {
        item = await patch<PriceDraft>(endpoint(base.id), { spec: validation.spec, expected_revision: base.revision });
      } else {
        attempt ||= { environment: environment as "test" | "live", body: { spec: validation.spec!, idempotency_key: crypto.randomUUID() } };
        setCreateAttempt(attempt);
        item = await post<PriceDraft>(`${API}/drafts`, attempt.body);
      }
      if (!item?.id || !item.spec || !Number.isInteger(item.revision)) throw new Error("Invalid draft response");
      setCreateAttempt(null); accept(item); setSaved(item.status === "draft" ? item : null); refresh();
    } catch (err) {
      // A later authentication/transport refusal cannot resolve an earlier lost response.
      if (!uncertain(err) && !createAttempt) setCreateAttempt(null);
      // PATCH has optimistic concurrency, but no idempotency key: compare before retrying it.
      if (base && uncertain(err)) { setConflict(true); setLatest(null); }
      fail(err);
    } finally { flight.current = false; setBusy(null); }
  }
  async function compare() {
    if (!base || flight.current || pending) return;
    flight.current = true; setBusy("compare"); setError(null); setLatest(null);
    const controller = new AbortController(); comparison.current = controller;
    try {
      const item = await get<PriceDraft>(endpoint(base.id), controller.signal);
      setLatest(item); setConflict(true);
    } catch (err) { if (!controller.signal.aborted) fail(err); } finally { comparison.current = null; flight.current = false; setBusy(null); }
  }
  async function preview() {
    if (flight.current || !base || base.status !== "draft" || !canWrite || !current) return;
    flight.current = true; setBusy("preview"); setError(null); setPlanInvalid(true); setConfirmed(false);
    try {
      const next = await post<Plan>(endpoint(base.id, "/preview"), { expected_revision: base.revision });
      if (next.draft.id !== base.id || next.draft.revision !== base.revision || next.draft.status !== "draft" ||
          !same(editable(next.draft.spec), editable(base.spec)) || next.draft.spec.effective_from !== base.spec.effective_from || next.risk !== "high" || !next.preview_token ||
          next.environment !== environment || !Number.isFinite(next.expires_at) || next.expires_at * 1000 <= Date.now()) {
        throw localError("预览与当前草稿或环境不一致，请重新读取服务器版本。");
      }
      setPlan(next); setPlanInvalid(false); setPublishAttempt(null); setNow(Date.now()); setDialogOpen(true);
    } catch (err) { fail(err); } finally { flight.current = false; setBusy(null); }
  }
  async function publish(reason: string): Promise<Publication> {
    if (flight.current || !plan || !canWrite || !confirmed || changedEnvironment) throw localError("请先核对有效预览及发布影响。");
    // A lost response may already have committed. Replay the exact original body and key,
    // including the original reason and token, even after its preview has expired.
    let attempt = publishUncertain ? publishAttempt : null;
    if (!attempt) {
      if (planInvalid || conflict || changedEnvironment || Date.now() >= plan.expires_at * 1000) throw localError("价格预览已失效，请重新预览后确认。");
      if (Array.from(reason.trim()).length < 8 || Array.from(reason.trim()).length > 500) throw localError("发布理由须为 8–500 个字符。");
      attempt = { id: plan.draft.id, body: { expected_revision: plan.draft.revision, preview_token: plan.preview_token,
        idempotency_key: crypto.randomUUID(), reason: reason.trim() } };
      setPublishAttempt(attempt);
    }
    flight.current = true; setBusy("publish"); setError(null);
    try {
      const out = await post<Publication>(endpoint(attempt.id, "/publish"), attempt.body);
      if (out?.status !== "succeeded" || !out.operation_id || out.draft?.status !== "published" || !out.draft.price_id || out.draft.id !== attempt.id) throw new Error("Invalid publication response");
      return out;
    } catch (err) {
      // The backend replays a committed result before checking preview expiry. These
      // explicit preview refusals therefore establish that this attempt did not commit.
      const uncommitted = err instanceof ApiError && !err.upstream &&
        ["admin_preview_expired", "admin_preview_invalid", "price_revision_conflict", "price_published"].includes(err.code);
      if (uncertain(err) || (publishUncertain && !uncommitted)) setPublishUncertain(true);
      else {
        setPublishUncertain(false); setPublishAttempt(null);
        if (!(err instanceof ApiError && err.code === "reauth_required")) setPlanInvalid(true);
      }
      fail(err); throw err;
    } finally { flight.current = false; setBusy(null); }
  }
  async function calculate(event: FormEvent) {
    event.preventDefault();
    if (flight.current || !current || !base || !rateKeys.every(key => countValid(counts[key]))) return;
    flight.current = true; setBusy("calculate"); setError(null); setCalculation(null);
    const sample = { ...counts };
    try {
      const out = await post<Calculation>(endpoint(base.id, "/calculate"), { expected_revision: base.revision,
        input_uncached: Number(sample.input_uncached), input_cached: Number(sample.input_cached), output: Number(sample.output) });
      setCalculation({ result: out, id: base.id, revision: base.revision, counts: sample });
    } catch (err) { fail(err); } finally { flight.current = false; setBusy(null); }
  }
  function field(key: keyof Form, label: string, options: { maxLength?: number; numeric?: boolean; decimal?: boolean; type?: "datetime-local" } = {}) {
    const problem = tried ? validation.errors[key] : undefined;
    return <label className="field" key={key} style={{ minWidth: 0 }} htmlFor={`${id}-${key}`}>
      <span>{t(label)}</span>
      <input id={`${id}-${key}`} className="input" type={options.type || "text"} required={key !== "previous_price_id"}
        value={form?.[key] || ""} onChange={event => edit(key, event.target.value)} maxLength={options.maxLength}
        inputMode={options.decimal ? "decimal" : options.numeric ? "numeric" : undefined}
        step={options.type ? 1 : undefined} aria-invalid={!!problem} aria-describedby={`${id}-rules${problem ? ` ${id}-${key}-error` : ""}`}
        style={{ minWidth: 0, width: "100%" }} />
      {problem && <span id={`${id}-${key}-error`} className="small">{t(problem)}</span>}
    </label>;
  }

  return <div className="col gap16" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
    <section className="card card-b col gap12" aria-labelledby={`${id}-title`}>
      <div className="row wrap gap12"><h2 id={`${id}-title`} className="grow">{t("模型价格")}</h2>
        <button type="button" className="btn" disabled={list.loading || !!busy} onClick={refresh}>{t("刷新价格列表")}</button>
        <button type="button" className="btn primary" disabled={!canWrite || frozen} onClick={() => choose({ kind: "new" })}>{t("新建价格草稿")}</button></div>
      <p className="notice info">{t("当前价格环境：{environment}", { environment: environmentName(environment) })}</p>
      <p className="muted">{t("发布仅保存不可修改的价格版本，不启用收费或模型路由，不追溯修改历史账单。恢复旧价格须复制为新版本。")}</p>
      {environment === null && <p className="notice warn">{t("价格环境尚未配置，创建、保存和发布已禁用。请联系部署管理员。")}</p>}
      {!manage && <p className="notice">{t("你可以查看价格和试算，不能创建、修改或发布价格。")}</p>}
      {list.err && <p className="notice err" role="alert">{t(list.err)}</p>}
      {error != null && <p className="notice err" role="alert">{errText(error)}</p>}
      {saved && <p className="notice ok" role="status">{t("服务器已保存草稿 {id}，修订 {revision}。尚未发布。", { id: saved.id, revision: saved.revision })}</p>}
      {result && <div className="notice ok col gap8" role="status">
        <p>{t("服务器已持久保存价格版本 {price}；草稿 {id}，修订 {revision}；操作编号 {operation}；状态：已完成。", { price: result.draft.price_id!, id: result.draft.id, revision: result.draft.revision, operation: result.operation_id })}</p>
        <p>{t("发布仅保存不可修改的价格版本，不启用收费或模型路由，不追溯修改历史账单。恢复旧价格须复制为新版本。")}</p>
      </div>}
      {pending && !busy && <div className="notice warn col gap8" role="alert">
        <p>{t("上次请求的结果尚不确定。原请求及幂等键已保留；请手动重试确认结果，暂勿离开或刷新此页面。")}</p>
        <p className="small">{t("幂等键：{key}", { key: createAttempt?.body.idempotency_key || publishAttempt?.body.idempotency_key || "" })}</p>
        {(createEnvironmentChanged || changedEnvironment) && <p>{t("环境已变化，不能在另一环境重试。请恢复原环境后核对同一次操作。")}</p>}
        {publishUncertain && <button type="button" className="btn" disabled={!!busy} onClick={() => setDialogOpen(true)}>{t("核对并重试原发布请求")}</button>}
      </div>}
    </section>

    <section className="card card-b col gap12" aria-labelledby={`${id}-list`} aria-busy={list.loading}>
      <h3 id={`${id}-list`}>{t("价格记录")}</h3>
      {list.loading ? <p role="status">{t("正在读取价格记录…")}</p> : !list.err && list.data && <>
        {!list.data.items.length && <p className="muted">{t("此页没有价格记录。请创建草稿或返回较新记录。")}</p>}
        {list.data.items.map(item => <article className="notice col gap8" key={item.id}>
          <div className="row wrap gap8"><b className="grow">{item.spec.name}</b><span className="badge">{t(item.status === "draft" ? "价格草稿" : "已发布价格")}</span></div>
          <p className="small">{t("{purpose} · 模型：{model} · 单位：{code} · 修订 {revision}", { purpose: t(item.spec.purpose === "sale" ? "销售价" : "成本价"), model: item.spec.model, code: item.spec.unit.code, revision: item.revision })}</p>
          <p className="small">{rateKeys.map(key => t("{kind} {amount}", { kind: t(rateNames[key]), amount: item.spec[key] })).join(" · ")}</p>
          <p className="tiny muted">{t("以上费率均为每百万 token 的主要单位金额。")}</p>
          <p className="tiny muted">{t("记录：{id}；价格版本：{price}；更新时间：{time}", { id: item.id, price: item.price_id || t("尚未发布"), time: formatTime(item.updated_at) })}</p>
          <div className="row wrap gap8"><button type="button" className="btn" disabled={frozen} aria-pressed={base?.id === item.id}
            aria-label={t("查看价格：{name}", { name: item.spec.name })}
            onClick={() => choose({ kind: "select", item })}>{t(base?.id === item.id ? "重新选择此记录" : "查看价格记录")}</button>
            {item.status === "published" && <button type="button" className="btn" disabled={!canWrite || frozen || !item.price_id}
              onClick={() => choose({ kind: "copy", item })}>{t("复制为新版本草稿")}</button>}</div>
        </article>)}
      </>}
      <nav className="row wrap gap8" aria-label={t("价格记录分页")}>
        <button type="button" className="btn" disabled={list.loading || cursors.length === 1 || !!busy} onClick={() => setCursors(values => values.slice(0, -1))}>{t("较新一页")}</button>
        <span className="small">{t("第 {page} 页，每页最多 20 条", { page: cursors.length })}</span>
        <button type="button" className="btn" disabled={list.loading || !!list.err || list.data?.next_before == null || !!busy}
          onClick={() => { const next = list.data?.next_before; if (next != null) setCursors(values => [...values, next]); }}>{t("较旧一页")}</button>
        {cursors.length > 1 && <button type="button" className="btn" disabled={list.loading || !!busy} onClick={() => setCursors([null])}>{t("返回最新价格")}</button>}
      </nav>
    </section>

    {form && <section className="card card-b col gap16" aria-labelledby={`${id}-editor`}>
      <h3 id={`${id}-editor`} ref={heading} tabIndex={-1}>{t(base?.status === "published" ? "已发布价格详情" : base ? "编辑价格草稿" : "新价格草稿")}</h3>
      {base && <p className="small muted">{t("草稿 {id} · 修订 {revision} · 操作者 {actor} · 创建于 {time}", { id: base.id, revision: base.revision, actor: base.actor_id, time: formatTime(base.created_at) })}</p>}
      {base?.price_id && <p className="small">{t("持久价格版本：{id}", { id: base.price_id })}</p>}
      {conflict && <div className="notice warn col gap12" role="alert">
        <p>{t("服务器版本可能已变化，输入已保留。请读取并比较最新记录，再决定是否保存。")}</p>
        <button type="button" className="btn" disabled={!!busy || pending} onClick={() => void compare()}>{t("读取服务器版本以比较")}</button>
        {latest && <>
          <p>{t("服务器修订 {revision}，状态：{status}", { revision: latest.revision, status: t(latest.status === "draft" ? "价格草稿" : "已发布价格") })}</p>
          <PriceDetails spec={latest.spec} />
          <button type="button" className="btn" disabled={!canWrite || !!busy || pending} onClick={() => {
            if (latest.status === "draft") { setBase(latest); setSavedForm(editable(latest.spec)); }
            else { setBase(null); setSavedForm(emptyForm()); setForm({ ...form, previous_price_id: latest.price_id || "" }); }
            setConflict(false); setLatest(null); setPlan(null); setCalculation(null); setError(null);
          }}>{t(latest.status === "draft" ? "保留输入，采用此修订继续编辑" : "保留输入，改为新的价格版本草稿")}</button>
        </>}
      </div>}
      {base?.status === "published" ? <>
        <PriceDetails spec={base.spec} />
        <p className="muted">{t("已发布价格不可编辑。复制会保留前一价格版本引用，用于发布新的恢复版本。")}</p>
        <div><button type="button" className="btn" disabled={!canWrite || frozen || !base.price_id} onClick={() => choose({ kind: "copy", item: base })}>{t("复制为新版本草稿")}</button></div>
      </> : <form className="col gap16" onSubmit={save} noValidate>
        <div id={`${id}-rules`} className="notice info col gap8 small">
          <p>{t("所有价格信息均须自行填写；本页不预设售价、币种或兑换比例。单位代码是计价标识，不代表已配置货币兑换。")}</p>
          <p>{t("费率是每百万 token 的主要单位金额，不是钱包最小单位。精度表示主要单位的小数位数，例如精度 6 时，钱包整数 1 = 0.000001 个主要单位。")}</p>
          <p>{t("日期按浏览器本地时区 {zone} 输入并转换为 Unix 秒；夏令时重复时刻采用较早的时刻，请在预览中核对。", { zone: timeZone() })}</p>
        </div>
        <fieldset className="col gap16" disabled={!canWrite || frozen} style={fieldset}>
          <legend className="small">{t("价格定义（前一版本可留空，其余必填）")}</legend>
          <div style={grid}>
            {field("name", "价格名称", { maxLength: 80 })}
            <label className="field"><span>{t("价格用途")}</span><select id={`${id}-purpose`} className="input" value={form.purpose} required aria-invalid={tried && !!validation.errors.purpose}
              onChange={event => edit("purpose", event.target.value)}><option value="">{t("请选择价格用途")}</option><option value="sale">{t("销售价")}</option><option value="cost">{t("成本价")}</option></select>
              {tried && validation.errors.purpose && <span>{t(validation.errors.purpose)}</span>}</label>
            {field("code", "单位代码（2–16 位，以大写字母开头）", { maxLength: 16 })}
            {field("scale", "单位精度（0–12）", { numeric: true, maxLength: 2 })}
            <label className="field"><span>{t("模型提供方")}</span><select id={`${id}-provider`} className="input" value={form.provider} required aria-invalid={tried && !!validation.errors.provider}
              onChange={event => edit("provider", event.target.value)}><option value="">{t("请选择模型提供方")}</option><option value="deepseek">DeepSeek</option></select>
              {tried && validation.errors.provider && <span>{t(validation.errors.provider)}</span>}</label>
            {field("model", "模型标识", { maxLength: 100 })}
            {rateKeys.map(key => field(key, key === "input_uncached" ? "未缓存输入费率（每百万 token）" : key === "input_cached" ? "缓存输入费率（每百万 token）" : "输出费率（每百万 token）", { decimal: true }))}
            {field("terms_ref", "条款引用标识", { maxLength: 200 })}
            {field("effective", "生效日期与时间（本地）", { type: "datetime-local" })}
            {field("previous_price_id", "前一价格版本标识（可选）", { maxLength: 200 })}
          </div>
        </fieldset>
        {tried && Object.keys(validation.errors).length > 0 && <p className="notice err" role="alert">{t("请修正已标出的价格字段，再保存草稿。")}</p>}
        <div className="row wrap gap8">
          <button type="submit" className="btn primary" disabled={!canWrite || !!busy || conflict || publishUncertain || dialogOpen || createEnvironmentChanged || (!!base && !dirty)}>{t(createAttempt ? "使用原幂等键重试创建" : base ? "保存草稿修订" : "创建价格草稿")}</button>
          <button type="button" className="btn" disabled={!canWrite || !!busy || !current || dialogOpen} onClick={() => void preview()}>{t("获取签名预览并发布")}</button>
        </div>
        {dirty && <p className="small muted">{t("当前输入尚未保存；试算和发布仅使用服务器已保存的修订。")}</p>}
      </form>}
      {base && <form className="col gap12" onSubmit={calculate}>
        <h4>{t("示例用量试算")}</h4>
        <p id={`${id}-usage-help`} className="muted small">{t("仅计算示例用量，不是实际账单，也不会发送模型请求。各项 token 数量须为 0–1000000000 的整数。")}</p>
        <fieldset style={fieldset} disabled={!!busy || !current || dialogOpen}>
          <div style={grid}>{rateKeys.map(key => <label className="field" key={key}><span>{t("{kind} token 数", { kind: t(rateNames[key]) })}</span>
            <input className="input" type="text" inputMode="numeric" pattern="[0-9]+" required value={counts[key]}
              aria-describedby={`${id}-usage-help`}
              aria-invalid={counts[key] !== "" && !countValid(counts[key])} onChange={event => { setCounts({ ...counts, [key]: event.target.value }); setCalculation(null); }} /></label>)}</div>
        </fieldset>
        <div><button type="submit" className="btn" disabled={!!busy || !current || dialogOpen || !rateKeys.every(key => countValid(counts[key]))}>{t("计算示例用量")}</button></div>
        {calculation && <div className="notice info col gap8" role="status">
          <p>{t("试算依据：草稿 {id}，修订 {revision}。", { id: calculation.id, revision: calculation.revision })}</p>
          <p>{rateKeys.map(key => t("{kind} token：{count}", { kind: t(rateNames[key]), count: calculation.counts[key] })).join(" · ")}</p>
          <p>{t("试算金额：{amount} {code}", { amount: calculation.result.display_amount, code: calculation.result.unit.code })}</p>
          <p>{t("精确金额（钱包最小单位）：{numerator} / {denominator}", calculation.result.exact)}</p>
          <p>{t("无前期余数时的钱包整数扣减：{units}；单位精度：{scale}。", { units: calculation.result.charge_units_if_no_prior_remainder, scale: calculation.result.unit.scale })}</p>
          <p>{t("仅计算示例用量，不是实际账单，也不会发送模型请求。各项 token 数量须为 0–1000000000 的整数。")}</p>
        </div>}
      </form>}
    </section>}
    {busy && <p className="small muted" role="status">{t("正在等待服务器响应…")}</p>}
    {choice && <ActionDialog title={t("放弃未保存的价格输入？")} confirmLabel={t("放弃输入并继续")} reason="none"
      impact={t("切换记录或创建草稿会清除当前尚未保存的输入。服务器记录不会被删除。")}
      run={async () => choice} onDone={applyChoice} onClose={() => setChoice(null)} />}
    {dialogOpen && plan && <ActionDialog<Publication> title={t("确认发布价格版本")} confirmLabel={t(publishUncertain ? "重试原发布请求" : "发布此价格版本")}
      reason={publishUncertain ? "none" : "required"} size="wide"
      ready={canWrite && confirmed && !busy && !changedEnvironment && (publishUncertain || (!expired && !planInvalid && !conflict))}
      impact={<div className="col gap12">
        <p><b>{t("高风险操作 · {environment}", { environment: environmentName(plan.environment) })}</b></p>
        <p>{t("草稿 {id}，修订 {revision}", { id: plan.draft.id, revision: plan.draft.revision })}</p>
        <PriceDetails spec={plan.draft.spec} />
        <p>{t("发布仅保存不可修改的价格版本，不启用收费或模型路由，不追溯修改历史账单。恢复旧价格须复制为新版本。")}</p>
        <p>{t("签名预览有效期为五分钟，截止 {time}。到期须重新预览。", { time: formatTime(plan.expires_at) })}</p>
        {(expired || planInvalid || changedEnvironment || conflict) && !publishUncertain && <p className="notice warn" role="alert">{t("价格预览已失效，请重新预览后确认。")}</p>}
        {publishUncertain && publishAttempt && <div className="notice warn col gap8" role="alert">
          <p>{t("发布结果尚不确定。重试将使用原理由、原签名预览和原幂等键查询或完成同一次发布，不会更换发布内容或幂等键。")}</p>
          <p>{t("原发布理由：{reason}", { reason: publishAttempt.body.reason })}</p>
          <p>{t("幂等键：{key}", { key: publishAttempt.body.idempotency_key })}</p>
        </div>}
      </div>}
      run={publish} onClose={() => { if (flight.current) return; setDialogOpen(false); if (!publishUncertain) { setPlan(null); setPublishAttempt(null); } }}
      onDone={out => {
        setPublishUncertain(false); setPublishAttempt(null); setCreateAttempt(null); setDialogOpen(false);
        accept(out.draft); setResult(out); setSaved(null); setError(null); refresh();
      }}>
      {!publishUncertain && <button type="button" className="btn" disabled={!!busy || !canWrite || !current} onClick={() => void preview()}>{t("重新获取价格预览")}</button>}
      <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{t("我已核对环境、具体费率、计价单位、条款引用、生效时间及发布影响。")}</label>
    </ActionDialog>}
  </div>;
}
