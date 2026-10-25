/** MCP on the administration page (docs/platform/mcp.md §5) — the tab is offered by
 *  `platform.config.view` (governance/access.ts, admin/navigation.ts); the policy editor shows every
 *  field of the current version and lets only `platform.config.manage` preview and publish; the
 *  overview shows each connection masked (name, host, owner, state, counts — never a credential, an
 *  argument or a result) with its platform stop, and offers the stop and its lifting only to that
 *  permission, through the server's own routes with a written reason; the inbound card names the
 *  workplace endpoint as a path and derives only this origin's own metadata address; the history
 *  lists each version with its reason and actor (governance/McpTab.tsx).
 *
 *  Rendered the way the browser renders it (vite's module graph, React's server renderer), with the
 *  administration data loader replaced by fixture data: no API, no user data. The roles are read
 *  from platform_access.py itself, the two paths from the server modules that serve them, as in
 *  admin-apps.test.mjs. Run from the package: `npm test`. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { testServer } from "./_vite.mjs";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const ROOT = fileURLToPath(new URL("../../../../", import.meta.url));
const require = createRequire(WEB + "package.json");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

// ------------------------------------------------------------------------------------ roles
const PY = readFileSync(join(ROOT, "packages/core/synwevo_core/platform_access.py"), "utf8");
const block = (start, end) => { const i = PY.indexOf(start); assert.ok(i >= 0, start); return PY.slice(i, PY.indexOf(end, i)); };
const PERMISSIONS = [...block("PERMISSIONS: dict[str, str] = {", "\n}\n").matchAll(/^\s+"([a-z_.]+)":/gm)].map((m) => m[1]);
const ROLES = { super_admin: new Set(PERMISSIONS) };
for (const m of block("ROLES: dict[str, frozenset[str]] = {", "\n}\n").matchAll(/"(\w+)": frozenset\(\{([^}]*)\}\)/g)) {
  ROLES[m[1]] = new Set([...m[2].matchAll(/"([a-z_.]+)"/g)].map((x) => x[1]));
}

// Explicit browser-environment fixture, as in admin-apps.test.mjs. The inbound card derives this
// origin's own metadata address from it, and nothing else.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/admin?tab=mcp"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

/** Fixture loader in place of `./data` for McpTab.tsx: answers each path from `globalThis.__fixtures`. */
const FIXTURE_LOADER = `
export function useAdminData(path) {
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  return { data: f.data ?? null, err: f.err ?? null, loading: !!f.loading, reload() {} };
}
export function useAdminPage() { throw new Error("the MCP page reads whole lists, never pages"); }`;

