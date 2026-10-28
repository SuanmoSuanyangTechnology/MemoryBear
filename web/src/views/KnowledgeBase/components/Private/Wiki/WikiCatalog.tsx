import { useRef } from 'react';
import { Skeleton, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import { getKnowledgeWikiPagesUrl } from '@/api/kbWiki';
import PageScrollList from '@/components/PageScrollList';
import type { WikiPage, WikiPageItem, WikiPagesQuery, WikiPagesResponse } from './types';
import Empty from '@/components/Empty';

interface WikiCatalogProps {
  knowledgeBaseId: string;
  summaryOnly: boolean;
  keyword?: string;
  onPagesLoaded: (pages: WikiPage[]) => void;
  onPageSelect: (id: string) => void;
}

const toWikiPages = (items: WikiPageItem[]): WikiPage[] => {
  const pages = new Map<string, WikiPage>();
  items.forEach((item) => {
    const id = item.page_id || item.id;
    pages.set(id, {
      id,
      title: item.title,
      category: item.folder_path.map((folder) => folder.name).join(' / '),
      folderId: item.folder_id,
      folderPath: item.folder_path,
      summary: item.summary,
      markdown: '',
      sourceIds: [],
      relatedIds: [],
      importance: 0,
    });
  });
  return [...pages.values()];
};

const WikiCatalog = ({
  knowledgeBaseId,
  summaryOnly,
  keyword,
  onPagesLoaded,
  onPageSelect,
}: WikiCatalogProps) => {
  const { t } = useTranslation();
  const loadedItemsRef = useRef<WikiPageItem[]>([]);

  return (
    <Flex vertical gap={16} className="rb:h-full! rb:overflow-hidden!">
      <h1 className="rb:text-2xl rb:font-semibold rb:shrink-0">
        {t(summaryOnly ? 'knowledgeBase.summaryCatalog' : 'knowledgeBase.wikiCatalog')}
      </h1>
      <PageScrollList<WikiPageItem, WikiPagesQuery>
        key={JSON.stringify([knowledgeBaseId, summaryOnly, keyword])}
        url={getKnowledgeWikiPagesUrl(knowledgeBaseId)}
        query={{
          page_types: summaryOnly ? 'summary' : 'entity,concept',
          keyword: keyword || undefined,
          pagesize: 50,
        }}
        heightClass="rb:flex-1!"
        pageLoading={<Skeleton active paragraph={{ rows: 2 }} />}
        empty={(
          <Empty
            subTitle={t('knowledgeBase.wikiEmptyDescription')}
            size={140}
            className="rb:h-full!"
          />
        )}
        onLoad={(response: WikiPagesResponse) => {
          loadedItemsRef.current = response.page.page === 1
            ? response.items
            : [...loadedItemsRef.current, ...response.items];
          if (summaryOnly) onPagesLoaded(toWikiPages(loadedItemsRef.current));
        }}
        renderItems={(items) => {
          const groups = new Map<string, WikiPage[]>();
          toWikiPages(items).forEach((page) => {
            const key = page.folderId ?? '';
            const group = groups.get(key) ?? [];
            group.push(page);
            groups.set(key, group);
          });

          return (
            <Flex vertical gap={16}>
              <blockquote className="rb:border-l-4 rb:border-gray-200 rb:pl-4 rb:text-gray-500">
                {t(summaryOnly ? 'knowledgeBase.wikiSummaryDescription' : 'knowledgeBase.wikiCatalogSummary')}
              </blockquote>
              {[...groups].map(([id, pages]) => (
                <section key={id}>
                  <h2 className="rb:text-lg rb:font-semibold rb:mb-3! rb:wrap-break-word">
                    {pages[0].category}
                  </h2>
                  <ul className="rb:space-y-3 rb:pl-6! rb:list-disc">
                    {pages.map((page) => (
                      <li key={page.id} className="rb:wrap-break-word">
                        <span className="rb:cursor-pointer rb:text-blue-500" onClick={() => onPageSelect(page.id)}>
                          {page.title}
                        </span>
                        {page.summary && (
                          <span className="rb:text-gray-500 rb:ml-1">— {page.summary}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </Flex>
          );
        }}
      />
    </Flex>
  );
};

export default WikiCatalog;
