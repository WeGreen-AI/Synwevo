/** A form's fill-in link is opened by people without an account. Its page must not ask the
 *  signed-in questions — who is signed in (`SessionProvider`: GET /api/auth/me) and which
 *  applications they may open (`CatalogProvider`: GET /api/suite/catalog) — which can only be
 *  refused for them, as a 401 in their console on every load (acceptance D7).
 *
 *  The shell's real tree (src/App.tsx, loaded through Vite like the browser loads it) is walked
 *  from the root to each route: the fill-in route must have neither provider above it, and a
 *  workspace page must have both. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { isValidElement, type ReactElement } from "react";
import { createServer, type ViteDevServer } from "vite";

const WEB = fileURLToPath(new URL("../", import.meta.url));
let server: ViteDevServer;
let mod: { app: any; client: any; catalog: any };

before(async () => {
  server = await createServer({ root: WEB, configFile: false, logLevel: "error", appType: "custom",
                                server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  mod = { app: await server.ssrLoadModule("/src/App.tsx"), client: await server.ssrLoadModule("@synwevo/auth-client"),
          catalog: await server.ssrLoadModule("/src/shared/catalog.tsx") };
});
after(async () => { await server?.close(); });

/** Every route element with the element types above it. Expands children, a route's `element`,
 *  and the shell's own signed-in tree (a plain function of no state). */
function routes(node: unknown, above: unknown[] = [], out: { path: string; above: unknown[] }[] = []) {
  if (Array.isArray(node)) { for (const n of node) routes(n, above, out); return out; }
  if (!isValidElement(node)) return out;
  const el = node as ReactElement<any>;
  if (typeof el.props.path === "string") out.push({ path: el.props.path, above });
  const inner = [...above, el.type];
  if (el.type === mod.app.SignedInShell) routes((el.type as () => unknown)(), inner, out);
  routes(el.props.element, inner, out);
  routes(el.props.children, inner, out);
  return out;
}

test("the fill-in link is rendered without the session and the catalogue", () => {
  const all = routes(mod.app.App());
  const signedIn = [mod.client.SessionProvider, mod.catalog.CatalogProvider];
  const fill = all.filter((r) => r.path === "/forms/fill/:token");
  assert.equal(fill.length, 1, "one fill-in route");
  for (const p of signedIn) assert.ok(!fill[0].above.includes(p), `${p.name} is above the fill-in page`);
  // …while the workspace pages keep both.
  const forms = all.find((r) => r.path === "/forms/w/:wid/*");
  assert.ok(forms, "the workspace route is there");
  for (const p of signedIn) assert.ok(forms.above.includes(p), `${p.name} is missing above the workspace pages`);
});
