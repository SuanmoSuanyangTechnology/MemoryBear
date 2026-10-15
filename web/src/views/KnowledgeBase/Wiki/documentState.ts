interface DocumentProgress { run?: number; progress?: number }
export const DOCUMENT_RUN = { idle: 0, active: 1, cancelled: 2 } as const;
export const DOCUMENT_PROGRESS = { queued: 0, complete: 1 } as const;
export type DocumentPhase = 'queued' | 'processing' | 'complete' | 'failed' | 'cancelled';

export function getDocumentPhase(document: DocumentProgress): DocumentPhase {
  if (document.run === DOCUMENT_RUN.cancelled) return 'cancelled';
  if (document.run === DOCUMENT_RUN.active) return 'processing';
  if ((document.progress ?? DOCUMENT_PROGRESS.queued) < 0) return 'failed';
  if (document.progress === DOCUMENT_PROGRESS.complete) return 'complete';
  if (!document.progress) return 'queued';
  return 'processing';
}

export const isDocumentSuccessful = (document: DocumentProgress) => getDocumentPhase(document) === 'complete';
export const isDocumentRetryable = (document: DocumentProgress) => ['failed', 'cancelled'].includes(getDocumentPhase(document));

export function isDocumentTerminal(document: DocumentProgress) {
  return ['complete', 'failed', 'cancelled'].includes(getDocumentPhase(document));
}

export function documentState(document: DocumentProgress) {
  const states = {
    cancelled: { label: '已取消', color: 'default' },
    failed: { label: '处理失败', color: 'error' },
    complete: { label: '已完成', color: 'success' },
    queued: { label: '等待处理', color: 'warning' },
    processing: { label: '处理中', color: 'processing' },
  };
  return states[getDocumentPhase(document)];
}
