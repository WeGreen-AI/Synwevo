/** The administration pages offer what the caller's platform permissions reach, and word what the
 *  service writes in the reader's language (synwevo_core.platform_access).
 *
 *  The permission catalogue and the built-in roles are read from platform_access.py itself, so a
 *  role that gains or loses a permission is checked against the pages without editing this file.
 *  The pages' decisions live in `governance/access.ts`; the grant reasons and refusal notes in
 *  `governance/PlatformAccess.tsx`.
 *
 *  Fixtures are isolated: no API, no user data. Run from the package: `npm test`.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const ROOT = fileURLToPath(new URL("../../../../", import.meta.url));
const CORE = "packages/core/synwevo_core/";
const source = (file) => readFileSync(join(ROOT, file), "utf8");

// ------------------------------------------------------------------------------------ catalogue
const PY = source(CORE + "platform_access.py");
const block = (start, end) => { const i = PY.indexOf(start); assert.ok(i >= 0, start); return PY.slice(i, PY.indexOf(end, i)); };
const PERMISSIONS = [...block("PERMISSIONS: dict[str, str] = {", "\n}\n").matchAll(/^\s+"([a-z_.]+)":/gm)].map((m) => m[1]);
const ROLES = { super_admin: new Set(PERMISSIONS) };
for (const m of block("ROLES: dict[str, frozenset[str]] = {", "\n}\n").matchAll(/"(\w+)": frozenset\(\{([^}]*)\}\)/g)) {
  ROLES[m[1]] = new Set([...m[2].matchAll(/"([a-z_.]+)"/g)].map((x) => x[1]));
}
const SYSTEM_REASONS = [...block("SYSTEM_REASONS: dict[str, str] = {", "\n}\n").matchAll(/^\s+"\w+": "([^"]+)",/gm)].map((m) => m[1]);

// Explicit browser-environment fixture, as in refusals.test.mjs: no account, no API.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };

let server, ui, access, dialog;
before(async () => {
  server = await createServer({ root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ui = await server.ssrLoadModule("@synwevo/ui");
  access = await server.ssrLoadModule("/src/governance/access.ts");
  dialog = await server.ssrLoadModule("/src/governance/PlatformAccess.tsx");
  await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { await server?.close(); });

const HAN = /[㐀-鿿]/;
/** The sentence in English and Traditional Chinese, and neither is the Simplified source. */
function translated(text) {
  assert.ok(ui.hasMessage(text), `no catalogue entry: ${text}`);
  for (const locale of ["en", "zh-Hant"]) assert.notEqual(ui.translate(locale, text, { count: 2 }), undefined);
  assert.ok(!HAN.test(ui.translate("en", text, { count: 2 })), `untranslated in English: ${text}`);
}
function inEnglish(run) { ui.setLocale("en"); try { return run(); } finally { ui.setLocale("zh-Hans"); } }

// ------------------------------------------------------------------------------------ tests
test("the catalogue read from platform_access.py is the one the pages decide on", () => {
  assert.ok(PERMISSIONS.includes("platform.workspaces.claim") && PERMISSIONS.includes("api_platform.manage"));
  assert.deepEqual(Object.keys(ROLES).sort(), ["access_admin", "api_platform_admin", "auditor", "super_admin", "support"]);
  for (const perms of Object.values(ROLES)) for (const p of perms) assert.ok(PERMISSIONS.includes(p), p);
  // every view's permission is a catalogued one
  for (const p of Object.values(access.TAB_PERMISSION)) assert.ok(PERMISSIONS.includes(p), p);
});

test("the historical data is offered only to whoever may assign it and search for the account", () => {
  const offered = Object.fromEntries(Object.entries(ROLES).map(([role, perms]) => [role, access.mayClaim(perms)]));
  // an API platform administrator's overview used to draw the picker, whose account search it may
  // not run; an account and access administrator used to be offered a claim it can no longer make
  assert.deepEqual(offered, { super_admin: true, access_admin: false, support: false, auditor: false, api_platform_admin: false });
  assert.deepEqual(access.visibleTabs(ROLES.api_platform_admin), ["overview"]);
  assert.deepEqual(access.visibleTabs(ROLES.super_admin), Object.keys(access.TAB_PERMISSION));
});

test("model provider uses the existing config grants, never a role name or a manage-implies-view default", () => {
  assert.equal(access.TAB_PERMISSION['model-provider'], 'platform.config.view');
  for (const [role, held] of Object.entries(ROLES)) {
    assert.equal(access.visibleTabs(held).includes('model-provider'), held.has('platform.config.view'), role);
    assert.equal(access.permitted(held, 'platform.config.manage').disabled, !held.has('platform.config.manage'), role);
  }
  for (const identity of [undefined, null, {}, { role: 'owner' }, { role: 'admin' }, { platform_role: 'super_admin' }]) {
    assert.equal(access.visibleTabs(access.heldPermissions(identity)).includes('model-provider'), false);
  }
  const readOnly = access.heldPermissions({ platform_permissions: ['platform.config.view'] });
  assert.equal(access.visibleTabs(readOnly).includes('model-provider'), true);
  assert.equal(access.permitted(readOnly, 'platform.config.manage').disabled, true);
  assert.equal(access.visibleTabs(new Set(['platform.config.manage'])).includes('model-provider'), false);
});

