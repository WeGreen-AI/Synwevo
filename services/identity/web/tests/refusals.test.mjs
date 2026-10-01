/** CI-85: every refusal a person can meet in the account shell reaches them as a sentence in their
 *  language — never as "操作失败，请重试。" when the server said something specific, and never as
 *  "请求未完成（code）".
 *
 *  This reads every `ServiceError(...)` of the account service and of the shared modules it calls,
 *  decides from `SERVER` on which pages of this shell a person can meet it, loads each of those
 *  pages the way the browser does (the entry chunk plus that page's chunk, in a module graph of its
 *  own, so a sentence catalogued only for another page does not count) and words the refusal with
 *  that page's error helper in English. The English must be a translated sentence, not a generic
 *  fallback and not the shared label for its code (`API_ERROR_SOURCE`) — unless the code is listed
 *  in `SHARED_CODES` with a reason. A refusal no person can cause is listed in `API_ONLY` with a
 *  reason, so adding a server refusal means deciding which of the two it is.
 *
 *  Fixtures are isolated: no API, no user data. Run from the package: `npm test`.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const ROOT = fileURLToPath(new URL("../../../../", import.meta.url));
const ID = "services/identity/synwevo_identity/";
const CORE = "packages/core/synwevo_core/";
const CLIENT = "packages/auth-client/python/synwevo_auth_client/";

// ------------------------------------------------------------------------------------ pages
/** What the browser has loaded when a refusal arrives: `main.tsx`'s static imports (the sign-in
 *  page and AuthFlow, with `lib/refusals.ts`) and the chunk of the page the person is on. */
const ENTRY = ["/src/pages/Login.tsx"];
const SURFACES = {
  signin: { modules: ENTRY, helper: ["/src/pages/org/AuthFlow.tsx", (m, ui) => (e) => m.errorText(e, ui.t)] },
  account: { modules: [...ENTRY, "/src/pages/Account.tsx"], helper: ["/src/pages/account/common.tsx", (m, ui) => (e) => m.errorText(e, ui.t)] },
  org: { modules: [...ENTRY, "/src/pages/Organizations.tsx"], helper: ["/src/pages/org/AuthFlow.tsx", (m, ui) => (e) => m.errorText(e, ui.t)] },
  admin: { modules: [...ENTRY, "/src/Admin.tsx"], helper: ["/src/governance/labels.ts", (m) => (e) => m.errText(e)] },
  members: { modules: [...ENTRY, "/src/pages/Members.tsx"], helper: ["/src/governance/labels.ts", (m) => (e) => m.errText(e)] },
  api: { modules: [...ENTRY, "/src/pages/ApiPlatform.tsx"], helper: ["/src/pages/apiplatform/common.tsx", (m, ui) => (e) => m.failureText(e, ui.t)] },
};
const ALL = ["signin", "account", "org", "admin", "members", "api"];

// ------------------------------------------------------------------------------------ server
/** Where each refusal can be met. The first entry whose file and function match decides; `fns`
 *  names functions (a dotted prefix or a bare name). `surfaces: []` means no page of this shell
 *  shows it, and says why. `ext` is the external sign-in callback: it redirects to the sign-in
 *  page with the refusal's code, which `EXTERNAL_ERRORS` in Login.tsx words. */
