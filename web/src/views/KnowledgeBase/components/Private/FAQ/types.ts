import type { PageResponse } from '../Wiki/types'

export interface Source {
    wiki_page_id: null | string;
    title: null | string;
    source_status: 'none' | 'available' | 'unavailable';
    generated_wiki_version: null | number;
    approved_wiki_version: null | number;
    latest_wiki_version: null | number;
    latest_published_at_ms: null | number;
}
export interface FaqItem {
    id: string;
    standard_question: string;
    standard_answer: string;
    answer_preview: string;
    review_status: FaqStatus;
    is_enabled: boolean;
    can_enable: boolean;
    origin: 'manual' | 'auto';
    source: Source;
    source_changed: boolean;
    content_revision: number;
    row_version: number;
    created_at_ms: number | null;
    updated_at_ms: number | null;
    reviewed_at_ms: number | null;
}

export type FaqStatus = 'pending' | 'approved' | 'change_pending'
export type AllFaqStatus = FaqStatus | 'all';

export interface FaqQuery {
  review_status?: FaqStatus;
  is_enabled?: boolean;
  keyword?: string;
  page?: number;
  pagesize?: number;
}

export interface FaqListResponse {
    items: FaqItem[]
    total_all: number;
    review_counts: Record<FaqStatus, number>
    page: PageResponse
}

export interface FaqForm {
    standard_question: string;
    standard_answer: string;
    expected_row_version?: number;
}

export interface Snapshot {
    snapshot_id: string;
    version: number;
    title: string;
    content: string;
    summary: string;
    published_at_ms: number;
}
export type FaqIndexStatus = 'ready' | 'pending' | 'not_required' | 'failed' | 'superseded'
export type BaselineKind = 'approved' | 'generated' | 'none'
export interface GetReviewFaqContext {
    faq: FaqItem;
    source: Source
    baseline_kind: BaselineKind
    before: Snapshot;
    after: Snapshot;
    source_changed: boolean;
    can_approve: boolean;
    expected_row_version: number;
    reviewed_wiki_snapshot_id: string;
}
export interface GetBatchReviewFaqContextResponse {
    items: GetReviewFaqContext[]
}
export interface FaqIndexStatusVo {
    faq_id: string;
    content_revision: number;
    status: FaqIndexStatus
    error_code: string;
}

export interface ApproveFaqItem {
    expected_row_version: number;
    content_revision: number;
    reviewed_wiki_snapshot_id: string;
}
export interface BatchApproveFaqData {
    items: ApproveFaqItem[]
}

export interface EnabledFaqItem {
    faq_id: string;
    expected_row_version: number;
}
export interface BatchEnabledFaq {
    items: EnabledFaqItem[];
    is_enabled: boolean;
}

export type GenerationJobStatus = 'pending' | 'running' | 'retry_wait' | 'succeeded' | 'failed' | 'cancelled'
export interface FaqGenerationJobItem {
    id: string;
    wiki_page_id: string;
    source_snapshot_id: string;
    source_version: number;
    status: GenerationJobStatus;
    publish_confirmed: boolean;
    task_id: string;
    result_count: number;
    failure_attempt: number;
    dispatch_attempt: number;
    row_version: number;
    error_code: string;
    error_stage: string;
    updated_at_ms: number;
}
export interface FaqGenerationJobsResponse {
    items: FaqGenerationJobItem[];
    page: PageResponse;
}
