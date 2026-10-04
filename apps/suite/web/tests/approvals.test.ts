/** Approvals wording a person meets: refusals and template warnings from the server, worded in
 *  every locale, and what the request page shows. Fixtures are isolated: no API, no user data. */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { createServer, type ViteDevServer } from "vite";
import { translate } from "@synwevo/ui/i18n-core";
import { approvals } from "../src/apps/approvals/messages.ts";
import * as text from "../src/apps/approvals/text.ts";

// The pages themselves, compiled the way the shell is and rendered to markup (in the default
// locale: the server render does not read a saved language), without any API.
let vite: ViteDevServer;
before(async () => {
  vite = await createServer({ root: join(import.meta.dirname, ".."), configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
});
after(async () => { await vite?.close(); });
async function render(module: string, name: string, props: object): Promise<string> {
  const page = await vite.ssrLoadModule(module);
  return renderToString(createElement(MemoryRouter, null, createElement(page[name], props)));
}
const visible = (html: string) => html.replace(/<!-- -->/g, "").replace(/<[^>]+>/g, "\n").split("\n").map(x => x.trim()).filter(Boolean);
const ws = { id: "w_fixture", name: "Fixture workspace", role: "editor" };
const fixtureStage = (id: string, name: string, mode = "all") => ({ id, name, mode, approvers: { users: ["u_bob"] } });
/** A request fixture: stage 1 passed, stage 2 skipped, stage 3 in progress, stage 4 not reached. */
function fixtureRequest(note = "") {
  const snapshot = { revision: 1, fields: { f_amount: "12.50" }, basis: "Fixture basis", fields_hash: "h1", basis_hash: "h2", note, submitted_at: 1791130949, submitted_by: "u_alice" };
  const run = (n: number, status: string) => ({ id: `ast_${n}`, stage_name: `S${n}`, mode: "all", required: 1, status, due_at: null, reminded_at: null, escalated_at: null, skip: [] });
  return { id: "apr_fixture00001", title: "Fixture claim", status: "in_review", revision: 1, state_version: 5, updated_at: 1791130949,
    template_id: "apt_fixture00001", template_version: 1, requester_id: "u_alice",
    definition: { fields: [{ id: "f_amount", label: "Amount", type: "money", currency: "USD", required: true, options: [] }],
      stages: [fixtureStage("s_one", "Fixture first"), fixtureStage("s_two", "Fixture gated"), fixtureStage("s_three", "Fixture third"), fixtureStage("s_four", "Fixture last", "any")],
      rules: { distinct_deciders: false } },
    snapshot, previous: null, people: { u_alice: "Alice Fixture", u_bob: "Bob Fixture" },
    revisions: [{ revision: 1, fields_hash: "h1", basis_hash: "h2", note, submitted_at: 1791130949, submitted_by: "u_alice" }],
    stages: [run(1, "approved"), run(2, "skipped"), run(3, "active")],
    tasks: [{ id: "atk_fixture00001", assignee_id: "u_bob", status: "pending", eligible: true, source: "explicit" }],
    permissions: { requester: false, manage: false, task_ids: [] }, decisions: [] };
}
const detail = (r = fixtureRequest()) => render("/src/apps/approvals/Pages.tsx", "RequestDetail", { ws, r, reload: () => {} });

const SERVER = join(import.meta.dirname, "..", "..", "synwevo_suite", "approvals");
const codes = (file: string, call: RegExp) => [...new Set([...readFileSync(join(SERVER, file), "utf8").matchAll(call)].map(m => m[1]))].sort();

/** The English of a source sentence, which must exist: a sentence left in Chinese is a missing entry. */
function english(source: string | undefined, label: string): string {
  assert.ok(source, `${label}: no sentence`);
  const en = translate("en", source!);
  assert.notEqual(en, source, `${label}: no English for “${source}”`);
  assert.notEqual(translate("zh-Hant", source!), "", label);
  return en;
}

test("every refusal the approvals service raises is a translated sentence on the page", () => {
  const raised = codes("service.py", /\bfail\("([a-z_]+)"/g);
  assert.ok(raised.includes("stage_needs_requester") && raised.length > 20, raised.join());
  for (const code of raised) english(text.errors[code], code);
});

test("every template problem and warning the definition reports is worded", () => {
  const reported = codes("definition.py", /\b_p\("([a-z_]+)"/g);
  const words = { ...text.problems, ...text.warnings };
  for (const code of reported) english(words[code], code);
  // A request a stage could never complete is refused up front; the editor says which stages.
  assert.match(english(text.errors.stage_needs_requester, "refusal"), /cannot approve their own requests/);
  assert.match(english(text.warnings.stage_needs_requester, "warning"), /refused/);
});

test("a stage on the approval path shows its own progress, not a template's status", async () => {
  // Acceptance: the stage in progress read 可用 / "Ready", the word for an active template.
  const lines = visible(await detail());
  const path = lines.slice(lines.indexOf("审批路径"), lines.indexOf("当前阶段参与人"));
  const badge = (stage: string) => path[path.indexOf(stage) + 2];
  assert.deepEqual(["Fixture first", "Fixture gated", "Fixture third", "Fixture last"].map(badge), ["已通过", "条件不符，已跳过", "进行中", "尚未开始"]);
  assert.ok(!path.includes(text.statuses.active), path.join(" | "));
  for (const status of ["active", "stalled", "approved", "rejected", "returned", "skipped", "cancelled", "not_started"]) {
    english(text.stageStatuses[status], status);
  }
  assert.equal(translate("en", text.stageStatuses.active), "In progress");
});

test("counted phrases choose their English plural from the count they show", async () => {
  // Acceptance: "Requires 1 approval votes" and "1 named approvers". The catalogue picks the form
  // from `count`, and these phrases were passed `{n}`, so English always took the plural.
  const dir = join(import.meta.dirname, "..", "src", "apps", "approvals");
  const source = readdirSync(dir).filter(f => /\.tsx?$/.test(f) && f !== "messages.ts").map(f => readFileSync(join(dir, f), "utf8")).join("\n");
  const counted = Object.entries(approvals as Record<string, { en: unknown }>).filter(([, v]) => typeof v.en !== "string").map(([k]) => k);
  assert.ok(counted.length >= 3, counted.join());
  for (const key of counted) {
    assert.match(key, /\{count\}/, `${key}: a counted phrase shows {count}`);
    const quoted = JSON.stringify(key).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const calls = [...source.matchAll(new RegExp(`\\bt\\(${quoted}\\s*(,\\s*\\{[^}]*\\})?\\)`, "g"))].map(m => m[0]);
    assert.ok(calls.length, `${key}: not used`);
    for (const call of calls) assert.match(call, /\bcount:/, call);
  }
  assert.equal(translate("en", "需要 {count} 票同意", { count: 1 }), "Requires 1 approval vote");
  assert.equal(translate("en", "需要 {count} 票同意", { count: 2 }), "Requires 2 approval votes");
  assert.equal(translate("en", "指定 {count} 人", { count: 1 }), "1 named approver");
  assert.equal(translate("en", "共 {count} 项", { count: 1 }), "1 item");
  assert.equal(translate("zh-Hant", "指定 {count} 人", { count: 3 }), "指定 3 人");
  // The page fills the phrase it renders.
  const lines = visible(await detail());
  assert.ok(lines.includes("需要 1 票同意"), lines.join(" | "));
  assert.deepEqual(lines.filter(x => /\{[a-z]+\}/.test(x)), []);
});

test("a separator joins two things, never one and nothing", async () => {
  // Acceptance: a revision without a note read "2026年10月5日 00:21 ·".
  const stray = (lines: string[]) => lines.filter(x => /^·|·$/.test(x));
  let lines = visible(await detail(fixtureRequest("")));
  assert.deepEqual(stray(lines), []);
  lines = visible(await detail(fixtureRequest("Fixture revision note")));
  assert.ok(lines.some(x => / · Fixture revision note$/.test(x)), lines.join(" | "));
  assert.deepEqual(stray(lines), []);
  // A template problem or a refused field with nothing to name is listed without a separator.
  const template = { id: "apt_fixture00001", title: "Fixture template", description: "", status: "active", draft_rev: 1, published_version: null,
    draft: { fields: [], stages: [{ ...fixtureStage("s_one", "Fixture gated"), condition: { match: "all", rules: [{ field: "f_x", op: "filled" }] } }], rules: { distinct_deciders: false } },
    problems: [{ code: "no_unconditional_stage", message: "x" }, { code: "condition_unknown_field", message: "x", stage: "s_one", field: "f_x" }], warnings: [], versions: [] };
  lines = visible(await render("/src/apps/approvals/Editor.tsx", "Edit", { ws: { ...ws, role: "admin" }, template, reload: () => {} }));
  assert.ok(lines.includes("至少有一个阶段必须始终执行") && lines.includes("Fixture gated · 条件引用的字段不存在"), lines.join(" | "));
  assert.deepEqual(stray(lines), []);
  const { ApiError } = await vite.ssrLoadModule("@synwevo/auth-client");
  const error = new ApiError(422, "not_publishable", "fixture", { problems: template.problems });
  lines = visible(await render("/src/apps/approvals/common.tsx", "ErrorBox", { error }));
  assert.ok(lines.includes("至少有一个阶段必须始终执行") && lines.includes("f_x · 条件引用的字段不存在"), lines.join(" | "));
  assert.deepEqual(stray(lines), []);
});
