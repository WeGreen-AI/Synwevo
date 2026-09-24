// Builds the importable standard-node workflow. It does not execute any API requests.
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const nodes = []; const connections = {};
function node(name, type, parameters, typeVersion = 1) {
  const n = { id: name.toLowerCase().replaceAll(' ', '-'), name, type: `n8n-nodes-base.${type}`,
    typeVersion, position: [nodes.length * 220, 0], parameters };
  nodes.push(n); return n;
}
function edge(from, to, branch = 0) {
  connections[from] ??= { main: [] };
  connections[from].main[branch] ??= [];
  connections[from].main[branch].push({ node: to, type: 'main', index: 0 });
}
function code(name, jsCode) { return node(name, 'code', { jsCode }, 2); }
function http(name, method, path, body, key, extra = {}) {
  const parameters = { method, url: `={{ $('Config').first().json.baseUrl + ${path} }}`,
    authentication: 'genericCredentialType', genericAuthType: 'oAuth2Api',
    options: { timeout: 30000, redirect: { redirect: { followRedirects: false } },
      response: { response: { responseFormat: 'json' } } }, ...extra };
  if (body) Object.assign(parameters, { sendBody: true, specifyBody: 'json', jsonBody: body });
  if (key) Object.assign(parameters, { sendHeaders: true, headerParameters: { parameters: [
    { name: 'Idempotency-Key', value: `={{ $('Config').first().json.runKey + ':${key}' }}` }] } });
  return node(name, 'httpRequest', parameters, 4.2);
}
function read(name, file) {
  return node(name, 'readWriteFile', { operation: 'read',
    fileSelector: `={{ $('Config').first().json.kitDir + '/${file}' }}`, options: {} }, 1.1);
}
function binaryHash(name) {
  code(name, `const item = $input.first();
const bytes = await this.helpers.getBinaryDataBuffer(0, 'data');
item.json.sha256 = require('crypto').createHash('sha256').update(bytes).digest('hex');
return [item];`);
}
function upload(name, path, key) {
  const n = http(name, 'POST', path, null, key, { sendBody: true, contentType: 'multipart-form-data',
    bodyParameters: { parameters: [{ parameterType: 'formBinaryData', name: 'file', inputDataFieldName: 'data' }] } });
  n.parameters.headerParameters.parameters.push({ name: 'X-Content-SHA256', value: '={{ $json.sha256 }}' });
}
function conditional(name, expression) {
  node(name, 'if', { conditions: { options: { typeValidation: 'strict', version: 2 },
    conditions: [{ id: name, leftValue: expression, rightValue: true,
      operator: { type: 'boolean', operation: 'true', singleValue: true } }], combinator: 'and' } }, 2.2);
}
function pause(name) { node(name, 'wait', { amount: 1, unit: 'seconds' }, 1.1); }
const pid = "$('Create project').first().json.id";
const tid = "$('Upload template').first().json.id";
const jid = "$('Submit job').first().json.id";
node('Manual trigger', 'manualTrigger', {});
code('Config', `// Configure these three paths/values after import. No credentials belong here.
const config = { baseUrl: 'http://127.0.0.1:1', kitDir: '/replace-with-synthetic-kit',
  outDir: '/replace-with-output-directory', runKey: 'synthetic-' + $execution.id, startedAt: Date.now() };
const port = config.baseUrl.slice('http://127.0.0.1:'.length);
if (!config.baseUrl.startsWith('http://127.0.0.1:') || !/^[0-9]+$/.test(port) || Number(port) < 1 || Number(port) > 65535) throw new Error('Loopback service required');
return [{ json: config }];`);
const health = http('Health', 'GET', "'/healthz'", null);
delete health.parameters.authentication; delete health.parameters.genericAuthType;
code('Require test instance', "if ($json.instance !== 'test') throw new Error('Refusing a non-test instance'); return $input.all();");
http('Whoami', 'GET', "'/api/v1/whoami'");
code('Require write scope', "if (!$json.scopes.some(s => ['write', 'docweave.write'].includes(s))) throw new Error('Write scope required'); return $input.all();");
http('Create project', 'POST', "'/api/v1/projects'", '={{ { name: "Synthetic n8n acceptance" } }}', 'project');
read('Read source', 'customers.xlsx'); binaryHash('Hash source');
upload('Upload source', `'/api/v1/projects/' + ${pid} + '/files'`, 'source');
http('Source status', 'GET', "'/api/v1/files/' + $('Upload source').first().json.id");
code('Check source', "if ($json.status === 'error') throw new Error('Source parsing failed'); if (Date.now() - $('Config').first().json.startedAt > 180000) throw new Error('Source timeout'); return $input.all();");
conditional('Source ready', "={{ $json.status !== 'parsing' }}"); pause('Wait source');
read('Read template', 'template.docx'); binaryHash('Hash template');
upload('Upload template', "'/api/v1/templates'", 'template');
read('Read fields', 'fields.json');
code('Parse fields', "return [{ json: JSON.parse((await this.helpers.getBinaryDataBuffer(0, 'data')).toString('utf8')) }];");
http('Create fields', 'POST', `'/api/v1/templates/' + ${tid} + '/fields'`, '={{ $json }}', 'fields');
http('Bind template', 'PUT', `'/api/v1/projects/' + ${pid} + '/template'`, `={{ { template_id: ${tid} } }}`);
read('Read definition', 'project.json');
code('Parse definition', "return [{ json: JSON.parse((await this.helpers.getBinaryDataBuffer(0, 'data')).toString('utf8')) }];");
http('Save definition', 'PUT', `'/api/v1/projects/' + ${pid} + '/definition'`, '={{ $json }}');
http('Preflight', 'POST', `'/api/v1/projects/' + ${pid} + '/preflight'`, '={{ { formats: ["docx"] } }}');
code('Check preflight', "if ($json.invalid || $json.expected_outputs !== 12) throw new Error('Synthetic preflight failed'); return $input.all();");
http('Preview render', 'POST', `'/api/v1/projects/' + ${pid} + '/preflight'`, '={{ { formats: ["docx"], simulate: true } }}');
code('Check preview', "if ($json.simulation.failed || $json.simulation.checked !== 12) throw new Error('Synthetic preview failed'); return $input.all();");
const previewFile = http('Download preview', 'POST', `'/api/v1/projects/' + ${pid} + '/preview'`,
  "={{ { index: 0, signature: $('Preflight').first().json.signature } }}");
