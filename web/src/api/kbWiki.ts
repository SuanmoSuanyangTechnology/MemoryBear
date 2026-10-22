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
  // BatchApproveFaqData,
  ApproveFaqItem,
  BatchEnabledFaq,
  FaqGenerationJobsResponse,
  FaqIndexStatusVo,
} from '@/views/KnowledgeBase/components/Private/FAQ/types';

export interface WikiRevision {
  version: number;
  markdown: string;
  created_at: string;
  created_by: string;
}

export type WikiRevisionSummary = Omit<WikiRevision, 'markdown'>;

// Wiki 相关接口
// Wiki 目录树（逐层分页）
export const getKnowledgeWikiTree = (kb_id: string, params?: WikiTreeQuery) => {
  return request.get<WikiTreeResponse>(`/knowledges/${kb_id}/wiki/tree`, params)
}
// Wiki 索引与统计
export const getKnowledgeWikiIndex = (kb_id: string) => {
  return request.get<WikiIndexResponse>(`/knowledges/${kb_id}/wiki/index`)
}
// Wiki 页面列表与搜索
export const getKnowledgeWikiPagesUrl = (kb_id: string) => `/knowledges/${kb_id}/wiki/pages`;
export const getKnowledgeWikiPages = (kb_id: string, params?: WikiPagesQuery) => {
  return request.get<WikiPagesResponse>(getKnowledgeWikiPagesUrl(kb_id), params)
}
// Wiki 页面详情与来源
export const getKnowledgeWikiPageDetail = (kb_id: string, page_id: string) => {
  return request.get<WikiPageDetail>(`/knowledges/${kb_id}/wiki/pages/${page_id}`)
}
// Wiki 页面关系图
export const getKnowledgeWikiGraph = (kb_id: string, params?: WikiGraphQuery) => {
  return request.get<WikiGraph>(`/knowledges/${kb_id}/wiki/graph`, params)
}
// 生成文档 Wiki（异步及任务复用）
export const documentGenerate = (kb_id: string, document_id: string) => {
  return request.post(`/knowledges/${kb_id}/wiki/documents/${document_id}/generate`)
}



// Wiki 正文编辑与历史接口由服务端按页面版本提供。
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





// FAQ 列表与全库状态计数
export const getFaqListUrl = (kb_id: string) => `/knowledges/${kb_id}/faqs`;
export const getFaqList = (kb_id: string, data: FaqQuery) => {
  return request.get<FaqListResponse>(getFaqListUrl(kb_id), data)
};

// 手工新建 FAQ
export const createFaq = (kb_id: string, data: FaqForm) => {
  return request.post(`/knowledges/${kb_id}/faqs`, data)
};
// 编辑 FAQ 标准问与标准答
export const updateFaq = (kb_id: string, faq_id: string, data: FaqForm) => {
  return request.patch(`/knowledges/${kb_id}/faqs/${faq_id}`, data)
};
// 批量获取 FAQ 审核上下文
export const batchReviewFaqContext = (kb_id: string, data: { faq_ids: string[]; }) => {
  return request.post<GetBatchReviewFaqContextResponse>(`/knowledges/${kb_id}/faqs/batch/review-context`, data)
};
// 批量审核通过 FAQ
// export const batchApproveFaq = (kb_id: string, data: BatchApproveFaqData) => {
//   return request.post(`/knowledges/${kb_id}/faqs/batch/approve`, data)
// };
// 审核通过 FAQ
export const approveFaq = (kb_id: string, faq_id: string, data: ApproveFaqItem) => {
  return request.post(`/knowledges/${kb_id}/faqs/${faq_id}/approve`, data)
};
// 批量启用或停用 FAQ
export const batchEnabledFaq = (kb_id: string, data: BatchEnabledFaq) => {
  return request.patch(`/knowledges/${kb_id}/faqs/batch/enabled`, data)
};
// 启用或停用 FAQ
export const enabledFaq = (kb_id: string, faq_id: string, data: { expected_row_version: number; is_enabled: boolean;}) => {
  return request.patch(`/knowledges/${kb_id}/faqs/${faq_id}/enabled`, data)
};
// FAQ 首次生成任务列表
export const getFaqGenerationJobs = (kb_id: string) => {
  return request.get<FaqGenerationJobsResponse>(`/knowledges/${kb_id}/faqs/generation-jobs`)
};
// 重试 FAQ 首次生成任务
export const retryFaqGenerationJob = (kb_id: string, job_id: string, data: { expected_row_version: number; }) => {
  return request.get(`/knowledges/${kb_id}/faqs/generation-jobs/${job_id}/retry`, data)
};
// FAQ 详情
export const getFaqDetail = (kb_id: string, faq_id: string) => {
  return request.get<FaqItem>(`/knowledges/${kb_id}/faqs/${faq_id}`)
};
// FAQ 索引状态
export const getFaqIndexStatus = (kb_id: string, faq_id: string) => {
  return request.get<FaqIndexStatusVo>(`/knowledges/${kb_id}/faqs/${faq_id}/index-status`)
};
// 主动重试 FAQ 索引同步
export const retryFaqIndexStatus = (kb_id: string, faq_id: string, data: { expected_row_version: number; }) => {
  return request.post<FaqIndexStatusVo>(`/knowledges/${kb_id}/faqs/${faq_id}/index/retry`, data)
};
// 删除 FAQ delete 不能传 body
export const deleteFaq = (kb_id: string, faq_id: string, expected_row_version: number) => {
  return request.delete(`/knowledges/${kb_id}/faqs/${faq_id}?expected_row_version=${expected_row_version}`)
};
// 获取 FAQ 审核上下文与 Wiki 版本对照
export const getFaqReviewContext = (kb_id: string, faq_id: string) => {
  return request.get<GetReviewFaqContext>(`/knowledges/${kb_id}/faqs/${faq_id}/review-context`)
};