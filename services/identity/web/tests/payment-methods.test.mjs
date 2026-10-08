/** Isolated front-end contract/markup/event-handler fixtures. No account, backend or payment. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

let server, methods, payments, wallet, ui;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    logLevel: 'error', server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  methods = await server.ssrLoadModule('/src/lib/paymentMethods.ts');
  payments = await server.ssrLoadModule('/src/governance/PaymentsTab.tsx');
  wallet = await server.ssrLoadModule('/src/pages/account/walletModel.ts');
  ui = await server.ssrLoadModule('@synwevo/ui');
  await server.ssrLoadModule('/src/pages/account/wallet.messages.ts');
  await server.ssrLoadModule('/src/pages/account/topup.messages.ts');
  await server.ssrLoadModule('/src/org-billing.messages.ts');
});
after(async () => { await server?.close(); });

const legacy = { enabled: true, operator_name: 'Fixture merchant', packages: [{ id: 'fixture', amount_minor: 1000, label: 'Fixture' }],
  custom_amount: false, min_amount_minor: 1000, max_amount_minor: 1000,
  velocity: { max_orders_per_hour: 5, max_orders_per_day: 20, max_pending: 3, max_amount_per_day_minor: 100000 } };

test('new forms select nothing; only an existing spec without the field selects the legacy three', () => {
  for (const configured of [true, false]) {
    assert.deepEqual(payments.formOf(null, configured).paymentMethods, []);
    assert.deepEqual(payments.formOf(legacy, configured).paymentMethods, ['card', 'wechat_pay', 'alipay']);
    assert.deepEqual(payments.formOf({ ...legacy, payment_methods: ['alipay'] }, configured).paymentMethods, ['alipay']);
  }
  assert.deepEqual(methods.configuredPaymentMethods(null), []);
  assert.deepEqual(methods.configuredPaymentMethods({ payment_methods: [] }), []);
  assert.equal(Object.hasOwn(legacy, 'payment_methods'), false, 'reading must not rewrite a historical version');
});

test('even a closed configuration needs a channel; each nonempty subset round-trips in canonical order', () => {
  const form = payments.formOf(legacy, true);
  for (const enabled of [true, false]) {
    const result = payments.specOf({ ...form, enabled, paymentMethods: [] });
    assert.equal(result.spec, undefined);
    assert.equal(result.problem, '请至少选择一种支付方式。');
  }
  for (let mask = 1; mask < 8; mask++) {
    const selected = methods.PAYMENT_METHODS.filter((_, i) => mask & (1 << i));
    const result = payments.specOf({ ...form, paymentMethods: [...selected].reverse() });
    assert.equal(result.problem, undefined);
    assert.deepEqual(result.spec.payment_methods, selected);
    assert.deepEqual(payments.formOf(result.spec, true).paymentMethods, selected);
    assert.deepEqual({ ...result.spec, payment_methods: undefined }, { ...legacy, payment_methods: undefined });
  }
  assert.deepEqual(payments.specOf({ ...form, paymentMethods: ['alipay', 'card', 'alipay'] }).spec.payment_methods, ['card', 'alipay']);
});

function descendants(tree) {
  return React.Children.toArray(tree).flatMap(node => React.isValidElement(node) ? [node, ...descendants(node.props.children)] : []);
}

test('labelled native checkboxes toggle independently and send unique channels in canonical order', () => {
  let selected = [], tree;
  const draw = (invalid = false) => renderToStaticMarkup(React.createElement(function Fixture() {
    tree = payments.PaymentMethodChoices({ selected, onChange: next => { selected = next; }, invalid });
    return tree;
  }));
  let html = draw();
  assert.match(html, /<fieldset/);
  assert.match(html, /<legend>支付方式（至少选择一项）<\/legend>/);
  assert.match(html, /仅选择商户已开通的支付方式，选择不代表渠道资格已验证/);
  assert.match(html, /min-width:0/);
  assert.equal((html.match(/type="checkbox"/g) || []).length, 3);
  assert.doesNotMatch(html, /checked=""|tabindex="-1"/);
  for (const id of tree.props['aria-describedby'].split(' ')) assert.ok(descendants(tree).some(node => node.props.id === id));
  const toggle = (value, checked) => {
    descendants(tree).find(node => node.type === 'input' && node.props.value === value).props.onChange({ target: { checked } });
    html = draw();
  };
  toggle('alipay', true); toggle('card', true); toggle('card', true);
  assert.deepEqual(selected, ['card', 'alipay']);
  toggle('card', false);
  assert.deepEqual(selected, ['alipay']);
  assert.equal((html.match(/checked=""/g) || []).length, 1);
  toggle('alipay', false);
  assert.deepEqual(selected, []);
  assert.match(draw(true), /aria-invalid="true"/);
});

test('published and confirmation summaries distinguish new subsets, legacy specs and no configuration', () => {
  const render = spec => renderToStaticMarkup(React.createElement('dl', null, React.createElement(payments.PaymentMethodSummary, { spec })));
  assert.match(render(null), /未配置/);
  assert.doesNotMatch(render(null), /银行卡|微信支付|支付宝/);
  const old = render(legacy);
  for (const label of ['银行卡', '微信支付', '支付宝']) assert.ok(old.includes(label));
  const card = render({ ...legacy, payment_methods: ['card'] });
  assert.match(card, /银行卡/);
  assert.doesNotMatch(card, /微信支付|支付宝/);
  const ali = render({ ...legacy, payment_methods: ['alipay'] });
  assert.match(ali, /支付宝/);
  assert.doesNotMatch(ali, /银行卡|微信支付/);
});

test('top-up availability never borrows the legacy settings default, and account refusals win', () => {
  const options = { available: true, payment_methods: ['card'] };
  assert.equal(wallet.topupClosedReason(options, null), null);
  for (const payment_methods of [undefined, null, [], ['future_channel']]) {
    assert.equal(wallet.topupClosedReason({ ...options, payment_methods }, null), 'not_configured');
    assert.equal(methods.paymentMethodsText(payment_methods, s => s, 'en'), '');
  }
  for (const reason of ['email_unverified', 'wallet_frozen', 'not_permitted'])
    assert.equal(wallet.topupClosedReason({ ...options, available: false, reason }, null), reason);
});

test('channel labels and every changed sentence have matching English and Traditional Chinese placeholders', async () => {
  const paths = ['src/lib/paymentMethods.ts', 'src/governance/PaymentsTab.tsx', 'src/pages/account/TopupDialog.tsx', 'src/pages/org/OrganizationBilling.tsx'];
  const tokens = value => [...value.matchAll(/\{([^}]+)\}/g)].map(match => match[1]).sort();
  for (const path of paths) {
    const code = (await readFile(new URL(`../${path}`, import.meta.url), 'utf8')).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    for (const match of code.matchAll(/'([^'\n]*[\u3400-\u9fff][^'\n]*)'|"([^"\n]*[\u3400-\u9fff][^"\n]*)"/g)) {
      const key = match[1] ?? match[2];
      assert.ok(ui.hasMessage(key), `${path}: ${key}`);
      for (const locale of ['en', 'zh-Hant']) {
        const translated = ui.translate(locale, key);
        assert.deepEqual(tokens(translated), tokens(key), `${locale}: ${key}`);
        if (locale === 'en') assert.doesNotMatch(translated, /[\u3400-\u9fff]/, key);
      }
    }
  }
  for (const [locale, expected] of [['zh-Hans', '银行卡和支付宝'], ['zh-Hant', '銀行卡和支付寶'], ['en', 'Card and Alipay']])
    assert.equal(methods.paymentMethodsText(['alipay', 'card'], key => ui.translate(locale, key), locale), expected);
});
