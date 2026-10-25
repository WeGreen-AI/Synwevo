import { Fragment, useEffect, useId, useState } from "react";
import { ApiError, post, qs } from "@synwevo/auth-client";
import { fmtTime, formatNumber, t as tr, useI18n } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { permitted, type Held } from "./access";
import { useAdminData } from "./data";
import { errText } from "./labels";
import { useDebounced, useUnsavedChanges } from "../lib/hooks";
import "./messages";
import "./settings.messages";
import "./mcp.messages";

/**
 * Platform governance of the Model Context Protocol (synwevo_identity.admin_mcp): the versioned
 * MCP policy with its signed preview and publication, a masked overview of every connection's
 * health, and the platform stop of a connection and its lifting. Reading needs `platform.config.view`;
 * publishing, stopping and lifting need `platform.config.manage` with re-authentication (ActionDialog).
 *
 * A platform stop is the platform's decision, apart from the owner's own on/off (`status`): the
 * overview names it (`platform_disabled`, since `platform_disabled_at`), offers the stop for any
 * connection not already stopped, and the lift for one that is. Lifting gives the connection back to
 * its owner still disabled; the page never enables a connection for its owner.
 *
 * Nothing here reads a connection's credential, a call's arguments or a result: the server sends
 * names, hosts, states, counts and error codes, and that is all this page shows. No host is
 * invented: the inbound card names the workplace endpoint as a path and derives only this
 * origin's own metadata address from the current location.
 */

export type ClientRegistration = "preregistered_only" | "cimd_and_preregistered";
export type McpPolicySpec = {
  outbound_enabled: boolean; inbound_enabled: boolean; allow_http: boolean;
  allow_private_networks: string[]; allowed_hosts: string[] | null; blocked_hosts: string[];
  max_connections_per_user: number; max_connections_per_workspace: number;
  max_concurrent_calls_per_connection: number; max_concurrent_calls_per_user: number;
  call_timeout_s: number; discover_timeout_s: number; max_result_chars: number; max_resource_bytes: number;
  breaker_failures: number; breaker_open_s: number;
  client_registration: ClientRegistration; cimd_allowed_domains: string[];
};
export type NumericKey = "max_connections_per_user" | "max_connections_per_workspace" | "max_concurrent_calls_per_connection"
  | "max_concurrent_calls_per_user" | "call_timeout_s" | "discover_timeout_s" | "max_result_chars" | "max_resource_bytes"
  | "breaker_failures" | "breaker_open_s";
type ListKey = "allow_private_networks" | "allowed_hosts" | "blocked_hosts" | "cimd_allowed_domains";
export type PolicyState = { revision: number; spec: McpPolicySpec; actor_id: string | null; reason: string | null;
  created_at: number | null; source: "builtin_default" | "published" };
type Bounds = { minimum?: number; maximum?: number };
type PolicyRead = { state: PolicyState; default: McpPolicySpec; schema?: { properties?: Record<string, Bounds> } };
type Plan = { before: McpPolicySpec; after: McpPolicySpec; changed: string[]; preview_token: string; expires_at: number };
type Published = { operation_id: string; status: string; revision: number; state: PolicyState };
type Version = { revision: number; spec: McpPolicySpec | null; actor_id: string | null; reason: string | null;
  operation_id: string | null; created_at: number };
export type Connection = {
  id: string; name: string; owner_kind: "user" | "workspace"; owner_name: string | null; host: string | null;
  auth_kind: string; status: string; platform_disabled: boolean; platform_disabled_at: number | null;
  /** Not stopped itself, but at a server address another connection's stop closes. */
  destination_stopped?: boolean;
  health: string; last_ok_at: number | null; last_error_code: string | null;
  failure_streak: number; circuit_open_until: number | null; protocol_version: string | null;
  tools: number; granted: number; agents: number; calls_7d: number; failed_7d: number; last_call_at: number | null;
};
/** What `POST …/disable` and `POST …/lift` answer: the owner's own status, and whether the platform stop holds. */
type Stopped = { id: string; status: string; platform_disabled: boolean };
type Totals = { connections: number; failed: number; auth_required: number; calls_24h: number; failed_calls_24h: number; inbound_requests_24h: number };
/** A server address a platform stop closes (suite_schema.mcp.stopped_destinations): its host only
 *  (a path may carry the server's own secret), since when, the stopped connection, and how many
 *  other connections it holds back. */
export type StoppedDestination = { host: string | null; since: number; connection_id: string; connection_name: string | null;
  other_connections: number };
type Overview = { items: Connection[]; totals: Totals; policy: PolicyState; stopped_destinations?: StoppedDestination[] };
type Call = { id: string; origin: string; kind: string; name: string | null; status: string; error_code: string | null;
  started_at: number; finished_at: number | null; duration_ms: number | null; request_id: string | null };

const BASE = "/api/admin/mcp";
const DASH = "—";
/** The path of the MCP server on the workplace origin (apps/suite/synwevo_suite/mcp_server). A path,
 *  never a host: the deployment's addresses come from the deployment. */
export const MCP_ENDPOINT_PATH = "/api/suite/v1/mcp";
/** RFC 8414 metadata of this account service (synwevo_identity.provider_routes). */
export const AUTHORIZATION_SERVER_METADATA_PATH = "/.well-known/oauth-authorization-server";

/** The numeric limits of the policy, with the bounds of synwevo_core.mcp_policy.McpPolicySpec. The
 *  server's JSON schema, when it comes, replaces the bounds; the labels stay. */
