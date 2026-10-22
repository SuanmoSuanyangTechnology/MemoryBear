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

export const useWikiTree = (
  knowledgeBaseId: string,
  active = true,
  pageTypes: PageTypesQuery = 'entity,concept',
  keyword = '',
  onLoaded?: () => void,
) => {
  const [branches, setBranches] = useState<Record<string, Branch>>({});
  const cache = useRef<Record<string, Branch>>({});
  const generation = useRef(0);
  const onLoadedRef = useRef(onLoaded);

  useEffect(() => {
    onLoadedRef.current = onLoaded;
  }, [onLoaded]);

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
        keyword: keyword || undefined,
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
          hasNext: response.page.hasnext,
          loading: false,
          error: false,
        },
      };
    } catch {
      if (version !== generation.current) return;
      cache.current = { ...cache.current, [folderId]: { ...next, loading: false, error: true } };
      setBranches(cache.current);
      return;
    }
    setBranches(cache.current);
    if (!folderId) onLoadedRef.current?.();
  }, [knowledgeBaseId, active, pageTypes, keyword]);

  useEffect(() => {
    generation.current += 1;
    cache.current = {};
    setBranches({});
    if (active && knowledgeBaseId) void load();
    return () => {
      generation.current += 1;
    };
  }, [knowledgeBaseId, active, load]);

  return { branches, load };
};
