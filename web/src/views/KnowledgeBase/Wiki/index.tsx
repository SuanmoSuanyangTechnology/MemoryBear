import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Button, Dropdown, Input, Skeleton, Tag, Tooltip } from 'antd';
import { ShareAltOutlined, FilterOutlined, MenuFoldOutlined, MenuUnfoldOutlined, MoreOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { getWikiIndex, getWikiKnowledge, WIKI_TYPE_FILTERS, type WikiDetail } from '@/api/wiki';
import { useBreadcrumbManager } from '@/hooks/useBreadcrumbManager';
import { useWikiResource } from './useWikiResource';
import WikiTree from './WikiTree';
import WikiSummaryList from './WikiSummaryList';
import WikiDirectory from './WikiDirectory';
import WikiReader from './WikiReader';
import WikiFiles from './WikiFiles';
import WikiSettings from './WikiSettings';
import './wiki.css';

const WikiGraph = lazy(() => import('./WikiGraph'));

function WikiWorkspace({ kbId }: { kbId: string }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [collapsed, setCollapsed] = useState(() => window.matchMedia('(max-width: 700px)').matches);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [pageDetail, setPageDetail] = useState<WikiDetail>();
  const contentRef = useRef<HTMLDivElement>(null);
  const readingPositions = useRef(new Map<string, number>());
  const readablePageId = useRef<string>();
  const query = params.get('q') ?? '';
  const [search, setSearch] = useState(query);
  const selectedId = params.get('page') ?? undefined;
  const centerId = params.get('center') ?? undefined;
  const view = params.get('view') === 'files' ? 'files' : params.get('view') === 'graph' ? 'graph' : 'wiki';
  const typeFilter = WIKI_TYPE_FILTERS.find(item => item.key === params.get('type')) ?? WIKI_TYPE_FILTERS.find(item => item.key === 'knowledge')!;
  const summaries = typeFilter.key === 'summary';
  const types = typeFilter.types.join(',') || undefined;
  const requestedPage = Number(params.get('p'));
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const loadKnowledge = useCallback((signal: AbortSignal) => getWikiKnowledge(kbId, signal), [kbId]);
  const knowledge = useWikiResource(loadKnowledge);
  const loadIndex = useCallback((signal: AbortSignal) => getWikiIndex(kbId, signal), [kbId]);
  const index = useWikiResource(loadIndex);
  const { updateBreadcrumbs } = useBreadcrumbManager({ breadcrumbType: 'detail' });

  useEffect(() => { setSearch(query); }, [query]);
  useLayoutEffect(() => { readablePageId.current = undefined; }, [selectedId]);
  useEffect(() => {
    if (knowledge.data) updateBreadcrumbs({ knowledgeBaseFolderPath: [], documentFolderPath: [],
      knowledgeBase: { id: kbId, name: knowledge.data.name, type: 'knowledgeBase' } });
  }, [kbId, knowledge.data, updateBreadcrumbs]);

  const go = (values: Record<string, string>) => setParams({ ...(typeFilter.key !== 'all' ? { type: typeFilter.key } : {}), ...values });
  const selectPage = (id: string) => {
    if (selectedId && contentRef.current && readablePageId.current === selectedId) {
      readingPositions.current.set(selectedId, contentRef.current.scrollTop);
    }
    if (window.matchMedia('(max-width: 700px)').matches) setCollapsed(true);
    go({ page: id });
  };
  const showFiles = () => go({ view: 'files' });
  const showWikiTab = () => go({ type: 'knowledge' });
  const showSummaryTab = () => go({ type: 'summary' });
  const showGraph = (center?: string) => go({ view: 'graph', ...(center ? { center } : {}) });
  const showDirectory = () => {
    setRevision(value => value + 1);
    index.reload();
    go({});
  };
  const manageFiles = () => navigate(`/knowledge-base/${kbId}/private`);
  const importFiles = () => {
    if (['Web', 'Third-party'].includes(knowledge.data?.type ?? '')) { manageFiles(); return; }
    navigate(`/knowledge-base/${kbId}/create-dataset`, { state: { source: 'local', knowledgeBaseId: kbId, parentId: kbId } });
  };
  const openDocument = (id: string) => navigate(`/knowledge-base/${kbId}/DocumentDetails?documentId=${encodeURIComponent(id)}`, {
    state: { documentId: id, wikiReturn: `/knowledge-base/${kbId}/wiki?${params.toString()}` },
  });
  const refresh = () => { setRevision(value => value + 1); index.reload(); };
  const submitSearch = (value: string) => go({ ...(view === 'files' ? { view: 'files' } : {}), ...(value.trim() ? { q: value.trim() } : {}) });
  const handlePageLoaded = useCallback((detail: WikiDetail) => {
    setPageDetail(detail);
    readablePageId.current = detail.status === 'ready' ? detail.page_id : undefined;
    if (contentRef.current && readablePageId.current) contentRef.current.scrollTop = readingPositions.current.get(detail.page_id) ?? 0;
    if ((detail.type === 'summary') !== summaries) {
      setParams(current => { const next = new URLSearchParams(current); next.set('type', detail.type === 'summary' ? 'summary' : 'knowledge'); return next; }, { replace: true });
    }
  }, [setParams, summaries]);

  if (knowledge.loading) return <div className="wiki-loading"><Skeleton active paragraph={{ rows: 12 }} /></div>;
  if (knowledge.error || !knowledge.data) return <div className="wiki-loading"><Alert type="error" showIcon message="知识库加载失败"
    description={knowledge.error} action={<Button onClick={knowledge.reload}>重试</Button>} /><Button onClick={() => navigate('/knowledge-base')}>返回知识库管理</Button></div>;
  const kb = knowledge.data;
  const enabled = kb.parser_config?.wiki?.enabled === true;
  const selectedPage = pageDetail?.page_id === selectedId ? pageDetail : undefined;
  const directoryTitle = summaries ? '摘要文档' : 'Wiki 目录';
  const title = view === 'files' ? '文件' : view === 'graph' ? 'Wiki 图谱' : selectedId ? selectedPage?.title ?? '加载页面…' : query ? '搜索结果' : directoryTitle;
  const visibleCount = index.data ? typeFilter.types.length
    ? typeFilter.types.reduce((total, type) => total + (index.data!.counts[type] ?? 0), 0)
    : index.data.total_pages : undefined;

  return <div className={`wiki-workspace ${collapsed ? 'is-collapsed' : ''}`}>
    <aside className="wiki-sidebar" aria-label="Wiki 导航">
      <div className="wiki-kb-heading"><strong title={kb.name}>{kb.name}</strong>
        <Tag title={kb.status === 1 ? '知识库已启用' : '知识库已停用'} bordered={false} color={kb.status === 1 ? 'success' : 'default'}>{kb.status === 1 ? '● 已启用' : '已停用'}</Tag>
        <Dropdown trigger={['click']} menu={{ items: [
          { key: 'settings', label: 'Wiki 设置', onClick: () => setSettingsOpen(true) },
          { key: 'files', label: '文件管理', onClick: manageFiles },
          { key: 'back', label: '返回知识库管理', onClick: () => navigate('/knowledge-base') },
        ] }}><Button type="text" icon={<MoreOutlined />} aria-label="知识库菜单" /></Dropdown>
      </div>
      <div className="wiki-search-row"><Input value={search} maxLength={400} allowClear prefix={<SearchOutlined />}
        placeholder={view === 'files' ? '搜索文件...' : summaries ? '搜索摘要文档...' : '搜索 Wiki 页面...'} aria-label={view === 'files' ? '搜索文件' : summaries ? '搜索摘要文档' : '搜索 Wiki 页面'}
        onChange={event => { setSearch(event.target.value); if (!event.target.value) submitSearch(''); }}
        onPressEnter={() => submitSearch(search)} suffix={view !== 'files' ? <Dropdown trigger={['click']} menu={{
          items: WIKI_TYPE_FILTERS.map(item => ({ key: item.key, label: item.label })), selectable: true, selectedKeys: [typeFilter.key],
          onClick: ({ key }) => go({ ...(view === 'graph' ? { view: 'graph' } : {}), type: key }),
        }}><Button type="text" size="small" icon={<FilterOutlined />} aria-label="筛选 Wiki 类型" title={typeFilter.label} /></Dropdown> : undefined} />
        <Tooltip title="导入文件"><Button icon={<PlusOutlined />} aria-label="导入文件" onClick={importFiles} /></Tooltip>
      </div>
      <button className={`wiki-graph-entry ${view === 'graph' ? 'is-active' : ''}`} onClick={() => showGraph()}><ShareAltOutlined />Wiki 图谱</button>
      <div className="wiki-tabs" role="tablist" aria-label="知识库内容">
        <button role="tab" aria-selected={view !== 'files' && !summaries} className={view !== 'files' && !summaries ? 'is-active' : ''} onClick={showWikiTab}>Wiki</button>
        <button role="tab" aria-selected={view !== 'files' && summaries} className={view !== 'files' && summaries ? 'is-active' : ''} onClick={showSummaryTab}>摘要</button>
        <button role="tab" aria-selected={view === 'files'} className={view === 'files' ? 'is-active' : ''} onClick={showFiles}>文件</button>
      </div>
      <div className="wiki-sidebar-scroll">
        {view === 'files' ? <button className="wiki-directory-entry is-selected" onClick={showFiles}>全部文件</button> : <>
          <button className={`wiki-directory-entry ${view === 'wiki' && !selectedId && !query ? 'is-selected' : ''}`} onClick={showDirectory}>{directoryTitle}</button>
          {summaries ? <WikiSummaryList key={`${kbId}:${revision}:${query}`} kbId={kbId} query={query} selectedId={selectedId} onSelect={selectPage} />
            : <WikiTree key={`${kbId}:${revision}:${types ?? ''}`} kbId={kbId} selectedId={selectedId} onSelect={selectPage} types={types} />}
        </>}
      </div>
      <div className="wiki-sidebar-footer"><span>{visibleCount !== undefined ? `${visibleCount} 个${summaries ? '摘要文档' : ' Wiki 页面'}` : index.error ? '目录统计暂不可用' : '加载目录…'}</span>
        <button onClick={() => setSettingsOpen(true)}>Wiki 设置</button></div>
    </aside>
    <main className="wiki-main">
      <header className="wiki-toolbar"><Button type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
        aria-label={collapsed ? '展开侧栏' : '收起侧栏'} onClick={() => setCollapsed(value => !value)} />
        <nav className="wiki-breadcrumb" aria-label="Wiki 路径"><button onClick={showDirectory}>Wiki</button>
          {view === 'wiki' && selectedPage ? selectedPage.folder_path.map(folder => <span className="wiki-breadcrumb-folder" key={folder.id}><span>/</span>{folder.name}</span>) : null}
          <span>/</span><span aria-current="page" title={title}>{title}</span></nav>
        <div className="wiki-toolbar-actions"><Tooltip title="刷新 Wiki"><Button type="text" icon={<ReloadOutlined />} onClick={refresh} aria-label="刷新 Wiki" /></Tooltip>
          <Button type="text" onClick={() => setSettingsOpen(true)}>Wiki 设置</Button>
          <Button type="text" icon={<ShareAltOutlined />} onClick={() => showGraph(selectedId)}>图谱</Button></div>
      </header>
      <div className="wiki-content" ref={contentRef} role="region" aria-label="Wiki 内容" tabIndex={0} key={`${view}:${selectedId ?? ''}:${query}:${revision}:${types ?? ''}`}
        onScroll={event => {
          if (view === 'wiki' && selectedId && readablePageId.current === selectedId) readingPositions.current.set(selectedId, event.currentTarget.scrollTop);
        }}>
        {view === 'files' ? <WikiFiles kbId={kbId} query={query} enabled={enabled} onDocument={openDocument} onImport={importFiles} onManage={manageFiles} />
          : view === 'graph' ? <Suspense fallback={<div className="wiki-loading"><Skeleton active /></div>}>
              <WikiGraph kbId={kbId} centerId={centerId} types={types} onSelect={selectPage} onDocument={openDocument} onOverview={() => showGraph()} /></Suspense>
          : selectedId ? <WikiReader kbId={kbId} pageId={selectedId} onSelect={selectPage} onDocument={openDocument} onGraph={showGraph} onLoaded={handlePageLoaded} />
          : <WikiDirectory kbId={kbId} query={query} page={page} enabled={enabled} onSelect={selectPage} types={types}
              onPageChange={next => go({ ...(query ? { q: query } : {}), p: String(next) })} onSettings={() => setSettingsOpen(true)} onFiles={showFiles} />}
      </div>
    </main>
    <WikiSettings knowledge={kb} open={settingsOpen} onClose={() => { setSettingsOpen(false); knowledge.reload(); refresh(); }} onSaved={refresh} />
  </div>;
}

export default function Wiki() {
  const { knowledgeBaseId } = useParams<{ knowledgeBaseId: string }>();
  return knowledgeBaseId ? <WikiWorkspace key={knowledgeBaseId} kbId={knowledgeBaseId} /> : null;
}
