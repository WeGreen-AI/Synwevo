/**
 * How a refusal from the account service reaches a person in this shell (CI-85).
 *
 * Every error helper of the shell — the sign-in and organization pages' `errorText`, the account
 * page's `errorText`, the administration pages' `errText` and the API console's notice — words a
 * refusal through `refusalText`, in one order:
 *
 *   1. a template registered here, for a refusal whose sentence carries a number or a name from
 *      the refusal's `detail` (a limit, a provider, a password rule);
 *   2. the server's own sentence, when a catalogue of this shell holds it — the most specific
 *      wording there is;
 *   3. the helper's own code map, then `REFUSAL_SOURCES` below, for a code whose several server
 *      sentences share one meaning or whose sentence the catalogue cannot hold;
 *   4. the codes every shell shares (`API_ERROR_SOURCE`), and only then "请求未完成（code）".
 *
 * `tests/refusals.test.mjs` reads every `ServiceError(...)` the account service and the shared
 * modules it calls can raise and holds each one to 1–3 on the pages where a person can meet it,
 * or to a listed reason why no person can.
 */
import { ApiError } from "@synwevo/auth-client";
import { apiFailureSentence, registerErrorTemplates, type ErrorTemplate } from "@synwevo/auth-client/api-messages";
import { formatDate, t } from "@synwevo/ui";
import "../refusals.messages";

const positive = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null);
const text = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);
const MB = 1024 * 1024;
const megabytes = (bytes: number) => Math.max(1, Math.floor(bytes / MB));

/** A password the policy refused, from the rule it broke (`detail.reason`, see
 *  synwevo_core/passwords.py) and its numbers: the floor is 8 or 15 depending on a second factor. */
const PASSWORD_RULES: Record<string, string> = {
  common: "这个口令在常见泄露口令名单里，请换一个。",
  repetitive: "这个口令重复度太高，请换一个。",
  service_name: "口令不能包含本服务的名称，请换一个。",
  personal: "口令不能包含你的邮箱或姓名，请换一个。",
};
function passwordRule(d: any): readonly [string, Record<string, string | number>?] | null {
  if (d.reason === "too_short" && positive(d.min) && typeof d.length === "number") {
    return ["口令至少需要 {min} 个字符（当前 {length} 个）。长句子比复杂符号更好记也更安全。", { min: d.min, length: d.length }];
  }
  if (d.reason === "too_long" && positive(d.max)) return ["口令过长（上限 {max} 个字符）。", { max: d.max }];
  return Object.hasOwn(PASSWORD_RULES, d.reason) ? [PASSWORD_RULES[d.reason]] : null;
}

/** Why an external identity provider could not be used, named by `detail.reason`. */
const PROVIDER_FAULTS: Record<string, string> = {
  http_status: "无法连接 {provider}，请稍后再试",
  network: "无法连接 {provider}，请稍后再试",
  incomplete: "{provider} 的发现文档不完整",
  issuer_mismatch: "{provider} 的发现文档 issuer 与配置不一致",
  not_https: "{provider} 的端点不是 https",
  no_keys: "无法取得 {provider} 的签名密钥",
};

/** A44-L1: a departure — leaving or being removed from an organization, deleting or suspending the
 *  account — that would leave a workspace the platform deleted, and can still recover, without its
 *  only usable owner (`role` owner) or without anybody to manage it (`role` admin). Named with the
 *  end of its recovery window (`recover_until`, seconds), and the two ways out. */
const DELETED_WORKSPACE_OWNER: Record<string, string> = {
  owner: "此账户是已删除、仍在恢复期内的工作空间「{name}」唯一可用的拥有者；恢复期限（{until}）结束前不能执行此操作。可请平台管理员恢复该工作空间后移交所有权，或等待恢复期限结束。",
  admin: "此账户是已删除、仍在恢复期内的工作空间「{name}」唯一可用的管理者；恢复期限（{until}）结束前不能执行此操作。可请平台管理员恢复该工作空间并另设拥有者或管理者，或等待恢复期限结束。",
};

