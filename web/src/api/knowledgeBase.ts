import { request, cookieUtils } from "@/utils/request";
import type { AxiosProgressEvent } from "axios";
import type {
  ShareRequestParams,
  SpaceItem,
  UploadFileFormData,
  FolderFormData,
  UploadFileResponse,
  KnowledgeBase,
  KnowledgeBaseFormData,
  ListQuery,
  PathQuery,
  KnowledgeBaseDocumentData,
  KnowledgeBaseShareListResponse,
  RecallTestParams,
  RetrievalPolicy,
} from "@/views/KnowledgeBase/types";
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

// FAQ demo adapter: isolated by knowledge base; intentionally reset on reload.
export interface FaqItem {
  id: string;
  question: string;
  answer: string;
  status?: 'enabled' | 'disabled' | 'pending' | 'changed';
  updatedAt?: string;
  revision?: number;
  sourceHistory?: {
    pageTitle: string;
    before: { version: string; date: string; markdown: string };
    after: { version: string; date: string; markdown: string };
  };
}

const faqMockStore = new Map<string, FaqItem[]>();

export const getFaqListUrl = '/knowledge/faq';
export const getFaqList = async (knowledgeBaseId: string): Promise<FaqItem[]> => {
  if (!faqMockStore.has(knowledgeBaseId)) {
    faqMockStore.set(knowledgeBaseId, [
      { id: 'demo-1', question: '如何向知识库中添加文件？', answer: '进入文件页面，点击导入文件，等待解析完成后即可用于检索。', status: 'enabled', updatedAt: new Date().toISOString() },
      { id: 'demo-2', question: 'Wiki、文件和 FAQ 有什么区别？', answer: '文件保留原始资料，Wiki 整理主题知识，FAQ 提供直接的问题与答案。', status: 'pending', updatedAt: new Date().toISOString() },
      { id: 'demo-3', question: '如何调整检索策略？', answer: '在知识库设置中修改检索配置。', status: 'disabled', updatedAt: new Date().toISOString() },
      { id: 'demo-4', question: '源文件更新后，如何同步 FAQ 答案？', answer: '根据更新后的源文件修改相关答案，审核确认后重新启用。', status: 'changed', updatedAt: new Date().toISOString(), revision: 1,
        sourceHistory: {
          pageTitle: '文件导入指南',
          before: { version: 'v1.2', date: '2026-10-14 14:20', markdown: '# 文件导入指南\n\n## 原文件更新\n\n重新上传文件后，系统会对新版本进行解析。相关 Wiki 页面需要在文件解析完成后重新整理。\n\n## 关联 FAQ 维护\n\n已生成的 FAQ 不会随来源自动更新，需要维护人员逐条检查并修改答案。' },
          after: { version: 'v1.3', date: '2026-10-16 09:30', markdown: '# 文件导入指南\n\n## 原文件更新\n\n重新上传文件后，系统会解析新版本，并重新整理受影响的 Wiki 页面，保留更新前后的内容版本。\n\n## 关联 FAQ 维护\n\n文档或 Wiki 的更新影响答案时，关联 FAQ 同步生成新的候选内容。已启用的 FAQ 转为已变更，复核通过后重新启用；尚未审核的 FAQ 保持待审核。' },
        },
      },
    ]);
  }
  return structuredClone(faqMockStore.get(knowledgeBaseId)!);
};

export const saveFaq = async (knowledgeBaseId: string, input: Pick<FaqItem, 'question' | 'answer' | 'status'>, id?: string) => {
  const rows = await getFaqList(knowledgeBaseId);
  if (!input.question.trim() || !input.answer.trim()) throw new Error('Question and answer are required');
  const previous = rows.find((row) => row.id === id);
  const item: FaqItem = {
    ...previous,
    ...input,
    question: input.question.trim(),
    answer: input.answer.trim(),
    id: id ?? '',
  };
  if (id && !rows.some((row) => row.id === id)) throw new Error('FAQ not found');
  faqMockStore.set(knowledgeBaseId, id ? rows.map((row) => row.id === id ? item : row) : [item, ...rows]);
  return { ...item };
};

export const approveFaqChange = async (knowledgeBaseId: string, snapshot: FaqItem) => {
  const rows = await getFaqList(knowledgeBaseId);
  const current = rows.find((row) => row.id === snapshot.id);
  if (!current || current.status !== 'changed' || !current.sourceHistory
    || current.revision !== snapshot.revision
    || current.sourceHistory.after.version !== snapshot.sourceHistory?.after.version) {
    throw new Error('FAQ_REVIEW_CONFLICT');
  }
  faqMockStore.set(knowledgeBaseId, rows.map((row) => row.id === current.id
    ? { ...row, status: 'enabled', updatedAt: new Date().toISOString() }
    : row));
};