previewFile.parameters.options.response.response = { responseFormat: 'file', outputPropertyName: 'data', fullResponse: true };
code('Verify preview', `const item = $input.first(); const h = item.json.headers;
const digest = require('crypto').createHash('sha256').update(await this.helpers.getBinaryDataBuffer(0, 'data')).digest('hex');
if (digest !== h['x-content-sha256'] || !h['x-project-signature'] || !h['x-template-version'] || !h['x-record-key']) throw new Error('Invalid preview checksum/metadata');
item.json = { path: $('Config').first().json.outDir + '/preview.docx', sha256: digest,
  header_sha256: h['x-content-sha256'], project_signature: h['x-project-signature'],
  template_version: h['x-template-version'], record_key: h['x-record-key'] };
return [item];`);
node('Write preview', 'readWriteFile', { operation: 'write', fileName: '={{ $json.path }}', dataPropertyName: 'data', options: {} }, 1.1);
http('Submit job', 'POST', `'/api/v1/projects/' + ${pid} + '/jobs'`, '={{ { formats: ["docx"], error_policy: "block" } }}', 'job');
http('Poll job', 'GET', `'/api/v1/jobs/' + ${jid}`);
code('Check job', "if (['failed', 'cancelled', 'completed_with_errors'].includes($json.status)) throw new Error('Job ended with errors'); if (Date.now() - $('Config').first().json.startedAt > 180000) throw new Error('Job timeout'); return $input.all();");
conditional('Job complete', "={{ $json.status === 'completed' }}"); pause('Wait job');
const manifest = http('Manifest', 'GET', `'/api/v1/jobs/' + ${jid} + '/artifacts?limit=3'`);
manifest.parameters.options.pagination = { pagination: { paginationMode: 'updateAParameterInEachRequest',
  parameters: { parameters: [{ type: 'qs', name: 'cursor', value: '={{ $response.body.next_cursor }}' }] },
  paginationCompleteWhen: 'other', completeExpression: '={{ !$response.body.next_cursor }}',
  limitPagesFetched: true, maxRequests: 100, requestInterval: 50 } };
code('Artifact items', `const pages = $input.all();
if (pages.at(-1).json.next_cursor) throw new Error('Manifest page limit exceeded');
const artifacts = pages.flatMap(i => i.json.items);
if (artifacts.length !== 12 || new Set(artifacts.map(a => a.id)).size !== 12) throw new Error('Incomplete synthetic manifest');
return artifacts.map(a => ({ json: a }));`);
const download = http('Download artifacts', 'GET', "'/api/v1/artifacts/' + $json.id + '/content'");
download.parameters.options.response.response = { responseFormat: 'file', outputPropertyName: 'data', fullResponse: true };
code('Verify checksums', `const items = $input.all(); const artifacts = $('Artifact items').all();
for (let i = 0; i < items.length; i++) {
  const bytes = await this.helpers.getBinaryDataBuffer(i, 'data');
  const digest = require('crypto').createHash('sha256').update(bytes).digest('hex');
  const a = artifacts[i].json; const header = items[i].json.headers['x-content-sha256'];
  if (digest !== a.sha256 || digest !== header) throw new Error('Checksum mismatch');
  if (/[\\/\\\\]/.test(a.filename) || a.filename === '..') throw new Error('Unsafe artifact filename');
  items[i].json = { ...a, manifest_sha256: a.sha256, header_sha256: header,
    path: $('Config').first().json.outDir + '/' + a.filename };
}
return items;`);
node('Write artifacts', 'readWriteFile', { operation: 'write', fileName: '={{ $json.path }}', dataPropertyName: 'data', options: {} }, 1.1);
code('Summary', `return [{ json: { project_id: ${pid}, template_id: ${tid}, job: $('Check job').first().json,
  preflight: $('Preflight').first().json, preview: { ...$('Preview render').first().json, download: $('Verify preview').first().json },
  artifacts: $('Verify checksums').all().map(i => i.json) } }];`);
// Sequential main chain with two bounded polling branches.
const skip = new Set(['Wait source', 'Wait job']);
const chain = nodes.map(n => n.name).filter(n => !skip.has(n));
for (let i = 0; i < chain.length - 1; i++) edge(chain[i], chain[i + 1]);
edge('Source ready', 'Wait source', 1); edge('Wait source', 'Source status');
edge('Job complete', 'Wait job', 1); edge('Wait job', 'Poll job');
export const workflow = { id: 'synwevo-synthetic-http', name: 'Synwevo Docs — synthetic HTTP acceptance',
  active: false, nodes, connections, settings: { executionOrder: 'v1', executionTimeout: 240 } };
await writeFile(fileURLToPath(new URL('./generate-documents.json', import.meta.url)), JSON.stringify(workflow, null, 2) + '\n');
