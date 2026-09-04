/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:30:52 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-10 15:01:42
 */
/**
 * UploadImages Component
 * 
 * A comprehensive image upload component with:
 * - Single/multiple file upload support
 * - File type and size validation
 * - Image preview functionality
 * - Auto or manual upload modes
 * - Drag-and-drop support
 * - Base64 conversion for non-auto upload
 * 
 * @component
 */

import { useState, useEffect, useRef, forwardRef, useImperativeHandle } from 'react';
import { Upload, Image, App } from 'antd';
import type { GetProp, UploadFile, UploadProps } from 'antd';
import type { UploadProps as RcUploadProps } from 'antd/es/upload/interface';
import { useTranslation } from 'react-i18next';

import { cookieUtils } from '@/utils/request'
import { fileUploadUrl } from '@/api/fileStorage'
import styles from './index.module.less'

/** Props interface for UploadImages component */
export interface UploadImagesProps extends Omit<UploadProps, 'onChange' | 'fileList'> {
  /** Upload API URL */
  action?: string;
  /** Support multiple file selection */
  multiple?: boolean;
  /** Uploaded file list */
  fileList?: UploadFile[] | UploadFile;
  /** File list change callback */
  onChange?: (fileList?: UploadFile[] | UploadFile) => void;
  /** Disable upload */
  disabled?: boolean;
  /** File size limit (MB) */
  fileSize?: number;
  /** File type restrictions */
  fileType?: string[];
  /** Auto upload, default is true */
  isAutoUpload?: boolean;
  /** Maximum upload file count */
  maxCount?: number;
  className?: string;
}

/** Supported file type mappings (extension to MIME type) */
const ALL_FILE_TYPE: {
  [key: string]: string;
} = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  gif: 'image/gif',
  bmp: 'image/bmp',
  webp: 'image/webp',
  svg: 'image/svg+xml',
}

/** Ref methods exposed to parent component */
export interface UploadImagesRef {
  fileList: UploadFile[];
  clearFiles: () => void;
}
type FileType = Parameters<GetProp<UploadProps, 'beforeUpload'>>[0];

/** Convert file to base64 string for preview */
const getBase64 = (file: FileType): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = (error) => reject(error);
  });
}

/**
 * Common upload component based on Ant Design Upload component
 * Supports single/multiple file upload, drag-and-drop, file validation, preview, etc.
 */
