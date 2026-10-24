/** Send-and-go (2026-10-23): every refusal that can also stop a running answer is worded so it is
 *  true mid-run ("no new model call was made … nothing was charged for it"), the next steps follow
 *  the shared error-code table (design §3), and the organization-limit sentence no longer claims a
 *  per-workspace limit (money-3). Explicit fixtures only; no server is involved. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { testServer } from './_vite.ts';

let server: any, core: any, copy: any, refusals: any;
before(async () => {
  server = await testServer();
  core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  [copy, refusals] = await Promise.all([server.ssrLoadModule('/src/apps/agent/chat.messages.ts'), server.ssrLoadModule('/src/apps/agent/refusals.ts')]);
});
after(async () => { await server?.close(); });

/** Kinds a model call of a running answer can end with (design §4 B test 6). */
const MID_RUN = ['funds', 'org_funds', 'frozen', 'org_frozen', 'org_limit', 'org_payer', 'limit', 'brake', 'suspended', 'breaker',
  'allowlist', 'call_cap', 'access_suspended', 'price_changed', 'quote', 'run_calls', 'run_budget', 'run_time'] as const;
/** The form in each language; a short qualifier ("at the old price") may sit between "no" and "new model call". */
const FORM = {
  'zh-Hans': /没有.{0,6}发起新的模型调用[\s\S]*也没有为此扣费/,
  en: /no new model call was made[\s\S]*nothing was charged for it/i,
  'zh-Hant': /沒有.{0,6}發起新的模型呼叫[\s\S]*也沒有為此扣費/,
} as const;

test('every refusal that can stop a running answer says no new model call was made and nothing was charged for it, in three languages', () => {
  for (const kind of MID_RUN) {
    const key = refusals.REFUSAL_TEXT[kind];
    assert.ok(key, `kind ${kind} has a sentence`);
    const source = copy.chatCopy[key][0];
    assert.match(source, FORM['zh-Hans'], `${kind} zh-Hans`);
    assert.match(core.translate('en', source), FORM.en, `${kind} en`);
    assert.match(core.translate('zh-Hant', source), FORM['zh-Hant'], `${kind} zh-Hant`);
  }
});

test('the organization limit refusal is about the member\'s monthly allowance only, never a workspace limit', () => {
  const [source, en, hant] = copy.chatCopy.refusedOrgLimit;
  assert.doesNotMatch(source, /工作空间/);
  assert.doesNotMatch(en, /workspace/i);
  assert.doesNotMatch(hant, /工作空間/);
  assert.match(source, /本月额度已用完/);
  assert.equal(refusals.refusalOf({ code: 'billing_org_workspace_monthly_limit_exceeded' }).text, 'refusedOrgLimit');
});

