/** The consent page's note on a self-described client (a Client ID Metadata Document, docs/platform/mcp.md
 *  §3.3): it names where the document is published as the host of the client id — the address Synwevo
 *  fetched it from — never the document's own `client_uri`, which is an unchecked claim. A document at
 *  evil.example naming https://claude.ai as its home page used to be shown as "published at claude.ai".
 *
 *  Rendered the way the browser renders it (vite's module graph, React's server renderer) from the page
 *  module itself (pages/OAuthConsent.tsx); the wording in the other two locales is read from the
 *  catalogue that module registers.
 *  FIXTURE: synthetic client records as provider_routes.consent_request returns them; no API, no user
 *  data. Run from the package: `npm test`. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { testServer } from "./_vite.mjs";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(WEB + "package.json");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

let server, ui, page;
before(async () => {
  server = await testServer({
    root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  page = await server.ssrLoadModule("/src/pages/OAuthConsent.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

// The server renderer answers React's locale store with its server snapshot (zh-Hans); the other two
// locales are read from the same catalogue the page registered, with the same parameters.
const render = (client) => renderToStaticMarkup(React.createElement(page.SelfDescribedNotice, { client }))
  .replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
const NAMED = "这个客户端用它自己发布在 {host} 的文档描述自己。Synwevo 读取并检查了这份文档，但没有核实是谁在运营它。";
const UNNAMED = "这个客户端用它自己发布的文档描述自己。Synwevo 读取并检查了这份文档，但没有核实是谁在运营它。";

// FIXTURE: client records in the consent request's shape (the reserved .test / .invalid domains, and
// the name and home page an impersonating document would claim).
const IMPERSONATOR = { id: "https://evil.fixture.invalid/c.json", name: "Claude", app_id: "platform",
  registration: "cimd", client_uri: "https://claude.ai" };
const HONEST = { id: "https://client.fixture.test/oauth/client.json", name: "Fixture MCP Client", app_id: "platform",
  registration: "cimd", client_uri: "https://client.fixture.test" };
const OPERATOR = { id: "mcp-pre", name: "Pre-registered client", app_id: "platform", registration: "operator", client_uri: null };

test("the note names the host the document was fetched from, not the home page it claims", () => {
  const html = render(IMPERSONATOR);
  assert.ok(html.includes("evil.fixture.invalid") && !html.includes("claude.ai") && !html.includes("{host}"), html);
  assert.equal(page.publishedAt(IMPERSONATOR), "evil.fixture.invalid");
  assert.equal(page.publishedAt(HONEST), "client.fixture.test");
  for (const locale of ["en", "zh-Hant"]) {
    const sentence = ui.translate(locale, NAMED, { host: page.publishedAt(IMPERSONATOR) });
    assert.ok(sentence.includes("evil.fixture.invalid") && !sentence.includes("{host}") && sentence !== NAMED, `${locale}: ${sentence}`);
  }
});

test("the note is a translated sentence in each locale and says nobody verified the operator", () => {
  const zh = render(HONEST);
  assert.ok(zh.includes("自描述客户端") && zh.includes("发布在 client.fixture.test 的文档") && zh.includes("没有核实"), zh);
  const en = ui.translate("en", NAMED, { host: "client.fixture.test" });
  assert.ok(en.includes("publishes at client.fixture.test") && en.includes("did not verify") && !/[\u3400-\u9fff]/.test(en), en);
  assert.equal(ui.translate("en", "自描述客户端"), "Self-described client");
  const hant = ui.translate("zh-Hant", NAMED, { host: "client.fixture.test" });
  assert.ok(hant.includes("發佈在 client.fixture.test 的文件"), hant);
  assert.equal(ui.translate("zh-Hant", "自描述客户端"), "自描述客戶端");
  for (const locale of ["en", "zh-Hant"]) assert.notEqual(ui.translate(locale, UNNAMED), UNNAMED, locale);
});

test("an id that names no host is not described by its home page either", () => {
  const html = render({ ...IMPERSONATOR, id: "not a url" });
  assert.ok(html.includes(UNNAMED) && !html.includes("claude.ai"), html);
  assert.equal(page.publishedAt({ ...IMPERSONATOR, id: "not a url" }), null);
});

test("a client an operator registered gets no provenance note", () => {
  assert.equal(render(OPERATOR), "");
  assert.equal(page.publishedAt(OPERATOR), null);
});
