import { request } from "@/utils/request";
import type {
  WikiTreeQuery,
  WikiTreeResponse,
  WikiIndexResponse,
  WikiPagesQuery,
  WikiPagesResponse,
  WikiPageDetail,
  WikiGraphQuery,
  WikiGraph,
} from '@/views/KnowledgeBase/components/Private/Wiki/types';
import type {
  FaqQuery,
  FaqItem,
  FaqListResponse,
  FaqForm,
  GetReviewFaqContext,
  GetBatchReviewFaqContextResponse,
  BatchApproveFaqData,
  ApproveFaqItem,
  BatchEnabledFaq,
  FaqGenerationJobsResponse,
  FaqIndexStatusVo,
  FaqImportPayload,
  FaqImportSubmitResponse,
  FaqImportCurrentTaskResponse,
  FaqImportTaskVo,
} from '@/views/KnowledgeBase/components/Private/FAQ/types';

export interface WikiRevision {
  version: number;
  markdown: string;
  created_at: string;
  created_by: string;
}

export type WikiRevisionSummary = Omit<WikiRevision, 'markdown'>;

// Wiki-related APIs
// Wiki directory tree (paginated level by level)
export const getKnowledgeWikiTree = (kb_id: string, params?: WikiTreeQuery) => {
  return request.get<WikiTreeResponse>(`/knowledges/${kb_id}/wiki/tree`, params)
}
// Wiki index and statistics
export const getKnowledgeWikiIndex = (kb_id: string) => {
  return request.get<WikiIndexResponse>(`/knowledges/${kb_id}/wiki/index`)
}
// Wiki page listing and search
export const getKnowledgeWikiPagesUrl = (kb_id: string) => `/knowledges/${kb_id}/wiki/pages`;
export const getKnowledgeWikiPages = (kb_id: string, params?: WikiPagesQuery) => {
  return request.get<WikiPagesResponse>(getKnowledgeWikiPagesUrl(kb_id), params)
}
// Wiki page details and source
export const getKnowledgeWikiPageDetail = (kb_id: string, page_id: string) => {
  return request.get<WikiPageDetail>(`/knowledges/${kb_id}/wiki/pages/${page_id}`)
}
// Wiki page relationship graph
export const getKnowledgeWikiGraph = (kb_id: string, params?: WikiGraphQuery) => {
  return request.get<WikiGraph>(`/knowledges/${kb_id}/wiki/graph`, params)
}
// Generate a document Wiki (asynchronous, with job reuse)
export const documentGenerate = (kb_id: string, document_id: string) => {
  return request.post(`/knowledges/${kb_id}/wiki/documents/${document_id}/generate`)
}



// The server provides Wiki content editing and history APIs by page version.
export const getWikiPageRevision = (knowledgeBaseId: string, pageId: string, version: number) => {
  return request.get<WikiRevision>(`/knowledges/${knowledgeBaseId}/wiki/pages/${pageId}/revisions/${version}`);
};

export const saveWikiPage = (
  knowledgeBaseId: string,
  data: { page_id: string; markdown: string; base_version: number },
) => {
  return request.put<WikiRevision>(`/knowledges/${knowledgeBaseId}/wiki/pages/${data.page_id}`, data);
};

export const getWikiPageHistory = (knowledgeBaseId: string, pageId: string, offset = 0) => {
  return request.get<WikiRevisionSummary[]>(
    `/knowledges/${knowledgeBaseId}/wiki/pages/${pageId}/revisions`,
    { offset, limit: 50 },
  );
};





// FAQ list and knowledge-base-wide status counts
export const getFaqListUrl = (kb_id: string) => `/knowledges/${kb_id}/faqs`;
export const getFaqList = (kb_id: string, data: FaqQuery) => {
  return request.get<FaqListResponse>(getFaqListUrl(kb_id), data)
};

// 从响应头解析下载文件名，优先使用 RFC 5987 的 filename*
const parseFaqExportFileName = (contentDisposition: unknown) => {
  const fallback = 'FAQ.csv';
  if (typeof contentDisposition !== 'string' || !contentDisposition) return fallback;
  const encoded = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded.trim());
    } catch {
      return fallback;
    }
  }
  return contentDisposition.match(/filename="?([^";]+)"?/i)?.[1]?.trim() || fallback;
};

