import { useCallback, useEffect, useState } from 'react';
import { getKnowledgeWikiPageDetail } from '@/api/kbWiki';
import type { WikiPageDetail } from '../Wiki/types';

const POLLING_INTERVAL = 60000;

export const useWikiPageDetail = (knowledgeBaseId: string, pageId?: string) => {
  const [detail, setDetail] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);

  const refresh = useCallback(() => {
    setReload((value) => value + 1);
  }, []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setDetail(null);
    setError(false);
    setLoading(Boolean(knowledgeBaseId && pageId));

    if (!knowledgeBaseId || !pageId) {
      return;
    }

    const load = async () => {
      try {
        const data = await getKnowledgeWikiPageDetail(knowledgeBaseId, pageId);
        if (!active) {
          return;
        }
        setDetail(data);
        setError(false);
        if (['pending', 'updating', 'cleanup_pending'].includes(data.status)) {
          timer = setTimeout(() => void load(), POLLING_INTERVAL);
        }
      } catch {
        if (active) {
          setError(true);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    void load();

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [knowledgeBaseId, pageId, reload]);

  return { detail, loading, error, refresh };
};
