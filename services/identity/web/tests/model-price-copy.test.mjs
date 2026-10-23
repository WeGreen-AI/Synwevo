/** Source scan only: no server, fixture or render. Owner decision 2026-10-23 ("send-and-go"):
 *  a price, calendar or route change never asks a person to confirm anything again — answers
 *  started afterwards are charged at the new price and answers in progress stop before their
 *  next model call. The governance copy that told administrators otherwise must not come back. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const governance = new URL('../src/governance/', import.meta.url);
const FILES = ['ModelPriceCalendar.tsx', 'ModelPriceMarkups.tsx', 'ModelProviderTab.tsx',
  'model-provider.messages.ts', 'model-price-bands.messages.ts'];
const RETIRED = ['重新确认', '重新確認', 'confirm again', 'confirmed again', 'must be confirmed', 'asks users to confirm'];
// The approved wording says the opposite in so many words ("用户无需重新确认" / "people do not need
// to confirm again"); those negations are removed before the scan so only a promise of a
// re-confirmation fails it.
const NEGATED = ['无需重新确认', '無需重新確認', 'do not need to confirm again', 'do not need to confirm anything again'];
const STATED = ['正在进行的回答在下一次模型调用前停止', '正在進行的回答在下一次模型呼叫前停止', 'stop before their next model call'];

test('model price and provider copy never promises a re-confirmation, and states how a change applies', async () => {
  const sources = Object.fromEntries(await Promise.all(FILES.map(async name => [name, await readFile(new URL(name, governance), 'utf8')])));
  for (const [name, text] of Object.entries(sources)) {
    const promises = NEGATED.reduce((rest, negation) => rest.replaceAll(negation, ''), text);
    for (const phrase of RETIRED) assert.ok(!promises.includes(phrase), `${name}: ${phrase}`);
  }
  const everything = Object.values(sources).join('\n');
  for (const phrase of STATED) assert.ok(everything.includes(phrase), phrase);
});

test('the account export copy names accepted terms and run authorizations instead of quotes', async () => {
  const text = await readFile(new URL('../src/pages/account.messages.ts', import.meta.url), 'utf8');
  assert.ok(!text.includes('数据同意、报价与钱包流水'), 'zh-Hans still says 报价');
  assert.ok(!text.includes('資料同意、報價與錢包流水'), 'zh-Hant still says 報價');
  assert.ok(!text.includes('data consents, quotes and wallet statement'), 'en still says quotes');
  for (const phrase of ['条款接受记录、运行授权', '條款接受紀錄、執行授權', 'accepted terms, run authorizations']) assert.ok(text.includes(phrase), phrase);
});
