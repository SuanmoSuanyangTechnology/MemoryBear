import { useCallback, useEffect, useRef, useState } from 'react';
import { getDocumentList } from '@/api/knowledgeBase';
import type { TableRef } from '@/components/Table';
import type { KnowledgeBaseDocumentData } from '@/views/KnowledgeBase/types';
import { isDocumentSuccessful, isDocumentTerminal } from '../../../Wiki/documentState';

interface UseDocumentPollingOptions {
  knowledgeBaseId?: string;
  fileIds: string[];
  tableRef: React.RefObject<TableRef | null>;
  onCompleted: () => void;
}
const DOCUMENT_STATUS_BATCH_SIZE = 100;

const useDocumentPolling = ({ knowledgeBaseId, fileIds, tableRef, onCompleted }: UseDocumentPollingOptions) => {
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const completionRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    requestRef.current?.abort();
    requestRef.current = null;
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (completionRef.current) {
      clearTimeout(completionRef.current);
      completionRef.current = null;
    }
    setLoading(false);
  }, []);

  const poll = useCallback(async (autoReturn = false) => {
    if (!knowledgeBaseId || fileIds.length === 0 || requestRef.current) return;
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const ids = [...new Set(fileIds)];
      const responses = [];
      for (let offset = 0; offset < ids.length; offset += DOCUMENT_STATUS_BATCH_SIZE) {
        responses.push(getDocumentList(knowledgeBaseId, {
          document_ids: ids.slice(offset, offset + DOCUMENT_STATUS_BATCH_SIZE).join(','),
          pagesize: DOCUMENT_STATUS_BATCH_SIZE,
        }, controller.signal));
      }
      const documents = (await Promise.all(responses)).flatMap(response => Array.isArray(response)
        ? response
        : ((response as unknown as { items?: KnowledgeBaseDocumentData[] }).items ?? []));
      if (controller.signal.aborted) return;
      tableRef.current?.loadData();
      if (documents.length === ids.length && documents.every(isDocumentTerminal)) {
        stop();
        if (autoReturn && documents.every(isDocumentSuccessful)) {
          completionRef.current = setTimeout(onCompleted, 2000);
        }
      }
    } catch {
      if (!controller.signal.aborted) stop();
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
    }
  }, [fileIds, knowledgeBaseId, onCompleted, stop, tableRef]);

  const start = useCallback(() => {
    stop();
    setLoading(true);
    timerRef.current = setInterval(() => void poll(true), 3000);
    void poll(true);
  }, [poll, stop]);

  useEffect(() => stop, [stop]);

  return { loading, poll, start };
};

export default useDocumentPolling;
