/** Approvals wording a person meets: refusals and template warnings from the server, worded in
 *  every locale. Fixtures are isolated: no API, no user data. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { translate } from "@synwevo/ui/i18n-core";
import "../src/apps/approvals/messages.ts";
import * as text from "../src/apps/approvals/text.ts";

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

