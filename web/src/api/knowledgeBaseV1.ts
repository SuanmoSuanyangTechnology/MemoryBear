import { request } from '@/utils/request';
import type {
  V1ConfigurationOptions,
  V1FolderCreate,
  V1FolderTreeData,
  V1KnowledgeCreate,
  V1KnowledgeListItem,
  V1KnowledgeListResponse,
  V1ListQuery,
  V1KnowledgePatch,
} from '@/views/KnowledgeBase/typesV1';

/** Query the knowledge base list (cards only, excluding Folder) */
export const getKnowledgeBaseListV1Url ='/knowledges/v1';
export const getKnowledgeBaseListV1 = (query?: V1ListQuery) =>
  request.get<V1KnowledgeListResponse>(getKnowledgeBaseListV1Url, { params: query });

/** Get the knowledge base folder tree (normal browsing passes parent_id to return direct subdirectories; search passes keywords to return a pruned tree; the two are mutually exclusive) */
export const getKnowledgeBaseFolderTreeV1 = (
  params?: { parent_id?: string; keywords?: string },
) =>
  request.get<V1FolderTreeData>('/knowledges/v1/folder-tree', params);

/** Get knowledge base details */
export const getKnowledgeBaseDetailV1 = (id: string) =>
  request.get<V1KnowledgeListItem>(`/knowledges/v1/${id}`);

/** Create a knowledge base or directory */
export const createKnowledgeBaseV1 = (
  data: V1KnowledgeCreate | V1FolderCreate,
) => request.post<V1KnowledgeListItem>('/knowledges/v1', data);

/** Partially modify a knowledge base */
export const patchKnowledgeBaseV1 = (
  id: string,
  data: V1KnowledgePatch,
) => request.patch<V1KnowledgeListItem>(`/knowledges/v1/${id}`, data);

/** Get knowledge base configuration options (new-creation defaults, field definitions, combination restrictions, and space default models) */
export const getKnowledgeConfigurationOptions = () =>
  request.get<V1ConfigurationOptions>('/knowledges/v1/configuration-options');
