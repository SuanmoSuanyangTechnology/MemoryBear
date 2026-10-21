import { useCallback, useEffect, useState } from 'react';
import { getKnowledgeWikiPageDetail } from '@/api/kbWiki';
import type { WikiPageDetail } from '../Wiki/types';

const POLLING_INTERVAL = 60000;

export interface WikiPageDetailState {
  detail: WikiPageDetail | null;
  loading: boolean;
  error: boolean;
  refresh: () => void;
}

export const useWikiPageDetail = (knowledgeBaseId: string, pageId?: string): WikiPageDetailState => {
  const requestKey = `${knowledgeBaseId}:${pageId ?? ''}`;
  const [state, setState] = useState({
    requestKey,
    detail: null as WikiPageDetail | null,
    loading: Boolean(knowledgeBaseId && pageId),
    error: false,
  });
  const [reload, setReload] = useState(0);
  const refresh = useCallback(() => setReload((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setState({ requestKey, detail: null, loading: Boolean(knowledgeBaseId && pageId), error: false });
    if (!knowledgeBaseId || !pageId) return;

    const load = async () => {
      try {
        const detail = await getKnowledgeWikiPageDetail(knowledgeBaseId, pageId);
        if (!active) return;
        setState({ requestKey, detail, loading: false, error: false });
        if (['pending', 'updating', 'cleanup_pending'].includes(detail.status)) {
          timer = setTimeout(() => void load(), POLLING_INTERVAL);
        }
      } catch {
        if (active) setState({ requestKey, detail: null, loading: false, error: true });
      }
    };
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [knowledgeBaseId, pageId, requestKey, reload]);

  // 切换目标的这一帧也不能显示上一页，迟到响应由 effect 的 active 标记隔离。
  const current = state.requestKey === requestKey
    ? state
    : { detail: null, loading: Boolean(knowledgeBaseId && pageId), error: false };
  return { detail: current.detail, loading: current.loading, error: current.error, refresh };
};