export const NUMERIC_FIELDS: { key: NumericKey; label: string; unit?: string; min: number; max: number }[] = [
  { key: "max_connections_per_user", label: "每人最多连接数", min: 0, max: 1000 },
  { key: "max_connections_per_workspace", label: "每个工作空间最多连接数", min: 0, max: 1000 },
  { key: "max_concurrent_calls_per_connection", label: "每个连接的并发调用上限", min: 1, max: 64 },
  { key: "max_concurrent_calls_per_user", label: "每人并发调用上限", min: 1, max: 256 },
  { key: "call_timeout_s", label: "单次调用超时", unit: "秒", min: 5, max: 600 },
  { key: "discover_timeout_s", label: "连接测试与能力刷新超时", unit: "秒", min: 5, max: 300 },
  { key: "max_result_chars", label: "进入模型上下文的结果上限", unit: "字符", min: 1000, max: 200000 },
  { key: "max_resource_bytes", label: "读取资源的大小上限", unit: "字节", min: 10000, max: 50000000 },
  { key: "breaker_failures", label: "连续失败多少次后熔断", min: 1, max: 100 },
  { key: "breaker_open_s", label: "熔断持续时间", unit: "秒", min: 10, max: 3600 },
];
export const FIELD_LABELS: Record<keyof McpPolicySpec, string> = {
  outbound_enabled: "允许 Agent 连接外部 MCP 服务器", inbound_enabled: "允许外部 MCP 客户端调用 Synwevo",
  allow_http: "允许连接明文 http 地址（关闭时只接受 https）", allow_private_networks: "允许的私有网络主机或网段",
  allowed_hosts: "只允许连接这些主机", blocked_hosts: "禁止连接的主机", cimd_allowed_domains: "接受客户端元数据文档的域名",
  client_registration: "外部客户端的登记方式",
  ...Object.fromEntries(NUMERIC_FIELDS.map((f) => [f.key, f.label])) as Record<NumericKey, string>,
};
export const REGISTRATION_LABELS: Record<ClientRegistration, string> = {
  preregistered_only: "仅接受运营方预先登记的客户端",
  cimd_and_preregistered: "接受自描述登记（客户端元数据文档）与预先登记的客户端",
};
export const HEALTH_LABELS: Record<string, string> = { ok: "连接正常", failed: "连接失败", auth_required: "需要授权", unknown: "尚未检查" };
const HEALTH_BADGE: Record<string, string> = { ok: "ok", failed: "err", auth_required: "warn", unknown: "" };
export const STATUS_LABELS: Record<string, string> = { active: "启用中", disabled: "已被停用" };
export const AUTH_LABELS: Record<string, string> = { none: "无认证", bearer: "固定令牌", oauth: "OAuth 授权" };
const CALL_KIND: Record<string, string> = { "tools/call": "工具调用", "resources/read": "读取资源", "prompts/get": "提示词", discover: "能力发现" };
const CALL_ORIGIN: Record<string, string> = { agent: "Agent 对话", test: "连接测试", discover: "能力刷新", page: "页面操作" };
const CALL_STATUS: Record<string, string> = { running: "进行中", succeeded: "成功", failed: "失败", timeout: "超时", cancelled: "已取消", unknown: "结果未知" };
const CALL_BADGE: Record<string, string> = { running: "info", succeeded: "ok", failed: "err", timeout: "err", cancelled: "", unknown: "warn" };

// ------------------------------------------------------------------------------------ the draft
export type Draft = {
  outbound_enabled: boolean; inbound_enabled: boolean; allow_http: boolean; any_public_host: boolean;
  allow_private_networks: string; allowed_hosts: string; blocked_hosts: string; cimd_allowed_domains: string;
  client_registration: ClientRegistration; numbers: Record<NumericKey, string>;
};
const joinLines = (items: string[] | null | undefined) => (items || []).join("\n");
export function draftFrom(spec: McpPolicySpec): Draft {
  return {
    outbound_enabled: spec.outbound_enabled, inbound_enabled: spec.inbound_enabled, allow_http: spec.allow_http,
    any_public_host: spec.allowed_hosts === null,
    allow_private_networks: joinLines(spec.allow_private_networks), allowed_hosts: joinLines(spec.allowed_hosts),
    blocked_hosts: joinLines(spec.blocked_hosts), cimd_allowed_domains: joinLines(spec.cimd_allowed_domains),
    client_registration: spec.client_registration,
    numbers: Object.fromEntries(NUMERIC_FIELDS.map((f) => [f.key, String(spec[f.key])])) as Record<NumericKey, string>,
  };
}
/** Host entries as the server validates them (McpPolicySpec._host_entries): one per line or comma,
 *  1–253 characters, no whitespace inside; duplicates collapse. */
export function parseHostList(text: string): { items: string[]; bad: string[] } {
  const items: string[] = []; const bad: string[] = [];
  for (const raw of text.split(/[\n,]+/)) {
    const entry = raw.trim();
    if (!entry) continue;
    if (entry.length > 253 || /\s/.test(entry)) { bad.push(entry); continue; }
    if (!items.includes(entry)) items.push(entry);
  }
  return { items, bad };
}
export type DraftProblem = { field: keyof McpPolicySpec; text: string; params: Record<string, string | number> };
/** The specification a draft describes, or the problems that keep it from being one. `bounds`
 *  are the server's (its JSON schema) where it sent them, else the ones this page knows. */
