/** The audit table's change column (admin coverage G24): the unified before/after record of a
 *  state-changing action rendered the same way for every action. Isolated UI fixtures. */
import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { testServer } from "./_vite.mjs";

const WEB = fileURLToPath(new URL("../", import.meta.url));
let server, mod;
before(async () => {
  server = await testServer({ root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  mod = await server.ssrLoadModule("/src/governance/audit.ts");
});
after(async () => { await server?.close(); });

test("a change record lists the keys that differ with both values, none for an action without one", () => {
  const rows = mod.auditChanges({ before: { mode: "off", grace_until: null }, after: { mode: "required", grace_until: 1760000000.5 },
                                  changed: ["grace_until", "mode"], reason: "fixture" });
  assert.deepEqual(rows, [{ key: "grace_until", before: "—", after: "1760000000.5" }, { key: "mode", before: "off", after: "required" }]);
  assert.deepEqual(mod.auditChanges({ reason: "a mail was sent again" }), []);
  assert.deepEqual(mod.auditChanges({ before: "open", after: "invite" }), [], "the old scalar shape is not a change record");
  assert.deepEqual(mod.auditChanges(null), []);
});

test("without an explicit changed list the differing keys are derived, and structures print as JSON", () => {
  const rows = mod.auditChanges({ before: { roles: [], frozen: false }, after: { roles: ["support"], frozen: false } });
  assert.deepEqual(rows, [{ key: "roles", before: "[]", after: '["support"]' }]);
  assert.equal(mod.auditValue(true), "true");
  assert.equal(mod.auditValue(0), "0");
});
