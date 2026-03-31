/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-06 21:09:42 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 18:23:04
 */
/**
 * File Upload Component
 * 
 * A reusable file upload component based on Ant Design Upload.
 * Supports single/multiple file uploads, drag-and-drop, file validation, and preview.
 * 
 * Features:
 * - File type validation (images, documents, etc.)
 * - File size validation
 * - Auto-upload or manual upload modes
 * - Progress tracking
 * - Custom upload actions and headers
 * - File list management
 * 
 * @component
 */
import { useState, useEffect, forwardRef, useImperativeHandle, useMemo } from 'react';
import { Upload, Progress, App, Flex } from 'antd';
import type { UploadProps, UploadFile } from 'antd';
import type { UploadProps as RcUploadProps, RcFile, UploadFileStatus } from 'antd/es/upload/interface';
import { useTranslation } from 'react-i18next';

import { request } from '@/utils/request'
import { fileUploadUrlWithoutApiPrefix } from '@/api/fileStorage'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';

interface UploadFilesProps extends Omit<UploadProps, 'onChange'> {
  /** Upload API endpoint */
  action?: string;
  /** Enable multiple file selection */
  multiple?: boolean;
  /** List of uploaded files */
  fileList?: UploadFile[];
  /** Callback when file list changes */
  onChange?: (fileList: UploadFile | UploadFile[]) => void;
  customRequest?: RcUploadProps['customRequest'];
  /** Custom upload request configuration */
  requestConfig?: {
    data?: Record<string, string | number | boolean>;
    headers?: Record<string, string>;
  };
  /** Disable upload */
  disabled?: boolean;
  /** File size limit in MB */
  fileSize?: number;
  /** Auto-upload on file selection, default is true */
  isAutoUpload?: boolean;
  /** Maximum number of files allowed */
  maxCount?: number;
  /** Custom file removal callback */
  onRemove?: (file: UploadFile) => boolean | void | Promise<boolean | void>;

  featureConfig: FeaturesConfigForm['file_upload']
}

export const transform_file_type: Record<string, string> = {
  'text/plain': 'document/text',
  'text/markdown': 'document/markdown',
  'text/x-markdown': 'document/x-markdown',

  'application/pdf': 'document/pdf',

  'application/msword': 'document/doc',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'document/docx',

  'application/vnd.ms-powerpoint': 'document/ppt',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'document/pptx',

  'application/vnd.ms-excel': 'document/xls',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'document/xlsx',
  'text/csv': 'document/csv',

  'application/json': 'document/json'
}
// Mapping of file extensions to MIME types
const ALL_FILE_TYPE: {
  [key: string]: string;
} = {
  txt: 'text/plain',
  md: 'text/markdown',
  xmd: 'text/x-markdown',

  pdf: 'application/pdf',

  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',

  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',

  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',

  csv: 'text/csv',

  json: 'application/json',

  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  gif: 'image/gif',
  bmp: 'image/bmp',
  webp: 'image/webp',
  svg: 'image/svg+xml',

  mp4: 'video/mp4',
  mov: 'video/quicktime',
  avi: 'video/x-msvideo',
  mkv: 'video/x-matroska',
  webm: 'video/webm',
  flv: 'video/x-flv',
  wmv: 'video/x-ms-wmv',

  mp3: 'audio/mpeg',
  wav: 'audio/wav',
  ogg: 'audio/ogg',
  aac: 'audio/aac',
  flac: 'audio/flac',
  m4a: 'audio/mp4',
  wma: 'audio/x-ms-wma',
  xm4a: 'audio/x-m4a',
}
export interface UploadFilesRef {
  /** Current file list */
  fileList: UploadFile[];
  /** Clear all uploaded files */
  clearFiles: () => void;
}

/**
 * Common upload component based on Ant Design Upload
 * Supports single/multiple file uploads, drag-and-drop, file validation, and preview
 */
