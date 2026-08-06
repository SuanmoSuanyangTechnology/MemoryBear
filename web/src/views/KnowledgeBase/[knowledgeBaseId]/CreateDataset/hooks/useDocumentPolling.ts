import { useCallback, useEffect, useRef, useState } from 'react';
import { getDocumentList } from '@/api/knowledgeBase';
import type { TableRef } from '@/components/Table';
import type { KnowledgeBaseDocumentData } from '@/views/KnowledgeBase/types';

interface UseDocumentPollingOptions {
  knowledgeBaseId?: string;
  fileIds: string[];
  tableRef: React.RefObject<TableRef | null>;
  onCompleted: () => void;
}

const useDocumentPolling = ({ knowledgeBaseId, fileIds, tableRef, onCompleted }: UseDocumentPollingOptions) => {
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setLoading(false);
  }, []);

  const poll = useCallback(async (autoReturn = false) => {
    if (!knowledgeBaseId || fileIds.length === 0) return;
    try {
      const response = await getDocumentList(knowledgeBaseId, { document_ids: fileIds.join(',') });
      const documents = Array.isArray(response)
        ? response
        : ((response as unknown as { items?: KnowledgeBaseDocumentData[] }).items ?? []);
      tableRef.current?.loadData();
      if (documents.length > 0 && documents.every((document) => document.progress === 1)) {
        stop();
        if (autoReturn) window.setTimeout(onCompleted, 2000);
      }
    } catch {
      stop();
    }
  }, [fileIds, knowledgeBaseId, onCompleted, stop, tableRef]);

  const start = useCallback(() => {
    setLoading(true);
    void poll(true);
    timerRef.current = setInterval(() => void poll(true), 3000);
  }, [poll]);

  useEffect(() => stop, [stop]);

  return { loading, poll, start };
};

export default useDocumentPolling;
