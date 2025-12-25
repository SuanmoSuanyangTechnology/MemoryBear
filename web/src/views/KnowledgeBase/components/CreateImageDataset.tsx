import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';

import { Form, Input, message } from 'antd';
import { useTranslation } from 'react-i18next';
import type { UploadFile } from 'antd';
import type { CreateSetModalRef, CreateSetMoealRefProps, UploadFileResponse } from '@/views/KnowledgeBase/types';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import RbModal from '@/components/RbModal';
import UploadFiles from '@/components/Upload/UploadFiles';
import { uploadFile } from '@/api/knowledgeBase';

interface ImageDatasetFormData {
  name: string;
  images: UploadFile[];
}

const CreateImageDataset = forwardRef<CreateSetModalRef, CreateSetMoealRefProps>(
  ({ refreshTable }, ref) => {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const [visible, setVisible] = useState(false);
    const [messageApi, contextHolder] = message.useMessage();

    const [form] = Form.useForm<ImageDatasetFormData>();
    const [loading, setLoading] = useState(false);
    const [kbId, setKbId] = useState<string>('');
    const [parentId, setParentId] = useState<string>('');
    const uploadRef = useRef<{ fileList: UploadFile[]; clearFiles: () => void }>(null);
    // const fileIds = [];

    const handleClose = () => {
      form.resetFields();
      uploadRef.current?.clearFiles();
      setLoading(false);
      setVisible(false);
      setKbId('');
      setParentId('');
    };

    const handleOpen = (kb_id: string, parent_id: string) => {
      setKbId(kb_id);
      setParentId(parent_id);
      form.resetFields();
      uploadRef.current?.clearFiles();
      setVisible(true);
    };

    const handleSave = async () => {
      try {
        await form.validateFields();
        setLoading(true);

        const fileList = uploadRef.current?.fileList || [];

        if (fileList.length === 0) {
          throw new Error(t('knowledgeBase.pleaseUploadImages'));
        }
        const ids = fileList.map((file) => file.response?.id);
        handleChunking(kbId, parentId, ids)
        // // 上传所有图片
        // const uploadPromises = fileList.map(async (file) => {
        //   if (file.originFileObj) {
        //     const formData = new FormData();
        //     formData.append('file', file.originFileObj);
            
        //     return uploadFile(formData, {
        //       kb_id: kbId,
        //       parent_id: parentId,
        //     });
        //   }
        //   return null;
        // });

        // await Promise.all(uploadPromises);

        if (refreshTable) {
          await refreshTable();
        }

        handleClose();
      } catch (err) {
        console.error('创建图片数据集失败:', err);
      } finally {
        setLoading(false);
      }
    };
    const handleChunking = (kb_id: string, parent_id: string, file_id: Array<string>) => {
      if (!kb_id) return;
      const targetFileId = file_id
      navigate(`/knowledge-base/${kb_id}/create-dataset`, {
        state: {
          source: 'local',
          knowledgeBaseId: kb_id,
          parentId: parent_id ?? kb_id,
          startStep: 'parameterSettings',
          fileId: targetFileId,
        },
      });
    }
    useImperativeHandle(ref, () => ({
      handleOpen,
    }));
    // 检查媒体文件时长的辅助函数
    const checkMediaDuration = (file: File): Promise<number> => {
      return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const media = document.createElement(file.type.startsWith('video/') ? 'video' : 'audio');
        
        media.onloadedmetadata = () => {
          URL.revokeObjectURL(url);
          resolve(media.duration);
        };
        
        media.onerror = () => {
          URL.revokeObjectURL(url);
          reject(new Error('无法读取媒体文件'));
        };
        
        media.src = url;
      });
    };
    // 上传文件
    const handleUpload = async (options: UploadRequestOption) => {
      const { file, onSuccess, onError, onProgress, filename = 'file' } = options;
      // 获取文件扩展名
    const fileExtension = (file as File).name.split('.').pop()?.toLowerCase();
    const mediaExtensions = ['mp3', 'mp4', 'mov', 'wav'];
    
    // 如果是媒体文件，进行大小和时长检查
    if (fileExtension && mediaExtensions.includes(fileExtension)) {
      const fileSizeInMB = (file as File).size / (1024 * 1024);
      
      // 检查文件大小（256MB限制）
      if (fileSizeInMB > 256) {
        messageApi.error(`${t('knowledgeBase.sizeLimitError')}：${fileSizeInMB.toFixed(2)}MB`);
        onError?.(new Error(`${t('knowledgeBase.fileSizeExceeds')}`));
        return;
      }
      
      try {
        // 检查媒体时长（150秒限制）
        const duration = await checkMediaDuration(file as File);
        if (duration > 150) {
          messageApi.error(`${t('knowledgeBase.fileDurationLimitError')}：${Math.round(duration)}秒`);
          onError?.(new Error(`${t('knowledgeBase.fileDurationExceeds')}`));
          return;
        }
      } catch (error) {
        messageApi.error(`${t('knowledgeBase.unableReadFile')}`);
        onError?.(error as Error);
        return;
      }
    }
      const formData = new FormData();

      formData.append(filename, file as File);
      if (kbId) {
        formData.append('kb_id', kbId);
      }
      if (parentId) {
        formData.append('parent_id', parentId);
      }

      uploadFile(formData, {
        kb_id: kbId,
        parent_id: parentId,
        onUploadProgress: (event) => {
          if (!event.total) return;
          const percent = Math.round((event.loaded / event.total) * 100);
          onProgress?.({ percent }, file);
        },
      })
        .then((res: UploadFileResponse) => {
          onSuccess?.(res, new XMLHttpRequest());
          if (res?.id) {
            // 上传成功
            // fileIds.push(res.id)
          }
        })
        .catch((error) => {
          onError?.(error as Error);
        });
    };
    return (
      <>
      {contextHolder}
      <RbModal
        title={`${t('knowledgeBase.createA')} ${t('knowledgeBase.imageDataSet')}`}
        open={visible}
        onCancel={handleClose}
        okText={t('common.create')}
        onOk={handleSave}
        confirmLoading={loading}
      >
        <Form form={form} layout="vertical">
          {/* <Form.Item
            name="name"
            label={t('knowledgeBase.datasetName')}
            rules={[{ required: true, message: t('knowledgeBase.pleaseEnterDatasetName') }]}
          >
            <Input placeholder={t('knowledgeBase.pleaseEnterDatasetName')} />
          </Form.Item> */}

          <Form.Item label={t('knowledgeBase.uploadMedia')}>
            <UploadFiles 
              ref={uploadRef}
              isCanDrag={true} 
              fileSize={100} 
              multiple={true} 
              maxCount={99} 
              fileType={['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'mp3', 'mp4', 'mov', 'wav']} 
              customRequest={handleUpload} 
            />
          </Form.Item>
        </Form>
      </RbModal>
    </>);
  }
);

export default CreateImageDataset;
