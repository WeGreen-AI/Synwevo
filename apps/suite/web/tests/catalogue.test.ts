import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { validateMessages } from "@synwevo/ui/i18n-core";

const SRC = join(import.meta.dirname, "..", "src");
function walk(dir: string, out: string[] = []): string[] {
  for (const f of readdirSync(dir)) { const p = join(dir, f); if (statSync(p).isDirectory()) walk(p, out); else if (/\.tsx?$/.test(p)) out.push(p); }
  return out;
}

/** Every catalogue of the shell: the shared one and each application's `messages.ts`. */
async function catalogues(): Promise<Record<string, any>[]> {
  const out: Record<string, any>[] = [];
  for (const f of walk(SRC)) {
    if (!/[/\\]messages\.ts$/.test(f)) continue;
    const mod = await import(f);
    for (const v of Object.values(mod)) if (v && typeof v === "object") out.push(v as Record<string, any>);
  }
  return out;
}

test("every catalogue entry has en and zh-Hant with the same parameters", async () => {
  for (const cat of await catalogues()) assert.deepEqual(validateMessages(cat), []);
});

test("every Chinese string literal in the shell is a catalogue key", async () => {
  const keys = new Set<string>();
  for (const cat of await catalogues()) for (const k of Object.keys(cat)) keys.add(k);
  const missing: string[] = [];
  for (const f of walk(SRC)) {
    if (/[/\\]messages\.ts$/.test(f)) continue;
    const src = readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    for (const m of src.matchAll(/"([^"\\\n]*[㐀-鿿][^"\\\n]*)"/g)) if (!keys.has(m[1])) missing.push(`${f}: ${m[1]}`);
  }
  assert.deepEqual(missing, []);
});
