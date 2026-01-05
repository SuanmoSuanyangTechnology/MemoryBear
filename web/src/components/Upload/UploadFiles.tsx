import { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Upload, Button, Modal, Progress, App } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import type { UploadProps, UploadFile } from 'antd';
// import { request } from '@/utils/request';
import type { UploadProps as RcUploadProps } from 'antd/es/upload/interface';
import CloudUploadOutlined from '@/assets/images/CloudUploadOutlined.png'
import { useTranslation } from 'react-i18next';
import { cookieUtils } from '@/utils/request'

const { confirm } = Modal;
const { Dragger } = Upload;

interface UploadFilesProps extends Omit<UploadProps, 'onChange'> {
  /** 上传接口地址 */
  action?: string;
  /** 是否支持多选 */
  multiple?: boolean;
  /** 已上传的文件列表 */
  fileList?: UploadFile[];
  /** 文件列表变化回调 */
  onChange?: (fileList: UploadFile[]) => void;
  customRequest?: RcUploadProps['customRequest'];
  /** 自定义上传请求配置 */
  requestConfig?: {
    data?: Record<string, string | number | boolean>;
    headers?: Record<string, string>;
  };
  /** 禁用上传 */
  disabled?: boolean;
  /** 文件大小限制（MB） */
  fileSize?: number;
  /** 文件类型限制 ['doc', 'xls', 'ppt', 'pdf'] */
  fileType?: string[];
  /** 是否自动上传，默认为true */
  isAutoUpload?: boolean;
  /** 最大上传文件数 */
  maxCount?: number;
  /** 是否支持拖拽上传，默认为false */
  isCanDrag?: boolean;
  /** 自定义移除文件回调 */
  onRemove?: (file: UploadFile) => boolean | void | Promise<boolean | void>;
}
const ALL_FILE_TYPE: {
  [key: string]: string;
} = {
  txt: 'text/plain',
  pdf: 'application/pdf',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  csv: 'text/csv',
  md: 'text/markdown',
  htm: 'text/html',
  html: 'text/html',
  json: 'application/json',
}
export interface UploadFilesRef {
  fileList: UploadFile[];
  clearFiles: () => void;
}

/**
 * 公共上传组件，基于Ant Design Upload组件封装
 * 支持单文件/多文件上传、拖拽上传、文件验证、预览等功能
 */
