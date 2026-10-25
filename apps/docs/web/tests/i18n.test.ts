import { test } from 'node:test';
import assert from 'node:assert/strict';
import { messages } from '../src/messages.ts';
import { serverMessages } from '../src/messages.server.ts';
import { refusalMessages, refusalPatterns } from '../src/messages.refusals.ts';
import { ERROR_SOURCE, issueText, serverText } from '../src/lib/presentation.ts';
import { jobStatus, itemStatus, ruleMode } from '../src/lib/status.ts';
import { errorText, READ_ONLY_TEXT } from '../src/lib/perm.ts';
import { setLocale, translate, validateMessages } from '@synwevo/ui/i18n-core';

test('Docs resources cover both target locales and preserve every parameter', () => {
  assert.ok(Object.keys(messages).length > 1200);
  assert.deepEqual(validateMessages(messages), []);
  assert.deepEqual(validateMessages(serverMessages), []);
  assert.deepEqual(validateMessages(refusalMessages), []);
  assert.deepEqual(validateMessages(refusalPatterns), []);
  for (const source of Object.values(ERROR_SOURCE)) {
    assert.ok(source in messages || source in serverMessages || source in refusalMessages, `missing error resource: ${source}`);
  }
});

test('statuses and permission refusals translate using stable API codes without changing records', () => {
  const issue = { code: 'read_only_role', message: 'forbidden' };
  setLocale('en');
  assert.equal(jobStatus('cancelling')[0], 'Cancelling');
  assert.equal(itemStatus('skipped')[0], 'Skipped');
  assert.equal(ruleMode('per_block'), 'One per block');
  assert.match(errorText(issue), /viewer in this workspace/);
  assert.deepEqual(issue, { code: 'read_only_role', message: 'forbidden' });
  setLocale('zh-Hant');
  assert.equal(jobStatus('completed')[0], '已完成');
  assert.match(errorText(issue), /檢視者|查看者/);
  setLocale('zh-Hans');
  assert.equal(errorText(issue), READ_ONLY_TEXT);
});

test('error and capability presentation is local; identifiers and original diagnostics survive', () => {
  setLocale('en');
  const issue = { code: 'missing_column', message: '无法找到列', detail: { field: '客户名称' }, requestId: 'request-123' };
  assert.equal(issueText(issue), 'Missing column (客户名称) · Request ID: request-123');
  assert.equal(issue.message, '无法找到列');
  assert.equal(issueText({ code: 'future_error', message: 'Unknown backend prose', requestId: 'fixture-2' }), 'The request could not be completed (future_error). · Request ID: fixture-2');
  assert.equal(issueText('Unknown backend prose'), translate('en', '操作失败，请检查设置后重试。'));
  assert.equal(serverText('DOCX（模板原生格式）'), 'DOCX (native template format)');
  assert.equal(serverText('服务端本地'), 'Local server');
  assert.equal(serverText('客户原文 第 1–5 行'), '客户原文 · Rows 1–5');
  assert.equal(serverText('第 2 页–第 3 页'), 'Pages 2–3');
  assert.equal(serverText('custom engine diagnostic 123'), 'custom engine diagnostic 123');
  assert.equal(translate('en', '字段「{v1}」', { v1: '我的文档 <b>{v1}</b>' }), 'Field “我的文档 <b>{v1}</b>”');
  assert.equal(translate('en', '{count} 个项目', { count: 1 }), '1 project');
  assert.equal(translate('en', '{count} 个项目', { count: 2 }), '2 projects');
  setLocale('zh-Hans');
});

test('successful formula diagnostics and unknown informational notices are not presented as failed requests', () => {
  const issue = { level: 'info', code: 'formula_computed', message: '内置公式引擎已计算 1 个公式并写入结果' };
  for (const locale of ['zh-Hans', 'zh-Hant', 'en'] as const) {
    setLocale(locale);
    // the engine's whole sentence, its count included (Q13); the code's label is what is left
    // when the words are not the engine's
    assert.equal(issueText(issue), translate(locale, '内置公式引擎已计算 {count} 个公式并写入结果', { count: '1' }));
    assert.equal(issueText({ ...issue, message: 'Unregistered engine prose' }), translate(locale, '内置公式引擎已计算公式并写入结果。'));
    assert.equal(issueText({ level: 'info', code: 'future_info', message: 'Unregistered engine prose' }), translate(locale, '处理提示（{code}）。', { code: 'future_info' }));
  }
  assert.equal(issue.message, '内置公式引擎已计算 1 个公式并写入结果');
  setLocale('zh-Hans');
});

// Parse the actual call sites, so adding a source string without its catalog fails immediately.
// This does not translate a DOM or treat user data as a message.
import { API } from 'typescript/unstable/sync';
import { SyntaxKind as K } from 'typescript/unstable/ast';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';

test('all literal Docs translation calls have resources; saved font names and patterns remain stable', () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const api = new API();
  const missing: string[] = [];
  try {
    const project = api.updateSnapshot({ openProjects: [root + 'tsconfig.json'] }).getProjects()[0];
    for (const path of project.program.getSourceFileNames().filter(p => p.startsWith(root + 'src/') && !p.includes('ApiPlatform'))) {
      const sf = project.program.getSourceFile(path)!;
      function visit(n: any) {
        if (n.kind === K.CallExpression && ['tr', 't'].includes(n.expression.getText(sf)) && n.arguments[0]?.kind === K.StringLiteral) {
          const source = n.arguments[0].text;
          if (/[\u3400-\u9fff]/.test(source) && !(source in messages) && !(source in serverMessages)) missing.push(`${path}: ${source}`);
        }
        if (n.kind === K.JsxAttribute && ['href', 'src'].includes(n.name.getText(sf))) {
          // Authenticated resource components handle API URLs; raw anchors/images cannot
          // attach the tab workspace header and must not be reintroduced. The one exception is the
          // AGPL-3.0 §13 source archive: anonymous on every origin and about no workspace, so a
          // plain download link is what it should be (synwevo_core.http SOURCE_OFFER_PATHS).
          const element = n.parent?.parent?.tagName?.getText(sf);
          const sourceOffer = n.initializer?.kind === K.StringLiteral && n.initializer.text === '/api/source-code';
          if (['a', 'img'].includes(element) && /\/api\//.test(n.getText(sf)) && !sourceOffer) missing.push(`${path}: unbound API ${element}`);
        }
        n.forEachChild(visit);
      }
      visit(sf);
    }
  } finally { api.close(); }
  assert.deepEqual(missing, []);
  const doc = readFileSync(root + 'src/pages/DocEditor.tsx', 'utf8');
  assert.match(doc, /<option key=\{x\} value=\{x\}>\{tr\(x\)\}<\/option>/);
  const transforms = readFileSync(root + 'src/components/Transforms.tsx', 'utf8');
  assert.match(transforms, /init\.pattern = "yyyy年m月d日"/);
  assert.match(transforms, /init\.table = \{ TRUE: "是", FALSE: "否" \}/);
});
