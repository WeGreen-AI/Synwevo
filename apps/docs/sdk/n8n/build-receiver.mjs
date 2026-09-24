// Standard n8n webhook/binary/HTTP workflow. Verification is delegated to the local companion.
import { writeFile } from 'node:fs/promises';
const nodes = []; const connections = {};
function node(name, type, parameters, version = 1) {
  const n = { id: name.toLowerCase().replaceAll(' ', '-'), name, type: 'n8n-nodes-base.' + type,
    typeVersion: version, position: [nodes.length * 220, 0], parameters };
  nodes.push(n); return n;
}
function edge(from, to, branch = 0) {
  connections[from] ??= { main: [] }; connections[from].main[branch] ??= [];
  connections[from].main[branch].push({ node: to, type: 'main', index: 0 });
}
const code = (name, jsCode) => node(name, 'code', { jsCode }, 2);
function http(name, method, url, auth = 'oAuth2Api') {
  return node(name, 'httpRequest', { method, url, authentication: 'genericCredentialType', genericAuthType: auth,
    options: { timeout: 15000, redirect: { redirect: { followRedirects: false } },
      response: { response: { responseFormat: 'json' } } } }, 4.2);
}
const bridge = path => `={{ $('Receiver config').first().json.verifierUrl + '${path}' }}`;
const docs = path => `={{ $('Receiver config').first().json.baseUrl + ${path} }}`;
const receipt = "$('Verify raw body').first().json";
node('Signed event', 'webhook', { httpMethod: 'POST', path: 'synwevo-events', responseMode: 'responseNode',
  options: { rawBody: true } }, 2.1).webhookId = 'synwevo-synthetic-events';
code('Receiver config', `const config = { baseUrl: 'http://127.0.0.1:1', verifierUrl: 'http://127.0.0.1:2',
  outDir: '/replace-with-receiver-output', tenant: 'replace-with-workspace' };
for (const url of [config.baseUrl, config.verifierUrl]) {
  const port = url.slice('http://127.0.0.1:'.length);
  if (!url.startsWith('http://127.0.0.1:') || !/^[0-9]+$/.test(port) || Number(port) < 1 || Number(port) > 65535) throw new Error('Loopback required');
}
return [{ json: config, binary: $input.first().binary }];`);
const v = http('Verify raw body', 'POST', bridge('/verify'), 'httpCustomAuth');
Object.assign(v.parameters, { sendBody: true, contentType: 'binaryData', inputDataFieldName: 'data', sendHeaders: true,
  headerParameters: { parameters: ['webhook-id', 'webhook-timestamp', 'webhook-signature'].map(name =>
    ({ name, value: `={{ $('Signed event').first().json.headers['${name}'] || '' }}` })) } });
v.parameters.options.response.response.neverError = true;
code('Verification result', `const value = $json;
if (!['fetch', 'duplicate', 'pending'].includes(value.action)) return [{ json: { responseCode: 401 } }];
return $input.all();`);
node('Needs fetch', 'if', { conditions: { options: { typeValidation: 'strict', version: 2 },
  conditions: [{ id: 'fetch', leftValue: '={{ $json.action === "fetch" }}', rightValue: true,
    operator: { type: 'boolean', operation: 'true', singleValue: true } }], combinator: 'and' } }, 2.2);
http('Receiver whoami', 'GET', docs("'/api/v1/whoami'"));
code('Check authority', `if ($json.tenant !== $('Receiver config').first().json.tenant || !$json.scopes.includes('docweave.read')) throw new Error('Receiver credential workspace/scope mismatch'); return $input.all();`);
http('Fetch completed job', 'GET', docs(`'/api/v1/jobs/' + ${receipt}.jobId`));
code('Require completed job', `if ($json.status !== 'completed') throw new Error('Job is not complete'); return $input.all();`);
const manifest = http('Receiver manifest', 'GET', docs(`'/api/v1/jobs/' + ${receipt}.jobId + '/artifacts?limit=3'`));
manifest.parameters.options.pagination = { pagination: { paginationMode: 'updateAParameterInEachRequest',
  parameters: { parameters: [{ type: 'qs', name: 'cursor', value: '={{ $response.body.next_cursor }}' }] },
  paginationCompleteWhen: 'other', completeExpression: '={{ !$response.body.next_cursor }}',
  limitPagesFetched: true, maxRequests: 100, requestInterval: 10 } };
