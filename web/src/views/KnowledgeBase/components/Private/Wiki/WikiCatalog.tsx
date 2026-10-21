import { useEffect, useState } from 'react';
import { Alert, Button, Skeleton, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import { getKnowledgeWikiPages } from '@/api/kbWiki';
import type { WikiPage } from './types';
import Empty from '@/components/Empty';

interface WikiCatalogProps {
  knowledgeBaseId: string;
  summaryOnly: boolean;
  onPageSelect: (id: string) => void;
}

const WikiCatalog = ({
  knowledgeBaseId,
  summaryOnly,
  onPageSelect,
}: WikiCatalogProps) => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  const [catalogPages, setCatalogPages] = useState<WikiPage[]>([]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true);
      setError(false);
      try {
        const result: WikiPage[] = [];
        let page = 1;
        let hasNext = true;
        while (hasNext && active) {
          const response = await getKnowledgeWikiPages(knowledgeBaseId, {
            page_types: summaryOnly ? 'summary' : 'entity,concept',
            page,
            pagesize: 200,
          });
          result.push(...response.items.map((item) => ({
            id: item.page_id || item.id,
            title: item.title,
            category: item.folder_path.map((folder) => folder.name).join(' / '),
            folderId: item.folder_id,
            folderPath: item.folder_path,
            summary: item.summary,
            markdown: '',
            sourceIds: [],
            relatedIds: [],
            importance: 0,
          })));
          hasNext = response.page.has_next;
          page += 1;
        }
        if (active) {
          setCatalogPages(result);
        }
      } catch {
        if (active) setError(true);
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [knowledgeBaseId, summaryOnly, reload]);

  if (loading) return <Skeleton active paragraph={{ rows: 12 }} />;
  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message={t('knowledgeBase.wikiLoadFailed')}
        action={<Button onClick={() => setReload((value) => value + 1)}>{t('knowledgeBase.privateWiki.retry')}</Button>}
      />
    );
  }

  const groups = new Map<string, WikiPage[]>();
  catalogPages.forEach((page) => {
    const key = page.folderId ?? '';
    const items = groups.get(key) ?? [];
    items.push(page);
    groups.set(key, items);
  });

  if (!groups.size) {
    return (
      <Empty
        subTitle={t('knowledgeBase.wikiEmptyDescription')}
        size={140}
        className="rb:h-full!"
      />
    )
  }

  return (
    <Flex vertical gap={16} className="rb:h-full! rb:overflow-hidden!">
      <h1 className="rb:text-2xl rb:font-semibold rb:shrink-0">
        {t(summaryOnly ? 'knowledgeBase.wikiSummary' : 'knowledgeBase.wikiCatalog')}
      </h1>
      <Flex vertical gap={16} className="rb:flex-1! rb:overflow-y-auto!">
        <blockquote className="rb:border-l-4 rb:border-gray-200 rb:pl-4 rb:text-gray-500">
          {t(summaryOnly ? 'knowledgeBase.wikiSummaryDescription' : 'knowledgeBase.wikiCatalogSummary')}
        </blockquote>
        {[...groups].map(([id, items]) => (
          <section key={id}>
            <h2 className="rb:text-lg rb:font-semibold rb:mb-3! rb:wrap-break-word">
              {items[0].category}
            </h2>
            <ul className="rb:space-y-3 rb:pl-6! rb:list-disc">
              {items.map((page) => (
                <li key={page.id} className="rb:wrap-break-word">
                  <span className="rb:cursor-pointer rb:text-blue-500" onClick={() => onPageSelect(page.id)}>
                    {page.title}
                  </span>
                  {page.summary &&
                    <span className="rb:text-gray-500 rb:ml-1">— {page.summary}</span>
                  }
                </li>
              ))}
            </ul>
          </section>
        ))}
      </Flex>
    </Flex>
  );
}

export default WikiCatalog