test("each account action is offered as the server would answer it", () => {
  const me = "u_me";
  const plain = { id: "u_plain", status: "active", platform_roles: [] };
  const higher = { id: "u_high", status: "active", platform_roles: ["super_admin"], outranks_caller: true };
  const suspended = { id: "u_susp", status: "suspended", platform_roles: [] };
  const deleting = { id: "u_del", status: "pending_delete", platform_roles: ["super_admin"], outranks_caller: true };
  const at = (role, user) => access.userOffers(ROLES[role], user, me);
  // the roles without users.manage are not offered suspension or restore at all
  for (const role of ["support", "auditor", "api_platform_admin"]) {
    assert.deepEqual(at(role, plain).status, { disabled: true, hint: access.NOT_IN_ROLE }, role);
  }
  assert.deepEqual(at("auditor", plain).recovery, { disabled: true, hint: access.NOT_IN_ROLE });
  assert.deepEqual(at("auditor", plain).revoke, { disabled: true, hint: access.NOT_IN_ROLE });
  assert.equal(at("auditor", plain).access.disabled, false, "the auditor reads platform access");
  assert.deepEqual(at("support", plain).access, { disabled: true, hint: access.NOT_IN_ROLE });
  // support may help an ordinary account, not a higher administrator
  assert.equal(at("support", plain).recovery.disabled, false);
  assert.equal(at("support", plain).revoke.disabled, false);
  assert.deepEqual(at("support", higher).revoke, { disabled: true, hint: access.OUTRANKS_YOU });
  assert.deepEqual(at("support", higher).recovery, { disabled: true, hint: access.OUTRANKS_YOU });
  // restoring is for suspended accounts only (never a pending deletion), and ranked
  assert.equal(at("access_admin", suspended).status.disabled, false);
  assert.deepEqual(at("access_admin", deleting).status, { disabled: true, hint: "只有已停用的账户可以恢复" });
  assert.deepEqual(at("access_admin", { ...suspended, outranks_caller: true }).status, { disabled: true, hint: access.OUTRANKS_YOU });
  // suspension: never oneself, never a grant holder
  assert.deepEqual(at("super_admin", { ...plain, id: me }).status, { disabled: true, hint: "不能停用自己的账户" });
  assert.deepEqual(at("super_admin", higher).status, { disabled: true, hint: "请先撤销该账户的全部平台权限" });
  // one's own row is never "outranking"
  assert.equal(at("support", { ...higher, id: me }).revoke.disabled, false);
  // the other views' writes
  for (const [role, perms] of Object.entries(ROLES)) {
    for (const p of ["platform.workspaces.manage", "platform.config.manage", "platform.mail.manage", "platform.orgs.manage"]) {
      assert.equal(access.permitted(perms, p).disabled, !perms.has(p), `${role} ${p}`);
    }
  }
  for (const hint of [access.NOT_IN_ROLE, access.OUTRANKS_YOU, "只有已停用的账户可以恢复", "不能停用自己的账户",
    "请先撤销该账户的全部平台权限", "该账户当前不能接收找回邮件"]) translated(hint);
});

test("the audit filter reaches the platform-access records", () => {
  const scopes = access.AUDIT_SCOPES.map(([prefix]) => prefix).filter(Boolean);
  const written = new Set();
  for (const file of [CORE + "platform_access.py", CORE + "journal.py"]) {
    for (const m of source(file).matchAll(/["'](admin\.(?:platform|access)\.[a-z_]+)["']/g)) written.add(m[1]);
  }
  assert.ok(written.has("admin.platform.grant") && written.has("admin.access.denied"), [...written].join());
  for (const action of written) assert.ok(scopes.some((p) => action.startsWith(p)), `no filter for ${action}`);
  for (const [, label] of access.AUDIT_SCOPES) translated(label);
});

test("reasons the service writes are shown in the reader's language; typed reasons as typed", () => {
  assert.ok(SYSTEM_REASONS.length >= 8, SYSTEM_REASONS.join());
  assert.deepEqual([...dialog.SYSTEM_REASONS].sort(), [...SYSTEM_REASONS].sort(), "PlatformAccess.tsx follows platform_access.SYSTEM_REASONS");
  for (const reason of SYSTEM_REASONS) translated(reason);
  inEnglish(() => {
    const migrated = dialog.grantReasonText({ granted_by: "migration:v27", reason: SYSTEM_REASONS[0] });
    assert.ok(!HAN.test(migrated), migrated);
    // an administrator's own words are never rewritten, even when they match a fixed sentence
    assert.equal(dialog.grantReasonText({ granted_by: "u_admin", reason: SYSTEM_REASONS[0] }), SYSTEM_REASONS[0]);
    assert.equal(dialog.systemReasonText("交接给新的支持同事"), "交接给新的支持同事");
  });
});

test("why a grant cannot be revoked, or nothing granted, is a visible sentence for each reason", () => {
  inEnglish(() => {
    for (const text of [dialog.revokeRefusalText, dialog.assignRefusalText]) {
      const said = ["platform_permission_denied", "role_exceeds_own", "self_assignment"].map((code) => text(code));
      for (const s of said) assert.ok(s && !HAN.test(s), s);
      assert.equal(new Set(said).size, 3, "each reason has its own sentence");
      assert.equal(text(null), "");
    }
  });
});
