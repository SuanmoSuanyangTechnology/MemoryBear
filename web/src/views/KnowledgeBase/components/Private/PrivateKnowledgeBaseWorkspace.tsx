import { useEffect, useMemo, useRef, useState, type FC, type ReactNode } from 'react';
import { App, Flex, Tabs } from 'antd';
import { getKnowledgeWikiPageDetail } from '@/api/kbWiki';
import FaqWorkspace from './FAQ/FaqWorkspace';
import KnowledgeGraphCard from '../KnowledgeGraphCard';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

import type { BreadcrumbPath } from '@/hooks/useBreadcrumbManager';
import type { KnowledgeBaseListItem } from '@/views/KnowledgeBase/types';

import PrivateDirectoryPanel from './PrivateDirectoryPanel';
import PrivateKnowledgeBaseHeader from './PrivateKnowledgeBaseHeader';
import WikiGraph from './Wiki/WikiGraph';
import WikiReader from './Wiki/WikiReader';
import {
  WIKI_CATALOG_PAGE_ID,
  WIKI_SUMMARY_PAGE_ID,
  type WikiPage,
  type WikiPageDetail,
  type WikiWorkspaceMode,
} from './Wiki/types';
import { useWikiData } from './hooks/useWikiData';

interface PrivateKnowledgeBaseWorkspaceProps {
  breadcrumbPath: BreadcrumbPath;
  knowledgeBase: KnowledgeBaseListItem;
  knowledgeBaseId: string;
  directoryCollapsed: boolean;
  onToggleDirectory: () => void;
  fileDirectory: ReactNode;
  fileContent: ReactNode;
  onCopyKnowledgeBaseId: () => void;
  onKnowledgeBaseStatusChange: (checked: boolean) => void;
  onShareKnowledgeBase: () => void;
  onOpenRecallTest: () => void;
  onOpenMetadata: () => void;
  onOpenKnowledgeBaseSettings: (step?: number) => void;
  onEditKnowledgeBase: () => void;
  onBatchDownloadKnowledgeBase: () => void;
  onExportKnowledgeBaseQa: () => void;
}

const buildCatalogPage = (
  pages: WikiPage[],
  title: string,
  summary: string,
): WikiPage => {
  return {
    id: WIKI_CATALOG_PAGE_ID,
    title,
    category: title,
    summary,
    markdown: '',
    sourceIds: [...new Set(pages.flatMap((page) => page.sourceIds))],
    relatedIds: pages.map((page) => page.id),
    importance: 1,
  };
};

