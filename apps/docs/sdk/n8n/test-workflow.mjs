import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const workflow = JSON.parse(await readFile(new URL('./generate-documents.json', import.meta.url)));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

test('every shipped Code node parses, every API request uses n8n credentials', () => {
  for (const node of workflow.nodes) {
    if (node.type === 'n8n-nodes-base.code') {
      assert.doesNotThrow(() => new AsyncFunction(node.parameters.jsCode), node.name);
      assert(!/\bfetch\s*\(/.test(node.parameters.jsCode), node.name);
    }
    if (node.type === 'n8n-nodes-base.httpRequest' && node.name !== 'Health') {
      assert.equal(node.parameters.genericAuthType, 'oAuth2Api', node.name);
      assert(!JSON.stringify(node).includes('Bearer '), node.name);
    }
  }
  assert.equal(workflow.active, false);
});

test('configuration fails closed on non-loopback destinations', async () => {
  const source = workflow.nodes.find(n => n.name === 'Config').parameters.jsCode;
  const config = new AsyncFunction('$execution', source);
  assert.equal((await config({ id: 'synthetic-test' }))[0].json.baseUrl, 'http://127.0.0.1:1');
  for (const url of ['http://example.invalid:80', 'http://127.0.0.1:80@other.invalid', 'http://127.0.0.1:99999']) {
    await assert.rejects(new AsyncFunction('$execution', source.replace("'http://127.0.0.1:1'", JSON.stringify(url)))({ id: 'synthetic' }), /Loopback/);
  }
});

const receiver = JSON.parse(await readFile(new URL('./receive-documents.json', import.meta.url)));
test('receiver preserves raw binary and delegates secrets to credential-store HTTP nodes', () => {
  assert.equal(receiver.nodes.find(n => n.type === 'n8n-nodes-base.webhook').parameters.options.rawBody, true);
  for (const node of receiver.nodes) {
    if (node.type === 'n8n-nodes-base.code') assert.doesNotThrow(() => new AsyncFunction(node.parameters.jsCode), node.name);
    if (node.type === 'n8n-nodes-base.httpRequest') assert(['oAuth2Api', 'httpCustomAuth'].includes(node.parameters.genericAuthType));
  }
  const verify = receiver.nodes.find(n => n.name === 'Verify raw body');
  assert.equal(verify.parameters.contentType, 'binaryData');
  assert.equal(verify.parameters.inputDataFieldName, 'data');
  assert(!JSON.stringify(receiver).includes('Bearer '));
  assert(!JSON.stringify(receiver).includes('whsec_'));
});