export function policyFromDraft(draft: Draft, bounds: Record<string, Bounds> = {}): { spec: McpPolicySpec | null; problems: DraftProblem[] } {
  const problems: DraftProblem[] = [];
  const lists: Record<ListKey, string[]> = { allow_private_networks: [], allowed_hosts: [], blocked_hosts: [], cimd_allowed_domains: [] };
  for (const key of Object.keys(lists) as ListKey[]) {
    if (key === "allowed_hosts" && draft.any_public_host) continue;
    const { items, bad } = parseHostList(draft[key]);
    if (bad.length) problems.push({ field: key, text: "{field}里有无效条目：{items}", params: { items: bad.join(", ") } });
    lists[key] = items;
  }
  const numbers = {} as Record<NumericKey, number>;
  for (const field of NUMERIC_FIELDS) {
    const min = bounds[field.key]?.minimum ?? field.min, max = bounds[field.key]?.maximum ?? field.max;
    const text = draft.numbers[field.key].trim();
    const value = Number(text);
    if (!/^\d+$/.test(text) || !Number.isSafeInteger(value) || value < min || value > max) {
      problems.push({ field: field.key, text: "{field}必须是 {min} 至 {max} 之间的整数。", params: { min, max } });
      continue;
    }
    numbers[field.key] = value;
  }
  if (problems.length) return { spec: null, problems };
  return { problems, spec: {
    outbound_enabled: draft.outbound_enabled, inbound_enabled: draft.inbound_enabled, allow_http: draft.allow_http,
    allow_private_networks: lists.allow_private_networks, allowed_hosts: draft.any_public_host ? null : lists.allowed_hosts,
    blocked_hosts: lists.blocked_hosts, cimd_allowed_domains: lists.cimd_allowed_domains,
    client_registration: draft.client_registration, ...numbers,
  } };
}
const canonical = (spec: McpPolicySpec) => JSON.stringify(Object.fromEntries(Object.keys(spec).sort().map((k) => [k, spec[k as keyof McpPolicySpec]])));
export const sameSpec = (a: McpPolicySpec, b: McpPolicySpec) => canonical(a) === canonical(b);

/** One value of the policy in words: for the preview's before → after and the history. */
export function valueText(key: string, value: unknown, t: (s: string, p?: Record<string, string | number>) => string): string {
  if (typeof value === "boolean") return t(value ? "开" : "关");
  if (key === "allowed_hosts" && value === null) return t("任何公网主机");
  if (Array.isArray(value)) return value.length ? value.join(", ") : t("（空）");
  if (key === "client_registration") return t(REGISTRATION_LABELS[value as ClientRegistration] || String(value));
  if (typeof value === "number") {
    const field = NUMERIC_FIELDS.find((f) => f.key === key);
    return field?.unit ? `${formatNumber(value)} ${t(field.unit)}` : formatNumber(value);
  }
  return value == null ? DASH : String(value);
}

// ------------------------------------------------------------------------------------ the tab
export function McpTab({ onDenied, held }: { onDenied: () => void; held: Held }) {
  useI18n(); const t = tr;
  if (!held.has("platform.config.view")) {
    return <section className="card card-b col gap12" aria-labelledby="mcp-denied"><h2 id="mcp-denied">{t("MCP")}</h2>
      <p className="notice" role="note">{t("你没有查看 MCP 策略与连接的权限。")}</p></section>;
  }
  return <McpViews onDenied={onDenied} held={held} />;
}

function McpViews({ onDenied, held }: { onDenied: () => void; held: Held }) {
  const manage = permitted(held, "platform.config.manage");
  const policy = useAdminData<PolicyRead>(`${BASE}/policy`, onDenied);
  const [before, setBefore] = useState<number | null>(null);
  const history = useAdminData<{ items: Version[] }>(`${BASE}/policy/history?limit=20${before === null ? "" : `&before_revision=${before}`}`, onDenied);
  const [draftFromVersion, setDraftFromVersion] = useState<{ version: Version; n: number } | null>(null);
  const reloadAll = () => { policy.reload(); history.reload(); };
  return <div className="col gap16">
    <PolicyPanel load={policy} manage={manage} onPublished={() => { setBefore(null); reloadAll(); }}
      loaded={draftFromVersion} />
    <OverviewPanel onDenied={onDenied} manage={manage} />
    <InboundCard state={policy.data?.state ?? null} />
    <HistoryPanel load={history} before={before} setBefore={setBefore} manage={manage} baseRevision={policy.data?.state.revision ?? null}
      onLoadDraft={(version) => setDraftFromVersion((prev) => ({ version, n: (prev?.n || 0) + 1 }))} />
  </div>;
}