const PrivateKnowledgeBaseWorkspace: FC<PrivateKnowledgeBaseWorkspaceProps> = ({
  breadcrumbPath,
  knowledgeBase,
  knowledgeBaseId,
  directoryCollapsed,
  onToggleDirectory,
  fileDirectory,
  fileContent,
  onCopyKnowledgeBaseId,
  onKnowledgeBaseStatusChange,
  onShareKnowledgeBase,
  onOpenRecallTest,
  onOpenMetadata,
  onOpenKnowledgeBaseSettings,
  onEditKnowledgeBase,
  onBatchDownloadKnowledgeBase,
  onExportKnowledgeBaseQa,
}) => {
  const { t } = useTranslation();
  const [mode, setMode] = useState<WikiWorkspaceMode>('files');
  const { message } = App.useApp();
  const navigationRequest = useRef(0);
  const [openedPage, setOpenedPage] = useState<WikiPage>();
  const [openedDetail, setOpenedDetail] = useState<WikiPageDetail>();
  const [directoryTab, setDirectoryTab] = useState<'files' | 'wiki' | 'summary'>('files');
  const [selectedPageId, setSelectedPageId] = useState<string>();
  const [catalogRefreshKey, setCatalogRefreshKey] = useState(0);
  const [graphInitialPageId, setGraphInitialPageId] = useState<string>();
  const { pages, loading, error } = useWikiData(knowledgeBaseId);
  const [summaryPages, setSummaryPages] = useState<WikiPage[]>([]);
  useEffect(() => {
    setSummaryPages([]);
    setOpenedPage(undefined);
    setSelectedPageId(undefined);
    return () => {
      navigationRequest.current += 1;
    };
  }, [knowledgeBaseId]);
  const allPages = useMemo(() => [...pages, ...summaryPages], [pages, summaryPages]);
  const catalogPage = useMemo(
    () =>
      buildCatalogPage(
        pages,
        t('knowledgeBase.wikiCatalog'),
        t('knowledgeBase.wikiCatalogSummary'),
      ),
    [pages, t],
  );
  const selectedPage =
    selectedPageId === WIKI_CATALOG_PAGE_ID
      ? catalogPage
      : selectedPageId === WIKI_SUMMARY_PAGE_ID
        ? { ...catalogPage, id: WIKI_SUMMARY_PAGE_ID, title: t('knowledgeBase.summaryCatalog') }
        : (openedPage?.id === selectedPageId ? openedPage : allPages.find((page) => page.id === selectedPageId));

  useEffect(() => {
    if (!selectedPageId && pages[0]) setSelectedPageId(pages[0].id);
  }, [pages, selectedPageId]);

  const openWikiPage = async (pageId: string) => {
    const requestId = ++navigationRequest.current;
    let nextMode: 'wiki' | 'summary' = 'wiki';
    if (pageId === WIKI_CATALOG_PAGE_ID || pageId === WIKI_SUMMARY_PAGE_ID) {
      nextMode = pageId === WIKI_SUMMARY_PAGE_ID ? 'summary' : 'wiki';
      setCatalogRefreshKey((value) => value + 1);
    } else {
      try {
        const detail = await getKnowledgeWikiPageDetail(knowledgeBaseId, pageId);
        if (requestId !== navigationRequest.current) return;
        nextMode = detail.type === 'summary' ? 'summary' : 'wiki';
        setOpenedDetail(detail);
        setOpenedPage({
          id: pageId,
          title: detail.title,
          category: detail.folder_path.map((folder) => folder.name).join(' / '),
          folderId: detail.folder_id,
          folderPath: detail.folder_path,
          summary: detail.summary,
          markdown: detail.content ?? '',
          sourceIds: detail.sources.map((source) => source.document_id),
          relatedIds: detail.outlinks.map((link) => link.page_id),
          importance: 0,
        });
      } catch {
        if (requestId === navigationRequest.current) {
          message.error(t('knowledgeBase.wikiLoadFailed'));
        }
        return;
      }
    }
    setSelectedPageId(pageId);
    setDirectoryTab(nextMode);
    setMode(nextMode);
  };

  const handleModeChange = (nextMode: WikiWorkspaceMode, fromTabs = false) => {
    navigationRequest.current += 1;
    if (nextMode === 'wikiGraph') {
      setGraphInitialPageId(
        (mode === 'wiki' || mode === 'summary') && selectedPage?.id !== WIKI_CATALOG_PAGE_ID && selectedPage?.id !== WIKI_SUMMARY_PAGE_ID
          ? selectedPage?.id
          : undefined,
      );
    } else if (nextMode === 'files' || nextMode === 'wiki' || nextMode === 'summary') {
      setDirectoryTab(nextMode);
    }
    if (
      (nextMode === 'wiki' || nextMode === 'summary') &&
      (fromTabs || !(mode === 'wikiGraph' && nextMode === directoryTab))
    ) {
      setSelectedPageId(nextMode === 'summary' ? WIKI_SUMMARY_PAGE_ID : WIKI_CATALOG_PAGE_ID);
      setCatalogRefreshKey((value) => value + 1);
    }
    setMode(nextMode);
  };

  return (
    <Flex vertical className="rb:h-full rb:min-w-0 rb:overflow-hidden rb:rounded-xl rb:bg-white">
      <PrivateKnowledgeBaseHeader
        knowledgeBase={knowledgeBase}
        onCopyKnowledgeBaseId={onCopyKnowledgeBaseId}
        onKnowledgeBaseStatusChange={onKnowledgeBaseStatusChange}
        onShareKnowledgeBase={onShareKnowledgeBase}
        onOpenRecallTest={onOpenRecallTest}
        onOpenMetadata={onOpenMetadata}
        onOpenKnowledgeBaseSettings={onOpenKnowledgeBaseSettings}
        onEditKnowledgeBase={onEditKnowledgeBase}
        onBatchDownloadKnowledgeBase={onBatchDownloadKnowledgeBase}
        onExportKnowledgeBaseQa={onExportKnowledgeBaseQa}
      />
      <Tabs
        className="rb:mx-4! rb:[&.ant-tabs-top>.ant-tabs-nav]:mb-0!"
        activeKey={mode}
        onChange={(key) => {
          if (key === 'wikiGraph') {
            navigationRequest.current += 1;
            setGraphInitialPageId(undefined);
            setMode('wikiGraph');
            return;
          }
          handleModeChange(key as WikiWorkspaceMode, true);
        }}
        items={[
          { key: 'files', label: t('knowledgeBase.files') },
          { key: 'wiki', label: t('knowledgeBase.wiki') },
          { key: 'summary', label: t('knowledgeBase.wikiSummary') },
          { key: 'faq', label: 'FAQ' },
          { key: 'wikiGraph', label: t('knowledgeBase.wikiGraph') },
          { key: 'knowledgeGraph', label: t('knowledgeBase.knowledgeGraph') },
        ]}
      />
      <Flex className="rb:flex-1 rb:min-h-0 rb:overflow-hidden">
        <div
          className={clsx(
            'rb:h-full rb:shrink-0 rb:overflow-hidden rb:transition-[width] rb:duration-300 rb:ease-in-out',
            directoryCollapsed ? 'rb:pointer-events-none rb:w-0' : 'rb:w-63',
            mode !== 'files' && mode !== 'wiki' && mode !== 'summary' && 'rb:hidden',
          )}
        >
          <PrivateDirectoryPanel
            key={knowledgeBaseId}
            knowledgeBaseId={knowledgeBaseId}
            directoryTab={directoryTab}
            wikiActive={mode === 'wiki' || mode === 'summary'}
            pages={[
              ...(directoryTab === 'summary' ? summaryPages : pages),
              ...(openedPage && openedPage.id === selectedPageId ? [openedPage] : []),
            ]}
            selectedPageId={selectedPage?.id}
            fileDirectory={fileDirectory}
            onPageSelect={openWikiPage}
            onTreeLoaded={(pageId) => {
              if (selectedPageId !== pageId) void openWikiPage(pageId);
            }}
          />
        </div>

        <div className="rb:flex-1 rb:min-w-0 rb:pl-3">
          <div className={clsx('rb:h-full', mode !== 'files' && 'rb:hidden')}>
            {fileContent}
          </div>
          {mode === 'faq'
            ? <FaqWorkspace key={knowledgeBaseId} knowledgeBaseId={knowledgeBaseId} onOpenWikiPage={openWikiPage} />
            : mode === 'knowledgeGraph'
            ? <KnowledgeGraphCard knowledgeBase={knowledgeBase} onRebuildGraph={() => onOpenKnowledgeBaseSettings(1)} />
            : mode === 'wiki' || mode === 'summary'
            ? (
              <WikiReader
                key={`${knowledgeBaseId}:${selectedPage?.id}:${catalogRefreshKey}`}
                summaryMode={mode === 'summary'}
                knowledgeBase={knowledgeBase}
                knowledgeBaseId={knowledgeBaseId}
                breadcrumbPath={breadcrumbPath}
                page={selectedPage}
                initialDetail={openedPage?.id === selectedPageId ? openedDetail : undefined}
                pages={pages}
                onSummaryPagesLoaded={setSummaryPages}
                loading={loading}
                error={error}
                directoryCollapsed={directoryCollapsed}
                onToggleDirectory={onToggleDirectory}
                onOpenGraph={() => handleModeChange('wikiGraph')}
                onOpenFiles={() => handleModeChange('files')}
                onOpenKnowledgeBaseSettings={onOpenKnowledgeBaseSettings}
                onPageSelect={openWikiPage}
              />
            )
            : mode === 'wikiGraph'
            ? (
              <WikiGraph
                breadcrumbPath={breadcrumbPath}
                knowledgeBaseId={knowledgeBaseId}
                pages={pages}
                initialPageId={graphInitialPageId}
                onBack={() => handleModeChange(directoryTab === 'summary' ? 'summary' : 'wiki')}
                onOpenPage={openWikiPage}
              />
            )
            : null
          }
        </div>
      </Flex>
    </Flex>
  );
};

export default PrivateKnowledgeBaseWorkspace;