export const REFUSAL_TEMPLATES: Record<string, ErrorTemplate> = {
  // a code at sign-in: how many tries are left, and what to do once none are (`exhausted`:
  // a second-factor ticket means signing in again, a mailed code means asking for a new one)
  bad_code: (d) => {
    if (typeof d.remaining !== "number" || !Number.isInteger(d.remaining) || d.remaining < 0) return null;
    if (d.remaining > 0) return ["验证码不正确，还可尝试 {count} 次。", { count: d.remaining }] as const;
    return [d.exhausted === "resend" ? "验证码不正确，请重新获取。" : "验证码不正确，请重新登录。"] as const;
  },
  weak_password: (d) => {
    const rule = passwordRule(d);
    if (!rule) return null;
    // A reset spends its link before the password is checked (see lib/password.ts).
    return d.link_spent ? ["{problem}（这个链接已失效，请重新申请一封邮件）", { problem: t(rule[0], rule[1]) }] as const : rule;
  },
  idp_unreachable: (d) => {
    const provider = text(d.provider); const source = PROVIDER_FAULTS[d.reason];
    return provider && source ? [source, { provider }] as const : null;
  },
  identity_linked_elsewhere: (d) => {
    const provider = text(d.provider);
    return provider ? ["这个 {provider} 账户已关联到另一个 Synwevo 账户", { provider }] as const : null;
  },
  invite_rate_limited: (d) => {
    const n = positive(d.limit);
    return n == null ? null : ["这个空间 24 小时内最多发出 {n} 个邀请，请稍后再试", { n }] as const;
  },
  file_too_large: (d) => { const n = positive(d.limit); return n == null ? null : ["成员文件超过 {n} MB 上限", { n: megabytes(n) }] as const; },
  scan_limit: (d) => {
    const n = positive(d.limit);
    return n == null ? null : ["文件超过安全检查的大小上限（{n} MB），无法接纳", { n: megabytes(n) }] as const;
  },
  malware_detected: (d) => {
    const filename = text(d.filename);
    if (!filename) return null;
    const signature = text(d.signature);
    return signature ? ["文件「{filename}」被判定为恶意内容（{signature}），已拒绝", { filename, signature }] as const
      : ["文件「{filename}」被判定为恶意内容，已拒绝", { filename }] as const;
  },
  // `too_many` is shared: the passkey limit says so in `kind`, a bulk request names its field
  too_many: (d) => {
    const n = positive(d.limit);
    if (n == null) return null;
    return d.kind === "passkey" ? ["最多只能登记 {n} 个安全密钥", { n }] as const : ["一次最多处理 {n} 项", { n }] as const;
  },
  too_many_devices: (d) => { const n = positive(d.limit); return n == null ? null : ["最多 {n} 个设备，请先移除不用的设备", { n }] as const; },
  too_many_roles: (d) => { const n = positive(d.limit); return n == null ? null : ["一个组织最多 {n} 个自定义角色", { n }] as const; },
  // the fixed "8 至 500 个字" sentences are catalogued; only the one quoting the floor is worded here
  missing_reason: (d) => { const n = positive(d.min); return n == null ? null : ["请写明理由（至少 {n} 个字），它会写入审计记录", { n }] as const; },
  // API-1 traffic and capacity policies: the range depends on the field
  invalid_limit: (d) => {
    const min = positive(d.min), max = positive(d.max);
    return min == null || max == null ? null : ["限制值必须是 {min} 至 {max} 之间的整数，留空表示继承", { min, max }] as const;
  },
  owner_of_deleted_workspace: (d) => {
    const name = text(d.name), until = positive(d.recover_until);
    if (!name || until == null || typeof d.role !== "string" || !Object.hasOwn(DELETED_WORKSPACE_OWNER, d.role)) return null;
    return [DELETED_WORKSPACE_OWNER[d.role], { name, until: formatDate(until * 1000, { dateStyle: "medium", timeStyle: "short" }) }] as const;
  },
};