code('Receiver artifact items', `const pages = $input.all();
if (pages.at(-1).json.next_cursor) throw new Error('Manifest page limit exceeded');
const artifacts = pages.flatMap(i => i.json.items);
if (!artifacts.length || new Set(artifacts.map(a => a.id)).size !== artifacts.length) throw new Error('Incomplete manifest');
return artifacts.map(a => ({ json: a }));`);
const download = http('Receiver download', 'GET', docs("'/api/v1/artifacts/' + encodeURIComponent($json.id) + '/content'"));
download.parameters.options.response.response = { responseFormat: 'file', outputPropertyName: 'data', fullResponse: true };
code('Receiver checksums', `const items = $input.all(); const artifacts = $('Receiver artifact items').all();
for (let i = 0; i < items.length; i++) {
  const a = artifacts[i].json;
  const sha = require('crypto').createHash('sha256').update(await this.helpers.getBinaryDataBuffer(i, 'data')).digest('hex');
  const header = items[i].json.headers['x-content-sha256'];
  if (sha !== a.sha256 || sha !== header) throw new Error('Checksum mismatch');
  if (!a.filename || /[\\/\\\\]/.test(a.filename) || a.filename === '..') throw new Error('Unsafe filename');
  items[i].json = { ...a, sha256: sha, manifest_sha256: a.sha256, header_sha256: header,
    path: $('Receiver config').first().json.outDir + '/' + ${receipt}.id + '-' + a.filename };
}
return items;`);
node('Write receiver artifacts', 'readWriteFile', { operation: 'write', fileName: '={{ $json.path }}', dataPropertyName: 'data', options: {} }, 1.1);
code('Receiver summary', `const result = { id: ${receipt}.id, job_id: ${receipt}.jobId,
  artifacts: $('Receiver checksums').all().map(i => i.json) };
return [{ json: { path: $('Receiver config').first().json.outDir + '/' + result.id + '.json' },
  binary: { data: await this.helpers.prepareBinaryData(Buffer.from(JSON.stringify(result)), 'result.json', 'application/json') } }];`);
node('Write receiver summary', 'readWriteFile', { operation: 'write', fileName: '={{ $json.path }}', dataPropertyName: 'data', options: {} }, 1.1);
function finish(name, path) {
  const n = http(name, 'POST', bridge(path), 'httpCustomAuth');
  Object.assign(n.parameters, { sendBody: true, specifyBody: 'json',
    jsonBody: `={{ { id: ${receipt}.id, lease: ${receipt}.lease } }}` });
}
finish('Complete receipt', '/complete');
node('Acknowledge event', 'respondToWebhook', { respondWith: 'noData', options: { responseCode: '={{ $json.responseCode || 503 }}' } }, 1.4);
finish('Release failed receipt', '/release');
node('Retry later', 'respondToWebhook', { respondWith: 'noData', options: { responseCode: 503 } }, 1.4);
const chain = nodes.slice(0, -2).map(n => n.name);
for (let i = 0; i < chain.length - 1; i++) edge(chain[i], chain[i + 1]);
edge('Needs fetch', 'Acknowledge event', 1);
edge('Release failed receipt', 'Retry later');
// Release the lease after download/auth/write failure so a signed retry can fetch again.
const first = nodes.findIndex(n => n.name === 'Receiver whoami');
const last = nodes.findIndex(n => n.name === 'Write receiver summary');
for (const n of nodes.slice(first, last + 1)) { n.onError = 'continueErrorOutput'; edge(n.name, 'Release failed receipt', 1); }
export const workflow = { id: 'synwevo-synthetic-receiver', name: 'Synwevo Docs — signed local receiver', active: false,
  nodes, connections, settings: { executionOrder: 'v1', executionTimeout: 50 } };
await writeFile(new URL('./receive-documents.json', import.meta.url), JSON.stringify(workflow, null, 2) + '\n');
