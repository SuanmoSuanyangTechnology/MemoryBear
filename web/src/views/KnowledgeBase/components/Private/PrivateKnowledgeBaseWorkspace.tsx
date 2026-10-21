import { useCallback, useState, type FC, type ReactNode } from 'react';
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
  type WikiWorkspaceMode,
} from './Wiki/types';
import { useWikiData } from './hooks/useWikiData';
import { useWikiPageDetail } from './hooks/useWikiPageDetail';

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
  const [selection, setSelection] = useState({ knowledgeBaseId, pageId: WIKI_CATALOG_PAGE_ID });
  const selectedPageId = selection.knowledgeBaseId === knowledgeBaseId ? selection.pageId : WIKI_CATALOG_PAGE_ID;
  const [catalogRefreshKey, setCatalogRefreshKey] = useState(0);
  const [graphInitialPageId, setGraphInitialPageId] = useState<string>();
  const [graphPreview, setGraphPreview] = useState<{ knowledgeBaseId: string; pageId?: string }>({ knowledgeBaseId });
  const { pages } = useWikiData(knowledgeBaseId);
  const previewPageId = graphPreview.knowledgeBaseId === knowledgeBaseId ? graphPreview.pageId : undefined;
  const activePageId = mode === 'wikiGraph' ? previewPageId : mode === 'wiki' ? selectedPageId : undefined;
  const detailState = useWikiPageDetail(knowledgeBaseId,
    activePageId === WIKI_CATALOG_PAGE_ID || activePageId === WIKI_SUMMARY_PAGE_ID ? undefined : activePageId);

  const openWikiPage = useCallback((pageId: string) => {
    if (pageId === WIKI_CATALOG_PAGE_ID) setCatalogRefreshKey((value) => value + 1);
    setSelection({ knowledgeBaseId, pageId });
    setDirectoryTab('wiki');
    setMode('wiki');
  }, [knowledgeBaseId]);
  const selectGraphPreview = useCallback((pageId?: string) => {
    setGraphPreview({ knowledgeBaseId, pageId });
  }, [knowledgeBaseId]);

  const handleModeChange = (nextMode: WikiWorkspaceMode) => {
    if (nextMode === 'wikiGraph') {
      const pageId = mode === 'wiki' && selectedPageId !== WIKI_CATALOG_PAGE_ID && selectedPageId !== WIKI_SUMMARY_PAGE_ID
        ? selectedPageId : undefined;
      setGraphInitialPageId(pageId);
      setGraphPreview({ knowledgeBaseId, pageId });
    } else if (nextMode === 'files' || nextMode === 'wiki') {
      setDirectoryTab(nextMode);
    }
    if (nextMode === 'wiki') {
      setSelection({ knowledgeBaseId, pageId: WIKI_CATALOG_PAGE_ID });
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
            setGraphPreview({ knowledgeBaseId });
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
            selectedPageId={selectedPageId}
            selectedDetail={mode === 'wiki' ? detailState.detail : null}
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
                key={`${knowledgeBaseId}:${selectedPageId}:${selectedPageId === WIKI_CATALOG_PAGE_ID ? catalogRefreshKey : 0}`}
                knowledgeBase={knowledgeBase}
                knowledgeBaseId={knowledgeBaseId}
                breadcrumbPath={breadcrumbPath}
                pageId={selectedPageId}
                detailState={detailState}
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
                key={knowledgeBaseId}
                breadcrumbPath={breadcrumbPath}
                knowledgeBaseId={knowledgeBaseId}
                pages={pages}
                directoryCollapsed={directoryCollapsed}
                onToggleDirectory={onToggleDirectory}
                initialPageId={selection.knowledgeBaseId === knowledgeBaseId ? graphInitialPageId : undefined}
                detailState={detailState}
                onPreviewPageChange={selectGraphPreview}
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