const UploadImages = forwardRef<UploadImagesRef, UploadImagesProps>(({
  action = fileUploadUrl,
  multiple = false,
  fileList: propFileList = [],
  onChange,
  disabled = false,
  fileSize,
  fileType = ['png', 'jpg', 'gif', 'svg'],
  isAutoUpload = true,
  maxCount = 1,
  className = 'rb:size-24! rb:leading-1!',
  ...props
}, ref) => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp()
  const readRequestIdRef = useRef(0);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [accept, setAccept] = useState<string | undefined>();
  // const [loading, setLoading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewImage, setPreviewImage] = useState('');

  useEffect(() => {
    if (!Array.isArray(propFileList) && typeof propFileList === 'object') {
      setFileList([propFileList]);
    }
  }, [propFileList])

  useEffect(() => () => {
    readRequestIdRef.current += 1;
  }, []);

  /** Update value based on maxCount (single or multiple) */
  const updateValue = (list: UploadFile[]) => {
    if (maxCount === 1) {
      onChange?.(list[0])
    } else {
      onChange?.(list)
    }
  }

  /** Handle file removal with confirmation dialog */
  const handleRemove = (file: UploadFile) => {
    modal.confirm({
      title: t('common.confirmRemoveFile'),
      okText: `${t('common.confirm')}`,
      okType: 'danger',
      cancelText: `${t('common.cancel')}`,
      onOk: () => {
        const newFileList = fileList.filter((item) => item.uid !== file.uid);
        setFileList(newFileList);
        updateValue(newFileList)
      },
    });
    return false; // Prevent default delete behavior, controlled by confirm
  };

  /** Validate file type and size before upload */
  const beforeUpload: RcUploadProps['beforeUpload'] = async (file) => {
    // Validate file size
    if (fileSize && file.size) {
      const isLtMaxSize = (file.size / 1024 / 1024) < fileSize;
      if (!isLtMaxSize) {
        message.error(t('common.fileSizeTip', { size: fileSize }));
        return Upload.LIST_IGNORE;
      }
    }
    // Validate file type
    const acceptedTypes = accept?.split(',').filter(Boolean) ?? [];
    if (acceptedTypes.length > 0 && !acceptedTypes.includes(file.type)) {
      message.error(`${t('common.fileAcceptTip')}${file.type || file.name}`);
      return Upload.LIST_IGNORE;
    }

    if (!isAutoUpload) {
      const readRequestId = readRequestIdRef.current + 1;
      readRequestIdRef.current = readRequestId;
      const dataUrl = await getBase64(file as FileType);
      if (readRequestId !== readRequestIdRef.current) {
        return Upload.LIST_IGNORE;
      }

      const manualFile: UploadFile = {
        uid: file.uid,
        name: file.name,
        status: 'done',
        type: file.type,
        size: file.size,
        originFileObj: file,
        url: dataUrl,
        thumbUrl: dataUrl,
      };
      const newFileList = maxCount === 1
        ? [manualFile]
        : [...fileList, manualFile].slice(-maxCount);
      setFileList(newFileList);
      updateValue(newFileList);
      return Upload.LIST_IGNORE; // Prevent auto upload
    }

    return true;
  };

  /** Handle upload status change */
  const handleChange: UploadProps['onChange'] = ({ fileList: newFileList }) => {
    setFileList(newFileList);
    updateValue(newFileList);
  };

  /** Clear all uploaded files */
  const clearFiles = () => {
    readRequestIdRef.current += 1;
    setFileList([]);
    setPreviewOpen(false);
    setPreviewImage('');
    updateValue([]);
  }

  /** Handle image preview */
  const handlePreview = async (file: UploadFile) => {
    if (!file.thumbUrl && !file.url && !file.preview) {
      file.preview = await getBase64(file.originFileObj as FileType);
    }

    setPreviewImage(file.thumbUrl || file.url || (file.preview as string));
    setPreviewOpen(true);
  };

  /** Build accept string from fileType array */
  useEffect(() => {
    if (fileType && fileType.length > 0) {
      const acceptArray = fileType.map((type: string) => ALL_FILE_TYPE[type.toLowerCase()]).filter(Boolean);
      setAccept(acceptArray.join(','));
    } else {
      setAccept(undefined);
    }
  }, [fileType])

  /** Generate upload component configuration */
  const uploadProps: UploadProps = {
    action: isAutoUpload ? action : undefined,
    multiple: multiple && maxCount > 1,
    fileList,
    beforeUpload,
    headers: isAutoUpload
      ? { authorization: `Bearer ${cookieUtils.get('authToken') }` }
      : undefined,
    onPreview: handlePreview,
    onRemove: handleRemove,
    onChange: handleChange,
    accept,
    disabled,
    listType: 'picture-card',
    showUploadList: {
      showPreviewIcon: true,
      showRemoveIcon: true,
      showDownloadIcon: false,
    },
    className: `${styles.imageUpload} ${className}`,
    ...props,
  };

  /** Expose methods to parent component via ref */
  useImperativeHandle(ref, () => ({
    fileList,
    clearFiles
  }));

  return (
    <>
      <Upload
        {...uploadProps}
      >
        {fileList.length < maxCount && (
          <div className="rb:size-7 rb:bg-cover rb:bg-[url('@/assets/images/plus.svg')]"></div>
        )}  
      </Upload>
      {previewImage && (
        <Image
          wrapperStyle={{ display: 'none' }}
          preview={{
            visible: previewOpen,
            onVisibleChange: (visible) => setPreviewOpen(visible),
            afterOpenChange: (visible) => !visible && setPreviewImage(''),
          }}
          src={previewImage}
        />
      )}
    </>
  );
});

export default UploadImages;