export const deleteFaq = async (knowledgeBaseId: string, ids: string[]) => {
  const rows = await getFaqList(knowledgeBaseId);
  faqMockStore.set(knowledgeBaseId, rows.filter((row) => !ids.includes(row.id ?? '')));
};

export interface WikiRevision {
  version: number;
  markdown: string;
  created_at: string;
  created_by: string;
}

export type WikiRevisionSummary = Omit<WikiRevision, 'markdown'>;


//获取知识库类型 (返回字符串数组，每个字符串是 KnowledgeBase 的 type 值)
export const getKnowledgeBaseTypeList = async (): Promise<string[]> => {
  const response = await request.get(`/knowledges/knowledgetype`);
  // 如果直接返回字符串数组，直接返回
  if (Array.isArray(response)) {
    return response.map(item => {
      // 如果是字符串，直接返回
      if (typeof item === 'string') {
        return item;
      }
      // 如果是对象且有 type 字段，提取 type 值
      if (typeof item === 'object' && item !== null && 'type' in item) {
        return String(item.type);
      }
      // 其他情况转换为字符串
      return String(item);
    });
  }
  // 如果不是数组，返回空数组
  return [];
};
// 获取文件地址
export const getFileUrl = (fileId: string) => {
  return `/files/${fileId}`;
};

// 知识库列表
export const getKnowledgeBaseListUrl = '/knowledges/knowledges';
export const getKnowledgeBaseList = (query?: ListQuery) => {
  return request.get(getKnowledgeBaseListUrl, query);
};
// 知识库详情
export const getKnowledgeBaseDetail = async (id: string) => {
  const response = await request.get(`/knowledges/${id}`);
  return response as KnowledgeBase;
};
// 创建知识库
export const createKnowledgeBase = async (data: KnowledgeBaseFormData) => {
  const payload: KnowledgeBaseFormData = {
    ...data,
    permission_id: data.permission_id ?? 'private',
  };
  const response = await request.post(`/knowledges/knowledge`, payload);
  return response as KnowledgeBase;
};
// 更新知识库
export const updateKnowledgeBase = async (id: string, data: KnowledgeBaseFormData) => {
  const payload: KnowledgeBaseFormData = {
    ...data,
  };
  const response = await request.put(`/knowledges/${id}`, payload);
  return response as any;
};
// 删除知识库(软删除)
export const deleteKnowledgeBase = async (id: string) => { 
    const response = await request.delete(`/knowledges/${id}`);
    return response as any;
}
// 复制知识库
export const copyKnowledgeBase = (knowledge_id: string, data: { name?: string; }) => {
    return request.post(`/knowledges/${knowledge_id}/copy`, data)
}       

// 知识库分享 获取分享空间列表
export const getShareSpaceList = async (id: string) => {
    const response = await request.get(`/knowledgeshares/${id}/knowledgeshares`);
    return response as KnowledgeBaseShareListResponse;
}

// 获取文件夹列表
export const getFolderList = async (query: FolderFormData) => { 
  const id = query.parent_id ?? query.kb_id;
  const response = await request.get(`/files/${query.kb_id}/${id}/files`);
  return response as any;
};
// 创建文件夹
export const createFolder = async (params: FolderFormData) => {
  const response = await request.post(`/files/folder`, undefined, {
    params,
  });
  return response as FolderFormData;
};
interface UploadFileOptions {
  kb_id?: string;
  parent_id?: string;
  onUploadProgress?: (event: AxiosProgressEvent) => void;
  signal?: AbortSignal;
}
// 上传文件
export const uploadFile = async (data: FormData, options?: UploadFileOptions) => {
  const { kb_id, parent_id, onUploadProgress, signal } = options || {};
  const params: Record<string, string> = {};
  if (kb_id) params.kb_id = kb_id;
  if (parent_id) params.parent_id = parent_id;
  const response = await request.uploadFile(`/files/file`, data, {
    params,
    onUploadProgress,
    signal,
  });
  return response as UploadFileResponse;
};
// 上传 QA 文件
export const uploadQaFile = async (data: FormData, options?: UploadFileOptions) => {
  const { kb_id, parent_id, onUploadProgress, signal } = options || {};
  const params: Record<string, string> = {};
  if (kb_id) params.kb_id = kb_id;
  if (parent_id) params.parent_id = parent_id;
  const response = await request.uploadFile(`/chunks/${kb_id}/import_qa`, data, {
    params,
    onUploadProgress,
    signal,
  });
  return response as UploadFileResponse;
};

