/** Knowledge base document v1 API wrapper (admin-side paths uniformly carry the /api prefix, provided by request's baseURL) */
import type { AxiosProgressEvent } from 'axios';

import { request } from '@/utils/request';
import type {
  DocumentProcessingWrite,
  RechunkData,
  UploadAndParseData,
  V1DocumentCreate,
  V1DocumentListQuery,
  V1DocumentListResponse,
  V1DocumentPatch,
  V1DocumentsItem,
} from '@/views/KnowledgeBase/typesDocumentV1';

interface UploadAndParseOptions {
  onUploadProgress?: (event: AxiosProgressEvent) => void;
  signal?: AbortSignal;
}

/** Single-file multipart; the configuration is submitted as a JSON string along with the form */
export const uploadAndParseDocument = (
  file: File,
  kbId: string,
  parentId: string,
  processingConfiguration: DocumentProcessingWrite,
  options?: UploadAndParseOptions,
) => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append(
    'processing_configuration',
    new Blob([JSON.stringify(processingConfiguration)], { type: 'application/json' }),
  );
  return request.uploadFile<UploadAndParseData>('/files/v1/upload-and-parse', formData, {
    params: { kb_id: kbId, parent_id: parentId },
    ...options,
  });
};

/** Create a document from an existing file without automatically dispatching parsing */
export const createDocumentWithFile = (data: V1DocumentCreate) =>
  request.post<V1DocumentsItem>('/documents/v1', data);

/** Document paginated list, without the full processing configuration */
export const getDocumentListV1Url = (kbId: string) => `/knowledges/v1/${kbId}/documents`
export const getDocumentListV1 = (kbId: string, query?: V1DocumentListQuery) =>
  request.get<V1DocumentListResponse>(getDocumentListV1Url(kbId), query);

/** Document detail, including the processing configuration */
export const getDocumentDetailV1 = (id: string) =>
  request.get<V1DocumentsItem>(`/documents/v1/${id}`);

/** Partially modify the processing configuration only; takes effect after re-chunking */
export const patchDocumentConfiguration = (id: string, data: V1DocumentPatch) =>
  request.patch<V1DocumentsItem>(`/documents/v1/${id}`, data);

/** Re-chunk by the current configuration; does not accept any request body */
export const rechunkDocumentV1 = (id: string) =>
  request.post<RechunkData>(`/documents/v1/${id}/rechunk`);
