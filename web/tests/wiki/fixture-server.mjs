// Local UI fixture only. It never connects to a database, model or external API.
// Run: node tests/wiki/fixture-server.mjs
// Then: VITE_API_PROXY_TARGET=http://127.0.0.1:4174 npm run dev -- --port 4173 --strictPort
// Open: http://localhost:4173/api/__wiki_fixture__/start
import { createServer } from 'node:http';

export const kbId = '00000000-0000-4000-8000-000000000001';
const uuid = number => `11111111-1111-4111-8111-${String(number).padStart(12, '0')}`;
const folders = ['产品与能力', '使用与实践', '架构与设计'].map((name, i) => ({ id: uuid(100 + i), name, parent_id: null, page_count: i === 0 ? 5 : i === 1 ? 2 : 4 }));
const catalog = [
  ['产品概览', 'MemoryBear 将文档、知识和长期记忆连接起来，为团队提供持续积累、可追溯的知识服务。', 0],
  ['记忆引擎', '将对话与文档中的重要事实转化为可检索、可更新的长期记忆，为后续交互提供连续上下文。', 0],
  ['知识检索与问答', '结合向量检索、重排序与来源引用，从知识库中召回相关内容，再由模型生成有依据的回答。', 0],
  ['MemoryBear', '面向团队的知识与长期记忆平台，将原文件、Wiki 和知识服务连接起来。', 0],
  ['知识库', '统一保存原始资料，并组织文档解析、知识检索和 Wiki 派生内容的空间。', 0],
  ['文件导入指南', '将 PDF、Word 或 Markdown 上传至知识库，完成文件解析，并在启用 Wiki 时自动生成摘要和主题页面。', 1],
  ['团队知识管理', '以主题而非上传文件名组织阅读内容，通过来源引用、页面关联和持续更新维护团队知识。', 1],
  ['权限与数据隔离', '知识库、Wiki 页面和关联关系保持一致的空间访问范围，并通过来源追溯核对信息的可见性。', 2],
  ['系统架构', '前端负责知识管理与阅读；服务层组织文档处理与模型调用；不同存储承担原文件、语义索引和关系数据。', 2],
  ['API 接入规范', '通过统一认证调用知识服务，显式指定知识库与空间，并在结果中保留内容来源。', 2],
  ['知识服务 API', '对外提供知识检索与调用能力的服务接口，是应用访问知识库内容的入口。', 2],
];
const pages = catalog.map(([title, summary, folder], i) => ({ id: uuid(i + 1), page_id: uuid(i + 1), title, summary,
  type: i === 0 ? 'summary' : i === 3 ? 'entity' : 'concept', version: 1, folder_id: folders[folder].id,
  folder_path: [{ id: folders[folder].id, name: folders[folder].name }], updated_at_ms: 1791925200000 }));
const documentId = uuid(200);
let scenario = 'normal';
const modelOptions = Object.fromEntries(['llm', 'embedding', 'rerank'].map((type, i) => [type, [{
  id: uuid(300 + i), name: `本地测试${type}模型`, type, provider: 'openai', is_active: true, is_available: true, is_deprecated: false,
}]]));
let knowledge = { id: kbId, name: 'xxxx', status: 1, type: 'General', llm_id: uuid(300), embedding_id: uuid(301), reranker_id: uuid(302),
  permission_id: 'Private', created_at: '2026-10-14T08:00:00Z', parser_config: { wiki: { enabled: true, language: 'zh-CN', extract_prompt: '' } } };
let doc = { id: documentId, file_id: uuid(201), kb_id: kbId, file_name: 'MemoryBear 产品说明.md', file_ext: 'md',
  chunk_num: 3, run: 0, progress: 1, status: 1, progress_msg: 'Wiki 生成完成：11 / 11 个页面', created_at: '2026-10-14T08:00:00Z' };
