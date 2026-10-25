/** A47: platform oversight of invitations across workspaces — the tab is offered by the workspace
 *  view (governance/access.ts, admin/navigation.ts), the list shows masked invitees only and words
 *  each state and mail failure in the reader's language (governance/InvitationsTab.tsx), the
 *  revocation — per row, by selection and its bar, in a danger confirmation with a reason — exists
 *  only for `platform.workspaces.manage`, and a batch answers every invitation (BulkResult).
 *
 *  Rendered the way the browser renders it (vite's module graph, React's server renderer), with
 *  the administration data loader replaced by fixture data: no API, no user data. The roles are
 *  read from platform_access.py itself, as in admin-access.test.mjs. Run from the package: `npm test`. */
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

// Explicit browser-environment fixture, as in admin-bulk-accounts.test.mjs.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/admin?tab=invitations"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

/** Fixture loader in place of `./data` for InvitationsTab.tsx: the invitation list answers the rows
 *  and the group counts in `globalThis.__fixtures`. */
const FIXTURE_LOADER = `
export function useAdminData(path) {
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  return { data: f.data ?? null, err: f.err ?? null, loading: !!f.loading, reload() {} };
}
export function useAdminPage(path) {
  globalThis.__lastPath = path;
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  const items = f.items ?? [];
  return { data: { items, total: items.length, offset: 0, limit: 50, snapshot: 0, groups: f.groups }, err: null, loading: false, reload() {}, offset: 0, setOffset() {} };
}`;

