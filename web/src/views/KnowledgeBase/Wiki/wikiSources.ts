import type { WikiSource } from '@/api/wiki';

/** A file can supply several excerpts; show the file once without dropping evidence. */
export function groupWikiSources(sources: WikiSource[]) {
  const groups = new Map<string, { documentId: string; title: string; excerpts: WikiSource[] }>();
  for (const source of sources) {
    if (!groups.has(source.document_id)) {
      groups.set(source.document_id, { documentId: source.document_id, title: source.document_title || '查看源文件', excerpts: [] });
    }
    groups.get(source.document_id)!.excerpts.push(source);
  }
  return [...groups.values()];
}