/** Codes whose several server sentences share one meaning, or whose sentence names something the
 *  catalogue cannot hold (an exception class, a provider's name). Consulted after the server's
 *  catalogued sentence and after the page's own code map. */
export const REFUSAL_SOURCES: Record<string, string> = {
  // Provider governance: safe defaults when the server sends only a code or an
  // uncatalogued diagnostic. Never interpolate submitted credential or digest detail.
  billing_not_configured: "此部署尚未配置计费环境",
  model_config_invalid: "模型配置字段无效，请检查路线、有效期和凭据",
  model_config_unavailable: "模型配置暂不可用，请稍后重试",
  model_bad_revision: "模型配置版本无效，请重新读取",
  payment_revision_conflict: "充值配置已变化，请刷新后重试",
  model_revision_conflict: "模型配置已变化，请保留输入并重新读取",
  model_credential_required: "启用模型前请配置平台凭据",
  model_route_expired: "模型路线核实已过期，请重新核实",
  model_price_unavailable: "请先发布同一环境的销售价格与成本价格",
  model_price_mismatch: "模型路线与已生效价格不匹配，请重新选择",
  model_price_markup_basis: "销售价由另一成本价加价生成，请选择与之对应的成本价",
  model_cost_policy_required: "请检查模型成本预算已启用，并与成本价格的单位一致。",
  cost_traffic_invalid: "速率与排队设置无效：付款人上限不能高于平台上限，排队上限须在总上限内",
  model_no_change: "模型配置未发生变化，无需提交",
  model_bad_page: "模型配置历史分页参数无效",
  invalid_idempotency_key: "Idempotency-Key 必须是 1–255 个可打印 ASCII 字符，建议使用 UUID",
  // operator settings: the server names the variable, a person needs to know whom to ask
  public_url_missing: "邮件链接地址尚未配置，请联系部署管理员。",
  mail_not_configured: "邮件服务尚未配置，请联系管理员。",
  smtp_not_configured: "邮件服务尚未配置，请联系管理员。",
  mail_config_invalid: "邮件服务配置无效，请联系部署管理员。",
  smtp_config_invalid: "邮件服务配置无效，请联系部署管理员。",
  idp_unreachable: "暂时无法连接外部身份提供商，请稍后再试。",
  identity_linked_elsewhere: "这个外部账户已关联到另一个 Synwevo 账户。",
  bad_attestation: "安全密钥登记失败，请重试。",
  bad_assertion: "安全密钥验证失败，请重试。",
  // "挑战已过期或不存在" / "挑战已被使用": the WebAuthn challenge is not a word a person knows
  no_challenge: "安全密钥验证已过期，请重新开始。",
  // the sentence names the role's id
  unknown_org_role: "所选的组织角色已不存在，请刷新后重新选择。",
  // the sentence quotes the status code word ("当前：frozen")
  not_active: "只能冻结状态正常的工作空间，请刷新后再试。",
  // A44-L1: the sentence names a workspace and a time; without them in `detail`, this
  owner_of_deleted_workspace: "此账户是一个已删除、仍在恢复期内的工作空间唯一可用的拥有者或管理者；恢复期限结束前不能执行此操作。可请平台管理员恢复该工作空间后移交，或等待恢复期限结束。",
};

registerErrorTemplates(REFUSAL_TEMPLATES);

/** A refusal (or a per-item result of a batch) as one sentence in the reader's language. */
export function refusalText(error: ApiError | { code: string; status?: number; originalMessage?: string; message?: string; detail?: unknown },
                            local: Record<string, string> = {}): string {
  const failure = error instanceof ApiError ? error
    : { code: error.code, status: error.status ?? 0, originalMessage: error.originalMessage ?? error.message ?? "", detail: error.detail };
  return apiFailureSentence(failure, { ...REFUSAL_SOURCES, ...local });
}
