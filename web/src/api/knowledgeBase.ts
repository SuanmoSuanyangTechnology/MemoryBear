import { request, cookieUtils } from "@/utils/request";
import type { AxiosProgressEvent } from "axios";
import type {
  ShareRequestParams,
  SpaceItem,
  UploadFileFormData,
  FolderFormData,
  UploadFileResponse,
  Model,
  PageRequest,
  KnowledgeBase,
  KnowledgeBaseFormData,
  ListQuery,
  PathQuery,
  KnowledgeBaseDocumentData,
  KnowledgeBaseListResponse,
  KnowledgeBaseShareListResponse,
} from "@/views/KnowledgeBase/types";

const apiPrefix = '';

// 从路由中获取空间ID (#号后第一个路径段)
export const getSpaceIdFromRoute = (): string | null => {
  if (typeof window === 'undefined') return null;
  const hash = window.location.hash;
  if (!hash || hash === '#') return null;
  // 移除 # 号，然后分割路径
  const path = hash.slice(1); // 移除 #
  const segments = path.split('/').filter(Boolean); // 分割并过滤空字符串
  return segments.length > 0 ? segments[0] : null;
};

export const spaceId = getSpaceIdFromRoute();
//获取知识库类型 (返回字符串数组，每个字符串是 KnowledgeBase 的 type 值)
export const getKnowledgeBaseTypeList = async (): Promise<string[]> => {
  const response = await request.get(`${apiPrefix}/knowledges/knowledgetype`);
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
// 知识库文档解析类型
export const getKnowledgeBaseDocumentParseTypeList = async () => {
  const response = await request.get(`${apiPrefix}/knowledges/parsertype`);
  return response as any[];
};

//获取模型类型
export const getModelTypeList = async () => {
    const response = await request.get(`${apiPrefix}/models/type`);
    return response as any[];
};
// 获取模型列表
export const getModelList = async (pageInfo: PageRequest) => {
    const response = await request.get(`${apiPrefix}/models`, pageInfo);
    return response as any;
};
//获取模型提供者
export const getModelProviderList = async () => {
    const response = await request.get(`${apiPrefix}/models/provider`);
    return response as any[];
};
// 获取模型信息
export const getModelDetail = async (id: string) => {
    const response = await request.get(`${apiPrefix}/models/${id}`);
    return response as Model;
};

// 知识库列表
export const getKnowledgeBaseList = async (parent_id?: string, query?: ListQuery) => {
  const response = await request.get(`${apiPrefix}/knowledges/knowledges`, query);
  return response as KnowledgeBaseListResponse;
};
// 知识库详情
export const getKnowledgeBaseDetail = async (id: string) => {
  const response = await request.get(`${apiPrefix}/knowledges/${id}`);
  return response as KnowledgeBase;
};
// 创建知识库
export const createKnowledgeBase = async (data: KnowledgeBaseFormData) => {
  const payload: KnowledgeBaseFormData = {
    ...data,
    permission_id: data.permission_id ?? 'private',
  };
  const response = await request.post(`${apiPrefix}/knowledges/knowledge`, payload);
  return response as KnowledgeBase;
};
// 更新知识库
export const updateKnowledgeBase = async (id: string, data: KnowledgeBaseFormData) => {
  const payload: KnowledgeBaseFormData = {
    ...data,
  };
  const response = await request.put(`${apiPrefix}/knowledges/${id}`, payload);
  return response as any;
};
// 删除知识库(软删除)
export const deleteKnowledgeBase = async (id: string) => { 
    const response = await request.delete(`${apiPrefix}/knowledges/${id}`);
    return response as any;
}     

// 知识库分享 获取分享空间列表
export const getShareSpaceList = async (id: string) => {
    const response = await request.get(`${apiPrefix}/knowledgeshares/${id}/knowledgeshares`);
    return response as KnowledgeBaseShareListResponse;
}

// 获取文件夹列表
export const getFolderList = async (query: FolderFormData) => { 
  const id = query.parent_id ?? query.kb_id;
  const response = await request.get(`${apiPrefix}/files/${query.kb_id}/${id}/files`);
  return response as any;
};
// 创建文件夹
export const createFolder = async (params: FolderFormData) => {
  const response = await request.post(`${apiPrefix}/files/folder`, undefined, {
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
  const response = await request.uploadFile(`${apiPrefix}/files/file`, data, {
    params,
    onUploadProgress,
    signal,
  });
  return response as UploadFileResponse;
};

// 下载文件
export const downloadFile = async (fileId: string, fileName?: string) => {
  const token = cookieUtils.get('authToken');
  const url = `${apiPrefix}/files/${fileId}`;
  
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
  const response = await request.put(`${apiPrefix}/files/${id}`, data);
  return response as UploadFileResponse;
};
// 删除文件 文件夹 id
export const deleteFile = async (id: string) => {
  const response = await request.delete(`${apiPrefix}/files/${id}`);
  return response as any;
};

// 获取文档列表
export const getDocumentList = async (kb_id:string, query: PathQuery) => {
  const response = await request.get(`${apiPrefix}/documents/${kb_id}/documents`, query);
  return response as KnowledgeBaseDocumentData[];
};
// 文档详情
export const getDocumentDetail = async (id: string) => {
  const response = await request.get(`${apiPrefix}/documents/${id}`);
  return response as KnowledgeBaseDocumentData;
};
// 创建文档
export const createDocument = async (data: KnowledgeBaseDocumentData) => {
  const response = await request.post(`${apiPrefix}/documents/document`, data);
  return response as KnowledgeBaseDocumentData;
};
// 自定义文档上传并创建
export const createDocumentAndUpload = async ( data: any, params: PathQuery) => {
  const response = await request.post(`${apiPrefix}/files/customtext`, data, { params } );
  return response as any;
};
// 更新文档
export const updateDocument = async (id: string, data: KnowledgeBaseDocumentData) => {
  const response = await request.put(`${apiPrefix}/documents/${id}`, data);
  return response as KnowledgeBaseDocumentData;
};
// 删除文档
export const deleteDocument = async (id: string) => {
  const response = await request.delete(`${apiPrefix}/documents/${id}`);
  return response;
};
// 文档解析 / 分块
export const parseDocument = async (id: string, data: any) => {
  const response = await request.post(`${apiPrefix}/documents/${id}/chunks`, data);
  return response as any;
};
// 文档分块预览
export const previewDocumentChunk = async (kb_id:string,id: string) => { // id document_id
  const response = await request.get(`${apiPrefix}/chunks/${kb_id}/${id}/previewchunks`);
  return response as any;
};
//文档分块列表
export const getDocumentChunkList = async (query: PathQuery) => {
  const response = await request.get(`${apiPrefix}/chunks/${query.kb_id}/${query.document_id}/chunks`, query);
  return response as any;
};
// 回归测试
export const reChunks = async (data: any) => {
  const response = await request.post(`${apiPrefix}/chunks/retrieval`, data);
  return response as any;
};
// 知识库授权 分享空间列表
export const getWorkspaceAuthorizationList = async (kb_id: string) => {
  const response = await request.get(`${apiPrefix}/knowledgeshares/${kb_id}/knowledgeshares`);
  return response as any;
};
// 知识库分享
export const shareKnowledgeBase = async (data: ShareRequestParams) => {
  const response = await request.post(`${apiPrefix}/knowledgeshares/knowledgeshare`, data);
  return response as KnowledgeBase;
}
// 空间列表
export const getSpaceList = async () => {
  const response = await request.get(`${apiPrefix}/workspaces`,{include_current:false});
  // API 返回的 data 直接是数组，需要包装成 { items: [] } 格式以保持一致性
  if (Array.isArray(response)) {
    return { items: response };
  }
  return response as { items: SpaceItem[] };
};
// 更新文档块儿
export const updateDocumentChunk = async (kb_id:string, document_id:string, doc_id:string, data: any) => {
  const response = await request.put(`${apiPrefix}/chunks/${kb_id}/${document_id}/${doc_id}`, data);
  return response as any;
};

// 文档块儿创建
export const createDocumentChunk = async (kb_id:string, document_id:string, data: any) => {
  const response = await request.post(`${apiPrefix}/chunks/${kb_id}/${document_id}/chunk`, data);
  return response as any;
};
// 获取检索模式类型
export const getRetrievalModeType = async () => {
  const response = await request.get(`${apiPrefix}/chunks/retrieve_type`);
  return response as any;
};
