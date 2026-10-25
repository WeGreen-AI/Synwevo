/** B3: what the conversation list says about the reader's organizations, from what the server
 *  answers (`disabled_organizations`, `platform_disabled`, `messages_enabled`, the lifecycle), and
 *  which refusals close a conversation as a whole. Fixture organizations only; no API. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError } from "@synwevo/auth-client/api";
import { translate } from "@synwevo/ui/i18n-core";
import { messages } from "../src/messages.messages.ts";
import { ORG_OFF, ORG_OFF_UNNAMED, ORG_PAUSED, PLATFORM_OFF, availabilityNotices, closedRefusal, lost, usable } from "../src/lib/availability.ts";
import { mayRetry } from "../src/lib/objects.ts";

const NOW = 1_800_000_000;
const A = { id: "org_fx_a", name: "Fixture A", status: "active", recover_until: null, messages_enabled: false };
const B = { id: "org_fx_b", name: "Fixture B", status: "active", recover_until: null, messages_enabled: true };
const FROZEN = { id: "org_fx_frozen", name: "Fixture Frozen", status: "frozen", recover_until: null, messages_enabled: true };
const WINDOW = { id: "org_fx_window", name: "Fixture Window", status: "deleted", recover_until: NOW + 3600, messages_enabled: true };
const GONE = { id: "org_fx_gone", name: "Fixture Gone", status: "deleted", recover_until: NOW - 1, messages_enabled: true };
const LEGACY = { id: "org_fx_legacy", name: "Fixture Legacy", status: "deleted", recover_until: null, messages_enabled: false };

test("an organization switched off is named; B is not; a paused one says it is paused; a lost one says nothing", () => {
  const notices = availabilityNotices([A, B, FROZEN, WINDOW, GONE, LEGACY], { disabled_organizations: [A.id, LEGACY.id], platform_disabled: false }, NOW);
  assert.deepEqual(notices, [
    { key: `off:${A.id}`, text: ORG_OFF, params: { name: "Fixture A" } },
    { key: `paused:${FROZEN.id}`, text: ORG_PAUSED, params: { name: "Fixture Frozen" } },
    { key: `paused:${WINDOW.id}`, text: ORG_PAUSED, params: { name: "Fixture Window" } },
  ]);
  assert.deepEqual(availabilityNotices([B], { disabled_organizations: [] }, NOW), [], "nothing to say when everything is open");
});

test("switched off outranks paused, the platform switch outranks everything, and an unnamed organization gets the server's sentence", () => {
  const frozenOff = { ...FROZEN, messages_enabled: false };
  assert.deepEqual(availabilityNotices([frozenOff], { disabled_organizations: [frozenOff.id] }, NOW).map(n => n.text), [ORG_OFF]);
  assert.deepEqual(availabilityNotices([A, B, FROZEN], { disabled_organizations: [A.id, B.id, FROZEN.id], platform_disabled: true }, NOW),
    [{ key: "platform", text: PLATFORM_OFF }]);
  // the list answered before the organizations: the organization cannot be named yet
  assert.deepEqual(availabilityNotices([], { disabled_organizations: [A.id] }, NOW), [{ key: "off:unnamed", text: ORG_OFF_UNNAMED }]);
  // an older server without the list fields: the organizations' own flag still says it
  assert.deepEqual(availabilityNotices([A, B], {}, NOW).map(n => n.key), [`off:${A.id}`]);
  assert.deepEqual(availabilityNotices([A, B], null, NOW).map(n => n.key), [`off:${A.id}`]);
});

test("a conversation is started only in an organization that is active with Message on", () => {
  assert.deepEqual([A, B, FROZEN, WINDOW].filter(usable).map(o => o.id), [B.id]);
  assert.ok(usable({ status: "active" }), "an older server without the flag: on");
  assert.ok(lost(GONE, NOW) && lost(LEGACY, NOW) && !lost(WINDOW, NOW) && !lost(FROZEN, NOW));
});

test("the refusals that close a conversation as a whole, and they are not offered a retry on the object page", () => {
  for (const code of ["messages_disabled", "organization_unavailable", "workspace_unavailable"]) {
    const e = new ApiError(code === "messages_disabled" ? 403 : 409, code, "");
    assert.ok(closedRefusal(e), code);
    assert.ok(!mayRetry(e), `${code}: trying again does not change it`);
  }
  assert.ok(!closedRefusal(new ApiError(403, "messages_disabled", "", undefined, true)), "an intermediary's 403 is not the application's");
  assert.ok(!closedRefusal(new ApiError(403, "blocked", "")) && !closedRefusal(new Error("offline")));
});

test("every notice and every closing refusal reads in English and Traditional Chinese", () => {
  const sentences = [PLATFORM_OFF, ORG_OFF, ORG_OFF_UNNAMED, ORG_PAUSED, "该组织当前不可用，会话已暂停", "项目所在的工作空间当前不可用，讨论已暂停"];
  for (const s of sentences) {
    assert.ok(messages[s as keyof typeof messages], `catalogued: ${s}`);
    for (const locale of ["en", "zh-Hant"] as const) {
      const worded = translate(locale, s, { name: "Fixture A" });
      assert.ok(worded && worded !== s || locale === "zh-Hant" && s === PLATFORM_OFF, `${locale}: ${s}`);
      if (locale === "en") assert.ok(!/[㐀-鿿]/.test(worded), `${s}: ${worded}`);
    }
  }
  assert.equal(translate("en", ORG_OFF, { name: "Fixture A" }), "Synwevo Message is turned off for “Fixture A”, so its conversations are unavailable for now");
});