const SERVER = [
  { file: ID + "account_routes.py", surfaces: ["account"] },
  { file: ID + "admin_routes.py", surfaces: ["admin"] },
  { file: ID + "admin_operations.py", surfaces: ["admin"] },
  { file: ID + "admin_settings.py", surfaces: ["admin"] },
  { file: ID + "admin_model_prices.py", surfaces: ["admin"] },
  { file: ID + "admin_model_costs.py", surfaces: ["admin"] },
  { file: ID + "wallet_routes.py", surfaces: ["account"] },
  { file: CORE + "billing/catalogue.py", surfaces: ["admin"] },
  { file: CORE + "billing/cost_settings.py", surfaces: ["admin"] },
  { file: ID + "admin_handover.py", surfaces: ["account", "admin"] },
  { file: ID + "api_platform.py", surfaces: ["api"] },
  { file: ID + "approval_routes.py", surfaces: ["org"] },
  // re-authentication is asked for on every page that changes something
  { file: ID + "auth_routes.py", fns: ["_too_many", "check_reauth_password", "reauth"], surfaces: ALL },
  { file: ID + "reauth_routes.py", surfaces: ["account", "org", "admin", "members", "api"] },
  { file: ID + "auth_routes.py", fns: ["build_router._require_session", "revoke_session", "build_passkey_router", "build_mfa_router"], surfaces: ["account"] },
  { file: ID + "auth_routes.py", fns: ["verify_send"], surfaces: ["signin", "account"] },
  { file: ID + "auth_routes.py", fns: ["build_invite_router._session"], surfaces: ["signin", "members"] },
  { file: ID + "auth_routes.py", surfaces: ["signin"] },
  { file: ID + "email_login.py", surfaces: ["signin"] },
  { file: ID + "external.py", fns: ["load_providers"], surfaces: [], why: "start-up configuration: a half-configured provider stops the service from starting" },
  { file: ID + "external.py", fns: ["complete", "verify_id_token", "link"], surfaces: ["ext"] },
  { file: ID + "external.py", fns: ["provider", "discovery", "jwks"], surfaces: ["ext", "account", "admin"] },
  { file: ID + "external.py", fns: ["build_router.start"], surfaces: [], why: "a start address the sign-in page builds itself" },
  { file: ID + "external.py", fns: ["offer_preview", "offer_confirm"], surfaces: ["signin"] },
  { file: ID + "external.py", surfaces: ["account"] },
  { file: ID + "invites.py", fns: ["may_invite", "_create"], surfaces: ["members", "org"] },
  { file: ID + "invites.py", fns: ["list_for_workspace", "update", "resend", "revoke"], surfaces: ["members"] },
  { file: ID + "invites.py", surfaces: ["signin"] },
  { file: ID + "mail.py", surfaces: ALL },
  { file: ID + "member_import_routes.py", surfaces: ["org"] },
  { file: ID + "members.py", surfaces: ["members"] },
  { file: ID + "messaging_reports_routes.py", surfaces: ["org"] },
  { file: ID + "mfa.py", surfaces: ["account"] },
  { file: ID + "organization_routes.py", surfaces: ["org"] },
  { file: ID + "passkeys.py", fns: ["verify"], surfaces: ["signin"] },
  { file: ID + "passkeys.py", fns: ["_spend_challenge_for"], surfaces: ["signin", "account"] },
  { file: ID + "passkeys.py", fns: ["current_reauth_session", "begin_reauthentication", "_reauth_client_data", "spend_reauthentication", "verify_reauthentication"], surfaces: ["account", "org", "admin", "members", "api"] },
  { file: ID + "passkeys.py", surfaces: ["account"] },
  { file: ID + "profile.py", surfaces: ["account"] },
  { file: ID + "provider_routes.py", fns: ["build_router._consent_actor", "build_router.consent_request", "build_router.consent_decide",
      "build_router.connected_apps", "build_router.connected_app_revoke"], surfaces: ["account"] },
  { file: ID + "provider_routes.py", surfaces: [], why: "OIDC protocol endpoints: a client's redirect or back channel, never a page of this shell" },
  { file: ID + "workspace_api.py", surfaces: [], why: "the workspace directory API answers machine clients only; no page of this shell calls it" },
  { file: ID + "workspace_actor.py", surfaces: ["signin", "members", "api"] },
  { file: ID + "push_routes.py", surfaces: ["account"] },
  { file: ID + "registration.py", surfaces: ["signin"] },
  { file: CLIENT + "session_routes.py", fns: ["switch_workspace"], surfaces: ["signin", "org"] },
  { file: CLIENT + "session_routes.py", surfaces: ALL },
  { file: CORE + "identity.py", fns: ["create_user", "set_password", "ensure_personal_workspace"], surfaces: ["signin", "account"] },
  { file: CORE + "identity.py", fns: ["add_member"], surfaces: ["signin", "admin"] },
  { file: CORE + "identity.py", fns: ["set_status", "set_workspace_frozen"], surfaces: ["admin"] },
  { file: CORE + "identity.py", fns: ["create_workspace"], surfaces: ["org"] },
  { file: CORE + "identity.py", surfaces: ["members"] },
  { file: CORE + "organizations.py", fns: ["accept_workspace_invitation"], surfaces: ["signin"] },
  { file: CORE + "organizations.py", fns: ["require_invitation_permission"], surfaces: ["members", "org"] },
  { file: CORE + "organizations.py", surfaces: ["org"] },
  { file: CORE + "org_roles.py", surfaces: ["org"] },
  { file: CORE + "member_import.py", surfaces: ["org"] },
  { file: CORE + "project_copies.py", fns: ["decide"], surfaces: ["org"] },
  { file: CORE + "project_copies.py", surfaces: [], why: "a copy is requested and withdrawn in Synwevo Docs, which words its own refusals" },
  { file: CORE + "notifications.py", fns: ["set_preferences"], surfaces: ["account"] },
  { file: CORE + "notifications.py", surfaces: [], why: "the shared notification centre sends only the filters and ids it lists" },
  { file: CORE + "push.py", surfaces: ["account"] },
  { file: CORE + "challenge.py", surfaces: ["signin"] },
  { file: CORE + "passwords.py", surfaces: ALL },
  { file: CORE + "avatar_image.py", surfaces: ["account", "org"] },
  { file: CORE + "sessions.py", fns: ["confirm_reauth"], surfaces: ["account", "org", "admin", "members", "api"] },
  { file: CORE + "sessions.py", surfaces: [], why: "an application identity has no password or passkey to sign in with" },
  { file: CORE + "api_platform.py", fns: ["enforce_principal"], surfaces: [], why: "checked on Synwevo Docs' business API, not in this shell" },
  { file: CORE + "api_platform.py", surfaces: ["api"] },
  // delegated platform roles: the one request check, grants and revocations (platform administration)
  { file: CORE + "platform_access.py", fns: ["_grant_accepted_offer"], surfaces: ["account", "admin"] },
  { file: CORE + "platform_access.py", surfaces: ["admin"] },
  { file: CORE + "platform_settings.py", fns: ["_entry"], surfaces: ALL },
  { file: CORE + "platform_settings.py", surfaces: ["admin"] },
  { file: CORE + "api_quotas.py", surfaces: ["api"] },
  { file: CORE + "idempotency.py", surfaces: ["api"] },
  { file: CORE + "oauth_consent.py", surfaces: ["account"] },
  { file: CORE + "auth.py", fns: ["rotate_key"], surfaces: ["api"] },
  { file: CORE + "auth.py", surfaces: [], why: "API keys presented to Synwevo Docs; the console only rotates and revokes them" },
  { file: CORE + "oidc.py", fns: ["rotate_secret"], surfaces: ["api"] },
  { file: CORE + "oidc.py", surfaces: [], why: "OIDC protocol: token, refresh and logout-token checks, and client registration from the CLI" },
  { file: CORE + "messaging.py", fns: ["review_report", "report_get"], surfaces: ["org"] },
  { file: CORE + "messaging.py", surfaces: [], why: "Synwevo Message's own refusals; its shell words them (CI-83)" },
  { file: CORE + "messaging_bridge.py", surfaces: [], why: "discussions of a Synwevo Docs object, in Synwevo Message's shell (CI-83)" },
  { file: CORE + "machine.py", surfaces: [], why: "credential checks on Synwevo Docs' business API" },
  { file: CORE + "http.py", surfaces: [], why: "the business gate and API routing: refusals of requests this shell never makes" },
  { file: CORE + "apps.py", surfaces: [], why: "`app` commands of the CLI" },
];

