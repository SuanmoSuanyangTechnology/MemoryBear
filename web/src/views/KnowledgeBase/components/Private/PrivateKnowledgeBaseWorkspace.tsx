import { useEffect, useMemo, useState, type FC, type ReactNode } from 'react';
import { Flex, Tabs } from 'antd';
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
  onBatchDownloadKnowledgeBase,
  onExportKnowledgeBaseQa,
}) => {
  const { t } = useTranslation();
  const [mode, setMode] = useState<WikiWorkspaceMode>('files');
  const [directoryTab, setDirectoryTab] = useState<'files' | 'wiki'>('files');
  const [selectedPageId, setSelectedPageId] = useState<string>();
  const [catalogRefreshKey, setCatalogRefreshKey] = useState(0);
  const [graphInitialPageId, setGraphInitialPageId] = useState<string>();
  const { pages, loading, error } = useWikiData(knowledgeBaseId);
  const [summaryPages, setSummaryPages] = useState<WikiPage[]>([]);
  useEffect(() => {
    setSummaryPages([]);
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
        ? { ...catalogPage, id: WIKI_SUMMARY_PAGE_ID, title: t('knowledgeBase.wikiSummary') }
        : (allPages.find((page) => page.id === selectedPageId) ?? pages[0]);

  useEffect(() => {
    if (!selectedPageId && pages[0]) setSelectedPageId(pages[0].id);
    if (selectedPageId === WIKI_CATALOG_PAGE_ID || selectedPageId === WIKI_SUMMARY_PAGE_ID) {
      return;
    }
    if (
      selectedPageId &&
      pages.length &&
      !allPages.some((page) => page.id === selectedPageId)
    ) {
      setSelectedPageId(pages[0].id);
    }
  }, [pages, allPages, selectedPageId]);

  const openWikiPage = (pageId: string) => {
    if (pageId === WIKI_CATALOG_PAGE_ID) {
      setCatalogRefreshKey((value) => value + 1);
    }
    setSelectedPageId(pageId);
    setDirectoryTab('wiki');
    setMode('wiki');
  };

  const handleModeChange = (nextMode: WikiWorkspaceMode) => {
    if (nextMode === 'wikiGraph') {
      setGraphInitialPageId(
        mode === 'wiki' && selectedPage?.id !== WIKI_CATALOG_PAGE_ID && selectedPage?.id !== WIKI_SUMMARY_PAGE_ID
          ? selectedPage?.id
          : undefined,
      );
    } else if (nextMode === 'files' || nextMode === 'wiki') {
      setDirectoryTab(nextMode);
    }
    if (nextMode === 'wiki') {
      setSelectedPageId(WIKI_CATALOG_PAGE_ID);
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
        onBatchDownloadKnowledgeBase={onBatchDownloadKnowledgeBase}
        onExportKnowledgeBaseQa={onExportKnowledgeBaseQa}
      />
      <Tabs
        className="rb:mx-4! rb:[&.ant-tabs-top>.ant-tabs-nav]:mb-0!"
        activeKey={mode}
        onChange={(key) => {
          if (key === 'wikiGraph') {
            setGraphInitialPageId(undefined);
            setMode('wikiGraph');
            return;
          }
          handleModeChange(key as WikiWorkspaceMode);
        }}
        items={[
          { key: 'files', label: t('knowledgeBase.files') },
          { key: 'wiki', label: t('knowledgeBase.wiki') },
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
            mode !== 'files' && mode !== 'wiki' && 'rb:hidden',
          )}
        >
          <PrivateDirectoryPanel
            key={knowledgeBaseId}
            knowledgeBaseId={knowledgeBaseId}
            directoryTab={directoryTab}
            wikiActive={mode === 'wiki'}
            pages={pages}
            selectedPageId={selectedPage?.id}
            fileDirectory={fileDirectory}
            onPageSelect={openWikiPage}
          />
        </div>

        <div className="rb:flex-1 rb:min-w-0 rb:pl-3">
          {mode === 'faq'
            ? <FaqWorkspace key={knowledgeBaseId} knowledgeBaseId={knowledgeBaseId} onOpenWikiPage={openWikiPage} />
            : mode === 'knowledgeGraph'
            ? <KnowledgeGraphCard knowledgeBase={knowledgeBase} onRebuildGraph={onOpenKnowledgeBaseSettings} />
            : mode === 'wiki'
            ? (
              <WikiReader
                key={`${knowledgeBaseId}:${selectedPage?.id}:${selectedPage?.id === WIKI_CATALOG_PAGE_ID ? catalogRefreshKey : 0}`}
                knowledgeBase={knowledgeBase}
                knowledgeBaseId={knowledgeBaseId}
                breadcrumbPath={breadcrumbPath}
                page={selectedPage}
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
                directoryCollapsed={directoryCollapsed}
                onToggleDirectory={onToggleDirectory}
                initialPageId={graphInitialPageId}
                onBack={() => {
                  setDirectoryTab('wiki');
                  setMode('wiki');
                }}
                onOpenPage={openWikiPage}
              />
            )
            : mode === 'files'
            ? fileContent
            : null
          }
        </div>
      </Flex>
    </Flex>
  );
};

export default PrivateKnowledgeBaseWorkspace;
