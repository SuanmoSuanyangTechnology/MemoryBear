import { useEffect, useState } from 'react';

import { getKnowledgeWikiPages } from '@/api/kbWiki';

import type {
  WikiData,
  WikiPage,
  PageTypesQuery,
  WikiPageItem,
} from '../Wiki/types';

const EMPTY_WIKI_DATA: WikiData = { pages: [], relations: [] };

const PAGE_TYPES: PageTypesQuery = 'summary,entity,concept';

const parseWikiData = async (knowledgeBaseId: string): Promise<WikiData> => {
  const pageItems: WikiPageItem[] = [];
  let page = 1;
  let hasNext = true;
  while (hasNext) {
    const response = await getKnowledgeWikiPages(knowledgeBaseId, {
      page_types: PAGE_TYPES,
      page,
      pagesize: 200,
    });
    pageItems.push(...response.items);
    hasNext = response.page.has_next;
    page += 1;
  }
  // 列表只提供导航元信息；正文按当前 pageId 独立读取，不逐页预取。
  const pages: WikiPage[] = pageItems.map((item) => ({
    id: item.page_id || item.id,
    title: item.title,
    pageType: item.type,
    category: item.folder_path.map((folder) => folder.name).join(' / '),
    folderId: item.folder_id,
    folderPath: item.folder_path,
    summary: item.summary,
    markdown: '',
    sourceIds: [],
    relatedIds: [],
    importance: 0,
  }));
  return { pages, relations: [] };
};

export const useWikiData = (knowledgeBaseId: string) => {
  const [data, setData] = useState<WikiData>(EMPTY_WIKI_DATA);
  const [loading, setLoading] = useState(Boolean(knowledgeBaseId));
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const loadWikiData = async () => {
      if (!knowledgeBaseId) {
        setData(EMPTY_WIKI_DATA);
        setLoading(false);
        setError(false);
        return;
      }

      setLoading(true);
      setError(false);
      try {
        const parsedData = await parseWikiData(knowledgeBaseId);
        if (!cancelled) {
          setData(parsedData);
        }
      } catch {
        if (!cancelled) {
          setData(EMPTY_WIKI_DATA);
          setError(true);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void loadWikiData();
    return () => {
      cancelled = true;
    };
  }, [knowledgeBaseId]);

  return { ...data, loading, error };
};
