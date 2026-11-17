import { request } from '@/utils/request';

export type ImportMode = 'append' | 'replace';
export interface ImportItem {
  standard_question: string;
  standard_answer: string;
  source_position: number;
}
export interface ImportFailure {
  item_index: number;
  source_position: number;
  standard_question: string;
  stage: string;
  reason: string;
}
export interface ImportProgress {
  task_id: string;
  status: 'pending' | 'running' | 'succeeded' | 'partially_succeeded' | 'failed';
  stage: 'queued' | 'validating' | 'indexing' | 'writing' | 'finished';
  progress: { total_count: number; processed_count: number };
  summary: { total_count: number; success_count: number; failure_count: number; removed_count: number; total_all: number } | null;
  failed_entries: ImportFailure[] | null;
  failure: { code: string; message: string } | null;
}
export const submitFaqImport = (kbId: string, mode: ImportMode, items: ImportItem[]) =>
  request.post<{ task_id: string; status: 'pending' }>(`/knowledges/${kbId}/faqs/import`, { mode, items });
export const getFaqImport = (kbId: string, taskId: string) =>
  request.get<ImportProgress>(`/knowledges/${kbId}/faqs/import/${taskId}`);
