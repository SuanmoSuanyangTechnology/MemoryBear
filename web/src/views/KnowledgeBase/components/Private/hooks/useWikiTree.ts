import { useCallback, useEffect, useRef, useState } from 'react';
import { getKnowledgeWikiTree } from '@/api/kbWiki';
import type { PageTypesQuery, WikiTreeItem } from '../Wiki/types';

interface Branch {
  items: WikiTreeItem[];
  page: number;
  hasNext: boolean;
  loading: boolean;
  error: boolean;
}

export const useWikiTree = (knowledgeBaseId: string, pageTypes: PageTypesQuery, active = true) => {
  const [branches, setBranches] = useState<Record<string, Branch>>({});
  const cache = useRef<Record<string, Branch>>({});
  const generation = useRef(0);

  const load = useCallback(async (folderId = '', more = false) => {
    if (!active || !knowledgeBaseId) return;
    const previous = cache.current[folderId];
    if (previous?.loading || (previous && !previous.error && (!more || !previous.hasNext))) return;
    const version = generation.current;
    const next = {
      items: previous?.items ?? [],
      page: previous?.page ?? 0,
      hasNext: previous?.hasNext ?? true,
      loading: true,
      error: false,
    };
    cache.current = { ...cache.current, [folderId]: next };
    setBranches(cache.current);
    try {
      const response = await getKnowledgeWikiTree(knowledgeBaseId, {
        folder_id: folderId || undefined,
        page_types: pageTypes,
        page: next.page + 1,
        pagesize: 50,
      });
      if (version !== generation.current) return;
      const items = new Map(next.items.map((item) => [`${item.node_kind}:${item.id}`, item]));
      response.items.forEach((item) => items.set(`${item.node_kind}:${item.id}`, item));
      cache.current = {
        ...cache.current,
        [folderId]: {
          items: [...items.values()],
          page: response.page.page,
          hasNext: response.page.has_next,
          loading: false,
          error: false,
        },
      };
    } catch {
      if (version !== generation.current) return;
      cache.current = { ...cache.current, [folderId]: { ...next, loading: false, error: true } };
    }
    setBranches(cache.current);
  }, [knowledgeBaseId, pageTypes, active]);

  // Tab 停用时保留它的缓存；只有知识库/类型变更或卸载才让旧响应失效。
  useEffect(() => {
    generation.current += 1;
    cache.current = {};
    setBranches({});
    return () => { generation.current += 1; };
  }, [knowledgeBaseId, pageTypes]);

  useEffect(() => {
    if (active && knowledgeBaseId) void load();
  }, [knowledgeBaseId, active, load]);

  return { branches, load };
};