let server, ui, client, access, nav, tab, messages, labels, admin;
before(async () => {
  server = await testServer({
    root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{
      name: "mcp-fixture", enforce: "pre",
      resolveId(id, importer) { if (id === "./data" && importer?.endsWith("/src/governance/McpTab.tsx")) return "\0mcp-fixture-data"; },
      load(id) { if (id === "\0mcp-fixture-data") return FIXTURE_LOADER; },
    }],
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  client = await server.ssrLoadModule("@synwevo/auth-client");
  access = await server.ssrLoadModule("/src/governance/access.ts");
  nav = await server.ssrLoadModule("/src/admin/navigation.ts");
  labels = await server.ssrLoadModule("/src/governance/labels.ts");
  messages = await server.ssrLoadModule("/src/governance/mcp.messages.ts");
  tab = await server.ssrLoadModule("/src/governance/McpTab.tsx");
  // the page's whole catalogue, as the browser has it on the administration page
  admin = await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

// ------------------------------------------------------------------------------------ fixtures
// FIXTURE: GET /api/admin/mcp/policy, /policy/history, /overview and /connections/{id}/calls as
// admin_mcp.py answers them — masked rows, counts and codes only. Synthetic ids, names and hosts
// (the reserved .invalid domain).
const T = 1_790_000_000;
const SPEC = { outbound_enabled: true, inbound_enabled: false, allow_http: false,
  allow_private_networks: ["10.20.0.0/16"], allowed_hosts: null, blocked_hosts: ["blocked.fixture.invalid"],
  max_connections_per_user: 10, max_connections_per_workspace: 20, max_concurrent_calls_per_connection: 4, max_concurrent_calls_per_user: 8,
  call_timeout_s: 60, discover_timeout_s: 30, max_result_chars: 24000, max_resource_bytes: 1000000, breaker_failures: 5, breaker_open_s: 120,
  client_registration: "preregistered_only", cimd_allowed_domains: [] };
const DEFAULT = { ...SPEC, outbound_enabled: false, allow_private_networks: [], blocked_hosts: [] };
const CIMD = { ...SPEC, inbound_enabled: true, client_registration: "cimd_and_preregistered", cimd_allowed_domains: [".clients.fixture.invalid"] };
// the server's JSON schema carries the bounds; the page's own are the same numbers (McpPolicySpec)
const SCHEMA = { properties: Object.fromEntries([["max_connections_per_user", 0, 1000], ["max_connections_per_workspace", 0, 1000],
  ["max_concurrent_calls_per_connection", 1, 64], ["max_concurrent_calls_per_user", 1, 256], ["call_timeout_s", 5, 600], ["discover_timeout_s", 5, 300],
  ["max_result_chars", 1000, 200000], ["max_resource_bytes", 10000, 50000000], ["breaker_failures", 1, 100], ["breaker_open_s", 10, 3600]]
  .map(([key, minimum, maximum]) => [key, { minimum, maximum }])) };
const STATE = { revision: 3, spec: SPEC, actor_id: "u_fx_admin", reason: "Fixture reason for revision three", created_at: T, source: "published" };
const BUILTIN = { revision: 0, spec: DEFAULT, actor_id: null, reason: null, created_at: null, source: "builtin_default" };
const HISTORY = [
  { revision: 3, spec: SPEC, actor_id: "u_fx_admin", reason: "Fixture reason for revision three", operation_id: "ao_fx_3", created_at: T },
  { revision: 2, spec: CIMD, actor_id: "u_fx_admin", reason: null, operation_id: "ao_fx_2", created_at: T - 86400 },
  { revision: 1, spec: null, actor_id: "u_fx_other", reason: "Fixture reason for revision one", operation_id: null, created_at: T - 2 * 86400 },
];
const OK = { id: "mcpc_fx_ok", name: "Fixture Weather", owner_kind: "workspace", owner_name: "Fixture Workspace", host: "weather.fixture.invalid",
  auth_kind: "oauth", status: "active", platform_disabled: false, platform_disabled_at: null,
  health: "ok", last_ok_at: T, last_error_code: null, failure_streak: 0, circuit_open_until: null,
  protocol_version: "2026-07-28", tools: 6, granted: 4, agents: 3, calls_7d: 42, failed_7d: 1, last_call_at: T };
const FAILED = { ...OK, id: "mcpc_fx_failed", name: "Fixture Tickets", owner_kind: "user", owner_name: null, host: "tickets.fixture.invalid",
  auth_kind: "bearer", health: "failed", last_ok_at: null, last_error_code: "mcp_timeout", failure_streak: 5, circuit_open_until: T + 120,
  tools: 2, granted: 0, agents: 1, calls_7d: 7, failed_7d: 7, last_call_at: T - 60 };
// switched off by its owner: the owner's own choice, which the platform may still turn into a stop
const OFF = { ...OK, id: "mcpc_fx_off", name: "Fixture Archive", host: "archive.fixture.invalid", status: "disabled", health: "auth_required",
  auth_kind: "none", protocol_version: null, agents: 0, calls_7d: 0, failed_7d: 0, last_call_at: null };
// stopped by the platform (admin_mcp.disable): disabled, credential forgotten, until the platform lifts it
const STOPPED = { ...OK, id: "mcpc_fx_stopped", name: "Fixture Ledger", host: "ledger.fixture.invalid", auth_kind: "bearer", status: "disabled",
  platform_disabled: true, platform_disabled_at: T - 3600, health: "auth_required", agents: 2, calls_7d: 3, failed_7d: 0, last_call_at: T - 7200 };
const TOTALS = { connections: 4, failed: 1, auth_required: 2, calls_24h: 19, failed_calls_24h: 2, inbound_requests_24h: 11 };
const CALLS = [
  { id: "mcpcall_fx_1", origin: "agent", kind: "tools/call", name: "get_forecast", status: "succeeded", error_code: null, started_at: T, finished_at: T + 1, duration_ms: 812, request_id: "req_fx_1" },
  { id: "mcpcall_fx_2", origin: "test", kind: "discover", name: null, status: "timeout", error_code: "mcp_timeout", started_at: T - 60, finished_at: T - 30, duration_ms: 30000, request_id: null },
];
function fixtures(over = {}) {
  globalThis.__fixtures = {
    "/api/admin/mcp/policy": { data: { state: STATE, default: DEFAULT, schema: SCHEMA } },
    "/api/admin/mcp/policy/history": { data: { items: HISTORY } },
    "/api/admin/mcp/overview": { data: { items: [FAILED, OK, OFF, STOPPED], totals: TOTALS, policy: STATE } },
    [`/api/admin/mcp/connections/${OK.id}/calls`]: { data: { items: CALLS } },
    [`/api/admin/mcp/connections/${OFF.id}/calls`]: { data: { items: [] } },
    ...over,
  };
}

const VIEW = new Set(["platform.config.view"]);
const MANAGE = new Set(["platform.config.view", "platform.config.manage"]);
const LOCALES = ["zh-Hans", "zh-Hant", "en"];
const HAN = /[㐀-鿿]/;
const FIXTURE_TEXT = /Fixture (?:Weather|Tickets|Archive|Ledger|Workspace|reason for revision (?:one|three))|mcpc_fx_\w+|mcpcall_fx_\d|u_fx_\w+|ao_fx_\d|req_fx_\d|get_forecast|mcp_timeout|[a-z.]+\.fixture\.invalid|10\.20\.0\.0\/16|2026-07-28/g;

function inLocale(locale, run) { ui.setLocale(locale); try { return run(); } finally { ui.setLocale("zh-Hans"); } }
const render = (locale, element) => inLocale(locale, () => renderToStaticMarkup(element).replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const tx = (locale, source, params) => ui.translate(locale, source, params);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const row = (html, id) => { const part = html.split("<tr").find((p) => p.includes(id)); assert.ok(part, id); return part.split("</tr>")[0]; };
const article = (html, needle) => { const part = html.split("<article").find((p) => p.includes(needle)); assert.ok(part, needle); return part.split("</article>")[0]; };
/** The `<label>` whose text holds `needle`, with its control. */
const field = (html, needle) => { const part = html.split("<label").find((p) => p.includes(needle)); assert.ok(part, needle); return part.split("</label>")[0]; };
function mcpTab(locale, held, over) { fixtures(over); return render(locale, React.createElement(tab.McpTab, { onDenied() {}, held })); }
const calls = (locale, id) => { fixtures(); return render(locale, React.createElement(tab.ConnectionCalls, { id, onDenied() {} })); };
const section = (html, headingId) => { const i = html.indexOf(`aria-labelledby="${headingId}"`); assert.ok(i >= 0, headingId); return html.slice(i, html.indexOf("</section>", i)); };

// ------------------------------------------------------------------------------------ tests
test("the MCP page is offered by platform.config.view, in the configuration group: the auditor sees it, support, finance and the API administrator do not", () => {
  assert.equal(access.TAB_PERMISSION.mcp, "platform.config.view");
  assert.ok(PERMISSIONS.includes("platform.config.view") && PERMISSIONS.includes("platform.config.manage"));
  const ids = (held) => nav.adminGroups(held).flatMap((group) => group.items.map((item) => item.id));
  const offered = Object.fromEntries(Object.entries(ROLES).map(([role, held]) => [role, ids(held).includes("mcp")]));
  assert.deepEqual(offered, { super_admin: true, access_admin: true, support: false, auditor: true, api_platform_admin: false, finance: false });
  for (const [role, held] of Object.entries(ROLES)) {
    assert.equal(access.visibleTabs(held).includes("mcp"), held.has("platform.config.view"), role);
    assert.equal(nav.resolveAdminPage("/admin", "mcp", held) === "mcp", held.has("platform.config.view"), role);
  }
  assert.equal(nav.resolveAdminPage("/admin", "mcp", new Set(["platform.config.manage"])), null, "managing is not reading");
  const group = nav.adminGroups(ROLES.auditor).find((g) => g.items.some((item) => item.id === "mcp"));
  assert.equal(group.label, "配置与治理");
  assert.equal(nav.adminHref("mcp", "?lang=en&tab=audit&q=fixture"), "/admin?lang=en&tab=mcp");
  const item = group.items.find((i) => i.id === "mcp");
  assert.ok(ui.hasMessage(item.label));
  for (const locale of LOCALES) assert.ok(tx(locale, item.label) && !HAN.test(tx("en", item.label)), locale);
  // the real Admin entry mounts the tab for the view permission, and refuses in words without it
  const shell = admin.default();
  const page = (permissions) => { fixtures(); return render("zh-Hans", shell.props.children({ held: new Set(permissions), active: "mcp" })); };
  assert.match(page(["platform.config.view"]), /MCP 策略历史/);
  assert.match(page(["platform.config.view"]), /你的平台角色可以查看配置，不能发布更改。/);
  assert.doesNotMatch(page(["platform.config.view", "platform.config.manage"]), /你的平台角色可以查看配置，不能发布更改。/);
  for (const permissions of [[], ["platform.config.manage"], ["api_platform.manage"], ["platform.users.view"]]) {
    const denied = page(permissions);
    assert.match(denied, /你没有查看 MCP 策略与连接的权限。/);
    assert.doesNotMatch(denied, /MCP 策略历史|MCP 连接概览|\/api\/suite\/v1\/mcp/);
  }
});

test("the policy editor shows every field of the current version; a reader gets the hint and disabled controls, a publisher the preview", () => {
  for (const locale of LOCALES) {
    const html = mcpTab(locale, VIEW);
    const policy = section(html, "mcp-policy-heading");
    assert.ok(policy.includes(tx(locale, "编辑基于版本 {version} · 作用域：全平台", { version: 3 })), `${locale}: the base version`);
    assert.ok(policy.includes(tx(locale, "你的平台角色可以查看配置，不能发布更改。")), `${locale}: the hint`);
    assert.match(policy, /<fieldset disabled=""/, `${locale}: the controls are disabled for a reader`);
    assert.ok(!policy.includes(tx(locale, "尚未发布过 MCP 策略：当前是内置默认值，两个方向都关闭——Agent 不能连接外部 MCP 服务器，外部客户端也不能调用 Synwevo。")), `${locale}: a published version is not the built-in default`);
    // the three switches, with their states
    for (const [key, on] of [["outbound_enabled", true], ["inbound_enabled", false], ["allow_http", false]]) {
      const control = field(policy, tx(locale, tab.FIELD_LABELS[key]));
      assert.equal(control.includes('checked=""'), on, `${locale} ${key}: ${control}`);
    }
    // destinations: any public host (allowed_hosts null) hides the list; the blocked and private lists hold their entries
    assert.ok(field(policy, tx(locale, "允许任何公网主机")).includes('checked=""'), `${locale}: any public host`);
    assert.ok(!policy.includes(tx(locale, tab.FIELD_LABELS.allowed_hosts)), `${locale}: no allow-list while any public host is allowed`);
    assert.ok(field(policy, tx(locale, tab.FIELD_LABELS.blocked_hosts)).includes(">blocked.fixture.invalid</textarea>"), `${locale}: blocked hosts`);
    assert.ok(field(policy, tx(locale, tab.FIELD_LABELS.allow_private_networks)).includes(">10.20.0.0/16</textarea>"), `${locale}: private networks`);
    // every numeric limit with its value, its bounds and its unit
    for (const f of tab.NUMERIC_FIELDS) {
      const control = field(policy, tx(locale, f.label));
      assert.ok(control.includes(`value="${SPEC[f.key]}"`), `${locale} ${f.key}: the value\n${control}`);
      const { minimum, maximum } = SCHEMA.properties[f.key];
      assert.ok(control.includes(`min="${minimum}"`) && control.includes(`max="${maximum}"`), `${locale} ${f.key}: the bounds`);
      const bounds = f.unit ? tx(locale, "允许 {min}–{max} {unit}。", { min: minimum, max: maximum, unit: tx(locale, f.unit) }) : tx(locale, "允许 {min}–{max}。", { min: minimum, max: maximum });
      assert.ok(text(control).includes(bounds), `${locale} ${f.key}: ${bounds}\n${text(control)}`);
    }
    // registration: the current choice selected; the document domains only when documents are accepted
    assert.match(field(policy, tx(locale, tab.FIELD_LABELS.client_registration)), /<option[^>]*selected=""[^>]*value="preregistered_only"|<option[^>]*value="preregistered_only"[^>]*selected=""/, `${locale}: the registration`);
    for (const key of Object.keys(tab.REGISTRATION_LABELS)) assert.ok(policy.includes(`>${tx(locale, tab.REGISTRATION_LABELS[key])}</option>`), `${locale}: option ${key}`);
    assert.ok(!policy.includes(tx(locale, tab.FIELD_LABELS.cimd_allowed_domains)), `${locale}: no document domains for pre-registered only`);
    assert.match(policy, new RegExp(`<button type="button" class="btn primary" disabled=""[^>]*>${esc(tx(locale, "预览策略差异"))}</button>`), `${locale}: the preview is offered but disabled for a reader`);
    // a publisher: no hint, live controls, the preview disabled only until the draft differs
    const manage = section(mcpTab(locale, MANAGE), "mcp-policy-heading");
    assert.ok(!manage.includes(tx(locale, "你的平台角色可以查看配置，不能发布更改。")), `${locale}: no hint for a publisher`);
    assert.doesNotMatch(manage, /<fieldset disabled=""/, locale);
    assert.ok(manage.includes(tx(locale, "策略草稿与当前版本相同。")), `${locale}: the unchanged draft is said`);
    // the built-in default (revision 0) is named as such; a policy accepting documents shows their domains
    const builtin = section(mcpTab(locale, MANAGE, { "/api/admin/mcp/policy": { data: { state: BUILTIN, default: DEFAULT, schema: SCHEMA } } }), "mcp-policy-heading");
    assert.ok(builtin.includes(tx(locale, "尚未发布过 MCP 策略：当前是内置默认值，两个方向都关闭——Agent 不能连接外部 MCP 服务器，外部客户端也不能调用 Synwevo。")), locale);
    assert.ok(builtin.includes(tx(locale, "编辑基于版本 {version} · 作用域：全平台", { version: 0 })), locale);
    const cimd = section(mcpTab(locale, MANAGE, { "/api/admin/mcp/policy": { data: { state: { ...STATE, spec: CIMD }, default: DEFAULT, schema: SCHEMA } } }), "mcp-policy-heading");
    assert.ok(field(cimd, tx(locale, tab.FIELD_LABELS.cimd_allowed_domains)).includes(">.clients.fixture.invalid</textarea>"), `${locale}: the document domains`);
    assert.ok(cimd.includes(tx(locale, "自描述登记：客户端的 client_id 是一个 https 地址，那里的文档说明它是谁、回调到哪里。本服务读取并检查它，但不核实运营方。")), locale);
  }
});

test("the draft is parsed the way the server validates it, and refused in words that name the field", () => {
  const draft = tab.draftFrom(SPEC);
  assert.deepEqual(tab.policyFromDraft(draft, SCHEMA.properties), { spec: SPEC, problems: [] });
  assert.deepEqual(tab.policyFromDraft(tab.draftFrom(CIMD)).spec, CIMD, "the page's own bounds agree with the server's");
  assert.ok(tab.sameSpec(SPEC, tab.policyFromDraft(draft).spec) && !tab.sameSpec(SPEC, CIMD));
  // host entries: one per line or comma, trimmed, duplicates collapsed; whitespace inside or over 253 characters refused
  assert.deepEqual(tab.parseHostList("a.fixture.invalid, b.fixture.invalid\n a.fixture.invalid \n\nbad host\n" + "x".repeat(254)),
    { items: ["a.fixture.invalid", "b.fixture.invalid"], bad: ["bad host", "x".repeat(254)] });
  // an allow-list switched on and left empty allows nothing (the server's semantics), never "any host"
  assert.deepEqual(tab.policyFromDraft({ ...draft, any_public_host: false, allowed_hosts: "" }).spec.allowed_hosts, []);
  assert.deepEqual(tab.policyFromDraft({ ...draft, any_public_host: false, allowed_hosts: ".fixture.invalid" }).spec.allowed_hosts, [".fixture.invalid"]);
  // numbers: integers within the server's bounds, else a problem naming the field and the bounds
  const low = tab.policyFromDraft({ ...draft, numbers: { ...draft.numbers, call_timeout_s: "4", max_result_chars: "1.5" } }, SCHEMA.properties);
  assert.equal(low.spec, null);
  assert.deepEqual(low.problems.map((p) => [p.field, p.params]), [["call_timeout_s", { min: 5, max: 600 }], ["max_result_chars", { min: 1000, max: 200000 }]]);
  const hosts = tab.policyFromDraft({ ...draft, blocked_hosts: "ok.fixture.invalid\nbad host" });
  assert.deepEqual(hosts.problems.map((p) => [p.field, p.params]), [["blocked_hosts", { items: "bad host" }]]);
  for (const locale of LOCALES) {
    for (const p of [...low.problems, ...hosts.problems]) {
      const worded = tx(locale, p.text, { field: tx(locale, tab.FIELD_LABELS[p.field]), ...p.params });
      assert.ok(worded.includes(tx(locale, tab.FIELD_LABELS[p.field])), `${locale}: ${worded}`);
      if (locale === "en") assert.ok(!HAN.test(worded) && !/\{\w+\}/.test(worded), worded);
    }
    // values in words, for the preview's before → after and the history
    const t = (s, params) => tx(locale, s, params);
    assert.equal(tab.valueText("outbound_enabled", true, t), tx(locale, "开"));
    assert.equal(tab.valueText("allow_http", false, t), tx(locale, "关"));
    assert.equal(tab.valueText("allowed_hosts", null, t), tx(locale, "任何公网主机"));
    assert.equal(tab.valueText("allowed_hosts", [], t), tx(locale, "（空）"));
    assert.equal(tab.valueText("blocked_hosts", ["a.fixture.invalid", "b.fixture.invalid"], t), "a.fixture.invalid, b.fixture.invalid");
    assert.equal(tab.valueText("client_registration", "cimd_and_preregistered", t), tx(locale, tab.REGISTRATION_LABELS.cimd_and_preregistered));
    assert.equal(tab.valueText("call_timeout_s", 60, t), `${inLocale(locale, () => ui.formatNumber(60))} ${tx(locale, "秒")}`);
    assert.equal(tab.valueText("max_connections_per_user", 10, t), inLocale(locale, () => ui.formatNumber(10)));
    if (locale === "en") for (const v of [tab.valueText("allowed_hosts", null, t), tab.valueText("call_timeout_s", 60, t), tab.valueText("client_registration", "preregistered_only", t)]) assert.ok(!HAN.test(v), v);
  }
});

test("the overview shows every connection masked — name, host, owner, authentication, state, platform stop, counts — and offers the stop and its lifting only to platform.config.manage", () => {
  for (const locale of LOCALES) {
    const html = mcpTab(locale, VIEW);
    const overview = section(html, "mcp-overview-heading");
    const stats = text(overview);
    for (const [name, value] of [["连接", 4], ["连接失败", 1], ["需要授权", 2], ["24 小时调用", 19], ["其中失败", 2], ["24 小时入站请求", 11]]) {
      assert.ok(stats.includes(`${tx(locale, name)} ${inLocale(locale, () => ui.formatNumber(value))}`), `${locale}: the ${name} tile\n${stats}`);
    }
    const ok = row(overview, OK.id), failed = row(overview, FAILED.id), off = row(overview, OFF.id), stopped = row(overview, STOPPED.id);
    for (const s of ["Fixture Weather", "weather.fixture.invalid", tx(locale, "工作空间：{name}", { name: "Fixture Workspace" }), tx(locale, "OAuth 授权"),
      tx(locale, "协议 {version}", { version: "2026-07-28" }), tx(locale, "启用中"), tx(locale, "连接正常"), tx(locale, "已授权 {granted} / {total}", { granted: 4, total: 6 }),
      tx(locale, "其中失败 {count}", { count: 1 }), tx(locale, "最近成功 {time}", { time: inLocale(locale, () => ui.fmtTime(T)) })]) {
      assert.ok(text(ok).includes(s), `${locale}: ${s}\n${text(ok)}`);
    }
    for (const s of ["Fixture Tickets", tx(locale, "个人连接"), tx(locale, "固定令牌"), tx(locale, "连接失败"), tx(locale, "尚未成功过"),
      tx(locale, "熔断至 {time}", { time: inLocale(locale, () => ui.fmtTime(T + 120)) }), tx(locale, "最近错误 {code}，连续 {count} 次", { code: "mcp_timeout", count: 5 }),
      tx(locale, "已授权 {granted} / {total}", { granted: 0, total: 2 })]) {
      assert.ok(text(failed).includes(s), `${locale}: ${s}\n${text(failed)}`);
    }
    for (const s of ["Fixture Archive", tx(locale, "已被停用"), tx(locale, "需要授权"), tx(locale, "无认证")]) assert.ok(text(off).includes(s), `${locale}: ${s}\n${text(off)}`);
    // the platform stop is named apart from the owner's own off switch, with the time it began
    for (const s of ["Fixture Ledger", tx(locale, "已被平台停用"), tx(locale, "平台停用于 {time}", { time: inLocale(locale, () => ui.fmtTime(T - 3600)) }),
      tx(locale, "固定令牌"), tx(locale, "需要授权")]) assert.ok(text(stopped).includes(s), `${locale}: ${s}\n${text(stopped)}`);
    assert.match(stopped, new RegExp(`<span class="badge err">${esc(tx(locale, "已被平台停用"))}</span>`), locale);
    assert.ok(!text(stopped).includes(tx(locale, "已被停用")), `${locale}: a stopped connection is not shown as merely switched off`);
    for (const c of [ok, failed, off]) assert.ok(!text(c).includes(tx(locale, "已被平台停用")) && !text(c).includes(tx(locale, "平台停用于 {time}", { time: "" }).trim()), `${locale}: no stop where there is none`);
    assert.ok(!text(off).includes(tx(locale, "协议 {version}", { version: "" }).trim()), `${locale}: no protocol version where the server sent none`);
    // the server's order is kept (failing connections first); every row opens its recent calls
    assert.ok(overview.indexOf(FAILED.id) < overview.indexOf(OK.id) && overview.indexOf(OK.id) < overview.indexOf(OFF.id)
      && overview.indexOf(OFF.id) < overview.indexOf(STOPPED.id), locale);
    for (const c of [OK, FAILED, OFF, STOPPED]) assert.ok(row(overview, c.id).includes(`aria-label="${tx(locale, "最近调用记录：{name}", { name: c.name })}"`), `${locale}: recent calls for ${c.id}`);
    // a reader: neither the stop nor its lifting, and the hint saying so; the search and the health filter
    assert.ok(!overview.includes(`>${tx(locale, "平台停用")}</button>`), `${locale}: the stop offered without the permission`);
    assert.ok(!overview.includes(`>${tx(locale, "解除平台停用")}</button>`), `${locale}: the lift offered without the permission`);
    assert.ok(overview.includes(tx(locale, "你的平台角色可以查看 MCP 策略与连接，不能发布策略，也不能平台停用连接或解除停用。")), `${locale}: the hint`);
    assert.match(overview, /<input type="search"/, locale);
    for (const key of Object.keys(tab.HEALTH_LABELS)) assert.ok(overview.includes(`<option value="${key}">${tx(locale, tab.HEALTH_LABELS[key])}</option>`), `${locale}: health option ${key}`);
    // a publisher: the stop as danger, named for the connection, wherever the platform has not stopped it
    const manage = section(mcpTab(locale, MANAGE), "mcp-overview-heading");
    const stop = (c) => new RegExp(`<button type="button" class="btn sm danger"[^>]*aria-label="${esc(tx(locale, "平台停用：{name}", { name: c.name }))}"[^>]*>${esc(tx(locale, "平台停用"))}</button>`);
    const lift = (c) => new RegExp(`<button type="button" class="btn sm"[^>]*aria-label="${esc(tx(locale, "解除平台停用：{name}", { name: c.name }))}"[^>]*>${esc(tx(locale, "解除平台停用"))}</button>`);
    assert.match(row(manage, OK.id), stop(OK), `${locale}: ${row(manage, OK.id)}`);
    assert.match(row(manage, FAILED.id), stop(FAILED), `${locale}: a failing connection can still be stopped`);
    // An owner's own off switch is theirs to turn back on: only a stop holds against them, so the
    // platform can stop a connection its owner has switched off. (Before the stop was recorded apart
    // from the owner's status, the page offered nothing here, and the platform could not keep such a
    // connection off.)
    assert.match(row(manage, OFF.id), stop(OFF), `${locale}: a connection its owner switched off can be stopped`);
    // a stopped connection is not stopped again; the lift is offered instead, and only there
    assert.doesNotMatch(row(manage, STOPPED.id), stop(STOPPED), `${locale}: a stopped connection is not stopped again`);
    assert.match(row(manage, STOPPED.id), lift(STOPPED), `${locale}: ${row(manage, STOPPED.id)}`);
    for (const c of [OK, FAILED, OFF]) assert.ok(!row(manage, c.id).includes(`>${tx(locale, "解除平台停用")}</button>`), `${locale}: no lift for ${c.id}`);
    assert.ok(!manage.includes(tx(locale, "你的平台角色可以查看 MCP 策略与连接，不能发布策略，也不能平台停用连接或解除停用。")), `${locale}: no hint for a role that may act`);
    // nothing to list
    const empty = section(mcpTab(locale, VIEW, { "/api/admin/mcp/overview": { data: { items: [], totals: { ...TOTALS, connections: 0 }, policy: STATE } } }), "mcp-overview-heading");
    assert.ok(empty.includes(tx(locale, "还没有任何 MCP 连接。")), locale);
  }
});

test("a stop's closed server address is listed by host only, and a second connection at it is marked as held back", () => {
  // FIXTURE: the overview as admin_mcp answers it while STOPPED's stop closes its address, which BEHIND also points at.
  const BEHIND = { ...OK, id: "mcpc_fx_behind", name: "Fixture Ledger Copy", host: "ledger.fixture.invalid", destination_stopped: true };
  const over = { "/api/admin/mcp/overview": { data: { items: [STOPPED, FAILED, OK, BEHIND], totals: TOTALS, policy: STATE,
    stopped_destinations: [{ host: "ledger.fixture.invalid", since: T - 3600, connection_id: STOPPED.id, connection_name: STOPPED.name, other_connections: 1 }] } } };
  for (const locale of LOCALES) {
    const overview = section(mcpTab(locale, VIEW, over), "mcp-overview-heading");
    const list = section(overview, "mcp-stopped-heading");
    for (const s of [tx(locale, "被平台停用的服务地址"), "ledger.fixture.invalid",
      tx(locale, "自 {time} 起，由「{name}」的平台停用关闭", { time: inLocale(locale, () => ui.fmtTime(T - 3600)), name: STOPPED.name }),
      tx(locale, "因此不能使用的其他连接：{count} 个", { count: 1 })]) assert.ok(text(list).includes(s), `${locale}: ${s}\n${text(list)}`);
    assert.ok(!/\/mcp|https?:/.test(list), `${locale}: the host only, never the address`);
    const held = row(overview, BEHIND.id);
    assert.match(held, new RegExp(`<span class="badge err">${esc(tx(locale, "地址已被平台停用"))}</span>`), locale);
    assert.ok(text(held).includes(tx(locale, "另一个连接的平台停用关闭了这个服务地址；解除那次停用之前，这个连接不能使用。")), locale);
    for (const id of [STOPPED.id, OK.id]) assert.ok(!row(overview, id).includes(tx(locale, "地址已被平台停用")), `${locale}: ${id}`);
  }
  // Without a closed address there is no list.
  assert.ok(!mcpTab("en", VIEW).includes('aria-labelledby="mcp-stopped-heading"'));
});

test("the stop and its lifting go to the server's own routes with the written reason, and its refusals reach the dialog", async () => {
  // the two routes are the server's (admin_mcp.build_router), dangerous and of platform.config.manage
  const py = readFileSync(join(ROOT, "services/identity/synwevo_identity/admin_mcp.py"), "utf8");
  assert.match(py, /APIRouter\(prefix="\/api\/admin\/mcp"/);
  for (const [route, action] of [["disable", "admin.mcp.connection.disable"], ["lift", "admin.mcp.connection.lift"]]) {
    assert.ok(py.includes(`@router.post("/connections/{cid}/${route}")`), `the account service serves …/${route}`);
    assert.ok(py.includes(`OPERATIONS.setdefault("${action}", Operation("platform.config.manage", "high"))`), action);
    const body = py.slice(py.indexOf(`@router.post("/connections/{cid}/${route}")`)).split("@router.")[1];
    assert.match(body, new RegExp(`require_platform\\(request, OPERATIONS\\["${esc(action)}"\\]\\.permission, store=store, audit=audit,\\s*dangerous=True\\)`), `${route} is dangerous`);
    assert.match(body, /reason = _reason\(body\)/, `${route} takes the written reason`);
  }
  // the page's dialogs send exactly that: the reason, through the helpers, and only with it required
  const src = readFileSync(new URL("../src/governance/McpTab.tsx", import.meta.url), "utf8");
  const dialog = (state) => { const i = src.indexOf(`{${state} && <ActionDialog`); assert.ok(i >= 0, state); return src.slice(i, src.indexOf("/>}", i)); };
  assert.match(dialog("disabling"), /reason="required" danger[\s\S]*run=\{\(reason\) => stopConnection\(disabling\.id, reason\)\}/);
  assert.match(dialog("lifting"), /reason="required"[\s\S]*run=\{\(reason\) => liftConnection\(lifting\.id, reason\)\}/);
  // FIXTURE transport: the real request helper of @synwevo/auth-client against a recorded fetch; no server
  const originalFetch = globalThis.fetch, originalDocument = globalThis.document;
  const sent = [];
  let answer = () => Response.json({ id: STOPPED.id, status: "disabled", platform_disabled: false });
  globalThis.document = { cookie: "dw_csrf=fixture-csrf-token" };
  globalThis.fetch = async (url, options) => { sent.push({ url, method: options.method, body: JSON.parse(options.body), headers: new Headers(options.headers) }); return answer(); };
  const REASON = "Fixture: the abuse report was resolved";
  try {
    assert.deepEqual(await tab.liftConnection(STOPPED.id, REASON), { id: STOPPED.id, status: "disabled", platform_disabled: false });
    answer = () => Response.json({ id: OFF.id, status: "disabled", platform_disabled: true });
    assert.deepEqual(await tab.stopConnection(OFF.id, REASON), { id: OFF.id, status: "disabled", platform_disabled: true });
    await tab.liftConnection("mcpc_fx/../policy", REASON);
    assert.deepEqual(sent.map((r) => [r.method, r.url, r.body]), [
      ["POST", `/api/admin/mcp/connections/${STOPPED.id}/lift`, { reason: REASON }],
      ["POST", `/api/admin/mcp/connections/${OFF.id}/disable`, { reason: REASON }],
      ["POST", "/api/admin/mcp/connections/mcpc_fx%2F..%2Fpolicy/lift", { reason: REASON }]]);
    for (const r of sent) assert.equal(r.headers.get("X-Synwevo-CSRF"), "fixture-csrf-token", "a change echoes the CSRF cookie");
    // the strict re-authentication arrives as the refusal ActionDialog answers (and then retries)
    answer = () => Response.json({ error: "reauth_required", message: "这一步需要重新验证身份。", detail: { scope: "admin" } }, { status: 403 });
    await assert.rejects(tab.liftConnection(STOPPED.id, REASON), (e) => e instanceof client.ApiError && e.status === 403 && e.code === "reauth_required");
    // lifting a connection someone else has already lifted: the server's sentence, in each language
    answer = () => Response.json({ error: "mcp_not_platform_disabled", message: "这个连接没有被平台停用" }, { status: 409 });
    const refused = await tab.liftConnection(STOPPED.id, REASON).catch((e) => e);
    assert.ok(refused instanceof client.ApiError && refused.code === "mcp_not_platform_disabled", String(refused));
    for (const locale of LOCALES) {
      const worded = inLocale(locale, () => labels.errText(refused));
      assert.ok(worded.startsWith(tx(locale, "这个连接没有被平台停用")), `${locale}: ${worded}`);
      if (locale === "en") assert.ok(!HAN.test(worded), worded);
    }
  } finally {
    globalThis.fetch = originalFetch;
    if (originalDocument === undefined) delete globalThis.document; else globalThis.document = originalDocument;
  }
});

test("a connection's recent calls show type, name, origin, outcome, duration and codes — never arguments or results", () => {
  for (const locale of LOCALES) {
    const html = text(calls(locale, OK.id));
    for (const s of [tx(locale, "最近 {count} 条调用：只有类型、名称、来源、结果与耗时；参数与结果不在这里显示。", { count: 2 }), "get_forecast", tx(locale, "工具调用"),
      tx(locale, "Agent 对话"), tx(locale, "成功"), tx(locale, "{n} 毫秒", { n: 812 }), "req_fx_1", tx(locale, "能力发现"), tx(locale, "连接测试"), tx(locale, "超时"),
      "mcp_timeout", tx(locale, "{n} 毫秒", { n: 30000 }), inLocale(locale, () => ui.fmtTime(T))]) {
      assert.ok(html.includes(s), `${locale}: ${s}\n${html}`);
    }
    assert.ok(text(calls(locale, OFF.id)).includes(tx(locale, "这个连接还没有调用记录。")), locale);
  }
});

test("the inbound card names the workplace endpoint as a path and derives only this origin's own metadata address", () => {
  // the two paths are the server's (synwevo_core.http.MCP_PATH, provider_routes' RFC 8414 route), not this page's invention
  const http = readFileSync(join(ROOT, "packages/core/synwevo_core/http.py"), "utf8");
  const suite = /^SUITE_V1 = "([^"]+)"$/m.exec(http)?.[1];
  const mcpPath = /^MCP_PATH = SUITE_V1 \+ "([^"]+)"$/m.exec(http)?.[1];
  assert.ok(suite && mcpPath, "synwevo_core.http names the MCP path");
  assert.equal(tab.MCP_ENDPOINT_PATH, suite + mcpPath);
  const routes = readFileSync(join(ROOT, "services/identity/synwevo_identity/provider_routes.py"), "utf8");
  assert.ok(routes.includes(`@r.get("${tab.AUTHORIZATION_SERVER_METADATA_PATH}")`), "the account service serves the metadata path the card names");
  for (const locale of LOCALES) {
    const card = section(mcpTab(locale, VIEW), "mcp-inbound-heading");
    assert.ok(card.includes(`<code>${tab.MCP_ENDPOINT_PATH}</code>`), `${locale}: the endpoint as a path`);
    assert.ok(card.includes(`<code>http://127.0.0.1${tab.AUTHORIZATION_SERVER_METADATA_PATH}</code>`), `${locale}: this origin's metadata address`);
    assert.deepEqual([...card.matchAll(/https?:\/\/[^\s<"]+/g)].map((m) => m[0]), [`http://127.0.0.1${tab.AUTHORIZATION_SERVER_METADATA_PATH}`], `${locale}: no other address`);
    assert.ok(text(card).includes(`${tx(locale, "入站状态")} ${tx(locale, "关")}`), `${locale}: inbound off\n${text(card)}`);
    assert.ok(text(card).includes(tx(locale, tab.REGISTRATION_LABELS.preregistered_only)), `${locale}: the registration`);
    const open = section(mcpTab(locale, VIEW, { "/api/admin/mcp/policy": { data: { state: { ...STATE, spec: CIMD }, default: DEFAULT, schema: SCHEMA } } }), "mcp-inbound-heading");
    assert.ok(text(open).includes(`${tx(locale, "入站状态")} ${tx(locale, "已开放")}`), `${locale}: inbound on`);
    assert.ok(text(open).includes(tx(locale, tab.REGISTRATION_LABELS.cimd_and_preregistered)), locale);
  }
});

test("the history lists each version with its summary, reason and actor, and offers loading a draft only to a publisher and never for the current version", () => {
  for (const locale of LOCALES) {
    const history = section(mcpTab(locale, VIEW), "mcp-history-heading");
    const three = text(article(history, "ao_fx_3")), two = text(article(history, "ao_fx_2")), one = text(article(history, "u_fx_other"));
    assert.ok(three.includes(tx(locale, "版本 {version}", { version: 3 })) && three.includes(tx(locale, "发布理由：{reason}", { reason: "Fixture reason for revision three" })), `${locale}: ${three}`);
    assert.ok(three.includes(tx(locale, "操作者：{actor}；操作编号：{operation}", { actor: "u_fx_admin", operation: "ao_fx_3" })), `${locale}: ${three}`);
    const t = (s, params) => tx(locale, s, params);
    assert.ok(three.includes(tx(locale, "出站 {outbound} · 入站 {inbound} · 明文 http {http} · {registration}", { outbound: tx(locale, "开"), inbound: tx(locale, "关"), http: tx(locale, "关"),
      registration: tab.valueText("client_registration", "preregistered_only", t) })), `${locale}: the summary\n${three}`);
    assert.ok(two.includes(tx(locale, "发布理由：{reason}", { reason: tx(locale, "没有写理由") })), `${locale}: a version without a reason\n${two}`);
    assert.ok(two.includes(tab.valueText("client_registration", "cimd_and_preregistered", t)), locale);
    assert.ok(one.includes(tx(locale, "这个版本的记录无法读取。")) && one.includes("—"), `${locale}: an unreadable snapshot and a missing operation id\n${one}`);
    assert.ok(!history.includes(`>${tx(locale, "载入为草稿")}</button>`), `${locale}: no draft loading for a reader`);
    const manage = section(mcpTab(locale, MANAGE), "mcp-history-heading");
    assert.ok(article(manage, "ao_fx_2").includes(`>${tx(locale, "载入为草稿")}</button>`), `${locale}: an older version can be loaded`);
    assert.ok(!article(manage, "ao_fx_3").includes(`>${tx(locale, "载入为草稿")}</button>`), `${locale}: not the current version`);
    assert.ok(!article(manage, "u_fx_other").includes(`>${tx(locale, "载入为草稿")}</button>`), `${locale}: not an unreadable one`);
    const empty = section(mcpTab(locale, VIEW, { "/api/admin/mcp/policy/history": { data: { items: [] } } }), "mcp-history-heading");
    assert.ok(empty.includes(tx(locale, "尚无发布记录，当前采用内置默认值。")), locale);
  }
});

test("the refusals of the policy, its publication and a connection read as sentences on this page", () => {
  const codes = { mcp_policy_bad_revision: "策略版本无效，请重新读取当前策略", mcp_policy_invalid: "MCP 策略不符合要求，请检查各项取值",
    mcp_policy_revision_conflict: "MCP 策略已变化，请重新预览", mcp_policy_unavailable: "MCP 策略记录暂不可用，请联系管理员检查",
    mcp_connection_not_found: "连接不存在", mcp_not_platform_disabled: "这个连接没有被平台停用", missing_reason: "请写明理由（8 至 500 个字）", admin_confirmation_required: "请先预览操作影响，再确认执行",
    idempotency_conflict: "此操作编号已用于不同的请求", admin_preview_invalid: "操作目标已变化，请重新预览", admin_preview_expired: "操作预览已过期，请重新预览" };
  const generic = (locale) => ["操作失败，请重试。", "操作未完成，请刷新后重试。"].map((s) => tx(locale, s));
  for (const [code, sentence] of Object.entries(codes)) {
    assert.ok(ui.hasMessage(sentence), `${code}: ${sentence} is catalogued for the administration page`);
    for (const locale of LOCALES) {
      // the server's sentence, and the code alone (a batch item or an older server)
      assert.equal(inLocale(locale, () => labels.errText(new client.ApiError(409, code, sentence))), tx(locale, sentence), `${locale} ${code}`);
      const bare = inLocale(locale, () => labels.errText(new client.ApiError(409, code, "")));
      assert.ok(bare && !bare.includes(code) && !generic(locale).includes(bare), `${locale} ${code}: ${bare}`);
      if (locale === "en") assert.ok(!HAN.test(bare) && !HAN.test(tx("en", sentence)), `${code}: ${bare}`);
    }
  }
});

test("the English render has no untranslated text", () => {
  const en = [mcpTab("en", MANAGE), mcpTab("en", VIEW), calls("en", OK.id), calls("en", OFF.id),
    mcpTab("en", MANAGE, { "/api/admin/mcp/policy": { data: { state: BUILTIN, default: DEFAULT, schema: SCHEMA } } }),
    mcpTab("en", MANAGE, { "/api/admin/mcp/policy": { data: { state: { ...STATE, spec: CIMD }, default: DEFAULT, schema: SCHEMA } } }),
    mcpTab("en", VIEW, { "/api/admin/mcp/overview": { data: { items: [], totals: TOTALS, policy: STATE } }, "/api/admin/mcp/policy/history": { data: { items: [] } } }),
    render("en", React.createElement(tab.McpTab, { onDenied() {}, held: new Set() }))]
    .map((html) => text(html).replace(FIXTURE_TEXT, "")).join("\n");
  assert.ok(!HAN.test(en), `untranslated in English:\n${en.split("\n").filter((l) => HAN.test(l)).join("\n").slice(0, 2000)}`);
  assert.doesNotMatch(en, /undefined|NaN|\[object|\{\w+\}/);
});

test("every sentence of the page is catalogued in English and Traditional Chinese, and this catalogue's wording is the one in effect", () => {
  assert.deepEqual(ui.validateMessages(messages.mcpMessages), []);
  for (const [source, entry] of Object.entries(messages.mcpMessages)) {
    for (const locale of ["en", "zh-Hant"]) assert.equal(tx(locale, source), entry[locale], `${locale}: ${source} is worded by another catalogue`);
    assert.ok(!HAN.test(entry.en), `untranslated: ${source}`);
  }
  const src = readFileSync(new URL("../src/governance/McpTab.tsx", import.meta.url), "utf8")
    .split("\n").filter((line) => !/^\s*(\/\/|\*|\/\*\*)/.test(line)).join("\n");
  const sentences = new Set([...src.matchAll(/"([^"\n]*[㐀-鿿][^"\n]*)"/g)].map((m) => m[1]));
  assert.ok(sentences.size > 120, `expected the page's sentences, found ${sentences.size}`);
  const missing = [], untranslated = [];
  for (const s of sentences) {
    if (!ui.hasMessage(s)) { missing.push(s); continue; }
    if (HAN.test(tx("en", s, { count: 2, version: 1 }))) untranslated.push(s);
  }
  assert.deepEqual(missing, [], "no catalogue entry in the admin bundle");
  assert.deepEqual(untranslated, [], "untranslated in English");
});