// Export all FAQs: stream a UTF-8 CSV with BOM; the file name comes from the response header
export const exportAllFaq = async (kb_id: string) => {
  // Streaming exports can take a while, but a stalled connection must not leave the Promise pending forever
  const response = await request.getBlobResponse<Blob>(
    `/knowledges/${kb_id}/faqs/export`,
    { timeout: 60_000 },
  );
  const blobUrl = URL.createObjectURL(response.data);
  const link = document.createElement('a');
  link.href = blobUrl;
  link.style.display = 'none';
  link.setAttribute('download', parseFaqExportFileName(response.headers['content-disposition']));
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  // The browser reads the Blob URL after click() returns; revoking immediately can fail the download or truncate it
  setTimeout(() => URL.revokeObjectURL(blobUrl), 10_000);
};

// Create an FAQ manually
export const createFaq = (kb_id: string, data: FaqForm) => {
  return request.post(`/knowledges/${kb_id}/faqs`, data)
};
// Edit an FAQ's standard question and answer
export const updateFaq = (kb_id: string, faq_id: string, data: FaqForm) => {
  return request.patch(`/knowledges/${kb_id}/faqs/${faq_id}`, data)
};
// Fetch review context for multiple FAQs
export const batchReviewFaqContext = (kb_id: string, data: { faq_ids: string[]; }) => {
  return request.post<GetBatchReviewFaqContextResponse>(`/knowledges/${kb_id}/faqs/batch/review-context`, data)
};
// Approve multiple FAQs
export const batchApproveFaq = (kb_id: string, data: BatchApproveFaqData) => {
  return request.post(`/knowledges/${kb_id}/faqs/batch/approve`, data)
};
// Approve an FAQ
export const approveFaq = (kb_id: string, faq_id: string, data: ApproveFaqItem) => {
  return request.post(`/knowledges/${kb_id}/faqs/${faq_id}/approve`, data)
};
// Enable or disable multiple FAQs
export const batchEnabledFaq = (kb_id: string, data: BatchEnabledFaq) => {
  return request.patch(`/knowledges/${kb_id}/faqs/batch/enabled`, data)
};
// Enable or disable an FAQ
export const enabledFaq = (kb_id: string, faq_id: string, data: { expected_row_version: number; is_enabled: boolean;}) => {
  return request.patch(`/knowledges/${kb_id}/faqs/${faq_id}/enabled`, data)
};
// List initial FAQ generation jobs
export const getFaqGenerationJobs = (kb_id: string) => {
  return request.get<FaqGenerationJobsResponse>(`/knowledges/${kb_id}/faqs/generation-jobs`)
};
// Retry an initial FAQ generation job
export const retryFaqGenerationJob = (kb_id: string, job_id: string, data: { expected_row_version: number; }) => {
  return request.get(`/knowledges/${kb_id}/faqs/generation-jobs/${job_id}/retry`, data)
};
// FAQ details
export const getFaqDetail = (kb_id: string, faq_id: string) => {
  return request.get<FaqItem>(`/knowledges/${kb_id}/faqs/${faq_id}`)
};
// FAQ index status
export const getFaqIndexStatus = (kb_id: string, faq_id: string) => {
  return request.get<FaqIndexStatusVo>(`/knowledges/${kb_id}/faqs/${faq_id}/index-status`)
};
// Manually retry FAQ index synchronization
export const retryFaqIndexStatus = (kb_id: string, faq_id: string, data: { expected_row_version: number; }) => {
  return request.post<FaqIndexStatusVo>(`/knowledges/${kb_id}/faqs/${faq_id}/index/retry`, data)
};
// Delete an FAQ; DELETE requests cannot include a body
export const deleteFaq = (kb_id: string, faq_id: string, expected_row_version: number) => {
  return request.delete(`/knowledges/${kb_id}/faqs/${faq_id}?expected_row_version=${expected_row_version}`)
};
// Fetch FAQ review context and the corresponding Wiki version
export const getFaqReviewContext = (kb_id: string, faq_id: string) => {
  return request.get<GetReviewFaqContext>(`/knowledges/${kb_id}/faqs/${faq_id}/review-context`)
};
// Submit an asynchronous FAQ batch import task; HTTP 202 only means the task was accepted
export const importFaqs = (kb_id: string, data: FaqImportPayload) => {
  return request.post<FaqImportSubmitResponse>(`/knowledges/${kb_id}/faqs/import`, data)
};
// Query the knowledge base's current (pending or running) FAQ import task on page entry
export const getCurrentFaqImportTask = (kb_id: string) => {
  return request.get<FaqImportCurrentTaskResponse>(`/knowledges/${kb_id}/faqs/import`)
};
// Poll the FAQ batch import task; succeeded/failed are the only terminal statuses
export const getFaqImportProgress = (kb_id: string, task_id: string) => {
  return request.get<FaqImportTaskVo>(`/knowledges/${kb_id}/faqs/import/${task_id}`)
};