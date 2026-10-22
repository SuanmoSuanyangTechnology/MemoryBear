import { type FC, type MouseEvent } from 'react';
import { Breadcrumb, Button, Flex } from 'antd';
import { useWikiPageDetail } from '../hooks/useWikiPageDetail';
import { useTranslation } from 'react-i18next';

import WikiCatalog from './WikiCatalog';
import type { BreadcrumbPath } from '@/hooks/useBreadcrumbManager';
import type { KnowledgeBaseListItem } from '@/views/KnowledgeBase/types';
import { WIKI_CATALOG_PAGE_ID, WIKI_SUMMARY_PAGE_ID, type WikiPage, type WikiPageDetail } from './types';
import DirectoryToggle from '../DirectoryToggle';
import Empty from '@/components/Empty';
import WikiArticle from './WikiArticle';

interface WikiReaderProps {
  breadcrumbPath: BreadcrumbPath;
  knowledgeBase: KnowledgeBaseListItem;
  page?: WikiPage;
  initialDetail?: WikiPageDetail;
  summaryMode?: boolean;
  pages: WikiPage[];
  onSummaryPagesLoaded: (pages: WikiPage[]) => void;
  knowledgeBaseId: string;
  directoryCollapsed: boolean;
  onToggleDirectory: () => void;
  loading: boolean;
  error: boolean;
  onOpenGraph: () => void;
  onOpenFiles: () => void;
  onOpenKnowledgeBaseSettings: (step?: number) => void;
  onPageSelect: (pageId: string) => void;
}

const WikiReader: FC<WikiReaderProps> = ({
  breadcrumbPath,
  page,
  initialDetail,
  summaryMode = false,
  onSummaryPagesLoaded,
  knowledgeBase,
  knowledgeBaseId,
  loading,
  error,
  onOpenGraph,
  onOpenFiles,
  onOpenKnowledgeBaseSettings,
  onPageSelect,
  directoryCollapsed,
  onToggleDirectory,
}) => {
  const { t } = useTranslation();
  const pageId = page?.id;
  const isSummary = pageId === WIKI_SUMMARY_PAGE_ID;
  const isCatalog = pageId === WIKI_CATALOG_PAGE_ID || isSummary;
  const {
    detail,
    loading: detailLoading,
    error: detailError,
    refresh,
  } = useWikiPageDetail(knowledgeBaseId, isCatalog ? undefined : pageId, initialDetail);
  const markdown = detail?.content ?? '';

  const handleMarkdownClick = (event: MouseEvent<HTMLDivElement>) => {
    const link = (event.target as HTMLElement).closest<HTMLAnchorElement>(
      'a[href^="#wiki-page="]',
    );
    const href = link?.getAttribute('href');
    if (!href) return;

    event.preventDefault();
    const pageId = href.slice('#wiki-page='.length);
    if (pageId) onPageSelect(decodeURIComponent(pageId));
  };

  const directoryToggle = (
    <DirectoryToggle
      directoryCollapsed={directoryCollapsed}
      onToggleDirectory={onToggleDirectory}
    />
  );

  const breadcrumb = (
    <Breadcrumb
      className="rb:min-w-0 rb:text-[12px]!"
      items={[
        {
          title: (
            <span
              className="rb:cursor-pointer rb:border-0 rb:bg-transparent rb:p-0! rb:text-inherit rb:hover:text-blue-500"
              onClick={() => onPageSelect(summaryMode ? WIKI_SUMMARY_PAGE_ID : WIKI_CATALOG_PAGE_ID)}
            >
              {t(summaryMode ? 'knowledgeBase.wikiSummary' : 'knowledgeBase.wiki')}
            </span>
          ),
        },
        ...(page
          ? [
            ...(isCatalog ? [] : (page.category ? [{ title: page.category }] : [])),
            ...(page.title ? [{ title: page.title }] : []),
          ]
          : []
        ),
      ]}
    />
  );

  const wikiDisabled = !knowledgeBase?.parser_config?.wiki?.enabled;
  const noSourceFiles = !(knowledgeBase.doc_num ?? 0);

  if (loading || error || wikiDisabled || noSourceFiles || !page) {
    const emptyProps = wikiDisabled
      ? {
        size: 140,
        title: t('knowledgeBase.privateWiki.disabled'),
        subTitle: t('knowledgeBase.privateWiki.disabledHint'),
      }
      : error
        ? { subTitle: t('knowledgeBase.wikiLoadFailed') }
        : noSourceFiles
          ? {
            size: 140,
            title: t('knowledgeBase.wikiNoSourceFiles'),
            subTitle: t('knowledgeBase.wikiNoSourceFilesDescription'),
          }
          : { subTitle: t('knowledgeBase.wikiEmptyDescription') };

    return (
      <Flex vertical className="rb:h-full">
        <Flex
          align="center"
          gap={12}
          className="rb:min-h-12 rb:shrink-0 rb:px-5! rb:py-2!"
        >
          {directoryToggle}
          {breadcrumb}
        </Flex>
        <Flex
          align="center"
          justify="center"
          className="rb:flex-1 rb:p-8"
        >
          {loading ? t('common.loading') : (
            <Flex vertical align="center" justify="center">
              <Empty {...emptyProps} />
              {noSourceFiles && !wikiDisabled && (
                <Button type="primary" onClick={onOpenFiles}>
                  {t('knowledgeBase.uploadFile')}
                </Button>
              )}
              {wikiDisabled && (
                <Button type="primary" onClick={() => onOpenKnowledgeBaseSettings(2)}>
                  {t('knowledgeBase.privateWiki.configure')}
                </Button>
              )}
            </Flex>
          )}
        </Flex>
      </Flex>
    );
  }

  return (
    <Flex
      vertical
      className="rb:h-full rb:min-w-0 rb:overflow-hidden rb:bg-white"
    >
      <Flex
        align="center"
        justify="space-between"
        gap={12}
        className="rb:shrink-0 rb:py-3! rb:pr-3!"
      >
        <Flex
          align="center"
          gap={12}
          className="rb:min-w-0 rb:flex-1!"
        >
          {directoryToggle}
          {breadcrumb}
        </Flex>

        <Flex
          align="center"
          gap={4}
          className="rb:shrink-0!"
        >
          <Button
            type="text"
            icon={
              <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/graph.svg')]" />
            }
            onClick={onOpenGraph}
          >
            {t('knowledgeBase.graph')}
          </Button>
        </Flex>
      </Flex>
      <div className="rb:flex-1 rb:pl-5! rb:pr-8! rb:py-3! rb:overflow-hidden">
        <div
          className="rb:mx-auto rb:max-w-250 rb:h-full rb:overflow-hidden"
          onClick={handleMarkdownClick}
        >
          {isCatalog ? (
            <WikiCatalog
              knowledgeBaseId={knowledgeBaseId}
              summaryOnly={isSummary}
              onPagesLoaded={onSummaryPagesLoaded}
              onPageSelect={onPageSelect}
            />
          ) : (
            <WikiArticle
              breadcrumbPath={breadcrumbPath}
              detail={detail}
              markdown={markdown}
              loading={detailLoading}
              error={detailError}
              onReload={refresh}
              onPageSelect={onPageSelect}
            />
          )}
        </div>
      </div>
    </Flex>
  );
};

export default WikiReader;
