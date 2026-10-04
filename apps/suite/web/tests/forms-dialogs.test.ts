/** A confirm dialog's action is never named like the dialog's own dismiss button (acceptance D6:
 *  关闭公开填写 offered two buttons named 关闭 — the ✕ and the destructive action — so a screen
 *  reader, or anyone reading the buttons by name, could not tell which one turns the link off). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const UI = join(import.meta.dirname, "..", "..", "..", "..", "packages", "ui", "src", "ui.tsx");
const FORMS = join(import.meta.dirname, "..", "src", "apps", "forms");

test("no Forms confirm action shares its name with the dialog's dismiss button", () => {
  // The dialogs' ✕ as packages/ui names it.
  const dismiss = new Set([...readFileSync(UI, "utf8").matchAll(/onClick=\{onClose\} aria-label=\{tr\("([^"]+)"\)\}/g)].map((m) => m[1]));
  assert.ok(dismiss.size > 0, "found the dismiss button's name");
  const actions: string[] = [];
  for (const f of readdirSync(FORMS).filter((x) => x.endsWith(".tsx"))) {
    const src = readFileSync(join(FORMS, f), "utf8");
    // dialog.confirm(title, message, action, …) and the [title, message, action] tuples handed to it.
    for (const m of src.matchAll(/(?:dialog\.confirm\(|\[)\s*t\("[^"]+"(?:,\s*\{[^}]*\})?\)\s*,\s*t\("[^"]+"\)\s*,\s*t\("([^"]+)"\)/g)) {
      actions.push(`${f}: ${m[1]}`);
    }
  }
  assert.ok(actions.length >= 5, `found the confirm actions: ${actions.join(" | ")}`);
  assert.deepEqual(actions.filter((a) => dismiss.has(a.split(": ")[1])), []);
});