const UploadFiles = forwardRef<UploadFilesRef, UploadFilesProps>(({
  action = '/api/upload',
  multiple = false,
  fileList: propFileList = [],
  onChange,
  // requestConfig = {},
  disabled = false,
  fileSize = 5,
  fileType = ['doc', 'xls', 'ppt', 'pdf'],
  isAutoUpload = true,
  maxCount = 1,
  isCanDrag = false,
  onRemove: customOnRemove,
  ...props
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [fileList, setFileList] = useState<UploadFile[]>(propFileList);
  const [accept, setAccept] = useState<string | undefined>();

  // 处理文件移除
  const handleRemove = (file: UploadFile) => {
    // 如果有自定义的 onRemove 回调，先执行它
    if (customOnRemove) {
      const result = customOnRemove(file);
      // 如果返回 false，阻止移除
      if (result === false) {
        return false;
      }
    }
    
    confirm({
      title: `${t('common.confirmRemoveFile')}`,
      okText: `${t('common.confirm')}`,
      okType: 'danger',
      cancelText: `${t('common.cancel')}`,
      onOk: () => {
        const newFileList = fileList.filter((item) => item.uid !== file.uid);
        setFileList(newFileList);
        onChange?.(newFileList);
      },
    });
    return false; // 阻止默认删除行为，由confirm控制
  };

  // 校验文件类型和大小
  const beforeUpload: RcUploadProps['beforeUpload'] = (file) => {
    // 校验文件大小
    if (fileSize) {
      const isLtMaxSize = (file.size / 1024 / 1024) < fileSize;
      if (!isLtMaxSize) {
        message.error(`文件大小不能超过 ${fileSize}MB`);
        return Upload.LIST_IGNORE;
      }
    }
    // 校验文件类型
    if (fileType && fileType.length > 0) {
      // 获取文件扩展名
      const fileName = file.name.toLowerCase();
      const fileExtension = fileName.substring(fileName.lastIndexOf('.') + 1);
      
      // 检查扩展名是否在允许的类型列表中
      const isValidExtension = fileType.some(type => type.toLowerCase() === fileExtension);
      
      // 如果有 MIME 类型，也检查 MIME 类型（作为备选验证）
      const isValidMimeType = file.type && accept ? accept.includes(file.type) : true;
      
      if (!isValidExtension && !isValidMimeType) {
        message.error(`不支持的文件类型: ${fileExtension || file.type}`);
        return Upload.LIST_IGNORE;
      }
    }

    if (!isAutoUpload) {
      const newFileList = [...fileList, file as UploadFile];
      setFileList(newFileList);
      onChange?.(newFileList);
      return Upload.LIST_IGNORE; // 阻止自动上传
    }

    return isAutoUpload;
  };

  // 自定义上传方法
  /*
    const customRequest: RcUploadProps['customRequest'] = ({ file, onSuccess, onError, onProgress }) => {
      setLoading(true);
      
      const formData = new FormData();
      formData.append('file', file as RcFile);
      
      // 添加额外的请求参数
      const requestData = requestConfig.data;
      if (requestData) {
        Object.keys(requestData).forEach(key => {
          const value = requestData[key];
          formData.append(key, String(value));
        });
      }

      request.post(action, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
          ...requestConfig.headers,
        },
        ...requestConfig,
      })
        .then((response) => {
          if (onSuccess) onSuccess(response);
        })
        .catch((error) => {
          message.error('上传失败，请重试');
          if (onError) onError(error);
          // setFileList(fileList.filter((item) => item.uid !== (file as UploadFile).uid));
        })
        .finally(() => {
          setLoading(false);
        });
    };
  */

  // 处理上传状态变化
  const handleChange: UploadProps['onChange'] = ({ fileList: newFileList, event }) => {
    console.log('event', event)
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

  useEffect(() => {
    if (fileType && fileType.length > 0) {
      // 同时包含 MIME 类型和文件扩展名
      const acceptArray: string[] = [];
      fileType.forEach((type: string) => {
        const lowerType = type.toLowerCase();
        // 添加 MIME 类型（如果存在）
        const mimeType = ALL_FILE_TYPE[lowerType];
        if (mimeType) {
          acceptArray.push(mimeType);
        }
        // 添加文件扩展名（.md, .html 等）
        acceptArray.push(`.${lowerType}`);
      });
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
      authorization:  cookieUtils.get('authToken') || '',
    },
    onRemove: handleRemove,
    onChange: handleChange,
    accept,
    disabled,
    showUploadList: {
      showPreviewIcon: false,
      showRemoveIcon: true,
      showDownloadIcon: false,
    },
    itemRender: (_, file, __, actions) => {
      return (
        <div key={file.uid} className="rb:relative rb:w-full rb:pt-[8px] rb:pl-[10px] rb:pr-[10px] rb-pb-[10px] rb:border-1 rb:border-[#EBEBEB] rb:rounded rb:p-2 rb:mt-2 rb:bg-white">
          <div className="rb:text-[12px] rb:flex rb:items-center rb:justify-between rb:mb-[2px]">
            {file.name}
            <span className="rb:text-[#5B6167] rb:cursor-pointer" onClick={() => actions?.remove()}>Cancel</span>
          </div>
          <Progress percent={file.percent || 0} strokeColor={file.status === 'error' ? '#FF5D34' : '#155EEF'} size="small" showInfo={false} />
        </div>
      );
    },
    ...props,
  };

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    fileList,
    clearFiles
  }));

  const hasProgress = fileList.some((item) => item.percent !== 100);

  if (isCanDrag) {
    return (
      <div className="rb:mb-[24px] rb:w-full">
        <Dragger {...uploadProps} style={{ height: '270px' }}>
          <div className="rb:flex rb:justify-center rb:flex-col rb:items-center">
            <img className="rb:w-[48px] rb:h-[48px]" src={CloudUploadOutlined} />
            {!hasProgress && (!fileList || !fileList.length) &&
              <>
                <div className="rb:text-base rb:text-[14px] rb:font-medium rb:flex rb:items-center rb:mt-[8px] rb:leading-[20px]">
                  {t('common.dragUploadTip')}<span className="rb:ml-[4px] rb:text-[#155EEF]">{t('common.uploadClickTip')}</span>
                </div>
                {fileType && <div className="rb:text-[12px] rb:text-[#A8A9AA] rb:leading-[14px] rb:mt-[8px] rb:cursor-pointer">{t('common.supportedFileTypes', { types: fileType.join(',') })}</div>}
                {(fileSize || fileType || maxCount > 1) && (
                  <div className='rb:text-xs rb:mt-2 rb:text-[#A8A9AA]'>
                    {t('common.uploadFileTipMax', { max: fileSize, maxCount: maxCount })}
                  </div>
                )}
              </>
            }
            {hasProgress && <div className="rb:text-base rb:text-[14px] rb:font-medium rb:flex rb:items-center rb:mt-[8px] rb:mb-[24px] rb:leading-[20px]">{t('common.uploading')}</div>}
          </div>
        </Dragger>
      </div>
    );
  }
  return (
    <Upload
      {...uploadProps}
    >
      <div>
      <Button
        type="default"
        icon={<UploadOutlined />}
        className="rb:w-full"
        disabled={fileList.length >= maxCount}
      >
        上传文件
      </Button>
      {(fileSize || fileType || maxCount > 1) && (
        <div>
          请上传
          {fileSize && <>大小不超过 <b style={{color: '#f56c6c'}}>{ fileSize }MB</b></>}
          {fileType && <>格式为 <b style={{color: '#f56c6c'}}>{ fileType.join('、') }</b></>}
          的文件
          {multiple && maxCount > 1 && <>，最多上传 <b style={{color: '#f56c6c'}}>{ maxCount } 个</b> 文件</>}
        </div>
      )}
      </div>
    </Upload>
  );
});

export default UploadFiles;