// 下载文件
export const downloadFile = async (fileId: string, fileName?: string, original?: boolean) => {
  const token = cookieUtils.get('authToken');
  const url = `/api/files/${fileId}${original ? '?original=true' : ''}`;
  
  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
    
    if (!response.ok) {
      throw new Error('下载失败');
    }
    
    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    
    // 创建临时链接触发下载
    const link = document.createElement('a');
    link.href = blobUrl;
    link.style.display = 'none';
    if (fileName) {
      link.setAttribute('download', fileName);
    }
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    // 释放 blob URL
    URL.revokeObjectURL(blobUrl);
  } catch (error) {
    console.error('下载文件失败:', error);
    throw error;
  }
};
// 更新文件信息
export const updateFile = async (id: string, data: UploadFileFormData) => {
  const response = await request.put(`/files/${id}`, data);
  return response as UploadFileResponse;
};
// 删除文件 文件夹 id
export const deleteFile = async (id: string) => {
  const response = await request.delete(`/files/${id}`);
  return response as any;
};

// 获取文档列表
export const getDocumentList = async (kb_id:string, query: PathQuery) => {
  const response = await request.get(`/documents/${kb_id}/documents`, query);
  return response as KnowledgeBaseDocumentData[];
};
// 文档详情
export const getDocumentDetail = async (id: string) => {
  const response = await request.get(`/documents/${id}`);
  return response as KnowledgeBaseDocumentData;
};
// 创建文档
export const createDocument = async (data: KnowledgeBaseDocumentData) => {
  const response = await request.post(`/documents/document`, data);
  return response as KnowledgeBaseDocumentData;
};
// 自定义文档上传并创建
export const createDocumentAndUpload = async ( data: any, params: PathQuery) => {
  const response = await request.post(`/files/customtext`, data, { params } );
  return response as any;
};
// web feishu yuque
export const createSync = async (knowledge_id: string) => {
  const response = await request.post(`/knowledges/${knowledge_id}/sync`);
  return response as any;
};
// check feishu
export const checkFeishuSync = async (params: any) => {
  const response = await request.get(`/knowledges/check/feishu/auth`, undefined, { params });
  return response as any;
};
// check yuque
export const checkYuqueSync = async (params: any) => {
  const response = await request.get(`/knowledges/check/yuque/auth`, undefined, { params });
  return response as any;
};
// 更新文档
export const updateDocument = async (id: string, data: KnowledgeBaseDocumentData) => {
  const response = await request.put(`/documents/${id}`, data);
  return response as KnowledgeBaseDocumentData;
};
// 删除文档
export const deleteDocument = async (id: string) => {
  const response = await request.delete(`/documents/${id}`);
  return response;
};
// 文档解析 / 分块
export const parseDocument = async (id: string, data: any) => {
  const response = await request.post(`/documents/${id}/chunks`, data);
  return response as any;
};
// 文档分块预览
export const previewDocumentChunk = async (kb_id:string,id: string) => { // id document_id
  const response = await request.get(`/chunks/${kb_id}/${id}/previewchunks`);
  return response as any;
};
//文档分块列表
export const getDocumentChunkList = async (query: PathQuery) => {
  const response = await request.get(`/chunks/${query.kb_id}/${query.document_id}/chunks`, query);
  return response as any;
};
// 回归测试
export const reChunks = async (data: RecallTestParams) => {
  const response = await request.post(`/chunks/retrieval`, data);
  return response as any;
};
// 获取检索策略
export const retrievalPolicyApi = (data: { kb_ids: string[] }): Promise<RetrievalPolicy> => {
  return request.post('/chunks/retrieval-policy', data);
}
// 知识库授权 分享空间列表
export const getWorkspaceAuthorizationList = async (kb_id: string) => {
  const response = await request.get(`/knowledgeshares/${kb_id}/knowledgeshares`);
  return response as any;
};
// 知识库分享
export const shareKnowledgeBase = async (data: ShareRequestParams) => {
  const response = await request.post(`/knowledgeshares/knowledgeshare`, data);
  return response as KnowledgeBase;
}
// 空间列表
export const getSpaceList = async () => {
  const response = await request.get(`/workspaces`,{include_current:false});
  // API 返回的 data 直接是数组，需要包装成 { items: [] } 格式以保持一致性
  if (Array.isArray(response)) {
    return { items: response };
  }
  return response as { items: SpaceItem[] };
};
// 更新文档块儿
export const updateDocumentChunk = async (kb_id:string, document_id:string, doc_id:string, data: any) => {
  const response = await request.put(`/chunks/${kb_id}/${document_id}/${doc_id}`, data);
  return response as any;
};
export const deleteDocumentChunk = async (kb_id: string, document_id: string, doc_id: string) => {
  const response = await request.delete(`/chunks/${kb_id}/${document_id}/${doc_id}?force_refresh=true`);
  return response as any;
};
// 文档块儿创建
export const createDocumentChunk = async (kb_id:string, document_id:string, data: any) => {
  const response = await request.post(`/chunks/${kb_id}/${document_id}/chunk`, data);
  return response as any;
};
// 获取检索模式类型
export const getRetrievalModeType = async () => {
  const response = await request.get(`/chunks/retrieve_type`);
  return response as any;
};