// ------------------------------------------------------------------------------------ the policy
function PolicyPanel({ load, manage, onPublished, loaded }: {
  load: ReturnType<typeof useAdminData<PolicyRead>>; manage: ReturnType<typeof permitted>; onPublished: () => void;
  loaded: { version: Version; n: number } | null;
}) {
  useI18n(); const t = tr;
  // The version the draft is edited against, and the draft itself. Until the first loaded state is
  // adopted (an effect, after the first render), the page edits the state it just read: the editor
  // is drawn with the data, not a render later.
  const [adopted, setAdopted] = useState<{ base: PolicyState; draft: Draft } | null>(null);
  const [conflict, setConflict] = useState<PolicyState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [confirmation, setConfirmation] = useState<{ spec: McpPolicySpec; plan: Plan; key: string } | null>(null);
  const [result, setResult] = useState<Published | null>(null);
  const base = adopted?.base ?? load.data?.state ?? null;
  const draft = adopted?.draft ?? (base ? draftFrom(base.spec) : null);
  const adopt = (state: PolicyState, next?: Draft) => setAdopted({ base: state, draft: next ?? draftFrom(state.spec) });
  const setDraft = (next: Draft) => { if (base) setAdopted({ base, draft: next }); };
  useEffect(() => {
    if (!load.data) return;
    if (!adopted) adopt(load.data.state);
    else if (load.data.state.revision > adopted.base.revision) setConflict(load.data.state);
  }, [load.data, adopted]); // eslint-disable-line react-hooks/exhaustive-deps
  // A history version loaded as the draft (manage only; it takes effect only through preview and publish).
  useEffect(() => {
    if (!loaded?.version.spec || manage.disabled || !base) return;
    adopt(base, draftFrom(loaded.version.spec)); setConfirmation(null); setError(null);
    setNotice(t("已把版本 {version} 载入为草稿；预览并发布后才会生效。", { version: loaded.version.revision }));
  }, [loaded?.n]); // eslint-disable-line react-hooks/exhaustive-deps
  const bounds = load.data?.schema?.properties || {};
  const parsed = draft ? policyFromDraft(draft, bounds) : { spec: null, problems: [] };
  const dirty = !!base && !!parsed.spec && !sameSpec(parsed.spec, base.spec);
  useUnsavedChanges(dirty);
  const idBase = useId();
  function recordFailure(err: unknown) {
    if (err instanceof ApiError && err.code === "mcp_policy_revision_conflict" && err.detail?.current) setConflict(err.detail.current);
    setError(errText(err));
  }
  async function preview(spec: McpPolicySpec, retainKey?: string) {
    if (previewing || manage.disabled || !base) return;
    setPreviewing(true); setError(null); setNotice(null);
    try {
      const plan = await post<Plan>(`${BASE}/policy/preview`, { expected_revision: base.revision, spec });
      setConfirmation({ spec, plan, key: retainKey || crypto.randomUUID() }); setConfirmed(false);
    } catch (err) { recordFailure(err); } finally { setPreviewing(false); }
  }
  // Keep the person's input, compare it with the latest version from now on.
  const rebase = () => { if (!conflict || !draft) return; adopt(conflict, draft); setConflict(null); setError(null); setConfirmation(null); };
  const set = (patch: Partial<Draft>) => draft && setDraft({ ...draft, ...patch });
  const setNumber = (key: NumericKey, value: string) => draft && setDraft({ ...draft, numbers: { ...draft.numbers, [key]: value } });
  const problemText = (p: DraftProblem) => t(p.text, { field: t(FIELD_LABELS[p.field]), ...p.params });
  const registration = draft?.client_registration ?? base?.spec.client_registration;
  return <section className="card card-b col gap16" aria-labelledby="mcp-policy-heading">
    <div className="row wrap gap12"><h2 id="mcp-policy-heading" className="grow">{t("MCP 策略")}</h2>
      <button type="button" className="btn sm" disabled={load.loading || previewing} onClick={load.reload}>{t("刷新策略")}</button></div>
    <p className="muted">{t("决定 Agent 能否连接外部 MCP 服务器、外部 MCP 客户端能否调用 Synwevo、允许的网络目的地、每人与每个工作空间的连接与并发上限，以及外部客户端如何登记。每次发布是一个带理由的完整快照，新的连接与调用立即按它判断。")}</p>
    <p className="small muted">{t("这里没有任何连接的凭据、调用参数或结果：平台管理员看到的是名称、主机、状态、数量与错误码。")}</p>
    <div role="status" aria-live="polite">
      {result && <p className="notice ok">{t("MCP 策略版本 {version} 已发布，操作编号：{operation}。新的连接与调用立即按此版本判断。", { version: result.revision, operation: result.operation_id })}</p>}
      {notice && <p className="notice">{notice}</p>}
    </div>
    {(load.err || error) && <p className="notice err" role="alert">{load.err || error}</p>}
    {load.loading && !base && <p role="status">{t("正在读取 MCP 策略…")}</p>}
    {base && draft && <>
      <p className="small">{t("编辑基于版本 {version} · 作用域：全平台", { version: base.revision })}</p>
      {base.source === "builtin_default" && <p className="notice" role="note">{t("尚未发布过 MCP 策略：当前是内置默认值，两个方向都关闭——Agent 不能连接外部 MCP 服务器，外部客户端也不能调用 Synwevo。")}</p>}
      {manage.disabled && <p className="notice" role="note">{t("你的平台角色可以查看配置，不能发布更改。")}</p>}
      {conflict && <div className="notice warn col gap8" role="alert">
        <b>{t("配置已更新为版本 {version}，你的输入已保留。", { version: conflict.revision })}</b>
        <ul>{(Object.keys(FIELD_LABELS) as (keyof McpPolicySpec)[]).filter((k) => canonical({ ...base.spec, [k]: conflict.spec[k] }) !== canonical(base.spec))
          .map((k) => <li key={k}>{t("{field}：{before} → {after}", { field: t(FIELD_LABELS[k]), before: valueText(k, base.spec[k], t), after: valueText(k, conflict.spec[k], t) })}</li>)}</ul>
        <button type="button" className="btn" onClick={rebase}>{t("保留我的输入，按最新版本重新比较")}</button>
      </div>}
      <fieldset disabled={manage.disabled || previewing || !!confirmation} className="col gap16" style={{ border: 0, padding: 0, minWidth: 0 }}>
        <div className="col gap8">
          <h3 className="secttl">{t("方向")}</h3>
          <label className="check"><input type="checkbox" checked={draft.outbound_enabled} onChange={(e) => set({ outbound_enabled: e.target.checked })} />{t(FIELD_LABELS.outbound_enabled)}</label>
          <label className="check"><input type="checkbox" checked={draft.inbound_enabled} onChange={(e) => set({ inbound_enabled: e.target.checked })} />{t(FIELD_LABELS.inbound_enabled)}</label>
          <label className="check"><input type="checkbox" checked={draft.allow_http} onChange={(e) => set({ allow_http: e.target.checked })} />{t(FIELD_LABELS.allow_http)}</label>
        </div>
        <div className="col gap8">
          <h3 className="secttl">{t("网络目的地")}</h3>
          <p className="small muted" id={`${idBase}-hosts`}>{t("每行一个条目：精确主机名，或以 . 开头的域名后缀（匹配该域及其全部子域）。")}</p>
          <label className="check"><input type="checkbox" checked={draft.any_public_host} onChange={(e) => set({ any_public_host: e.target.checked })} />{t("允许任何公网主机")}</label>
          {!draft.any_public_host && <label className="field"><span>{t(FIELD_LABELS.allowed_hosts)}</span>
            <textarea className="input" rows={3} value={draft.allowed_hosts} aria-describedby={`${idBase}-hosts ${idBase}-allowed`} onChange={(e) => set({ allowed_hosts: e.target.value })} />
            <span className="tiny muted" id={`${idBase}-allowed`}>{t("留空表示不允许任何主机。")}</span></label>}
          <label className="field"><span>{t(FIELD_LABELS.blocked_hosts)}</span>
            <textarea className="input" rows={3} value={draft.blocked_hosts} aria-describedby={`${idBase}-hosts ${idBase}-blocked`} onChange={(e) => set({ blocked_hosts: e.target.value })} />
            <span className="tiny muted" id={`${idBase}-blocked`}>{t("无论其它设置如何都拒绝。")}</span></label>
          <label className="field"><span>{t(FIELD_LABELS.allow_private_networks)}</span>
            <textarea className="input" rows={3} value={draft.allow_private_networks} aria-describedby={`${idBase}-private`} onChange={(e) => set({ allow_private_networks: e.target.value })} />
            <span className="tiny muted" id={`${idBase}-private`}>{t("每行一个主机或 CIDR 网段；留空表示只允许公网地址。")}</span></label>
        </div>
        <div className="col gap8">
          <h3 className="secttl">{t("限额与超时")}</h3>
          <div className="row gap12 wrap">
            {NUMERIC_FIELDS.map((field) => {
              const min = bounds[field.key]?.minimum ?? field.min, max = bounds[field.key]?.maximum ?? field.max;
              const unit = field.unit ? t(field.unit) : "";
              return <label className="field" key={field.key} style={{ minWidth: 220 }}><span>{t(field.label)}{unit && `（${unit}）`}</span>
                <input className="input" type="number" inputMode="numeric" min={min} max={max} step={1} value={draft.numbers[field.key]}
                  aria-describedby={`${idBase}-${field.key}`} onChange={(e) => setNumber(field.key, e.target.value)} />
                <span className="tiny muted" id={`${idBase}-${field.key}`}>{unit ? t("允许 {min}–{max} {unit}。", { min, max, unit }) : t("允许 {min}–{max}。", { min, max })}</span></label>;
            })}
          </div>
        </div>
        <div className="col gap8">
          <h3 className="secttl">{t("外部客户端登记")}</h3>
          <label className="field"><span>{t(FIELD_LABELS.client_registration)}</span>
            <select className="input" value={draft.client_registration} onChange={(e) => set({ client_registration: e.target.value as ClientRegistration })}>
              {(Object.keys(REGISTRATION_LABELS) as ClientRegistration[]).map((k) => <option key={k} value={k}>{t(REGISTRATION_LABELS[k])}</option>)}
            </select></label>
          <p className="small muted">{t("自描述登记：客户端的 client_id 是一个 https 地址，那里的文档说明它是谁、回调到哪里。本服务读取并检查它，但不核实运营方。")}</p>
          {registration === "cimd_and_preregistered" && <label className="field"><span>{t(FIELD_LABELS.cimd_allowed_domains)}</span>
            <textarea className="input" rows={3} value={draft.cimd_allowed_domains} aria-describedby={`${idBase}-cimd`} onChange={(e) => set({ cimd_allowed_domains: e.target.value })} />
            <span className="tiny muted" id={`${idBase}-cimd`}>{t("留空表示任何 https 主机。只在接受自描述登记时生效。")}</span></label>}
        </div>
      </fieldset>
      {parsed.problems.length > 0 && <ul className="notice err" role="alert">{parsed.problems.map((p) => <li key={`${p.field}-${p.text}`}>{problemText(p)}</li>)}</ul>}
      <div className="row gap12 wrap">
        <button type="button" className="btn primary" disabled={!parsed.spec || !dirty || manage.disabled || previewing || !!conflict || !!load.err}
          onClick={() => parsed.spec && void preview(parsed.spec)}>{previewing ? t("正在预览…") : t("预览策略差异")}</button>
        {parsed.spec && !dirty && <span className="small muted">{t("策略草稿与当前版本相同。")}</span>}
      </div>
    </>}
    {confirmation && base && <ActionDialog key={confirmation.key} title={t("发布 MCP 策略")} confirmLabel={t("发布策略")} reason="required" size="wide"
      ready={confirmed && !previewing && !conflict}
      impact={<div className="col gap8">
        <p>{t("将发布版本 {version}，全平台立即生效：新的连接请求、新的调用和外部客户端的授权按新策略判断；已经建立的连接不会被删除，超出新限额的部分在下一次调用时被拒绝。", { version: base.revision + 1 })}</p>
        <ul>{confirmation.plan.changed.map((k) => <li key={k}>{t("{field}：{before} → {after}", {
          field: t(FIELD_LABELS[k as keyof McpPolicySpec] || k), before: valueText(k, confirmation.plan.before[k as keyof McpPolicySpec], t), after: valueText(k, confirmation.plan.after[k as keyof McpPolicySpec], t) })}</li>)}</ul>
        <p className="small muted">{t("预览有效至 {time}，配置变化后需重新预览。", { time: fmtTime(confirmation.plan.expires_at) })}</p>
        {conflict && <p className="notice warn" role="alert">{t("请关闭确认框，比较最新配置后重新预览。")}</p>}
      </div>}
      run={async (reason) => {
        try {
          return await post<Published>(`${BASE}/policy/publish`, { expected_revision: base.revision, spec: confirmation.spec,
            preview_token: confirmation.plan.preview_token, idempotency_key: confirmation.key, reason });
        } catch (err) { recordFailure(err); throw err; }
      }}
      onClose={() => setConfirmation(null)}
      onDone={(out) => { setResult(out); adopt(out.state); setConflict(null); setConfirmation(null); setError(null); setNotice(null); onPublished(); }}>
      <button type="button" className="btn sm" disabled={previewing || !!conflict} onClick={() => void preview(confirmation.spec, confirmation.key)}>{t("重新预览策略差异")}</button>
      <label className="check"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />{t("我已核对策略差异及其影响。")}</label>
    </ActionDialog>}
  </section>;
}