const UploadFiles = forwardRef<UploadFilesRef, UploadFilesProps>(({
  action = fileUploadUrlWithoutApiPrefix,
  multiple = false,
  fileList: propFileList = [],
  onChange,
  disabled = false,
  fileSize = 5,
  isAutoUpload = true,
  maxCount = 1,
  onRemove: customOnRemove,
  requestConfig,
  featureConfig,
  ...props
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [fileList, setFileList] = useState<UploadFile[]>(propFileList);
  const [accept, setAccept] = useState<string | undefined>();

  const fileType = useMemo(() => {
    let types: string[] = [];
    ['image', 'document', 'video', 'audio'].forEach(type => {
      if (featureConfig[`${type}_enabled` as keyof FeaturesConfigForm['file_upload']]) {
        types = types.concat(featureConfig[`${type}_allowed_extensions` as keyof FeaturesConfigForm['file_upload']] as string[])
      }
    })

    return types
  }, [featureConfig])

  /**
   * Validates file type and size before upload
   * @returns Upload.LIST_IGNORE to prevent upload, or true to proceed
   */
  const beforeUpload: RcUploadProps['beforeUpload'] = (file) => {
    // Determine file category and get max size from featureConfig
    const mimePrefix = file.type?.split('/')[0]
    const categoryMap: Record<string, keyof FeaturesConfigForm['file_upload']> = {
      image: 'image_max_size_mb',
      video: 'video_max_size_mb',
      audio: 'audio_max_size_mb',
    }
    const maxSizeKey = categoryMap[mimePrefix] ?? 'document_max_size_mb'
    const maxSize = (featureConfig[maxSizeKey] as number) ?? fileSize

    const fileSizeMB = file.size / 1024 / 1024
    const isLtMaxSize = fileSizeMB < maxSize;
    if (!isLtMaxSize) {
      message.error(t('common.fileSizeTip', { size: maxSize }));
      return Upload.LIST_IGNORE;
    }
    // Validate file type
    if (fileType && fileType.length > 0) {
      // Get file extension
      const fileName = file.name.toLowerCase();
      const fileExtension = fileName.substring(fileName.lastIndexOf('.') + 1);

      // Check if extension is in allowed types list
      const isValidExtension = fileType.some(type => type.toLowerCase() === fileExtension);

      // Also check MIME type if available (as fallback validation)
      const isValidMimeType = file.type && accept ? accept.includes(file.type) : true;

      if (!isValidExtension && !isValidMimeType) {
        message.error(`${t('common.fileAcceptTip')} ${fileExtension || file.type}`);
        return Upload.LIST_IGNORE;
      }
    }

    if (!isAutoUpload) {
      const newFileList = [...fileList, file as UploadFile];
      setFileList(newFileList);
      onChange?.(newFileList);
      return Upload.LIST_IGNORE; // Prevent auto-upload
    }

    return isAutoUpload;
  };

  /**
   * Custom upload request handler
   */
  const handleCustomRequest: RcUploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options;
    if (typeof file === 'string') return;
    const rcFile = file as RcFile;
    const formData = new FormData();
    formData.append('file', rcFile);
    const fileVo: UploadFile = {
      uid: rcFile.uid,
      name: rcFile.name,
      status: 'uploading' as UploadFileStatus,
      percent: 0,
      type: rcFile.type,
      originFileObj: rcFile,
      thumbUrl: URL.createObjectURL(rcFile)
    }
    onChange?.(fileVo)
    request.uploadFile(action, formData, requestConfig)
      .then(res => {
        onSuccess?.({ data: res });
      })
      .catch((error) => {
        onError?.(error as Error);
        fileVo.status = 'error'
        onChange?.(fileVo)
      })
  };

  /**
   * Handles upload state changes
   */
  const handleChange: UploadProps['onChange'] = ({ fileList: newFileList }) => {
    newFileList.map(file => {
      const type = (file.type && transform_file_type[file.type as keyof typeof transform_file_type]) || file.type || 'document'
      file.type = type
      file.thumbUrl = file.thumbUrl || URL.createObjectURL(file.originFileObj as Blob)
    })
    setFileList(newFileList);
    if (onChange) {
      onChange(maxCount === 1 ? newFileList[newFileList.length - 1] : newFileList);
    }
  };

  /**
   * Clears all uploaded files
   */
  const clearFiles = () => {
    setFileList([]);
    if (onChange) {
      onChange([]);
    }
  }

  // Build accept string from file types (includes both MIME types and extensions)
  useEffect(() => {
    if (fileType && fileType.length > 0) {
      // Include both MIME types and file extensions
      const acceptArray: string[] = [];
      fileType.forEach((type: string) => {
        const lowerType = type.toLowerCase();
        // Add MIME type (if exists)
        const mimeType = ALL_FILE_TYPE[lowerType];
        if (mimeType) {
          acceptArray.push(mimeType);
        }
        // Add file extension (.md, .html, etc.)
        acceptArray.push(`.${lowerType}`);
      });
      setAccept(acceptArray.join(','));
    } else {
      setAccept(undefined);
    }
  }, [fileType])

  // Generate upload component configuration
  const uploadProps: UploadProps = {
    customRequest: handleCustomRequest,
    multiple: multiple && maxCount > 1,
    fileList,
    beforeUpload,
    onChange: handleChange,
    accept,
    disabled,
    showUploadList: false,
    itemRender: (_, file, __, actions) => {
      return (
        <div key={file.uid} className="rb:relative rb:w-full rb:pt-2 rb:px-2.5 rb-pb-[10px] rb:border rb:border-[#EBEBEB] rb:rounded rb:p-2 rb:mt-2 rb:bg-white">
          <Flex align="center" justify="space-between" className="rb:text-[12px] rb:mb-0.5!">
            {file.name}
            <span className="rb:text-[#5B6167] rb:cursor-pointer" onClick={() => actions?.remove()}>{t('common.cancel')}</span>
          </Flex>
          <Progress percent={file.percent || 0} strokeColor={file.status === 'error' ? '#FF5D34' : '#155EEF'} size="small" showInfo={false} />
        </div>
      );
    },
    className: 'rb:-mb-1.5! upload-block',
    ...props,
  };

  // Expose methods to parent component via ref
  useImperativeHandle(ref, () => ({
    fileList,
    clearFiles
  }));

  return (
    <Upload
      {...uploadProps}
    >
      <div>{t('memoryConversation.uploadFile')}</div>
    </Upload>
  );
});

export default UploadFiles;