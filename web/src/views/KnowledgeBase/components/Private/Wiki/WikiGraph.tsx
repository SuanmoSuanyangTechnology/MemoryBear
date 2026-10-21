import { useEffect, useMemo, useRef, useState, type FC } from 'react';
import { Button, Flex, Tooltip } from 'antd';
import { AimOutlined, MinusOutlined, PlusOutlined } from '@ant-design/icons';
import WikiD3Graph, { type WikiD3GraphRef } from './WikiD3Graph';
import { useTranslation } from 'react-i18next';

import type { BreadcrumbPath } from '@/hooks/useBreadcrumbManager';
import WikiArticle from './WikiArticle';
import { useWikiPageDetail } from '../hooks/useWikiPageDetail';
import Empty from '@/components/Empty';

import { getKnowledgeWikiGraph } from '@/api/kbWiki';
import { WIKI_CATALOG_PAGE_ID, type PageType, type WikiGraph as WikiGraphData, type WikiPage, type WikiRelation } from './types';
import DirectoryToggle from '../DirectoryToggle';

interface WikiGraphProps {
  breadcrumbPath: BreadcrumbPath;
  pages: WikiPage[];
  knowledgeBaseId: string;
  directoryCollapsed: boolean;
  onToggleDirectory: () => void;
  initialPageId?: string;
  onBack: () => void;
  onOpenPage: (pageId: string) => void;
}

const COLORS: Record<PageType, string> = {
  entity: '#4C79CC',
  concept: '#9B82C8',
  summary: '#40A599',
};

