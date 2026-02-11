/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:30:52 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-02 15:57:03
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

import { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Upload, Image, App } from 'antd';
import type { GetProp, UploadFile, UploadProps } from 'antd';
import type { UploadProps as RcUploadProps } from 'antd/es/upload/interface';
import { useTranslation } from 'react-i18next';

import PlusIcon from '@/assets/images/plus.svg'
import { cookieUtils } from '@/utils/request'
import { fileUploadUrl } from '@/api/fileStorage'
import styles from './index.module.less'

/** Props interface for UploadImages component */
interface UploadImagesProps extends Omit<UploadProps, 'onChange' | 'fileList'> {
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
interface UploadImagesRef {
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
  fileType = ['png', 'jpg', 'gif'],
  isAutoUpload = true,
  maxCount = 1,
  className = 'rb:size-24! rb:leading-1!',
  ...props
}, ref) => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp()
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
  const beforeUpload: RcUploadProps['beforeUpload'] = async (file: UploadFile) => {
    // Validate file size
    if (fileSize && file.size) {
      const isLtMaxSize = (file.size / 1024 / 1024) < fileSize;
      if (!isLtMaxSize) {
        message.error(t('common.fileSizeTip', { size: fileSize }));
        return Upload.LIST_IGNORE;
      }
    }
    // Validate file type
    if (accept && accept.length > 0 && file.type) {
      const isAccept = accept.includes(file.type);
      if (!isAccept) {
        message.error(`${t('common.fileAcceptTip')}${file.type}`);
        return Upload.LIST_IGNORE;
      }
    }

    if (!isAutoUpload) {
      if (!file.url && !file.preview) {
        file.url = await getBase64(file.originFileObj as FileType);
      }
      const newFileList = [...fileList, file];
      setFileList(newFileList);
      updateValue(newFileList);
      return Upload.LIST_IGNORE; // Prevent auto upload
    }

    return isAutoUpload;
  };

  /** Handle upload status change */
  const handleChange: UploadProps['onChange'] = ({ fileList: newFileList }) => {
    setFileList(newFileList);
    updateValue(newFileList);
  };

  /** Clear all uploaded files */
  const clearFiles = () => {
    setFileList([]);
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
    action,
    multiple: multiple && maxCount > 1,
    fileList,
    beforeUpload,
    headers: {
      authorization: `Bearer ${cookieUtils.get('authToken') }`,
    },
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
          <img src={PlusIcon} className="rb:size-7" />
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