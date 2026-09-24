// Source guards for rules that are easy to break one call site at a time.
// Run: npm test -w @synwevo/docs-web
//
//  1. Every keyboard handler that acts on Enter or Escape checks isImeKey() first: with a Chinese
//     input method, Enter confirms a candidate and Escape cancels one (MDN keydown guidance).
//  2. No programmatic scroll passes behavior: "smooth" directly; scrollBehavior() honours
//     prefers-reduced-motion, a literal "smooth" bypasses it.
//  3. The command palette never lists machine-interface management (API keys, service accounts,
//     webhooks, ports, developer documentation): those are managed through the CLI only.
//  4. Values that come from uploaded files never reach a style background without cssColor().
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const ROOTS = [join(here, "..", "src"), join(here, "..", "..", "..", "..", "packages", "ui", "src")];

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p));
    else if (/\.(tsx?|ts)$/.test(name)) out.push(p);
  }
  return out;
}
const ALL = ROOTS.flatMap(files);

/** The balanced `{ ... }` / `( ... )` expression starting at `start` (which must be an opener). */
function balanced(src: string, start: number): string {
  const open = src[start], close = open === "{" ? "}" : ")";
  let depth = 0;
  for (let i = start; i < src.length; i++) {
    if (src[i] === open) depth++;
    else if (src[i] === close && --depth === 0) return src.slice(start, i + 1);
  }
  return src.slice(start);
}

/** Body of `const name = ...` (arrow or useCallback) in the same file, if defined there. */
function definition(src: string, name: string): string | null {
  const m = new RegExp(`const ${name}\\s*=\\s*`).exec(src);
  if (!m) return null;
  const from = m.index + m[0].length;
  const brace = src.indexOf("{", from);
  return brace < 0 ? null : balanced(src, brace);
}

test("every Enter / Escape keyboard handler checks isImeKey first", () => {
  const offenders: string[] = [];
  for (const f of ALL) {
    const src = readFileSync(f, "utf8");
    const handlers: { at: number; body: string }[] = [];
    for (const m of src.matchAll(/onKeyDown=\{/g)) {
      const expr = balanced(src, m.index! + "onKeyDown=".length);
      const ident = /^\{\s*([A-Za-z_$][\w$]*)\s*\}$/.exec(expr);
      handlers.push({ at: m.index!, body: ident ? definition(src, ident[1]) ?? expr : expr });
    }
    for (const m of src.matchAll(/addEventListener\("keydown",\s*([A-Za-z_$][\w$]*)/g)) {
      handlers.push({ at: m.index!, body: definition(src, m[1]) ?? "" });
    }
    for (const h of handlers) {
      if (!/"Enter"|"Escape"/.test(h.body)) continue;
      // a handler that only forwards to the shared modal hook is covered by the hook's own check
      if (/isImeKey\(/.test(h.body)) continue;
      const line = src.slice(0, h.at).split("\n").length;
      offenders.push(`${relative(join(here, ".."), f)}:${line}`);
    }
  }
  assert.deepEqual(offenders, [], `handlers without an IME guard:\n${offenders.join("\n")}`);
});

test('no literal behavior: "smooth" outside the reduced-motion helper', () => {
  const hits = ALL.filter((f) => !f.endsWith("a11y.ts"))
    .flatMap((f) => (/behavior:\s*["']smooth["']/.test(readFileSync(f, "utf8")) ? [relative(join(here, ".."), f)] : []));
  assert.deepEqual(hits, []);
});

test("the command palette lists no machine-interface management", () => {
  const src = readFileSync(join(here, "..", "src", "pages", "Workspace.tsx"), "utf8");
  const start = src.indexOf("const writeCommands");
  const end = src.indexOf("return (", start);
  assert.ok(start > 0 && end > start, "command list not found");
  const labels = [...src.slice(start, end).matchAll(/label:\s*(?:tr\()?(`[^`]*`|"[^"]*")/g)].map((m) => m[1]);
  assert.ok(labels.length > 10);
  const bad = labels.filter((l) => /API|api|[Ww]ebhook|密钥|开发者|端口|服务账号|令牌|token/.test(l));
  assert.deepEqual(bad, []);
});

test("file-derived colours go through cssColor before reaching a background", () => {
  const views = ["views/DocView.tsx", "views/GridView.tsx", "views/SlidesView.tsx", "pages/SheetEditor.tsx"];
  for (const v of views) {
    const src = readFileSync(join(here, "..", "src", v), "utf8");
    // the raw fields the renderers pass through from the file
    for (const raw of ["st.shd", "r.shd", "cell.shd", "st.bg", "s.bg", "r.hl"]) {
      const re = new RegExp(`background(?:Color)?\\s*[:=]\\s*${raw.replace(".", "\\.")}\\b`);
      assert.equal(re.test(src), false, `${v} puts ${raw} into a background unchecked`);
    }
  }
  const editor = readFileSync(join(here, "..", "src", "pages", "DocEditor.tsx"), "utf8");
  assert.match(editor, /cssColor\(a\.background\)/, "stored editor JSON cell background must be sanitised");
});