/** Refusals no person can cause from this shell, with the reason. The shell sends these fields
 *  from its own controls, or the case is decided before the request is made. */
const FIXED = "chosen from the page's fixed options";
const QUIET = "the notification settings offer only these choices and set quiet hours as a pair with a time zone";
const API_ONLY = {
  // account page
  "请求字段无效": "the account forms send only their own fields, as text",
  "请求字段过长": "each account form caps its fields at the server's limits (email 254, password 1024)",
  "尚未上传头像": "the avatar is fetched as an image only when the profile says there is one",
  "只能修改名称、语言和时区": "the profile form sends only name, language and time zone",
  "请输入有效的个人资料": "the profile form sends only text fields",
  "通知偏好包含不支持的字段": QUIET, "未知的通知类别或字段": QUIET, "邮件方式必须是 immediate、digest 或 off": QUIET,
  "消息不逐条发邮件：只能选 digest（未读摘要）或 off": QUIET, "时间必须是 HH:MM": QUIET,
  "免打扰需要同时给出开始与结束时间": QUIET, "免打扰需要时区": QUIET, "preview 必须是 full 或 minimal": QUIET,
  "订阅内容无效": "the browser's push service writes the subscription", "推送端点必须是 https 地址": "the browser's push service writes the subscription",
  "订阅密钥无效": "the browser's push service writes the subscription",
  "登录响应缺少凭据标识": "the browser's WebAuthn response always carries the credential id",
  // sign-in
  "请提供链接令牌与新口令": "the reset page submits only with the link's token and a password",
  "请求包含不支持的字段": "the sign-in page sends only its own fields",
  "未知的邮件令牌类型 {kind!r}": "the service names its mail token kinds itself",
  "邮件主题不能包含换行。": "subjects are written by the service from names that cannot hold a line break",
  // administration and members
  "请选择有效的注册策略": FIXED, "未知的角色 {role!r}": FIXED, "状态只能是 active 或 suspended": FIXED,
  "未知的账户状态 {status!r}": FIXED, "移交后自己的角色只能是 admin、editor 或 viewer": FIXED,
  "entitlement 只能是 active、revoked 或 none": FIXED, "未知的邀请状态 {state!r}": FIXED, "action 只能是 revoke 或 resend": FIXED,
  "请选择要认领这些数据的账户": "the claim button stays disabled until an account is chosen",
  "expected_members 必须是账户 ID 列表": "the undo dialog sends the member ids of the preview it shows",
  "请提供 {key}（非空列表）": "bulk actions are offered only with a selection", "请提供 changes（非空列表）": "bulk actions are offered only with a selection",
  "请提供 invitations（非空列表）": "bulk actions are offered only with a selection", "缺少 user_id": "each change names the member it was chosen for",
  "can_export 必须是 true 或 false": "the page sends a checkbox value", "grant 必须是 true 或 false": "the page sends a checkbox value",
  "expires_in_days 必须是数字": "the page sends a number from its own field",
  "邀请有效期必须在 1 到 30 天之间": "the invitation dialogs keep the validity between 1 and 30 days",
  "没有名为 {app_id} 的应用": "the page names only the applications the service lists", "未知的空间类型 {kind!r}": "the service chooses the workspace kind",
  // organizations
  "角色请求包含不支持的字段": "the role dialogs send only their own fields", "包含未知的权限": "permissions are chosen from the page's list",
  "role_ids 必须是角色 ID 列表": "roles are chosen from the page's list", "mapping 必须是对象": "the mapping form sends an object",
  "reviews 必须是按行的对象，最多 500 行": "reviews are written by the row review control, one per OCR row",
  "reviews 的某一项无效": "the row review control sends a known key, text fields and checked=true",
  "默认空间角色只能是 viewer、editor 或 admin": FIXED, "action 只能是 approve 或 reject": FIXED, "处理结果只能是 removed 或 dismissed": FIXED,
  "不能执行 {row['action']} 行": "only invite and update rows are executed; the plan decides which rows those are",
  // API console
  "签发时不可修改已批准范围": "the console sends only the reason when issuing", "不支持的凭据操作": FIXED,
  "访问密钥撤销后不可恢复": "the console offers re-enabling only for service accounts", "不支持额外的请求字段": "the console sends only its own request fields",
  "密钥重叠有效期超出允许范围": "the console rotates without overlap (grace_seconds 0)",
  "宽限期必须在 0 到 {MAX_ROTATION_GRACE_S // 60} 分钟之间": "the console rotates without overlap (grace_seconds 0)",
  "不支持的凭据类型": FIXED, "不支持的操作": FIXED,
  // every page
  "请选择支持的界面语言": FIXED, "需要 ids 与 read/archived": "the notification centre sends the ids it lists",
  "需要整数 up_to_seq": "the notification centre sends the sequence it read", "请选择有效的工作空间": "a switch names a workspace from the page's list",
};

