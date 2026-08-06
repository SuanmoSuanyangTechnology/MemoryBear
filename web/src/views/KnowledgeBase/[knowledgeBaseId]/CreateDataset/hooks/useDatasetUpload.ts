import { useCallback, useRef } from 'react';
import type { UploadFile } from 'antd';
import type { MessageInstance } from 'antd/es/message/interface';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import type { TFunction } from 'i18next';
import { deleteDocument, uploadFile, uploadQaFile } from '@/api/knowledgeBase';
import type { UploadFileResponse } from '@/views/KnowledgeBase/types';
import type { SourceType } from '../types';

interface UseDatasetUploadOptions {
  source: SourceType;
  knowledgeBaseId?: string;
  parentId?: string;
  messageApi: MessageInstance;
  t: TFunction;
  onUploaded: (id: string) => void;
  onCsvUploaded: () => void;
  onRemoved: (id: string) => void;
}

const useDatasetUpload = ({
  source,
  knowledgeBaseId,
  parentId,
  messageApi,
  t,
  onUploaded,
  onCsvUploaded,
  onRemoved,
}: UseDatasetUploadOptions) => {
  const controllersRef = useRef(new Map<string, AbortController>());

  const checkMedia = useCallback(async (file: File) => {
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (!extension || !['mp3', 'mp4', 'mov', 'wav'].includes(extension)) return;
    const sizeInMb = file.size / (1024 * 1024);
    if (sizeInMb > 100) throw new Error(`${t('knowledgeBase.sizeLimitError')}: ${sizeInMb.toFixed(2)}MB`);

    const duration = await new Promise<number>((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const media = document.createElement(file.type.startsWith('video/') ? 'video' : 'audio');
      media.onloadedmetadata = () => {
        URL.revokeObjectURL(url);
        resolve(media.duration);
      };
      media.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error(t('knowledgeBase.unableReadFile')));
      };
      media.src = url;
    });
    if (duration > 150) throw new Error(`${t('knowledgeBase.fileDurationLimitError')}: ${Math.round(duration)}s`);
  }, [t]);

  const upload = useCallback(async (options: UploadRequestOption) => {
    const { file, onSuccess, onError, onProgress, filename = 'file' } = options;
    const target = file as File & { uid: string };
    const controller = new AbortController();
    controllersRef.current.set(target.uid, controller);

    try {
      await checkMedia(target);
      const formData = new FormData();
      formData.append(filename, target);
      if (knowledgeBaseId) formData.append('kb_id', knowledgeBaseId);
      if (parentId) formData.append('parent_id', parentId);

      const request = source === 'csv' ? uploadQaFile : uploadFile;
      const response: UploadFileResponse = await request(formData, {
        kb_id: knowledgeBaseId,
        parent_id: parentId,
        signal: controller.signal,
        onUploadProgress: (event) => {
          if (event.total) onProgress?.({ percent: Math.round((event.loaded / event.total) * 100) }, target);
        },
      });
      onSuccess?.(response, new XMLHttpRequest());
      if (source === 'csv') {
        messageApi.success(t('knowledgeBase.uploadSuccess'));
        onCsvUploaded();
      } else if (response.id) {
        onUploaded(response.id);
      }
    } catch (error) {
      const uploadError = error as Error & { code?: string };
      if (uploadError.name !== 'AbortError' && uploadError.code !== 'ERR_CANCELED') {
        messageApi.error(uploadError.message);
        onError?.(uploadError);
      }
    } finally {
      controllersRef.current.delete(target.uid);
    }
  }, [checkMedia, knowledgeBaseId, messageApi, onCsvUploaded, onUploaded, parentId, source, t]);

  const remove = useCallback(async (file: UploadFile) => {
    const controller = controllersRef.current.get(file.uid);
    if (controller) {
      controller.abort();
      controllersRef.current.delete(file.uid);
      return true;
    }
    const response = file.response as UploadFileResponse | undefined;
    if (!response?.id) return true;
    try {
      await deleteDocument(response.id);
      onRemoved(response.id);
      return true;
    } catch {
      messageApi.error(t('common.deleteFailed'));
      return false;
    }
  }, [messageApi, onRemoved, t]);

  return { upload, remove };
};

export default useDatasetUpload;
