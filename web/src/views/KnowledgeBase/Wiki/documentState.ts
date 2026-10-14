interface DocumentProgress { run?: number; progress?: number }

export function isDocumentTerminal(document: DocumentProgress) {
  return document.run === 2 || (document.run !== 1 && (document.progress === 1 || (document.progress ?? 0) < 0));
}

export function documentState(document: DocumentProgress) {
  if (document.run === 2) return { label: '已取消', color: 'default' };
  if (document.run === 0 && (document.progress ?? 0) < 0) return { label: '处理失败', color: 'error' };
  if (document.progress === 1 && document.run !== 1) return { label: '已完成', color: 'success' };
  if (!document.progress && document.run !== 1) return { label: '等待处理', color: 'warning' };
  return { label: '处理中', color: 'processing' };
}