// ------------------------------------------------------------------------------------ the history
function HistoryPanel({ load, before, setBefore, manage, baseRevision, onLoadDraft }: {
  load: ReturnType<typeof useAdminData<{ items: Version[] }>>; before: number | null; setBefore: (n: number | null) => void;
  manage: ReturnType<typeof permitted>; baseRevision: number | null; onLoadDraft: (version: Version) => void;
}) {
  useI18n(); const t = tr;
  const items = load.data?.items || [];
  const summary = (spec: McpPolicySpec) => t("出站 {outbound} · 入站 {inbound} · 明文 http {http} · {registration}", {
    outbound: valueText("outbound_enabled", spec.outbound_enabled, t), inbound: valueText("inbound_enabled", spec.inbound_enabled, t),
    http: valueText("allow_http", spec.allow_http, t), registration: valueText("client_registration", spec.client_registration, t) });
  return <section className="card card-b col gap12" aria-labelledby="mcp-history-heading">
    <h2 id="mcp-history-heading">{t("MCP 策略历史")}</h2>
    <p className="small muted">{t("每次发布是一个完整快照；这里只有版本、时间、理由与操作者，没有任何连接凭据。")}</p>
    {load.err && <p className="notice err" role="alert">{load.err}</p>}
    {load.loading && !load.data ? <p role="status">{t("加载中…")}</p> : load.data && <>
      {!items.length && <p className="muted">{t("尚无发布记录，当前采用内置默认值。")}</p>}
      {items.map((item) => <article className="notice col gap8" key={item.revision}>
        <div className="row wrap gap8"><b>{t("版本 {version}", { version: item.revision })}</b><span className="small muted">{fmtTime(item.created_at)}</span></div>
        {item.spec ? <p className="small">{summary(item.spec)}</p> : <p className="small muted">{t("这个版本的记录无法读取。")}</p>}
        <p className="small">{t("发布理由：{reason}", { reason: item.reason || t("没有写理由") })}</p>
        <p className="tiny muted">{t("操作者：{actor}；操作编号：{operation}", { actor: item.actor_id || DASH, operation: item.operation_id || DASH })}</p>
        {!manage.disabled && item.spec && item.revision !== baseRevision && <div><button type="button" className="btn sm" onClick={() => onLoadDraft(item)}>{t("载入为草稿")}</button></div>}
      </article>)}
      <div className="row wrap gap8">
        {before !== null && <button type="button" className="btn" onClick={() => setBefore(null)}>{t("返回最新记录")}</button>}
        {items.length >= 20 && <button type="button" className="btn" onClick={() => setBefore(items[items.length - 1].revision)}>{t("更早记录")}</button>}
      </div>
    </>}
  </section>;
}