const WikiGraph: FC<WikiGraphProps> = ({
  pages: wikiPages,
  breadcrumbPath,
  knowledgeBaseId,
  initialPageId,
  onBack,
  onOpenPage,
  directoryCollapsed,
  onToggleDirectory,
}) => {
  const { t } = useTranslation();
  const graphRef = useRef<WikiD3GraphRef>(null);
  const [centerPageId, setCenterPageId] = useState(
    initialPageId === WIKI_CATALOG_PAGE_ID ? undefined : initialPageId,
  );

  useEffect(() => {
    setCenterPageId(initialPageId === WIKI_CATALOG_PAGE_ID ? undefined : initialPageId);
  }, [initialPageId, knowledgeBaseId]);
  const [selectedPageId, setSelectedPageId] = useState(centerPageId);
  const [graph, setGraph] = useState<WikiGraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    setGraph(null);
    setLoading(true);
    setError(false);
    setSelectedPageId(centerPageId);
    setPreviewVisible(true);

    if (!knowledgeBaseId) {
      setLoading(false);
      return;
    }

    getKnowledgeWikiGraph(knowledgeBaseId, {
      mode: centerPageId ? 'ego' : 'overview',
      ...(centerPageId ? { center_page_id: centerPageId } : {}),
      types: 'entity,concept' as PageType,
      limit: 200,
    }).then((data) => {
      if (active) {
        setGraph(data);
      }
    }).catch(() => {
      if (active) {
        setError(true);
      }
    }).finally(() => {
      if (active) {
        setLoading(false);
      }
    });

    return () => {
      active = false;
    };
  }, [knowledgeBaseId, centerPageId]);

  const relations = useMemo<WikiRelation[]>(() => (
    graph?.edges.map((edge) => ({
      source: edge.source,
      target: edge.target,
      description: '',
      weight: 1,
    })) ?? []
  ), [graph]);
  const pages = useMemo<WikiPage[]>(() => {
    const pageMap = new Map(wikiPages.map((page) => [page.id, page]));
    const relatedIds = new Map<string, string[]>();
    const addRelation = (source: string, target: string) => {
      const ids = relatedIds.get(source) ?? [];
      ids.push(target);
      relatedIds.set(source, ids);
    };
    relations.forEach(({ source, target }) => {
      addRelation(source, target);
      if (source !== target) addRelation(target, source);
    });
    return graph?.nodes.map((node) => {
      const page = pageMap.get(node.id);
      return {
        id: node.id,
        title: node.title,
        category: node.type,
        summary: page?.summary ?? '',
        markdown: page?.markdown ?? `# ${node.title}`,
        sourceIds: page?.sourceIds ?? [],
        relatedIds: relatedIds.get(node.id) ?? [],
        importance: page?.importance ?? 0,
      };
    }) ?? [];
  }, [graph, wikiPages, relations]);
  const [previewVisible, setPreviewVisible] = useState(true);
  const {
    detail,
    loading: detailLoading,
    error: detailError,
    refresh,
  } = useWikiPageDetail(knowledgeBaseId, selectedPageId);

  const handleSelectPage = (pageId: string) => {
    setSelectedPageId(pageId);
    setPreviewVisible(true);
    if (!pages.some((page) => page.id === pageId)) {
      setCenterPageId(pageId);
    }
  };
  const categories = useMemo(
    () => [...new Set(pages.map((page) => page.category))],
    [pages],
  );

  const navigation = (
    <Flex
      align="center"
      gap={12}
    >
      <DirectoryToggle
        directoryCollapsed={directoryCollapsed}
        onToggleDirectory={onToggleDirectory}
      />
      <Button
        icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_right_dark.svg')] rb:rotate-180" />}
        onClick={onBack}
      >
        {t('knowledgeBase.backToWiki')}
      </Button>
    </Flex>
  );

  if (loading || error || !pages.length) {
    return (
      <Flex
        vertical
        className="rb:h-full"
      >
        <div className="rb:p-4!">{navigation}</div>
        <Flex
          align="center"
          justify="center"
          className="rb:flex-1 rb:p-8"
        >
          {loading ? (
            t('common.loading')
          ) : (
            <Empty
              size={140}
              subTitle={
                error
                  ? t('knowledgeBase.wikiLoadFailed')
                  : t('knowledgeBase.wikiGraphEmpty')
              }
            />
          )}
        </Flex>
      </Flex>
    );
  }

  return (
    <Flex className="rb:h-full rb:min-w-0 rb:overflow-hidden rb:bg-white">
      <div className="rb:relative rb:min-w-0 rb:flex-1">
        <div className="rb:absolute rb:left-0 rb:top-3 rb:z-10">{navigation}</div>
        <WikiD3Graph
          ref={graphRef}
          pages={pages}
          relations={relations}
          colors={COLORS}
          selectedPageId={selectedPageId}
          onSelect={handleSelectPage}
        />
        <Flex align="center" justify="end" wrap gap={12} className="rb:pointer-events-none rb:absolute rb:right-6 rb:top-3 rb:max-w-80 rb:h-8 rb:px-3! rb-border rb:rounded-lg rb:text-[12px] rb:text-gray-600">
          {categories.map((category) => (
            <Flex key={category} align="center" gap={4}>
              <span
                className="rb:size-2 rb:rounded-full"
                style={{ backgroundColor: COLORS[category as PageType] ?? '#6B8FA3' }}
              />
              {t(`knowledgeBase.${category}`)}
            </Flex>
          ))}
        </Flex>

        <Flex className="rb:absolute rb:bottom-4 rb:left-0 rb:z-10 rb:rounded-lg rb:border rb:border-gray-200 rb:bg-white rb:p-1!">
          <Tooltip title={t('knowledgeBase.wikiGraphZoomIn')}>
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined />}
              aria-label={t('knowledgeBase.wikiGraphZoomIn')}
              onClick={() => graphRef.current?.zoomIn()}
            />
          </Tooltip>
          <Tooltip title={t('knowledgeBase.wikiGraphZoomOut')}>
            <Button
              type="text"
              size="small"
              icon={<MinusOutlined />}
              aria-label={t('knowledgeBase.wikiGraphZoomOut')}
              onClick={() => graphRef.current?.zoomOut()}
            />
          </Tooltip>
          <Tooltip title={t('knowledgeBase.wikiGraphFitView')}>
            <Button
              type="text"
              size="small"
              icon={<AimOutlined />}
              aria-label={t('knowledgeBase.wikiGraphFitView')}
              onClick={() => graphRef.current?.fitView()}
            />
          </Tooltip>
        </Flex>
        <div className="rb:pointer-events-none rb:absolute rb:bottom-4 rb:right-3 rb:text-[12px] rb:text-gray-600">
          {t('knowledgeBase.wikiGraphStats', { nodes: pages.length, edges: relations.length })}
        </div>
      </div>

      {previewVisible && selectedPageId && (
        <Flex
          vertical
          className="rb:w-100 rb:max-w-[50%] rb:min-h-0 rb:shrink-0 rb-border-l rb:bg-white rb:p-5! rb:h-full! rb:overflow-hidden!"
        >
          <Flex align="center" justify={detail?.type ? "space-between" : 'flex-end'} className="rb:mb-3!">
            {detail?.type &&
              <Flex align="center" gap={4}>
                <span
                  className="rb:size-2 rb:rounded-full"
                  style={{ backgroundColor: COLORS[detail?.type as PageType] ?? '#6B8FA3' }}
                />
                {t(`knowledgeBase.${detail?.type}`)}
              </Flex>
            }
            <div
              className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/close_grey.svg')]"
              onClick={() => {
                setPreviewVisible(false);
                setSelectedPageId(undefined);
              }}
            />
          </Flex>

          <div
            className="rb:min-h-0 rb:flex-1 rb:overflow-hidden rb:text-[13px]"
            onClick={(event) => {
              const link = (event.target as HTMLElement).closest<HTMLAnchorElement>(
                'a[href^="#wiki-page="]',
              );
              const href = link?.getAttribute('href');
              if (!href) return;

              event.preventDefault();
              const pageId = href.slice('#wiki-page='.length);
              if (pageId) {
                handleSelectPage(decodeURIComponent(pageId));
              }
            }}
          >
            <WikiArticle
              key={selectedPageId}
              breadcrumbPath={breadcrumbPath}
              detail={detail}
              markdown={detail?.content ?? ''}
              loading={detailLoading}
              error={detailError}
              onReload={refresh}
              onTitleClick={() => onOpenPage(selectedPageId)}
              onPageSelect={handleSelectPage}
            />
          </div>
        </Flex>
      )}
    </Flex>
  );
};

export default WikiGraph;