let server, ui, access, nav, tab, messages, labels;
before(async () => {
  server = await testServer({
    root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{
      name: "invitations-fixture", enforce: "pre",
      resolveId(id, importer) { if (id === "./data" && importer?.endsWith("/src/governance/InvitationsTab.tsx")) return "\0invitations-fixture-data"; },
      load(id) { if (id === "\0invitations-fixture-data") return FIXTURE_LOADER; },
    }],
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  access = await server.ssrLoadModule("/src/governance/access.ts");
  nav = await server.ssrLoadModule("/src/admin/navigation.ts");
  labels = await server.ssrLoadModule("/src/governance/labels.ts");
  messages = await server.ssrLoadModule("/src/governance/invitations.messages.ts");
  tab = await server.ssrLoadModule("/src/governance/InvitationsTab.tsx");
  // the page's whole catalogue, as the browser has it on the administration page
  await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

// ------------------------------------------------------------------------------------ fixtures
// FIXTURE: GET /api/admin/invitations as the server answers it — the invitee's address only masked
// (first character, ***, domain), never a token or a link. Synthetic names and addresses on
// example.test; the inviters are on their own domain so an invitee address could not hide among them.
const T = 1_790_000_000;
const ws = (id, name, kind = "team", status = "active", organization_id = "org_fx_one") => ({ id, name, kind, status, organization_id });
const TEAM = ws("ws_fx_team", "Fixture Team Space");
const FROZEN = ws("ws_fx_frozen", "Fixture Frozen Space", "personal", "frozen", null);
const DELETED = ws("ws_fx_deleted", "Fixture Deleted Space", "team", "deleted");
const INVITER = { id: "u_fx_inviter", email: "inviter-fixture@staff.example.test", display_name: "Fixture Inviter" };
const BARE_INVITER = { id: "u_fx_bare", email: "bare-fixture@staff.example.test", display_name: null };
const VIA = "Fixture CRM integration";
const inv = (id, state, over = {}) => ({
  id, state, role: "editor", created_at: T, expires_at: T + 7 * 86400, revoked_at: null, accepted_at: null, declined_at: null,
  invitee: { masked: `${id.slice(-1)}***@invitee.example.test`, domain: "invitee.example.test", has_account: false },
  workspace: TEAM, inviter: INVITER, via: null, mail: { status: "sent", attempts: 1, last_error_code: null }, ...over,
});
const ROWS = [
  // A field the contract does not have is not drawn: were a token or a link ever sent, it stays off the page.
  inv("inv_fx_a", "pending", { invitee: { masked: "a***@invitee.example.test", domain: "invitee.example.test", has_account: true },
    token: "fixture-unrendered-token", link: "/invite#token=fixture-unrendered-token" }),
  inv("inv_fx_b", "pending", { inviter: null, via: VIA, mail: { status: "failed", attempts: 3, last_error_code: "smtp_recipient_rejected_550" } }),
  inv("inv_fx_c", "expired", { workspace: FROZEN, inviter: BARE_INVITER, role: "viewer", mail: { status: "dropped", attempts: 5, last_error_code: "transport_unavailable" } }),
  inv("inv_fx_d", "revoked", { revoked_at: T + 3600, mail: null }),
  inv("inv_fx_e", "accepted", { accepted_at: T + 7200, role: "admin" }),
  inv("inv_fx_f", "declined", { declined_at: T + 600, inviter: null, mail: { status: "queued", attempts: 2, last_error_code: "delivery_unknown" } }),
  // a deleted workspace's invitation is still listed, and says where it was
  inv("inv_fx_g", "pending", { workspace: DELETED, mail: { status: "sent", attempts: 1, last_error_code: null } }),
];
// The server counts every state under the search and the workspace, whichever chip is pressed;
// `mail_failed` is a part of `pending`, and the five states add up to the whole.
const GROUPS = { pending: 3, expired: 1, mail_failed: 1, revoked: 1, accepted: 1, declined: 1 };
const REVOCABLE = ROWS.filter((r) => r.state === "pending" || r.state === "expired");
const STATE_SOURCE = { pending: "待接受", expired: "已过期", revoked: "已撤销", accepted: "已接受", declined: "已拒绝" };
const MAIL_SOURCE = { sent: "已寄出", failed: "投递失败", dropped: "已放弃投递", queued: "排队发送中" };
const FAILURE_SOURCE = {
  smtp_recipient_rejected_550: "收件服务器拒收了这个地址",
  transport_unavailable: "投递失败；请检查邮件服务配置。",
  delivery_unknown: "投递结果不确定，禁止自动重发",
};

const LOCALES = ["zh-Hans", "zh-Hant", "en"];
const HAN = /[㐀-鿿]/;
const FIXTURE_TEXT = /[a-z]\*\*\*@invitee\.example\.test|[a-z-]+-fixture@staff\.example\.test|Fixture [A-Z][A-Za-z ]*?(?=[ ·<:"“”]|$)|inv_fx_[a-z]+|org_fx_[a-z]+|ws_fx_[a-z]+|Fixture CRM integration/g;
const VIEW = ROLES.auditor, SUPPORT = ROLES.support, MANAGE = ROLES.access_admin;

function inLocale(locale, run) { ui.setLocale(locale); try { return run(); } finally { ui.setLocale("zh-Hans"); } }
const render = (locale, element) => inLocale(locale, () => renderToStaticMarkup(element).replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
// The text a reader sees: tags break words and runs of HTML white space (space, tab, LF, FF, CR) fold into one
// space, as a browser lays them out. A thin or no-break space a formatter emits is part of the text and stays:
// ICU 78 (Node 22) puts U+2009 between the date and the time in zh-Hant, ICU 76 (Node 23) a plain space, and the
// expected dates come from the same ui.fmtTime, so they match under either.
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/[ \t\n\f\r]+/g, " ").trim();
const tx = (locale, source, params) => ui.translate(locale, source, params);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const button = (html, name) => new RegExp(`<button[^>]*>${esc(name)}</button>`).test(html);
const rowsOf = (html) => html.split("<tbody>")[1].split("</tbody>")[0].split("<tr").slice(1).map((r) => "<tr" + r);
function page(locale, held, items = ROWS, groups = GROUPS) {
  globalThis.__fixtures = { "/api/admin/invitations": { items, groups } };
  return render(locale, React.createElement(tab.InvitationsTab, { onDenied() {}, held }));
}
const labelOf = (id) => ROWS.find((r) => r.id === id)?.invitee.masked ?? id;

// ------------------------------------------------------------------------------------ tests
test("the tab is offered with the workspace view: auditor and support see it, finance does not", () => {
  assert.equal(access.TAB_PERMISSION.invitations, "platform.workspaces.view");
  const offered = Object.fromEntries(Object.entries(ROLES).map(([role, held]) => [role, access.visibleTabs(held).includes("invitations")]));
  assert.deepEqual(offered, { super_admin: true, access_admin: true, support: true, auditor: true, api_platform_admin: false, finance: false });
  for (const [role, held] of Object.entries(ROLES)) {
    const listed = nav.adminGroups(held).flatMap((group) => group.items.map((item) => item.id)).includes("invitations");
    assert.equal(listed, held.has("platform.workspaces.view"), `${role}: listed in the navigation`);
    assert.equal(nav.resolveAdminPage("/admin", "invitations", held) === "invitations", held.has("platform.workspaces.view"), `${role}: reachable by its address`);
  }
  // next to the workspaces, under accounts and access, with its label in both translations
  const group = nav.ADMIN_GROUPS.find((g) => g.items.some((item) => item.id === "invitations"));
  assert.equal(group.label, "账户与访问");
  const ids = group.items.map((item) => item.id);
  assert.equal(ids.indexOf("invitations"), ids.indexOf("workspaces") + 1);
  const label = group.items.find((item) => item.id === "invitations").label;
  assert.ok(ui.hasMessage(label) && !HAN.test(tx("en", label)) && tx("zh-Hant", label), label);
  // only the roles that may freeze a workspace may revoke its invitations
  const revokes = Object.fromEntries(Object.entries(ROLES).map(([role, held]) => [role, !access.permitted(held, "platform.workspaces.manage").disabled]));
  assert.deepEqual(revokes, { super_admin: true, access_admin: true, support: false, auditor: false, api_platform_admin: false, finance: false });
});

test("the list asks the server for the pending invitations first", () => {
  page("zh-Hans", VIEW);
  assert.equal(globalThis.__lastPath, "/api/admin/invitations?state=pending", "pending is the default view");
});

test("the table shows masked invitees only, with each state and mail state worded in every language", () => {
  for (const locale of LOCALES) {
    const html = page(locale, VIEW);
    const rows = rowsOf(html);
    assert.equal(rows.length, ROWS.length, `${locale}: one row per invitation`);
    ROWS.forEach((r, n) => {
      const row = rows[n], line = text(row);
      const who = `${locale} ${r.id}`;
      assert.ok(row.includes(`<span class="mono">${r.invitee.masked}</span>`), `${who}: the masked invitee\n${row}`);
      assert.ok(line.includes(r.workspace.name), `${who}: the workspace`);
      assert.ok(line.includes(tx(locale, ({ team: "团队", personal: "个人" })[r.workspace.kind])), `${who}: the workspace kind`);
      if (r.workspace.organization_id) assert.ok(line.includes(tx(locale, "所属组织 {id}", { id: r.workspace.organization_id })), `${who}: the organization`);
      if (r.workspace.status === "frozen") assert.ok(row.includes(`<span class="badge warn">${tx(locale, "已冻结")}</span>`), `${who}: a frozen workspace says so`);
      if (r.workspace.status === "deleted") assert.ok(row.includes(`<span class="badge err">${tx(locale, "已删除")}</span>`), `${who}: a deleted workspace says so`);
      if (r.workspace.status === "active") assert.ok(!row.split("</th>")[0].includes('class="badge'), `${who}: an active workspace needs no badge`);
      assert.equal(row.includes(tx(locale, "已有账户")), r.invitee.has_account, `${who}: the account badge`);
      assert.ok(line.includes(tx(locale, ({ editor: "编辑者", viewer: "查看者", admin: "管理员" })[r.role])), `${who}: the role`);
      assert.ok(line.includes(tx(locale, STATE_SOURCE[r.state])), `${who}: the state\n${line}`);
      if (r.revoked_at) assert.ok(line.includes(tx(locale, "撤销于 {time}", { time: inLocale(locale, () => ui.fmtTime(r.revoked_at)) })), `${who}: when revoked`);
      if (r.accepted_at) assert.ok(line.includes(tx(locale, "接受于 {time}", { time: inLocale(locale, () => ui.fmtTime(r.accepted_at)) })), `${who}: when accepted`);
      if (r.declined_at) assert.ok(line.includes(tx(locale, "拒绝于 {time}", { time: inLocale(locale, () => ui.fmtTime(r.declined_at)) })), `${who}: when declined`);
      assert.ok(line.includes(tx(locale, "有效期至 {time}", { time: inLocale(locale, () => ui.fmtTime(r.expires_at)) })), `${who}: the expiry`);
      if (!r.mail) assert.ok(line.includes(tx(locale, "无发送记录")), `${who}: no mail`);
      else {
        assert.ok(line.includes(tx(locale, MAIL_SOURCE[r.mail.status])), `${who}: the mail state\n${line}`);
        // the mail_failed case: a pending invitation whose latest mail failed or was dropped stands out
        const lost = r.state === "pending" && ["failed", "dropped"].includes(r.mail.status);
        assert.equal(row.includes(`<span class="badge err">${tx(locale, MAIL_SOURCE[r.mail.status])}</span>`), lost, `${who}: an undelivered pending invitation is highlighted`);
        assert.ok(line.includes(tx(locale, "已尝试 {count} 次", { count: r.mail.attempts })), `${who}: the attempts`);
        if (r.mail.last_error_code) {
          assert.ok(line.includes(tx(locale, FAILURE_SOURCE[r.mail.last_error_code])), `${who}: the failure worded\n${line}`);
          assert.ok(row.includes(`<code>${r.mail.last_error_code}</code>`), `${who}: and its code`);
        }
      }
      if (r.inviter) assert.ok(line.includes(r.inviter.display_name || r.inviter.email), `${who}: the inviter`);
      if (r.via) assert.ok(line.includes(r.via), `${who}: the integration that sent it`);
      if (!r.inviter && !r.via) assert.ok(line.includes(tx(locale, "无记录")), `${who}: no inviter recorded`);
      assert.ok(button(row, tx(locale, "只看这个空间")), `${who}: the workspace filter is offered from the row`);
    });
    // Every invitee address on the page is a masked one; the inviters are platform accounts.
    const addresses = [...text(html).matchAll(/\S+@invitee\.example\.test/g)].map((m) => m[0]);
    assert.equal(addresses.length, ROWS.length, `${locale}: ${addresses}`);
    // the search says what it matches
    assert.match(html, /<label for="([^"]+)">[^<]+<\/label><input id="\1" type="search"[^>]*aria-describedby="\1-hint"/, `${locale}: the search box and its hint`);
    assert.ok(html.includes(tx(locale, "输入完整邮箱、@域名，或工作空间名称的一部分；邮箱的一部分不会匹配。")), `${locale}: the search hint`);
    for (const a of addresses) assert.match(a, /^[a-z]\*\*\*@invitee\.example\.test$/, `${locale}: only masked invitee addresses`);
    assert.doesNotMatch(html, /fixture-unrendered-token|\/invite#/, `${locale}: no link or token`);
    // the state chips with their counts; the default view is pressed
    const chips = html.split('role="group"')[1].split("</div>")[0];
    for (const [state, count] of [["pending", 3], ["mail_failed", 1], ["expired", 1], ["revoked", 1], ["accepted", 1], ["declined", 1], ["all", 7]]) {
      const name = tx(locale, "{label}（{count}）", { label: tx(locale, tab.STATE_FILTER_LABELS[state]), count });
      assert.ok(new RegExp(`aria-pressed="${state === "pending"}"[^>]*>${esc(name)}</button>`).test(chips), `${locale} ${state}: ${name}\n${chips}`);
    }
    for (const sentence of ["这里列出所有工作空间发出的邀请，用来发现长期没有处理、已过期或投递失败的邀请，以及滥用的邀请。受邀地址只显示首字符和域名；这里看不到完整地址、邀请链接或令牌。",
      "撤销后，邀请链接在预览和接受时都会失效；邀请人之后可以重新邀请。", "撤销会写入邀请所在空间自己的审计记录，标明由平台管理执行，你填写的理由对能查看该组织审计的人可见；不会授予你该空间的任何访问权。"]) {
      assert.ok(html.includes(tx(locale, sentence)), `${locale}: the explanation — ${sentence}`);
    }
    assert.match(html, /<input [^>]*type="search"[^>]*maxLength="100"/, `${locale}: the search box`);
  }
  // without the counts yet, the chips are named alone
  const unloaded = page("en", VIEW, ROWS, null);
  assert.ok(button(unloaded, "Pending acceptance") && button(unloaded, "All"), unloaded);
});

test("revoke controls and the selection exist only with platform.workspaces.manage", () => {
  for (const locale of LOCALES) {
    for (const held of [VIEW, SUPPORT]) {
      const html = page(locale, held);
      assert.ok(!html.includes("data-pick") && !html.includes(tx(locale, "选择本页可撤销的邀请")), `${locale}: no selection without manage`);
      assert.ok(!button(html, tx(locale, "撤销邀请")), `${locale}: no row revocation without manage`);
      assert.ok(!html.includes('class="acct-bulk"'), `${locale}: no bar`);
      assert.ok(html.includes(tx(locale, "你的平台角色可以查看邀请，但不能撤销。")), `${locale}: says why`);
      assert.match(html, /<table class="gov admin-cards-table admin-invitations-table"/);
    }
    for (const held of [MANAGE, ROLES.super_admin]) {
      const html = page(locale, held);
      assert.match(html, /<table class="gov admin-cards-table admin-invitations-table selectable"/, locale);
      assert.ok(!html.includes(tx(locale, "你的平台角色可以查看邀请，但不能撤销。")), `${locale}: no hint for a role that can act`);
      const rows = rowsOf(html);
      ROWS.forEach((r, n) => {
        const can = REVOCABLE.includes(r);
        const label = `${r.invitee.masked} · ${r.workspace.name}`;
        assert.equal(rows[n].includes(`data-pick="${r.id}"`), can, `${locale} ${r.id}: selectable only while pending or expired`);
        if (can) assert.match(rows[n], new RegExp(`<input type="checkbox" data-pick="${r.id}"[^>]*aria-label="${esc(tx(locale, "选择邀请 {p0}", { p0: label }))}"`), `${locale} ${r.id}`);
        assert.equal(button(rows[n], tx(locale, "撤销邀请")), can, `${locale} ${r.id}: the row revocation`);
        if (can) assert.ok(rows[n].includes(`aria-label="${tx(locale, "撤销邀请：{p0}", { p0: label })}"`), `${locale} ${r.id}: named after the invitation`);
      });
      assert.match(html, new RegExp(`<th scope="col" class="pick"><label class="pick-page"><input type="checkbox"[^>]*/><span class="pick-page-text">${esc(tx(locale, "选择本页可撤销的邀请"))}</span></label></th>`), `${locale}: select this page`);
      assert.doesNotMatch(html, /tabindex="-1"[^>]*data-pick|data-pick[^>]*tabindex="-1"/, "no checkbox is taken out of the tab order");
      assert.ok(!html.includes('class="acct-bulk"'), `${locale}: no bar before anything is chosen`);
    }
  }
});

test("the selection bar offers the bulk revocation only with platform.workspaces.manage, and says so otherwise", () => {
  const bar = (locale, held, count = 3, full = false) => render(locale, React.createElement(tab.InvitationsBar, { held, count, full, onStart() {}, onClear() {} }));
  for (const locale of LOCALES) {
    const manage = bar(locale, MANAGE);
    assert.ok(manage.includes(tx(locale, "已选 {count} 个邀请", { count: 3 })), `${locale}: the count`);
    assert.ok(button(manage, tx(locale, "撤销所选邀请")) && /class="btn danger sm"/.test(manage), `${locale}: the revocation, as a danger action`);
    assert.ok(button(manage, tx(locale, "清除选择")), `${locale}: the way out`);
    assert.ok(!manage.includes(tx(locale, "你的平台角色不包含这项操作")), `${locale}: no hint while it is offered`);
    const view = bar(locale, VIEW);
    assert.ok(!button(view, tx(locale, "撤销所选邀请")), `${locale}: not offered without manage`);
    assert.ok(view.includes(tx(locale, "你的平台角色不包含这项操作")), `${locale}: the usual hint`);
    assert.ok(!manage.includes(tx(locale, "一次最多选择 {limit} 个邀请；已达到上限。", { limit: 100 })), `${locale}: no cap note below the cap`);
    assert.ok(bar(locale, MANAGE, 100, true).includes(tx(locale, "一次最多选择 {limit} 个邀请；已达到上限。", { limit: 100 })), `${locale}: the cap note`);
  }
  assert.ok(bar("en", MANAGE, 1).includes("1 invitation selected") && bar("en", MANAGE, 2).includes("2 invitations selected"), "English counts agree in number");
  assert.equal(tab.INVITATION_BULK_LIMIT, 100, "one batch is the server's limit");
});

test("both revocations are danger confirmations that need a reason and say what stops and where it is recorded", () => {
  for (const locale of LOCALES) {
    const one = render(locale, React.createElement(tab.RevokeInvitationDialog, { invitation: ROWS[0], onClose() {}, onDone() {} }));
    const many = render(locale, React.createElement(tab.BulkRevokeDialog, { ids: REVOCABLE.map((r) => r.id), labelOf, onClose() {}, onDone() {} }));
    for (const [html, name] of [[one, tx(locale, "撤销邀请")], [many, tx(locale, "撤销所选邀请")]]) {
      assert.ok(html.includes('role="alertdialog"'), `${locale}: a confirmation dialog`);
      assert.match(html.match(/<button type="submit"[^>]*>[^<]*<\/button>/)?.[0] ?? "", new RegExp(`class="btn danger"[^>]*>${esc(name)}</button>$`), `${locale}: ${name}`);
      assert.ok(html.includes("<textarea") && html.includes(tx(locale, "（必填，至少{p0}个字）", { p0: 8 })), `${locale}: a reason is required`);
      assert.ok(html.includes(tx(locale, "撤销后，邀请链接在预览和接受时都会失效；邀请人之后可以重新邀请。")), `${locale}: what stops`);
      assert.ok(html.includes(tx(locale, "撤销会写入邀请所在空间自己的审计记录，标明由平台管理执行，你填写的理由对能查看该组织审计的人可见；不会授予你该空间的任何访问权。")), `${locale}: where it is recorded`);
    }
    assert.ok(text(one).includes(tx(locale, "发给 {who} 的邀请将被撤销，对方不能再用它加入「{workspace}」（{role}）。",
      { who: ROWS[0].invitee.masked, workspace: ROWS[0].workspace.name, role: tx(locale, "编辑者") })), `${locale}: who and where\n${text(one)}`);
    assert.ok(text(many).includes(tx(locale, "已选 {count} 个邀请", { count: REVOCABLE.length })), `${locale}: how many`);
    for (const r of REVOCABLE) assert.ok(many.includes(`<li>${r.invitee.masked}</li>`), `${locale}: ${r.id} listed`);
    assert.match(many, /<ul class="names" tabindex="0" aria-label="[^"]+"/, `${locale}: the list scrolls on its own and from the keyboard`);
  }
});

test("a batch lists every invitation, failures worded by code, already revoked ones as done", () => {
  const RESPONSE = { ok: false, succeeded: 2, failed: 4, results: [
    { id: "inv_fx_a", ok: true, code: "ok", message: "邀请已撤销" },
    { id: "inv_fx_b", ok: true, code: "already_revoked", message: "邀请此前已撤销" },
    { id: "inv_fx_e", ok: false, code: "invite_not_pending", message: "" },
    { id: "inv_fx_x", ok: false, code: "no_invite", message: "" },
    // the administrator's own access check failed partway: the remaining invitations report it
    { id: "inv_fx_c", ok: false, code: "reauth_required", message: "这一步需要重新验证身份" },
    { id: "inv_fx_g", ok: false, code: "platform_permission_denied", message: "你的平台角色不包含这项操作所需的权限" },
  ], batch_id: "ivb_fixture" };
  for (const locale of LOCALES) {
    const html = render(locale, React.createElement(tab.InvitationsBulkResult, { response: RESPONSE, labelOf, onClose() {} }));
    const rows = text(html);
    assert.equal((html.match(/<li class="(ok|err)"/g) || []).length, RESPONSE.results.length, `${locale}: one line per invitation`);
    for (const r of RESPONSE.results) assert.ok(rows.includes(labelOf(r.id)), `${locale}: ${r.id}`);
    assert.ok(rows.includes(tx(locale, "此前已撤销，本次未重复执行。")) && rows.includes(tx(locale, "已撤销")), `${locale}: what each success did\n${rows}`);
    for (const r of RESPONSE.results.filter((x) => !x.ok)) {
      const worded = inLocale(locale, () => labels.resultText(r.code, r.message));
      assert.ok(worded && rows.includes(worded), `${locale} ${r.code}: ${worded}`);
    }
    assert.ok(html.includes(tx(locale, "撤销所选邀请")), `${locale}: titled by the act`);
    assert.ok(rows.includes(tx(locale, "成功{p0}项，失败{p1}项。请查看失败原因，并核对刷新后的列表。", { p0: 2, p1: 4 })), `${locale}: the summary`);
    if (locale === "en") assert.ok(!HAN.test(rows.replace(FIXTURE_TEXT, "")), rows);
  }
});

test("the refusals of the revocation read as sentences on the administration page", () => {
  const generic = (locale) => ["操作失败，请重试。", "操作未完成，请刷新后重试。"].map((s) => tx(locale, s));
  for (const code of ["invite_not_pending", "no_invite", "missing_ids", "too_many", "already_revoked", "missing_reason", "reauth_required", "platform_permission_denied"]) {
    for (const locale of LOCALES) {
      const worded = inLocale(locale, () => labels.resultText(code, ""));
      assert.ok(worded && !worded.includes(code) && !generic(locale).includes(worded), `${locale} ${code}: ${worded}`);
      if (locale === "en") assert.ok(!HAN.test(worded), `${code}: ${worded}`);
    }
  }
  // `too_many` with the limit the server sends is worded with that number
  assert.ok(inLocale("en", () => labels.resultText("too_many", "", { field: "ids", limit: 100 })).includes("100"));
  // the server's own sentence for an accepted or declined invitation (Invites.platform_revoke) is preferred to the code's label
  assert.equal(inLocale("en", () => labels.resultText("invite_not_pending", "已接受或已拒绝的邀请不能撤销")), "An accepted or declined invitation cannot be revoked.");
  // badly formed ids (admin_invitations `_ids`)
  assert.equal(inLocale("en", () => labels.resultText("missing_ids", "邀请编号格式无效")), "The invitation identifiers are not valid. Refresh the list and select again.");
});

test("mail failures are worded by their classified code, never as raw server text", () => {
  // mail.public_error_code: the known codes and `smtp_*rejected_4xx/5xx` as they are, anything else
  // `mail_delivery_failed`. The mail tab's sentences, and a mail server's refusal named as one.
  const SERVICE = "投递失败；请检查邮件服务配置。", UNCERTAIN = "投递结果不确定，禁止自动重发";
  const classes = {
    smtp_recipient_rejected_550: "收件服务器拒收了这个地址", smtp_recipient_rejected_450: "收件服务器拒收了这个地址",
    smtp_sender_rejected_553: "邮件服务器拒收了这封邮件", smtp_data_rejected_554: "邮件服务器拒收了这封邮件", smtp_rejected_421: "邮件服务器拒收了这封邮件",
    transport_unavailable: SERVICE, tls_certificate_invalid: SERVICE, tls_failed: SERVICE, smtp_authentication_failed: SERVICE,
    smtp_security_not_supported: SERVICE, mail_not_configured: SERVICE, mail_config_invalid: SERVICE, public_url_missing: SERVICE,
    mail_delivery_failed: SERVICE, delivery_unknown: UNCERTAIN, delivery_in_progress: UNCERTAIN, superseded: "已生成替代邮件",
  };
  for (const locale of LOCALES) {
    for (const [code, source] of Object.entries(classes)) assert.equal(inLocale(locale, () => tab.mailFailureText(code)), tx(locale, source), `${locale} ${code}`);
    assert.equal(inLocale(locale, () => tab.mailFailureText(null)), null);
  }
});

test("the English render has no untranslated text", () => {
  const en = [page("en", VIEW), page("en", MANAGE), page("en", MANAGE, [], { pending: 0, expired: 0, mail_failed: 0, revoked: 0, accepted: 0, declined: 0 }),
    render("en", React.createElement(tab.InvitationsBar, { held: MANAGE, count: 100, full: true, onStart() {}, onClear() {} })),
    render("en", React.createElement(tab.InvitationsBar, { held: VIEW, count: 2, onStart() {}, onClear() {} })),
    render("en", React.createElement(tab.RevokeInvitationDialog, { invitation: ROWS[2], onClose() {}, onDone() {} })),
    render("en", React.createElement(tab.BulkRevokeDialog, { ids: REVOCABLE.map((r) => r.id), labelOf, onClose() {}, onDone() {} })),
    render("en", React.createElement(tab.InvitationsBulkResult, { labelOf, onClose() {}, response: { ok: false, succeeded: 1, failed: 1, results: [
      { id: "inv_fx_a", ok: true, code: "already_revoked", message: "" }, { id: "inv_fx_e", ok: false, code: "invite_not_pending", message: "" }] } }))]
    .map((html) => text(html).replace(FIXTURE_TEXT, "")).join("\n");
  assert.ok(!HAN.test(en), `untranslated in English:\n${en.split(/(?<=[.。])\s/).filter((l) => HAN.test(l)).join("\n")}`);
  assert.doesNotMatch(en, /undefined|NaN|\[object/);
});

test("every sentence of the tab is catalogued in English and Traditional Chinese", () => {
  assert.deepEqual(ui.validateMessages(messages.invitationMessages), []);
  const src = readFileSync(new URL("../src/governance/InvitationsTab.tsx", import.meta.url), "utf8");
  // every Chinese literal of the file: tr("…") arguments, the state and mail maps, the shared sentences
  const sentences = new Set([...src.matchAll(/"([^"\n]*[㐀-鿿][^"\n]*)"/g)].map((m) => m[1]));
  sentences.add("已清除选择"); // announced by components/selection.ts escapeClears
  sentences.add("你的平台角色不包含这项操作"); // access.ts NOT_IN_ROLE, the bar's hint
  assert.ok(sentences.size > 50, `expected the tab's sentences, found ${sentences.size}`);
  const missing = [], untranslated = [];
  for (const s of sentences) {
    if (!ui.hasMessage(s)) { missing.push(s); continue; }
    if (HAN.test(ui.translate("en", s, { count: 2, label: "", p0: "", who: "", workspace: "", role: "", name: "", time: "", id: "", limit: 1 }))) untranslated.push(s);
  }
  assert.deepEqual(missing, [], "no catalogue entry in the admin bundle");
  assert.deepEqual(untranslated, [], "untranslated in English");
});