/** Codes whose shared label (`API_ERROR_SOURCE`) is the right sentence for every server wording. */
const SHARED_CODES = {
  unauthorized: "every wording means the sign-in is missing or has ended; the shared sentence asks to sign in again, and the session watcher takes the person there",
  too_many_attempts: "a limit that says when it lifts: the shared sentence, and the page adds the wait from Retry-After where it shows one",
};

/** Calls whose message is not a literal: the refusals each can carry. `match` is tested against
 *  the call's text from `ServiceError(` to the end of its line. */
const password = (text, detail) => ({ status: 400, code: "weak_password", text, detail });
const PASSWORD_REFUSALS = [
  password("口令至少需要 8 个字符（当前 5 个）。长句子比复杂符号更好记也更安全。", { reason: "too_short", min: 8, length: 5 }),
  password("口令过长（上限 1024 个字符）。", { reason: "too_long", max: 1024 }),
  password("这个口令在常见泄露口令名单里，请换一个。", { reason: "common" }),
  password("这个口令重复度太高，请换一个。", { reason: "repetitive" }),
  password("口令不能包含本服务的名称，请换一个。", { reason: "service_name" }),
  password("口令不能包含你的邮箱或姓名，请换一个。", { reason: "personal" }),
];
const INDIRECT = [
  { files: [ID + "account_routes.py", ID + "auth_routes.py", ID + "registration.py"], match: /"weak_password", str\(e(xc)?\)/, refusals: PASSWORD_REFUSALS },
  { files: [ID + "auth_routes.py"], match: /\*_LOGIN_FAILED\)/, refusals: [{ status: 401, code: "bad_credentials", text: "邮箱或口令不正确" }] },
  { files: [ID + "auth_routes.py"], match: /"bad_code", "验证码不正确" \+/, refusals: [
    { status: 401, code: "bad_code", text: "验证码不正确，还可尝试 2 次", detail: { remaining: 2, exhausted: "sign_in" } },
    { status: 401, code: "bad_code", text: "验证码不正确，请重新登录", detail: { remaining: 0, exhausted: "sign_in" } }] },
  { files: [ID + "email_login.py"], match: /"bad_code", "验证码不正确" \+/, refusals: [
    { status: 401, code: "bad_code", text: "验证码不正确，还可尝试 2 次", detail: { remaining: 2, exhausted: "resend" } },
    { status: 401, code: "bad_code", text: "验证码不正确，请重新获取", detail: { remaining: 0, exhausted: "resend" } }] },
  { files: [ID + "invites.py"], match: /f"invite_\{state\}", "已接受、已拒绝或已撤销的邀请不能重发"/,
    refusals: ["accepted", "declined", "revoked"].map((s) => ({ status: 409, code: `invite_${s}`, text: "已接受、已拒绝或已撤销的邀请不能重发" })) },
  { files: [ID + "invites.py"], match: /f"invite_\{state\}", \{/, refusals: [
    { status: 409, code: "invite_accepted", text: "这个邀请已经被使用过了" }, { status: 409, code: "invite_revoked", text: "这个邀请已被撤销" },
    { status: 409, code: "invite_expired", text: "这个邀请已过期，请让邀请人重新发送" },
    { status: 409, code: "invite_declined", text: "这个邀请已经被拒绝，请联系邀请人重新发送" }] },
  { files: [ID + "member_import_routes.py"], match: /"workspace_frozen" if ws/, refusals: [
    { status: 409, code: "workspace_frozen", text: "该工作空间当前不能增加成员" }, { status: 409, code: "workspace_unavailable", text: "该工作空间当前不能增加成员" }] },
  { files: [ID + "members.py"], match: /"workspace_frozen" if frozen/, refusals: [
    { status: 409, code: "workspace_frozen", text: "该工作空间已被冻结，暂时不能增加成员或权限" }, { status: 409, code: "workspace_unavailable", text: "该工作空间当前不可用" }] },
  { files: [CLIENT + "session_routes.py"], match: /ServiceError\(403, failure,/, refusals: [
    { status: 403, code: "organization_unavailable", text: "该组织或工作空间当前不可用" }, { status: 403, code: "not_an_organization_member", text: "该组织或工作空间当前不可用" }] },
];

/** `detail` a numbered refusal (an f-string) carries, keyed by its source text, for wording it
 *  through its template. The server tests (test_refusal_details.py) hold the server to these. */
const MB = 1024 * 1024;
const provider = (reason) => ({ provider: "Test IdP", reason });
const SAMPLE_DETAIL = {
  "{e}（这个链接已失效，请重新申请一封邮件）": { field: "password", link_spent: true, reason: "too_short", min: 15, length: 3 },
  "无法连接 {p.label}，请稍后再试": provider("http_status"), "{p.label} 的发现文档不完整": provider("incomplete"),
  "{p.label} 的发现文档 issuer 与配置不一致": provider("issuer_mismatch"), "{p.label} 的端点不是 https": provider("not_https"),
  "无法取得 {p.label} 的签名密钥": provider("no_keys"), "这个 {p.label} 账户已关联到另一个 Synwevo 账户": { provider: "Test IdP" },
  "这个空间 24 小时内最多发出 {INVITES_PER_DAY} 个邀请，请稍后再试": { retry_after: 3600, limit: 50 },
  "成员文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB 上限": { limit: 5 * MB },
  "文件超过安全检查的大小上限（{pol.max_bytes // (1024 * 1024)} MB），无法接纳": { limit: 25 * MB },
  "文件「{filename}」被判定为恶意内容（{res.signature}），已拒绝": { signature: "Eicar-Test-Signature", filename: "members.csv" },
  "最多只能登记 {MAX_PASSKEYS} 个安全密钥": { limit: 10, kind: "passkey" },
  "一次最多处理 {BULK_MAX} 项": { field: "user_ids", limit: 100 },
  "最多 {MAX_SUBSCRIPTIONS_PER_USER} 个设备，请先移除不用的设备": { limit: 10 },
  "一个组织最多 {MAX_ROLES} 个自定义角色": { limit: 50 },
  "请写明理由（至少 {REASON_MIN} 个字），它会写入审计记录": { field: "reason", min: 8 },
  "限制值必须是 {lo} 至 {hi} 之间的整数，留空表示继承": { field: "active_jobs", min: 1, max: 1000 },
};

// ------------------------------------------------------------------------------------ scanning
const LITERAL = /ServiceError\(\s*(\d{3})\s*,\s*"([a-z_]+)"\s*,\s*(f?)"((?:[^"\\\n]|\\.)*)"/y;
const MORE = /\s*(f?)"((?:[^"\\\n]|\\.)*)"/y;

