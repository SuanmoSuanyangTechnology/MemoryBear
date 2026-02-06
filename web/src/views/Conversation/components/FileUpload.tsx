/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-06 21:09:42 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-06 21:09:42 
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
import { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Upload, Progress, App } from 'antd';
import type { UploadProps, UploadFile } from 'antd';
import type { UploadProps as RcUploadProps } from 'antd/es/upload/interface';
import { useTranslation } from 'react-i18next';
import { cookieUtils } from '@/utils/request'
import { fileUploadUrl } from '@/api/fileStorage'

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
  /** Allowed file types ['doc', 'xls', 'ppt', 'pdf'] */
  fileType?: string[];
  /** Auto-upload on file selection, default is true */
  isAutoUpload?: boolean;
  /** Maximum number of files allowed */
  maxCount?: number;
  /** Custom file removal callback */
  onRemove?: (file: UploadFile) => boolean | void | Promise<boolean | void>;
  /** Trigger to reset file list */
  update?: boolean;
}
// Mapping of file extensions to MIME types
const ALL_FILE_TYPE: {
  [key: string]: string;
} = {
  // txt: 'text/plain',
  pdf: 'application/pdf',

  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  csv: 'text/csv',

  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  
  // md: 'text/markdown',
  // htm: 'text/html',
  // html: 'text/html',
  // json: 'application/json',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  gif: 'image/gif',
  bmp: 'image/bmp',
  webp: 'image/webp',
  svg: 'image/svg+xml',
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
  action = fileUploadUrl,
  multiple = false,
  fileList: propFileList = [],
  onChange,
  disabled = false,
  fileSize = 5,
  fileType = Object.entries(ALL_FILE_TYPE).map(([key]) => key),
  isAutoUpload = true,
  maxCount = 1,
  onRemove: customOnRemove,
  update,
  ...props
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [fileList, setFileList] = useState<UploadFile[]>(propFileList);
  const [accept, setAccept] = useState<string | undefined>();

  // Reset file list when update prop changes
  useEffect(() => {
    setFileList([])
  }, [update])

  /**
   * Validates file type and size before upload
   * @returns Upload.LIST_IGNORE to prevent upload, or true to proceed
   */
  const beforeUpload: RcUploadProps['beforeUpload'] = (file) => {
    // Validate file size
    if (fileSize) {
      const isLtMaxSize = (file.size / 1024 / 1024) < fileSize;
      if (!isLtMaxSize) {
        message.error(t('common.fileSizeTip', { size: fileSize }));
        return Upload.LIST_IGNORE;
      }
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
   * Handles upload state changes
   */
  const handleChange: UploadProps['onChange'] = ({ fileList: newFileList, event }) => {
    console.log('event', event)
    setFileList(newFileList);
    if (onChange) {
      onChange(maxCount === 1 ? newFileList[0] : newFileList);
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
    action,
    multiple: multiple && maxCount > 1,
    fileList,
    beforeUpload,
    headers: {
      authorization: `Bearer ${cookieUtils.get('authToken')}`,
    },
    onChange: handleChange,
    accept,
    disabled,
    showUploadList: false,
    itemRender: (_, file, __, actions) => {
      return (
        <div key={file.uid} className="rb:relative rb:w-full rb:pt-2 rb:px-2.5 rb-pb-[10px] rb:border rb:border-[#EBEBEB] rb:rounded rb:p-2 rb:mt-2 rb:bg-white">
          <div className="rb:text-[12px] rb:flex rb:items-center rb:justify-between rb:mb-0.5">
            {file.name}
            <span className="rb:text-[#5B6167] rb:cursor-pointer" onClick={() => actions?.remove()}>Cancel</span>
          </div>
          <Progress percent={file.percent || 0} strokeColor={file.status === 'error' ? '#FF5D34' : '#155EEF'} size="small" showInfo={false} />
        </div>
      );
    },
    className: 'rb:-mb-1.5!',
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
      {t('memoryConversation.uploadFile')}
    </Upload>
  );
});

export default UploadFiles;