// ------------------------------------------------------------------------------------ the inbound card
export function InboundCard({ state }: { state: PolicyState | null }) {
  useI18n(); const t = tr;
  const origin = typeof window !== "undefined" && window.location?.origin ? window.location.origin : "";
  return <section className="card card-b col gap12" aria-labelledby="mcp-inbound-heading">
    <h2 id="mcp-inbound-heading">{t("Synwevo 作为 MCP 服务器")}</h2>
    <p className="muted">{t("外部 MCP 客户端先在本账户服务完成 OAuth 授权，再调用工作空间站点上的 MCP 端点。每次授权都由本人在同意页面批准，令牌只在一个工作空间内有效。")}</p>
    <dl className="facts">
      <dt>{t("入站状态")}</dt><dd>{state ? <span className={`badge ${state.spec.inbound_enabled ? "ok" : ""}`}>{t(state.spec.inbound_enabled ? "已开放" : "关")}</span> : DASH}</dd>
      <dt>{t("MCP 端点（工作空间站点上的路径）")}</dt><dd><code>{MCP_ENDPOINT_PATH}</code></dd>
      <dt>{t("授权服务器元数据（本站点）")}</dt><dd><code>{origin}{AUTHORIZATION_SERVER_METADATA_PATH}</code></dd>
      <dt>{t("客户端登记")}</dt><dd>{state ? t(REGISTRATION_LABELS[state.spec.client_registration] || state.spec.client_registration) : DASH}</dd>
    </dl>
    <p className="tiny muted">{t("这一页只写出路径，不臆造主机名：工作空间站点的完整地址以部署为准，Agent 的「连接你的 MCP 客户端」会给出它。")}</p>
  </section>;
}

// ------------------------------------------------------------------------------------ the overview
/** The platform stop of a connection and its lifting (admin_mcp.disable / admin_mcp.lift). Both are
 *  dangerous operations: the server asks for strict re-authentication (403 `reauth_required`, which
 *  ActionDialog answers and retries) and refuses without the written reason it audits. */
export const stopConnection = (id: string, reason: string) =>
  post<Stopped>(`${BASE}/connections/${encodeURIComponent(id)}/disable`, { reason });
export const liftConnection = (id: string, reason: string) =>
  post<Stopped>(`${BASE}/connections/${encodeURIComponent(id)}/lift`, { reason });

function Stat({ name, value }: { name: string; value: number }) {
  return <div className="card stat"><div className="muted small">{name}</div><div className="v">{formatNumber(value)}</div></div>;
}