// 获取知识库图谱
export const getKnowledgeGraph = async (kb_id: string) => {
  const response = await request.get(`/knowledges/${kb_id}/knowledge_graph`);
  return response;
};
// 获取知识库图谱实体类型
export const getKnowledgeGraphEntityTypes = async (query: any) => {
  const response = await request.get(`/knowledges/knowledge_graph_entity_types`,query);
  return response ;
};
// 删除图谱
export const deleteKnowledgeGraph = async (kb_id: string) => {
  const response = await request.delete(`/knowledges/${kb_id}/knowledge_graph`);
  return response;
};
// 知识库图谱重建
export const rebuildKnowledgeGraph = async (kb_id: string) => {
  const response = await request.post(`/knowledges/${kb_id}/knowledge_graph`);
  return response;
};
export const batchDownloadFilesByKb = async (kb_id: string, fileName: string, callback: () => void) => {
  const response = await request.downloadFile(`/knowledges/${kb_id}/batch-download`, fileName, undefined, callback);
  return response;
};
// 知识库 QA 对导出接口
export const exportQaByKb = (kb_id: string, file_name: string, callback: () => void) => {
  return request.getDownloadFile(`/knowledges/${kb_id}/qa/export`, file_name, undefined, callback);
}
// 知识库分块模式
export const knowledgesChunkPolicy = async (kb_id: string) => {
  return request.get(`/knowledges/${kb_id}/chunk-policy`);
}
// 启用内置元数据
export const enableBuiltInMetadata = (kb_id: string, data: { enabled: boolean }) => {
  return request.post(`/knowledges/${kb_id}/metadata/builtin/enable`, data);
}
// 获取元数据字段列表
export const getMetadataFields = (kb_id: string) => {
  return request.get(`/knowledges/${kb_id}/metadata`);
}
// 创建元数据字段
export const createMetadataField = (kb_id: string, data: any) => {
  return request.post(`/knowledges/${kb_id}/metadata`, data);
}
// 修改元数据字段
export const updateMetadataField = (kb_id: string, metadata_id: string, data: { name: string; }) => {
  return request.put(`/knowledges/${kb_id}/metadata/${metadata_id}`, data);
}
// 删除元数据字段
export const deleteMetadataField = (kb_id: string, metadata_id: string) => {
  return request.delete(`/knowledges/${kb_id}/metadata/${metadata_id}`);
}
// 获取单个文档元数据
export const getDocumentMetadata = (document_id: string) => {
  return request.get(`/documents/${document_id}/metadata`);
}
// 批量更新文档元数据
export const batchUpdateDocumentMetadata = (data: Array<{ document_id: string; metadata: Record<string, any>; }>) => {
  return request.put(`/documents/metadata/batch`, data);
}
// 更新单个文档元数据
export const updateDocumentMetadata = (document_id: string, data: {metadata: Record<string, any>}) => {
  return request.put(`/documents/${document_id}/metadata`, data);
}
// 删除单个文档元数据
export const deleteDocumentMetadata = (document_id: string, data: { field_names: string[] }) => {
  return request.post(`/documents/${document_id}/metadata`, data);
}
// 获取公共元数据字段列表接口
export const getPublicMetadataFields = (data: { kb_ids: string[] }) => {
  return request.post(`/knowledges/metadata/fields`,data);
}

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
export const getKnowledgeWikiPages = (kb_id: string, params?: WikiPagesQuery) => {
  return request.get<WikiPagesResponse>(`/knowledges/${kb_id}/wiki/pages`, params)
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
