/** The DeepSeek data-sharing consent section (ProviderConsent.tsx): its notice exists in all three
 *  languages with the same parameters, names DeepSeek in each, and states the facts the privacy
 *  page records (docs/platform/public-pages.md §2.9) — storage in China and possible model
 *  improvement under DeepSeek's own policy — without claiming a partnership. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { validateMessages } from "@synwevo/ui/i18n-core";
import { providerConsentCopy, providerConsentMessages } from "../src/apps/agent/provider-consent.messages.ts";

test("every consent sentence has en and zh-Hant with the same parameters", () => {
  assert.deepEqual(validateMessages(providerConsentMessages), []);
  assert.equal(new Set(Object.values(providerConsentCopy).map(([zh]) => zh)).size, Object.keys(providerConsentCopy).length);
});

test("the notice names DeepSeek, storage in China and possible model improvement in each language", () => {
  for (const index of [0, 1, 2]) {
    assert.match(providerConsentCopy.title[index], /DeepSeek/);
    assert.match(providerConsentCopy.locationCN[index], /中国|China|中國/);
    assert.match(providerConsentCopy.trainingPolicy[index], /DeepSeek/);
    for (const [, ...langs] of Object.values(providerConsentCopy))
      for (const text of [langs[0], langs[1]]) assert.doesNotMatch(text, /partner|合作伙伴|合作夥伴/i);
  }
});

test("the settings page asks for this consent before anything else", () => {
  const settings = readFileSync(join(import.meta.dirname, "..", "src", "apps", "agent", "Settings.tsx"), "utf8");
  const body = settings.slice(settings.indexOf("export default function Settings"));
  assert.ok(body.indexOf("<ProviderConsent />") > 0 && body.indexOf("<ProviderConsent />") < body.indexOf("<AccessSettings />"));
  const section = readFileSync(join(import.meta.dirname, "..", "src", "apps", "agent", "ProviderConsent.tsx"), "utf8");
  assert.match(section, /id="ag-provider-consent"/);
  assert.match(section, /agent\.provider_consent\.accept/);
  assert.match(section, /version: state\.version/);   // accepts exactly the version shown
});
