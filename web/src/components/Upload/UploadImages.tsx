import { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Upload, Modal, Image, App } from 'antd';
import type { GetProp, UploadFile, UploadProps } from 'antd';
// import { UploadOutlined, } from '@ant-design/icons';
import type { UploadProps as RcUploadProps } from 'antd/es/upload/interface';
import { useTranslation } from 'react-i18next';
import PlusIcon from '@/assets/images/plus.svg'

const { confirm } = Modal;

interface UploadImagesProps extends Omit<UploadProps, 'onChange'> {
  /** 上传接口地址 */
  action?: string;
  /** 是否支持多选 */
  multiple?: boolean;
  /** 已上传的文件列表 */
  fileList?: UploadFile[];
  /** 文件列表变化回调 */
  onChange?: (fileList: UploadFile[]) => void;
  /** 禁用上传 */
  disabled?: boolean;
  /** 文件大小限制（MB） */
  fileSize?: number;
  /** 文件类型限制 */
  fileType?: string[];
  /** 是否自动上传，默认为true */
  isAutoUpload?: boolean;
  /** 最大上传文件数 */
  maxCount?: number;
}
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
interface UploadImagesRef {
  fileList: UploadFile[];
  clearFiles: () => void;
}
type FileType = Parameters<GetProp<UploadProps, 'beforeUpload'>>[0];
const getBase64 = (file: FileType): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = (error) => reject(error);
  });
}

/**
 * 公共上传组件，基于Ant Design Upload组件封装
 * 支持单文件/多文件上传、拖拽上传、文件验证、预览等功能
 */
const UploadImages = forwardRef<UploadImagesRef, UploadImagesProps>(({
  action = '/api/upload',
  multiple = false,
  fileList: propFileList = [],
  onChange,
  disabled = false,
  fileSize,
  fileType = ['png', 'jpg', 'gif'],
  isAutoUpload = true,
  maxCount = 1,
  ...props
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [fileList, setFileList] = useState<UploadFile[]>(propFileList);
  const [accept, setAccept] = useState<string | undefined>();
  // const [loading, setLoading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewImage, setPreviewImage] = useState('');

  // 处理文件移除
  const handleRemove = (file: UploadFile) => {
    confirm({
      title: '确定要删除此文件吗？',
      okText: '确定',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => {
        const newFileList = fileList.filter((item) => item.uid !== file.uid);
        setFileList(newFileList);
        onChange?.(newFileList);
      },
    });
    return false; // 阻止默认删除行为，由confirm控制
  };

  // 校验文件类型和大小
  const beforeUpload: RcUploadProps['beforeUpload'] = async (file: UploadFile) => {
    // 校验文件大小
    if (fileSize && file.size) {
      const isLtMaxSize = (file.size / 1024 / 1024) < fileSize;
      if (!isLtMaxSize) {
        message.error(`文件大小不能超过 ${fileSize}MB`);
        return Upload.LIST_IGNORE;
      }
    }
    // 校验文件类型
    if (accept && accept.length > 0 && file.type) {
      const isAccept = accept.includes(file.type);
      if (!isAccept) {
        message.error(`不支持的文件类型: ${file.type}`);
        return Upload.LIST_IGNORE;
      }
    }

    if (!isAutoUpload) {
      if (!file.url && !file.preview) {
        file.url = await getBase64(file.originFileObj as FileType);
      }
      const newFileList = [...fileList, file];
      setFileList(newFileList);
      onChange?.(newFileList);
      return Upload.LIST_IGNORE; // 阻止自动上传
    }

    return isAutoUpload;
  };

  // 处理上传状态变化
  const handleChange: UploadProps['onChange'] = ({ fileList: newFileList }) => {
    setFileList(newFileList);
    if (onChange) {
      onChange(newFileList);
    }
  };

  // 清空已上传文件
  const clearFiles = () => {
    setFileList([]);
    if (onChange) {
      onChange([]);
    }
  }

  const handlePreview = async (file: UploadFile) => {
    if (!file.thumbUrl && !file.url && !file.preview) {
      file.preview = await getBase64(file.originFileObj as FileType);
    }

    setPreviewImage(file.thumbUrl || file.url || (file.preview as string));
    setPreviewOpen(true);
  };

  useEffect(() => {
    if (fileType && fileType.length > 0) {
      const acceptArray = fileType.map((type: string) => ALL_FILE_TYPE[type.toLowerCase()]).filter(Boolean);
      setAccept(acceptArray.join(','));
    } else {
      setAccept(undefined);
    }
  }, [fileType])

  // 生成上传组件配置
  const uploadProps: UploadProps = {
    action,
    multiple: multiple && maxCount > 1,
    fileList,
    beforeUpload,
    headers: {
      authorization:  localStorage.getItem('token') || '',
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
    ...props,
  };

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    fileList,
    clearFiles
  }));

  return (
    <>
      <Upload
        {...uploadProps}
        style={{ 
          width: '136px', 
          height: '136px',
        }}
      >
        {fileList.length < maxCount && (
          <div className="rb:flex rb:flex-wrap rb:items-center rb:justify-center">
            <img src={PlusIcon} className="rb:w-[32px] rb:h-[32px]" />
            <div className="rb:mt-[12px] rb:text-[12px] rb:text-[#5B6167] rb:leading-[16px]">{t('common.clickUploadIcon')}</div>
          </div>
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