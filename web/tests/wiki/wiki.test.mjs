import assert from 'node:assert/strict';
import test from 'node:test';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import { remarkWikiLinks } from '../../src/views/KnowledgeBase/Wiki/wikiMarkdown.ts';
import { documentState, isDocumentTerminal } from '../../src/views/KnowledgeBase/Wiki/documentState.ts';

const id = '11111111-1111-4111-8111-111111111111';
const processor = unified().use(remarkParse).use(remarkGfm).use(remarkWikiLinks);
const parse = text => processor.runSync(processor.parse(text));
const flatten = node => [node, ...(node.children ?? []).flatMap(flatten)];

test('Wiki links and citations become navigation in prose', () => {
  const nodes = flatten(parse(`见 [[${id}|产品概览]] 与 [^R1]。`));
  assert.deepEqual(nodes.filter(node => node.type === 'link').map(node => node.url), [`/wiki-page/${id}`, '#wiki-source-R1']);
});

test('Wiki syntax inside inline, fenced and indented code stays literal', () => {
  const syntax = `[[${id}|示例]] [^R1]`;
  const nodes = flatten(parse('`' + syntax + '`\n\n```md\n' + syntax + '\n```\n\n    ' + syntax));
  assert.equal(nodes.filter(node => node.type === 'link').length, 0);
  assert.deepEqual(nodes.filter(node => ['code', 'inlineCode'].includes(node.type)).map(node => node.value), [syntax, syntax, syntax]);
});

test('Invalid ids and existing Markdown links are not rewritten', () => {
  const nodes = flatten(parse(`[[not-a-uuid|无效]] [示例 [^R1]](https://example.com) ![图片 [^R1]](image.png)`));
  assert.deepEqual(nodes.filter(node => node.type === 'link').map(node => node.url), ['https://example.com']);
});

test('Multiple Wiki links are preserved in lists, emphasis and tables', () => {
  const nodes = flatten(parse(`- **[[${id}|产品]]**\n\n| 页面 |\n| --- |\n| [[${id}\\|另一个]] [^R2] |`));
  assert.equal(nodes.filter(node => node.type === 'link').length, 3);
});

test('Queued Wiki-only and active retries are not terminal', () => {
  assert.equal(isDocumentTerminal({ run: 0, progress: 0 }), false);
  assert.equal(isDocumentTerminal({ run: 1, progress: .8 }), false);
  assert.equal(isDocumentTerminal({ run: 1, progress: -1 }), false);
});

test('Failed, cancelled and completed rounds stop the upload poll', () => {
  assert.equal(isDocumentTerminal({ run: 0, progress: -1 }), true);
  assert.equal(isDocumentTerminal({ run: 2, progress: .6 }), true);
  assert.equal(isDocumentTerminal({ run: 0, progress: 1 }), true);
  assert.equal(documentState({ run: 2, progress: 1 }).label, '已取消');
  assert.equal(documentState({ run: 0, progress: -1 }).label, '处理失败');
});