const requests = [];
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  let body = '';
  for await (const chunk of req) body += chunk;
  const payload = body ? JSON.parse(body) : undefined;
  requests.push({ method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams), payload });
  const send = (data, status = 200) => {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ code: status < 400 ? 0 : status, data, msg: status < 400 ? 'OK' : 'Fixture request failed', error: '', time: Date.now() }));
  };
  const path = url.pathname;
  if (path === '/api/__wiki_fixture__/requests') return send(requests);
  if (path === '/api/__wiki_fixture__/scenario') {
    scenario = url.searchParams.get('name') || 'normal';
    if (scenario === 'failed') doc = { ...doc, run: 0, progress: -1, progress_msg: 'Wiki 生成失败，请重试' };
    if (scenario === 'cancelled') doc = { ...doc, run: 2, progress: .8, progress_msg: '用户已取消' };
    if (scenario === 'disabled') knowledge.parser_config.wiki.enabled = false;
    return send({ scenario });
  }
  if (path === '/api/__wiki_fixture__/start') {
    const user = { id: uuid(400), username: 'xxxx', email: 'wiki@example.test', role: 'admin', current_workspace_id: uuid(401) };
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Set-Cookie': 'authToken=wiki-local-fixture; Path=/; SameSite=Lax' });
    return res.end(`<script>localStorage.setItem('user', ${JSON.stringify(JSON.stringify(user))});localStorage.setItem('language','zh');location.replace('/#/knowledge-base/${kbId}/wiki');</script>`);
  }
  if (path === `/api/knowledges/${kbId}`) {
    if (req.method === 'PUT') {
      if (scenario === 'model-disabled' && payload.llm_id) return send(null, 409);
      knowledge = { ...knowledge, ...payload, parser_config: { ...knowledge.parser_config, ...payload.parser_config } };
      if (scenario === 'save-error') return send(null, 503);
    }
    return send(knowledge);
  }
  if (path === '/api/knowledges/knowledges') return send({ items: [knowledge], page: { page: 1, pagesize: 20, total: 1, hasnext: false } });
  if (path === '/api/knowledges/knowledgetype') return send(['General', 'Folder']);
  if (path === '/api/knowledges/knowledge' && req.method === 'POST') return send({ ...knowledge, ...payload, id: uuid(700) });
  if (path === '/api/models') return send(Object.values(modelOptions).flat());
  if (path === '/api/workspaces/workspace_models') return send({ llm: uuid(300), embedding: uuid(301), rerank: uuid(302) });
  if (path.startsWith('/api/files/') && path.endsWith('/files')) return send({ items: [], page: { page: 1, pagesize: 20, total: 0, has_next: false } });
  if (path.endsWith('/wiki/index')) return send({ total_pages: scenario === 'empty' ? 0 : pages.length, counts: { summary: 1, entity: 1, concept: 9 }, folders });
  const filterPages = types => pages.filter(page => !types || types.split(',').includes(page.type));
  if (path.endsWith('/wiki/tree')) {
    const folder = url.searchParams.get('folder_id');
    const visible = filterPages(url.searchParams.get('page_types'));
    let items = folder ? visible.filter(page => page.folder_id === folder).map(page => ({ ...page, node_kind: 'page', page_type: page.type, parent_id: folder }))
      : folders.filter(folder => visible.some(page => page.folder_id === folder.id))
        .map(folder => ({ ...folder, title: folder.name, node_kind: 'folder', has_children: true }));
    if (scenario === 'tree-pages' && !url.searchParams.has('cursor')) return send({ items: items.slice(0, 1), parent_id: folder, next_cursor: `1:${uuid(999)}` });
    if (scenario === 'tree-pages') items = items.slice(1);
    return send({ items: scenario === 'empty' ? [] : items, parent_id: folder, next_cursor: null });
  }
  if (path.endsWith('/wiki/pages')) {
    if (scenario === 'error') return send(null, 503);
    let items = filterPages(url.searchParams.get('page_types')).filter(page => `${page.title} ${page.summary}`.includes(url.searchParams.get('q') || ''));
    if (scenario === 'empty') items = [];
    if (scenario === 'pagination') items = Array.from({ length: 62 }, (_, i) => ({ ...pages[i % pages.length], id: uuid(1000 + i), page_id: uuid(1000 + i) }));
    const page = Number(url.searchParams.get('page') || 1);
    const limit = Number(url.searchParams.get('limit') || 50);
    return send({ items: items.slice((page - 1) * limit, page * limit), page, limit, total: items.length, has_next: page * limit < items.length });
  }
  if (path.includes('/wiki/pages/')) {
    if (scenario === 'detail-error') return send(null, 404);
    const page = pages.find(page => page.id === path.split('/').pop());
    if (!page) return send(null, 404);
    if (scenario === 'slow' && page.id === pages[0].id) await new Promise(resolve => setTimeout(resolve, 1600));
    const ready = !['updating', 'unavailable'].includes(scenario);
    const topicContent = `## 关键概念\n\n知识库 · 长期记忆\n\n## 实体说明\n\nMemoryBear 是产品白皮书中描述的知识与记忆平台。原始资料经过解析后生成摘要、概念和实体 Wiki。\n\n## 核心能力\n\n通过 [[${pages[1].id}|记忆引擎]] 保留长期上下文，通过 [[${pages[2].id}|知识检索与问答]] 提供可追溯的知识回答。\n\n## 知识组织\n\n原文件与派生 Wiki 保持关联。同名概念和实体可以汇总多个原文件中的信息。`;
    return send({ ...page, status: ready ? 'ready' : scenario, reason: ready ? null : 'WIKI_UPDATING',
      content: ready ? page.title === 'MemoryBear' ? topicContent : `## 产品与知识\n\n${page.summary} [^R1]\n\n阅读 [[${pages[1].id}|记忆引擎]]，了解上下文如何持续积累。\n\n### 来源与版本\n\n本页根据上传文档生成，页面保留版本及引用。\n\n\`[[${pages[1].id}|代码示例]] [^R1]\`\n\n\`\`\`md\n[[${pages[1].id}|代码块示例]]\n\`\`\`\n\n<script>alert('unsafe')</script>\n\n[危险链接](javascript:alert(1))` : null,
      sources: ready ? [{ ref: 'R1', document_id: documentId, document_title: doc.file_name, chunk_id: uuid(500), quote: 'MemoryBear 将文档、知识和长期记忆连接起来。<script>不可执行的来源文本</script>' },
        { ref: 'R2', document_id: documentId, document_title: doc.file_name, chunk_id: uuid(501), quote: '第二段来源片段。' }] : [],
      outlinks: ready ? [{ page_id: pages[1].id, title: pages[1].title }] : [] });
  }
  if (path.endsWith('/wiki/graph')) {
    let nodes = filterPages(url.searchParams.get('types')).map(({ id, title, type, version }) => ({ id, title, type, version }));
    let edges = pages.slice(1).map(page => ({ source: pages[0].id, target: page.id }));
    if (url.searchParams.get('mode') === 'ego') {
      const included = new Set([url.searchParams.get('center_page_id')]);
      for (let level = 0; level < Number(url.searchParams.get('depth') || 1); level++) {
        const existing = new Set(included);
        for (const edge of edges) {
          if (existing.has(edge.source)) included.add(edge.target);
          if (existing.has(edge.target)) included.add(edge.source);
        }
      }
      nodes = nodes.filter(node => included.has(node.id));
    }
    const visible = new Set(nodes.map(node => node.id));
    edges = edges.filter(edge => visible.has(edge.source) && visible.has(edge.target));
    return send({ nodes, edges, total_nodes: pages.length, total_edges: pages.length - 1, returned_nodes: nodes.length, returned_edges: edges.length, truncated: scenario === 'truncated', totals_scope: 'knowledge_base' });
  }
  if (path.endsWith('/generate') && req.method === 'POST') {
    if (!knowledge.parser_config.wiki.enabled) return send(null, 409);
    doc = { ...doc, run: 1, progress: .7, progress_msg: '正在生成 Wiki：Map 已完成，正在整理页面' };
    setTimeout(() => { doc = { ...doc, run: 0, progress: 1, progress_msg: 'Wiki 生成完成：11 / 11 个页面' }; }, 7000);
    return send({ document_id: documentId, task_id: uuid(600) }, 202);
  }
  if (path === `/api/documents/${kbId}/documents`) return send({ items: [doc], page: { page: 1, pagesize: 20, total: 1, has_next: false } });
  if (path === `/api/documents/${documentId}`) return send(doc);
  if (path.startsWith('/api/chunks/') && path.endsWith('/chunks')) return send({ items: [{ page_content: 'MemoryBear source chunk is readable during Wiki processing.', metadata: { document_id: documentId, knowledge_id: kbId, sort_id: 1 }, children: [] }], page: { total: 1, has_next: false } });
  if (path === '/api/workspaces/model_options') return send({ ...modelOptions, ...(scenario === 'model-disabled' ? { llm: [] } : {}), vision: [], audio: [], video: [] });
  if (path.startsWith('/api/models/')) return send({ ...modelOptions.llm[0], is_active: scenario !== 'model-disabled' });
  if (path === '/api/workspaces/storage') return send({ storage_type: 'neo4j' });
  if (path === '/api/notifications/sync') return send({ notifications: [], banners: [], modals: [], unread_count: 0 });
  if (path.startsWith('/api/notifications')) return send({ items: [], total: 0 });
  if (path.includes('/metadata')) return send({ items: [] });
  return send({});
});
const port = Number(process.env.WIKI_FIXTURE_PORT || 4174);
server.listen(port, '127.0.0.1', () => console.log(`Wiki fixture API listening on http://127.0.0.1:${port}`));
