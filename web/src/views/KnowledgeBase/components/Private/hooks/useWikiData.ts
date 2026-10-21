import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useEffect, useState } from 'react';

import {
  getKnowledgeWikiPageDetail,
  getKnowledgeWikiPages,
} from '@/api/kbWiki';

import type {
  WikiData,
  WikiPage,
  PageTypesQuery,
  WikiPageDetail,
  WikiPageItem,
} from '../Wiki/types';

const EMPTY_WIKI_DATA: WikiData = { pages: [], relations: [] };

const PAGE_TYPES: PageTypesQuery = 'entity,concept'

const toMarkdown = (detail: WikiPageDetail, t: TFunction): string => {
  const sourceSection = detail.sources.length
    ? detail.sources.map((source) => `- ${source.quote || source.document_title}`).join('\n')
    : `- ${t('knowledgeBase.privateWiki.noSources')}`;
  const outlinkSection = detail.outlinks.length
    ? detail.outlinks.map((link) => `- [${link.title}](#wiki-page=${encodeURIComponent(link.page_id)})`).join('\n')
    : `- ${t('knowledgeBase.privateWiki.noRelated')}`;

  return `# ${detail.title}\n\n> ${detail.summary || t('knowledgeBase.privateWiki.noDescription')}\n\n## ${t('knowledgeBase.privateWiki.relatedWiki')}\n\n${outlinkSection}\n\n## ${t('knowledgeBase.privateWiki.sourceDocuments')}\n\n${sourceSection}\n`;
};

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
  const details = await Promise.all(
    pageItems.map(async (item) => {
      try {
        return await getKnowledgeWikiPageDetail(knowledgeBaseId, item.page_id || item.id);
      } catch {
        return undefined;
      }
    }),
  );
  const detailMap = new Map(
    details.filter((detail): detail is WikiPageDetail => Boolean(detail)).map((detail) => [detail.page_id || detail.id, detail]),
  );
  const pages: WikiPage[] = pageItems.map((item) => {
    const id = item.page_id || item.id;
    const detail = detailMap.get(id);
    const relatedIds = detail?.outlinks.map((link) => link.page_id) ?? [];
    return {
      id,
      title: detail?.title || item.title,
      category: item.folder_path.map((folder) => folder.name).join(' / '),
      folderId: item.folder_id,
      folderPath: item.folder_path,
      summary: detail?.summary || item.summary,
      markdown: detail ? toMarkdown(detail, t) : `# ${item.title}\n\n> ${item.summary || t('knowledgeBase.privateWiki.noDescription')}\n`,
      sourceIds: detail?.sources.map((source) => source.document_id) ?? [],
      relatedIds,
      importance: 0,
    };
  });
  return { pages, relations: [] };
};

export const useWikiData = (knowledgeBaseId: string) => {
  const { t } = useTranslation();
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
  }, [knowledgeBaseId, t]);

  return { ...data, loading, error };
};