test('error codes map to the refusal kinds and next steps of the send-and-go contract', () => {
  const table: [string, string, string | null][] = [
    ['provider_consent_required', 'consent', 'setup'],
    ['provider_consent_version_changed', 'consent_changed', 'setup'],
    ['terms_acceptance_required', 'terms', 'setup'],
    ['billing_payer_budget_not_configured', 'no_limit', 'setup'],
    ['billing_payer_budget_disabled', 'limit', 'settings'],
    ['billing_payer_daily_budget_exceeded', 'limit', 'settings'],
    ['billing_payer_weekly_budget_exceeded', 'limit', 'settings'],
    ['billing_payer_monthly_budget_exceeded', 'limit', 'settings'],
    ['billing_run_cap_below_call_hold', 'run_cap', 'settings'],
    ['billing_run_cap_unit_changed', 'run_cap', 'settings'],
    ['billing_org_payer_not_granted', 'org_payer', 'personal'],
    ['billing_org_payer_not_member', 'org_payer', 'personal'],
    ['billing_org_payer_inconsistent', 'org_payer', 'personal'],
    ['billing_invalid_payer', 'org_payer', 'personal'],
    ['billing_payer_forbidden', 'org_payer', 'personal'],
    ['billing_organization_unavailable', 'org_payer', 'personal'],
    ['billing_organization_billing_not_configured', 'org_payer', 'personal'],
    ['billing_organization_billing_not_supported', 'org_payer', 'personal'],
    ['billing_route_confirmation_expired', 'breaker', null],
    ['billing_model_configuration_unavailable', 'breaker', null],
    ['billing_model_route_unavailable', 'not_configured', null],
    ['billing_not_configured', 'not_configured', null],
    ['billing_model_configuration_changed', 'price_changed', 'retry'],
    // Addendum AD-1: a send carrying a price the page no longer shows is refused before anything is stored.
    ['billing_price_changed', 'price_changed', 'retry'],
    ['billing_consent_model_changed', 'price_changed', 'retry'],
    ['billing_price_not_applicable', 'price_changed', 'retry'],
    ['billing_price_route_mismatch', 'price_changed', 'retry'],
    ['billing_price_not_published', 'price_changed', 'retry'],
    ['billing_quote_expired_or_revoked', 'quote', 'retry'],
    ['billing_quote_context_changed', 'quote', 'retry'],
    ['billing_quote_already_used', 'quote', 'retry'],
    ['billing_quote_not_found', 'quote', 'retry'],
    ['billing_quote_conversation_unavailable', 'quote', 'retry'],
    ['billing_run_time_too_short', 'run_time', 'retry'],
    ['billing_run_budget_exceeded', 'run_budget', 'settings'],
    ['billing_run_call_limit_exceeded', 'run_calls', null],
    ['billing_insufficient_funds', 'funds', 'topup'],
    ['billing_wallet_frozen', 'frozen', 'wallet'],
    ['billing_risk_breaker_open', 'breaker', null],
    ['billing_risk_payer_brake', 'brake', null],
    ['billing_risk_not_allowlisted', 'allowlist', null],
    ['billing_cost_call_limit_exceeded', 'call_cap', null],
    ['agent_access_suspended', 'access_suspended', null],
  ];
  for (const [code, kind, next] of table) {
    const refusal = refusals.refusalOf({ code });
    assert.equal(refusal?.kind, kind, code);
    assert.equal(refusal?.next, next, code);
  }
  // The old per-send steps are gone: nothing asks for a quote or a re-confirmation any more.
  const steps = new Set(Object.values(refusals.REFUSAL_NEXT ?? {}));
  assert.ok(!steps.has('requote') && !steps.has('reconfirm') && !steps.has('consent'), [...steps].join(','));
  assert.ok(steps.has('retry') && steps.has('setup'));
  for (const gone of ['nextRequote', 'nextReconfirm', 'nextConsent']) assert.ok(!(gone in copy.chatCopy), gone);
  for (const key of ['nextRetry', 'nextRetryNewPrice', 'nextSetup']) assert.ok(key in copy.chatCopy, key);
  // Refused at the send (AD-1): the sentence says the message was not sent, in three languages.
  assert.equal(refusals.refusalOf({ code: 'billing_price_changed' }).text, 'refusedPriceUpdated');
  const [zh, en, hant] = copy.chatCopy.refusedPriceUpdated;
  assert.equal(zh, '价格或模型已更新，本次没有发送，也没有扣费。请查看新价格后重新发送。');
  assert.equal(core.translate('en', zh), en); assert.equal(core.translate('zh-Hant', zh), hant);
  assert.match(en, /not sent and nothing was charged/); assert.match(hant, /本次沒有傳送，也沒有扣費/);
  assert.doesNotMatch(zh + en + hant, /确认|確認|confirm/i);
  // The authorization conflicts are generic errors, not refusals with a next step.
  assert.equal(refusals.refusalOf({ code: 'billing_authorization_conflict' }), null);
  assert.equal(refusals.refusalOf({ code: 'billing_authorization_required' }), null);
});

test('the new and changed refusals carry the agreed wording in three languages', () => {
  const expected: Record<string, [RegExp, RegExp, RegExp]> = {
    refusedTerms: [/《服务条款》或《定价》已更新/, /Terms of service or the Pricing has been updated/, /《服務條款》或《定價》已更新/],
    refusedRunCap: [/低于一次模型调用所需的预留金额/, /below what one model call needs to hold/, /低於一次模型呼叫所需的預留金額/],
    refusedRunTime: [/已用完单次运行时限/, /used up its run time/, /已用完單次執行時限/],
    refusedNoLimit: [/需要设置个人每日、每周、每月支出上限/, /set your daily, weekly and monthly spending limits/, /需要設定個人每日、每週、每月支出上限/],
    refusedPriceChanged: [/可以按新价格重新发送/, /send again at the new price/, /可以按新價格重新傳送/],
    refusedQuote: [/运行授权已失效/, /run authorization is no longer valid/, /執行授權已失效/],
    refusedRunCalls: [/模型调用次数上限/, /model-call limit for one answer/, /模型呼叫次數上限/],
    refusedRunBudget: [/每条回答的最高金额/, /reached its maximum amount/, /每則回答的最高金額/],
  };
  for (const [key, [zh, en, hant]] of Object.entries(expected)) {
    const [source, english, traditional] = copy.chatCopy[key];
    assert.match(source, zh, key); assert.match(english, en, key); assert.match(traditional, hant, key);
    assert.equal(core.translate('en', source), english, key);
    assert.equal(core.translate('zh-Hant', source), traditional, key);
  }
  // No refusal asks the person to re-confirm a run time, a price or a quote.
  for (const [key, [source, en]] of Object.entries(copy.chatCopy) as [string, string[]][]) {
    if (!key.startsWith('refused')) continue;
    assert.doesNotMatch(source, /重新确认|重新获取报价/, key);
    assert.doesNotMatch(en, /confirm again|new quote|re-?confirm/i, key);
  }
});