function owners(lines) {
  const out = []; const stack = [];
  lines.forEach((line, i) => {
    if (line.trim()) {
      const indent = line.length - line.trimStart().length;
      while (stack.length && indent <= stack.at(-1)[0] && !line.trimStart().startsWith(")")) stack.pop();
      const m = /^\s*(?:async\s+)?def\s+(\w+)\(/.exec(line);
      if (m) stack.push([indent, m[1]]);
    }
    out[i] = stack.map((s) => s[1]).join(".");
  });
  return out;
}

function scan(file) {
  const src = readFileSync(join(ROOT, file), "utf8");
  const lines = src.split("\n");
  const owner = owners(lines);
  const literal = []; const indirect = [];
  for (const m of src.matchAll(/ServiceError\(/g)) {
    if (/class ServiceError|except ServiceError|isinstance\([^)]*ServiceError/.test(src.slice(src.lastIndexOf("\n", m.index) + 1, m.index + 13))) continue;
    const line = src.slice(0, m.index).split("\n").length - 1;
    LITERAL.lastIndex = m.index;
    const hit = LITERAL.exec(src);
    if (!hit) {
      indirect.push({ file, fn: owner[line], call: src.slice(m.index, src.indexOf("\n", m.index)) });
      continue;
    }
    let text = hit[4], fstring = hit[3] === "f", at = LITERAL.lastIndex;
    for (;;) {
      MORE.lastIndex = at;
      const more = MORE.exec(src);
      if (!more) break;
      text += more[2]; fstring ||= more[1] === "f"; at = MORE.lastIndex;
    }
    // "…" + (…): the sentence is built at run time, so it is read like a non-literal one
    if (/^\s*\+/.test(src.slice(at, at + 20))) {
      indirect.push({ file, fn: owner[line], call: src.slice(m.index, src.indexOf("\n", m.index)) });
      continue;
    }
    literal.push({ file, fn: owner[line], status: Number(hit[1]), code: hit[2], text, fstring });
  }
  return { literal, indirect };
}

const matchesFn = (owner, fn) => owner === fn || owner.startsWith(fn + ".") || owner.endsWith("." + fn) || owner.includes("." + fn + ".");
function placeOf(r) {
  const entry = SERVER.find((e) => e.file === r.file && (!e.fns || e.fns.some((fn) => matchesFn(r.fn, fn))));
  assert.ok(entry, `${r.file}: no SERVER entry for ${r.fn}`);
  return entry;
}

function refusals() {
  const files = [...new Set(SERVER.map((e) => e.file))];
  const out = []; const unplaced = []; const usedIndirect = new Set();
  for (const file of files) {
    const { literal, indirect } = scan(file);
    out.push(...literal);
    for (const call of indirect) {
      if (!placeOf(call).surfaces.length) continue;
      const i = INDIRECT.findIndex((x) => x.files.includes(call.file) && x.match.test(call.call));
      if (i < 0) { unplaced.push(`${call.file} (${call.fn}): ${call.call.trim()}`); continue; }
      usedIndirect.add(i);
      for (const r of INDIRECT[i].refusals) out.push({ file, fn: call.fn, fstring: false, ...r });
    }
  }
  return { all: out, unplaced, unusedIndirect: INDIRECT.filter((_, i) => !usedIndirect.has(i)) };
}

// ------------------------------------------------------------------------------------ pages
// Explicit browser-environment fixture, as in flow.validation.mjs: no account, no API.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };

const pages = {};
before(async () => {
  for (const [name, surface] of Object.entries(SURFACES)) {
    const server = await createServer({ root: WEB, configFile: false, logLevel: "error", appType: "custom",
      server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
    const load = (id) => server.ssrLoadModule(id);
    const ui = await load("@synwevo/ui");
    const client = await load("@synwevo/auth-client");
    const shared = await load("@synwevo/auth-client/api-messages");
    for (const id of surface.modules) await load(id);
    const [helperId, make] = surface.helper;
    const login = await load("/src/pages/Login.tsx");
    pages[name] = { server, ui, client, shared, word: make(await load(helperId), ui), external: login.EXTERNAL_ERRORS };
  }
});
after(async () => { for (const p of Object.values(pages)) await p.server.close(); });

const HAN = /[㐀-鿿]/;
function present(surface, r) {
  const { ui, client, word } = pages[surface];
  const detail = r.detail ?? (r.fstring ? SAMPLE_DETAIL[r.text] : undefined);
  ui.setLocale("en");
  try { return word(new client.ApiError(r.status, r.code, r.text, detail)); } finally { ui.setLocale("zh-Hans"); }
}
function problem(surface, r) {
  if (surface === "ext") return Object.hasOwn(pages.signin.external, r.code) ? null : "no EXTERNAL_ERRORS entry in Login.tsx";
  const { ui, shared } = pages[surface];
  const en = present(surface, r);
  if (HAN.test(en)) return `untranslated: ${en}`;
  const generic = ["操作失败，请重试。", "操作未完成，请刷新后重试。", "操作失败，请检查输入或重试。"].map((s) => ui.translate("en", s));
  if (generic.includes(en) || en.includes(ui.translate("en", "请求未完成（{code}）。", { code: r.code }))) return `generic: ${en}`;
  const label = shared.API_ERROR_SOURCE[r.code];
  if (label && en === ui.translate("en", label) && !Object.hasOwn(SHARED_CODES, r.code)) return `only the shared label: ${en}`;
  return null;
}

// ------------------------------------------------------------------------------------ tests
test("the scan finds the server's refusals (guards the pattern itself)", () => {
  const { all } = refusals();
  assert.ok(all.length > 400, `only ${all.length} refusals found`);
  assert.ok(all.some((r) => r.code === "last_factor" && r.file.endsWith("passkeys.py")));
  assert.ok(all.some((r) => r.code === "uv_required" && r.text.includes("动态验证码登录")), "a sentence split over two literals is read whole");
  assert.ok(all.some((r) => r.code === "invite_rate_limited" && r.fstring));
  // each page has a catalogue of its own: a sentence only the organization pages load is not the console's
  assert.equal(pages.org.ui.hasMessage("找不到该复制申请"), true);
  assert.equal(pages.api.ui.hasMessage("找不到该复制申请"), false);
  assert.equal(pages.signin.ui.hasMessage("找不到该复制申请"), false);
});

test("every module the account service reaches is placed, and every call is read", () => {
  const listed = new Set(SERVER.map((e) => e.file));
  const seen = new Set(); const queue = [];
  const enqueue = (mod) => { const file = CORE + mod + ".py"; if (existsSync(join(ROOT, file)) && !seen.has(file)) { seen.add(file); queue.push(file); } };
  for (const f of readdirSync(join(ROOT, ID)).filter((f) => f.endsWith(".py"))) queue.push(ID + f);
  queue.push(CLIENT + "session_routes.py");
  const missing = [];
  while (queue.length) {
    const file = queue.shift(); const src = readFileSync(join(ROOT, file), "utf8");
    if (/ServiceError\(/.test(src.replace(/class ServiceError\(|except ServiceError|isinstance\([^)]*ServiceError/g, "")) && !listed.has(file) && !file.endsWith("errors.py") && !file.endsWith("cli.py")) missing.push(file);
    for (const m of src.matchAll(/synwevo_core\.(\w+)/g)) enqueue(m[1]);
    for (const m of src.matchAll(/from synwevo_core import ([\w, ]+)/g)) m[1].split(",").forEach((x) => enqueue(x.trim()));
    if (file.startsWith(CORE)) for (const m of src.matchAll(/from \.(\w+) import/g)) enqueue(m[1]);
  }
  assert.deepEqual(missing, [], "a module with refusals the account service reaches is not in SERVER");
  const { unplaced, unusedIndirect } = refusals();
  assert.deepEqual(unplaced, [], "a ServiceError whose message is not a literal needs an INDIRECT entry");
  assert.deepEqual(unusedIndirect.map((x) => `${x.files}: ${x.match}`), []);
  for (const e of SERVER) if (!e.surfaces.length) assert.ok(e.why, `${e.file} ${e.fns ?? ""}: say why no page shows it`);
});

test("every refusal a person can meet reads as a sentence in their language on that page", () => {
  const failures = [];
  for (const r of refusals().all) {
    const place = placeOf(r);
    if (!place.surfaces.length || Object.hasOwn(API_ONLY, r.text)) continue;
    for (const surface of place.surfaces) {
      const why = problem(surface, r);
      if (why) failures.push(`${surface} ← ${r.file.split("/").pop()} ${r.fn} ${r.code}: ${r.text} — ${why}`);
    }
  }
  assert.deepEqual(failures, []);
});

test("the API-only list names nothing the server no longer says, and every entry has a reason", () => {
  const said = new Set(refusals().all.map((r) => r.text));
  assert.deepEqual(Object.keys(API_ONLY).filter((s) => !said.has(s)), []);
  assert.deepEqual(Object.keys(SAMPLE_DETAIL).filter((s) => !said.has(s)), [], "a sample for a sentence the server no longer says");
  for (const [text, why] of Object.entries(API_ONLY)) assert.ok(why.length > 10, `${text}: give a reason`);
  for (const [code, why] of Object.entries(SHARED_CODES)) assert.ok(why.length > 10, `${code}: give a reason`);
});

/** One case: the page, the refusal, and what a reader sees in each named locale. */
function reads(surface, status, code, message, detail, expected) {
  const { ui, client, word } = pages[surface];
  try {
    for (const [locale, text] of Object.entries(expected)) {
      ui.setLocale(locale);
      assert.equal(word(new client.ApiError(status, code, message, detail)), text, `${surface} ${code} ${locale}`);
    }
  } finally { ui.setLocale("zh-Hans"); }
}

test("numbered refusals read in every language, from the detail the server sends", () => {
  reads("account", 400, "weak_password", "口令至少需要 8 个字符（当前 5 个）。长句子比复杂符号更好记也更安全。", { field: "new_password", reason: "too_short", min: 8, length: 5 },
    { en: "Your password needs at least 8 characters (it has 5). A long sentence is easier to remember and safer than complex symbols.",
      "zh-Hant": "密碼至少需要 8 個字元（目前 5 個）。長句子比複雜符號更好記也更安全。" });
  // a reset spends its link first: the rule and the spent link, both in the reader's language
  reads("signin", 400, "weak_password", "这个口令重复度太高，请换一个。（这个链接已失效，请重新申请一封邮件）", { field: "password", link_spent: true, reason: "repetitive" },
    { en: "This password is too repetitive. Choose another. (This link can no longer be used; request a new email.)" });
  reads("signin", 401, "bad_code", "验证码不正确，还可尝试 2 次", { remaining: 2, exhausted: "sign_in" }, { en: "Incorrect code. 2 attempts remaining." });
  reads("signin", 401, "bad_code", "验证码不正确，请重新获取", { remaining: 0, exhausted: "resend" },
    { en: "Incorrect code. Request a new one.", "zh-Hant": "驗證碼不正確，請重新取得。" });
  reads("members", 429, "invite_rate_limited", "这个空间 24 小时内最多发出 50 个邀请，请稍后再试", { limit: 50 },
    { en: "A workspace can send at most 50 invitations in 24 hours. Try again later." });
  reads("account", 409, "too_many", "最多只能登记 10 个安全密钥", { limit: 10, kind: "passkey" }, { en: "You can register at most 10 security keys." });
  reads("members", 400, "too_many", "一次最多处理 100 项", { field: "user_ids", limit: 100 }, { "zh-Hant": "一次最多處理 100 項" });
  reads("account", 503, "idp_unreachable", "Test IdP 的发现文档 issuer 与配置不一致", { provider: "Test IdP", reason: "issuer_mismatch" },
    { en: "The issuer in the discovery document from Test IdP does not match the configuration." });
  reads("org", 413, "file_too_large", "成员文件超过 5 MB 上限", { limit: 5 * MB }, { en: "The member file is over the 5 MB limit." });
  reads("api", 409, "client_disabled", "客户端 svc_1 已停用，停用的客户端没有可轮换的密钥", {}, { en: "This service client is disabled, so it has no secret to rotate." });
});

test("the server's catalogued sentence is preferred to a page's label for its code, and a template without its number falls back", () => {
  // the account page's label for `last_factor` spoke of passkeys only; the server's sentence is exact
  reads("account", 409, "last_factor", "这是你唯一的登录方式，删除后将无法登录。请先设置口令或再登记一个安全密钥。", {},
    { en: "This is your only way to sign in; without it you could not sign in. Set a password or register another security key first." });
  // a 401 on re-authentication is not always a wrong password
  reads("admin", 401, "session_ended", "口令错误次数过多，这个登录已结束，请重新登录", {}, { en: pages.admin.ui.translate("en", "口令错误次数过多，这个登录已结束，请重新登录") });
  reads("members", 429, "invite_rate_limited", "这个空间 24 小时内最多发出 50 个邀请，请稍后再试", { retry_after: 60 }, { en: "Too many invitations. Try again later." });
  // something in front of Synwevo is named as such, whatever its status
  const { ui, client, word } = pages.account;
  ui.setLocale("en");
  try {
    const gate = new client.ApiError(403, "gateway_blocked", "x", { gateway: "前置网关" }, true);
    assert.equal(word(gate), "Gateway blocked the request (HTTP 403); it did not reach Synwevo.");
  } finally { ui.setLocale("zh-Hans"); }
});

test("a refused item inside a successful batch or import reads like the same refusal on its own", async () => {
  const { ui } = pages.members;
  const labels = await pages.members.server.ssrLoadModule("/src/governance/labels.ts");
  const flow = await pages.org.server.ssrLoadModule("/src/pages/org/AuthFlow.tsx");
  try {
    ui.setLocale("en"); pages.org.ui.setLocale("en");
    assert.equal(labels.resultText("too_many", "一次最多处理 100 项", { limit: 100 }), "At most 100 items can be processed at once.");
    assert.equal(labels.resultText("role_too_high", "不能授予比自己更高的角色"), "You cannot grant a role above your own.");
    assert.equal(flow.resultText({ error: "already_member", message: "该邮箱已经是这个空间的成员" }), "This email address is already a member of this workspace.");
    assert.equal(flow.resultText({ error: "internal_error", message: "KeyError" }), "An internal server error occurred. Please try again later.");
  } finally { ui.setLocale("zh-Hans"); pages.org.ui.setLocale("zh-Hans"); }
});
