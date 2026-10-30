import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useEffect, useState } from 'react';

import {
  getKnowledgeWikiPages,
} from '@/api/knowledgeBase';

import type {
  WikiData,
  WikiPage,
  PageTypesQuery,
  WikiPageItem,
} from '../Wiki/types';

const EMPTY_WIKI_DATA: WikiData = { pages: [], relations: [] };

const PAGE_TYPES: PageTypesQuery = 'entity,concept'

const parseWikiData = async (knowledgeBaseId: string, t: TFunction): Promise<WikiData> => {
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
    hasNext = response.page.hasnext;
    page += 1;
  }
  // List metadata is sufficient here; article readers load the selected detail on demand.
  const pages: WikiPage[] = pageItems.map((item) => {
    const id = item.page_id || item.id;
    return {
      id,
      title: item.title,
      category: item.folder_path.map((folder) => folder.name).join(' / '),
      folderId: item.folder_id,
      folderPath: item.folder_path,
      summary: item.summary,
      markdown: `# ${item.title}\n\n> ${item.summary || t('knowledgeBase.privateWiki.noDescription')}\n`,
      sourceIds: [],
      relatedIds: [],
      importance: 0,
    };
  });
  return { pages, relations: [] };
};

export const useWikiData = (knowledgeBaseId: string, enabled = true) => {
  const { t } = useTranslation();
  const [data, setData] = useState<WikiData>(EMPTY_WIKI_DATA);
  const [loading, setLoading] = useState(enabled && Boolean(knowledgeBaseId));
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const loadWikiData = async () => {
      if (!enabled || !knowledgeBaseId) {
        setData(EMPTY_WIKI_DATA);
        setLoading(false);
        setError(false);
        return;
      }

      setLoading(true);
      setError(false);
      try {
        const parsedData = await parseWikiData(knowledgeBaseId, t);
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
  }, [knowledgeBaseId, enabled, t]);

  return { ...data, loading, error };
};
