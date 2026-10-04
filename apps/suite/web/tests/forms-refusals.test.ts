/** Every refusal Synwevo Forms' server can send reaches a person as a sentence in their language,
 *  never as "请求未完成（code）" (acceptance D5: link_not_found on the fill page, invalid_input in
 *  the editor). The codes are read from the server's own sources, so a new refusal without a
 *  sentence fails here. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { translate } from "@synwevo/ui/i18n-core";
import "../src/apps/forms/messages.ts";
import { failureText } from "../src/apps/forms/text.ts";

const SERVER = join(import.meta.dirname, "..", "..", "synwevo_suite", "forms");
const codes = new Set<string>(["invalid_input"]);          // the operation layer's, for every operation
for (const f of readdirSync(SERVER)) {
  if (!f.endsWith(".py")) continue;
  for (const m of readFileSync(join(SERVER, f), "utf8").matchAll(/ServiceError\(\s*\d+\s*,\s*"([a-z_]+)"/g)) codes.add(m[1]);
}

for (const locale of ["en", "zh-Hant", "zh-Hans"] as const) {
  const say = (k: string, p?: Record<string, any>) => translate(locale, k, p);
  test(`every Forms refusal is a sentence (${locale})`, () => {
    assert.ok(codes.size > 40, "the server's refusals were read");
    for (const code of codes) {
      // What the shared client would show for an uncatalogued code.
      const raw = say("请求未完成（{code}）。", { code });
      const shown = failureText(say, { code, message: raw });
      assert.notEqual(shown, raw, code);
      assert.ok(!shown.includes(code), `${code}: ${shown}`);
    }
  });
}

test("the two the acceptance met read as what happened", () => {
  const en = (k: string, p?: Record<string, any>) => translate("en", k, p);
  assert.equal(failureText(en, { code: "link_not_found" }), "This link does not exist or no longer works. Ask the sender for a new one.");
  assert.equal(failureText(en, { code: "invalid_input", detail: { fields: [{ loc: ["recipient", "email"] }] } }),
               "The recipient's email address is not valid");
  // Anything else keeps the client's own wording (already translated, with its request id).
  assert.equal(failureText(en, { code: "network", message: "Network connection failed." }), "Network connection failed.");
});