export function OverviewPanel({ onDenied, manage }: { onDenied: () => void; manage: ReturnType<typeof permitted> }) {
  useI18n(); const t = tr;
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const [health, setHealth] = useState("");
  const load = useAdminData<Overview>(`${BASE}/overview${qs({ q: query, health, limit: 500 })}`, onDenied);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [disabling, setDisabling] = useState<Connection | null>(null);
  const [lifting, setLifting] = useState<Connection | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const items = load.loading || load.err ? [] : load.data?.items || [];
  const stoppedDestinations = load.loading || load.err ? [] : load.data?.stopped_destinations || [];
  const totals = load.data?.totals;
  const busy = load.loading || !!load.err;
  return <section className="card card-b col gap12" aria-labelledby="mcp-overview-heading">
    <div className="row gap12 wrap"><h2 id="mcp-overview-heading" className="grow">{t("MCP 连接概览")}</h2>
      <button type="button" className="btn sm" disabled={load.loading} onClick={load.reload}>{t("刷新连接概览")}</button></div>
    <p className="muted small">{t("每个连接的名称、主机、状态与数量，不含凭据、参数或结果。平台停用的连接排在最前，其次是失败的连接。")}</p>
    {manage.disabled && <p className="notice" role="note">{t("你的平台角色可以查看 MCP 策略与连接，不能发布策略，也不能平台停用连接或解除停用。")}</p>}
    <div role="status" aria-live="polite">{notice && <p className="notice ok">{notice}</p>}</div>
    {totals && <div className="row gap12 wrap">
      <Stat name={t("连接")} value={totals.connections} />
      <Stat name={t("连接失败")} value={totals.failed} />
      <Stat name={t("需要授权")} value={totals.auth_required} />
      <Stat name={t("24 小时调用")} value={totals.calls_24h} />
      <Stat name={t("其中失败")} value={totals.failed_calls_24h} />
      <Stat name={t("24 小时入站请求")} value={totals.inbound_requests_24h} />
    </div>}
    {stoppedDestinations.length > 0 && <div className="col gap8" role="group" aria-labelledby="mcp-stopped-heading">
      <h3 id="mcp-stopped-heading">{t("被平台停用的服务地址")}</h3>
      <p className="muted small">{t("平台停用一个连接时，也会关闭它的服务地址：解除之前，任何人都不能为这个地址添加连接、提供凭据或通过它调用。这里只显示主机，不显示地址的路径。")}</p>
      <ul className="col gap4">{stoppedDestinations.map((d) => <li key={d.connection_id}>
        <b>{d.host || DASH}</b>
        <span className="cell-note">{t("自 {time} 起，由「{name}」的平台停用关闭", { time: fmtTime(d.since), name: d.connection_name || d.connection_id })}</span>
        {d.other_connections > 0 && <span className="cell-note">{t("因此不能使用的其他连接：{count} 个", { count: d.other_connections })}</span>}
      </li>)}</ul>
    </div>}
    <div className="row gap12 wrap">
      <input type="search" className="input" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} placeholder={t("搜索名称或主机")} aria-label={t("搜索连接")} />
      <label className="field">{t("筛选健康状态")}<select className="input" aria-label={t("筛选健康状态")} value={health} onChange={(e) => setHealth(e.target.value)}>
        <option value="">{t("全部健康状态")}</option>
        {Object.keys(HEALTH_LABELS).map((k) => <option key={k} value={k}>{t(HEALTH_LABELS[k])}</option>)}
      </select></label>
    </div>
    {load.err && <div className="notice err" role="alert">{load.err} <button type="button" className="btn sm" onClick={load.reload}>{t("重试")}</button></div>}
    {load.loading && <p role="status">{t("正在读取连接概览…")}</p>}
    <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={t("MCP 连接概览")} aria-busy={load.loading}>
      <thead><tr>
        <th scope="col">{t("连接")}</th><th scope="col">{t("主机")}</th><th scope="col">{t("状态")}</th>
        <th scope="col">{t("工具")}</th><th scope="col">{t("绑定的 Agent")}</th><th scope="col">{t("近 7 天调用")}</th>
        <th scope="col">{t("最近调用")}</th><th scope="col" className="act">{t("操作")}</th>
      </tr></thead>
      <tbody>{items.map((c) => <Fragment key={c.id}>
        <tr>
          <th scope="row">{c.name}<div className="who2 mono">{c.id}</div>
            <span className="cell-note">{c.owner_kind === "workspace" ? t("工作空间：{name}", { name: c.owner_name || DASH }) : t("个人连接")}</span></th>
          <td data-label={t("主机")}><div>{c.host || DASH}
            <span className="cell-note">{t(AUTH_LABELS[c.auth_kind] || c.auth_kind)}{c.protocol_version && <> · {t("协议 {version}", { version: c.protocol_version })}</>}</span></div></td>
          <td data-label={t("状态")}><div className="row gap4 wrap">
            {c.platform_disabled ? <span className="badge err">{t("已被平台停用")}</span>
              : <span className={`badge ${c.status === "active" ? "ok" : "warn"}`}>{t(STATUS_LABELS[c.status] || c.status)}</span>}
            {!c.platform_disabled && c.destination_stopped && <span className="badge err">{t("地址已被平台停用")}</span>}
            <span className={`badge ${HEALTH_BADGE[c.health] ?? ""}`}>{t(HEALTH_LABELS[c.health] || c.health)}</span></div>
            {c.platform_disabled && <span className="cell-note">{t("平台停用于 {time}", { time: c.platform_disabled_at ? fmtTime(c.platform_disabled_at) : DASH })}</span>}
            {!c.platform_disabled && c.destination_stopped && <span className="cell-note">{t("另一个连接的平台停用关闭了这个服务地址；解除那次停用之前，这个连接不能使用。")}</span>}
            {c.circuit_open_until && <span className="cell-note">{t("熔断至 {time}", { time: fmtTime(c.circuit_open_until) })}</span>}
            {c.last_error_code && <span className="cell-note">{t("最近错误 {code}，连续 {count} 次", { code: c.last_error_code, count: c.failure_streak })}</span>}</td>
          <td data-label={t("工具")}>{t("已授权 {granted} / {total}", { granted: c.granted, total: c.tools })}</td>
          <td data-label={t("绑定的 Agent")}>{formatNumber(c.agents)}</td>
          <td data-label={t("近 7 天调用")}><div>{formatNumber(c.calls_7d)}<span className="cell-note">{t("其中失败 {count}", { count: c.failed_7d })}</span></div></td>
          <td data-label={t("最近调用")}><div>{c.last_call_at ? fmtTime(c.last_call_at) : DASH}
            <span className="cell-note">{c.last_ok_at ? t("最近成功 {time}", { time: fmtTime(c.last_ok_at) }) : t("尚未成功过")}</span></div></td>
          <td className="act" data-label={t("操作")}><div className="row gap4 wrap">
            <button type="button" className="btn sm" aria-expanded={!!open[c.id]} aria-controls={`mcp-calls-${c.id}`} aria-label={t("最近调用记录：{name}", { name: c.name })}
              onClick={() => setOpen({ ...open, [c.id]: !open[c.id] })}>{t("最近调用记录")}</button>
            {!manage.disabled && (c.platform_disabled
              ? <button type="button" className="btn sm" disabled={busy}
                aria-label={t("解除平台停用：{name}", { name: c.name })} onClick={() => setLifting(c)}>{t("解除平台停用")}</button>
              : <button type="button" className="btn sm danger" disabled={busy}
                aria-label={t("平台停用：{name}", { name: c.name })} onClick={() => setDisabling(c)}>{t("平台停用")}</button>)}
          </div></td>
        </tr>
        {open[c.id] && <tr><td colSpan={8} id={`mcp-calls-${c.id}`}><ConnectionCalls id={c.id} onDenied={onDenied} /></td></tr>}
      </Fragment>)}
      {!load.loading && !load.err && !items.length && <tr className="empty-row"><td colSpan={8}>{query || health ? t("没有匹配的连接。") : t("还没有任何 MCP 连接。")}</td></tr>}</tbody>
    </table></div>
    {disabling && <ActionDialog title={t("平台停用连接")} confirmLabel={t("平台停用")} reason="required" danger
      impact={<p>{t("将在全平台停用「{name}」（{host}）：它保存的凭据（令牌、OAuth 客户端密钥与动态登记、进行中的授权）会被清除，绑定它的 {count} 个 Agent 不再得到它的工具，新的调用、读取与连接测试都会被拒绝。它的服务地址也一并关闭（同一主机、端口与路径，不论大小写、路径参数或查询怎样书写）：同一地址的其他连接停止使用，任何人都不能再为这个地址添加连接；要停用整个主机，请在策略的「禁止连接的主机」中加入它。拥有者不能自行恢复或删除它，也不能为它填写新的凭据或地址；只有平台管理员可以解除停用。连接本身、工具授权与调用记录保留。",
        { name: disabling.name, host: disabling.host || DASH, count: disabling.agents })}</p>}
      run={(reason) => stopConnection(disabling.id, reason)}
      onClose={() => setDisabling(null)}
      onDone={() => { setNotice(t("已在全平台停用「{name}」；只有平台管理员可以解除。", { name: disabling.name })); setDisabling(null); load.reload(); }} />}
    {lifting && <ActionDialog title={t("解除平台停用")} confirmLabel={t("解除平台停用")} reason="required"
      impact={<p>{t("将解除「{name}」（{host}）自 {time} 起的平台停用。连接仍保持停用，也没有凭据：由它的拥有者决定是否重新启用，并在需要时重新填写凭据或重新授权；平台不会替拥有者启用它。",
        { name: lifting.name, host: lifting.host || DASH, time: lifting.platform_disabled_at ? fmtTime(lifting.platform_disabled_at) : DASH })}</p>}
      run={(reason) => liftConnection(lifting.id, reason)}
      onClose={() => setLifting(null)}
      onDone={() => { setNotice(t("已解除「{name}」的平台停用；连接仍保持停用，由拥有者决定是否重新启用。", { name: lifting.name })); setLifting(null); load.reload(); }} />}
  </section>;
}

/** The last calls of one connection (`GET /api/admin/mcp/connections/{id}/calls`): type, name, origin,
 *  outcome, duration, error code and request id — the server sends neither arguments nor results. */
export function ConnectionCalls({ id, onDenied }: { id: string; onDenied: () => void }) {
  useI18n(); const t = tr;
  const load = useAdminData<{ items: Call[] }>(`${BASE}/connections/${encodeURIComponent(id)}/calls?limit=50`, onDenied);
  if (load.loading && !load.data) return <p role="status">{t("正在读取调用记录…")}</p>;
  if (load.err) return <div className="notice err" role="alert">{load.err} <button type="button" className="btn sm" onClick={load.reload}>{t("重试")}</button></div>;
  const items = load.data?.items || [];
  if (!items.length) return <p className="muted small">{t("这个连接还没有调用记录。")}</p>;
  return <div className="col gap8">
    <p className="muted small">{t("最近 {count} 条调用：只有类型、名称、来源、结果与耗时；参数与结果不在这里显示。", { count: items.length })}</p>
    <div className="tbl-wrap"><table className="tbl">
      <thead><tr><th scope="col">{t("时间")}</th><th scope="col">{t("类型")}</th><th scope="col">{t("名称")}</th><th scope="col">{t("调用来源")}</th>
        <th scope="col">{t("结果")}</th><th scope="col">{t("耗时")}</th><th scope="col">{t("错误码")}</th><th scope="col">{t("请求编号")}</th></tr></thead>
      <tbody>{items.map((call) => <tr key={call.id}>
        <td>{fmtTime(call.started_at)}</td>
        <td>{t(CALL_KIND[call.kind] || call.kind)}</td>
        <td>{call.name || DASH}</td>
        <td>{t(CALL_ORIGIN[call.origin] || call.origin)}</td>
        <td><span className={`badge ${CALL_BADGE[call.status] ?? ""}`}>{t(CALL_STATUS[call.status] || call.status)}</span></td>
        <td>{call.duration_ms == null ? DASH : t("{n} 毫秒", { n: call.duration_ms })}</td>
        <td>{call.error_code ? <code>{call.error_code}</code> : DASH}</td>
        <td>{call.request_id ? <code>{call.request_id}</code> : DASH}</td>
      </tr>)}</tbody>
    </table></div>
  </div>;
}
