import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';

import { Form, message } from 'antd';
import { useTranslation } from 'react-i18next';
import type { UploadFile } from 'antd';
import type { CreateSetModalRef, CreateSetMoealRefProps } from '@/views/KnowledgeBase/types';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import RbModal from '@/components/RbModal';
import UploadFiles from '@/components/Upload/UploadFiles';
import { uploadFile, deleteDocument } from '@/api/knowledgeBase';

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
    const [hasFiles, setHasFiles] = useState(false);
    const uploadRef = useRef<{ fileList: UploadFile[]; clearFiles: () => void }>(null);
    // Store AbortController for each file to cancel upload
    const abortControllersRef = useRef<Map<string, AbortController>>(new Map());
    // const fileIds = [];

    const handleClose = () => {
      // Cancel all ongoing uploads
      abortControllersRef.current.forEach((controller) => {
        controller.abort();
      });
      abortControllersRef.current.clear();
      
      form.resetFields();
      uploadRef.current?.clearFiles();
      setLoading(false);
      setVisible(false);
      setKbId('');
      setParentId('');
      setHasFiles(false);
    };

    const handleOpen = (kb_id: string, parent_id: string) => {
      setKbId(kb_id);
      setParentId(parent_id);
      form.resetFields();
      uploadRef.current?.clearFiles();
      setHasFiles(false);
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
        // // Upload all images
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
        console.error('Failed to create image dataset:', err);
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
    // Helper function to check media file duration
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
    // Delete uploaded file
    const handleDeleteFile = async (fileId: string) => {
      try {
        await deleteDocument(fileId);
        console.log(`${t('common.deleteSuccess')}`);
      } catch (error) {
        messageApi.error(`${t('common.deleteFailed')}`);
      }
    };

    // Upload file
    const handleUpload = async (options: UploadRequestOption) => {
      const { file, onSuccess, onError, onProgress, filename = 'file' } = options;
      
      // Create AbortController to cancel upload
      const abortController = new AbortController();
      const fileUid = (file as any).uid;
      abortControllersRef.current.set(fileUid, abortController);
      
      // Get file extension
    const fileExtension = (file as File).name.split('.').pop()?.toLowerCase();
    const mediaExtensions = ['mp3', 'mp4', 'mov', 'wav'];
    
    // If it's a media file, check size and duration
    if (fileExtension && mediaExtensions.includes(fileExtension)) {
      const fileSizeInMB = (file as File).size / (50 * 1024);
      
      // Check file size (50MB limit)
      if (fileSizeInMB > 50) {
        messageApi.error(`${t('knowledgeBase.sizeLimitError')}：${fileSizeInMB.toFixed(2)}MB`);
        onError?.(new Error(`${t('knowledgeBase.fileSizeExceeds')}`));
        abortControllersRef.current.delete(fileUid);
        return;
      }
      
      try {
        // Check media duration (150 seconds limit)
        const duration = await checkMediaDuration(file as File);
        if (duration > 150) {
          messageApi.error(`${t('knowledgeBase.fileDurationLimitError')}：${Math.round(duration)}秒`);
          onError?.(new Error(`${t('knowledgeBase.fileDurationExceeds')}`));
          abortControllersRef.current.delete(fileUid);
          return;
        }
      } catch (error) {
        messageApi.error(`${t('knowledgeBase.unableReadFile')}`);
        onError?.(error as Error);
        abortControllersRef.current.delete(fileUid);
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

      try {
        const res = await uploadFile(formData, {
          kb_id: kbId,
          parent_id: parentId,
          signal: abortController.signal,
          onUploadProgress: (event) => {
            if (!event.total) return;
            const percent = Math.round((event.loaded / event.total) * 100);
            onProgress?.({ percent }, file);
          },
        });
        
        // Upload successful, remove AbortController
        abortControllersRef.current.delete(fileUid);
        onSuccess?.(res, new XMLHttpRequest());
        
        if (res?.id) {
          // Upload successful
          // fileIds.push(res.id)
        }
      } catch (error: any) {
        // Remove AbortController
        abortControllersRef.current.delete(fileUid);
        
        // If user actively cancelled, don't show error message
        if (error.name === 'AbortError' || error.code === 'ERR_CANCELED') {
          console.log('Upload cancelled:', (file as File).name);
          return;
        }
        
        onError?.(error as Error);
      }
    };
    return (
      <>
      {contextHolder}
      <RbModal
        title={`${t('knowledgeBase.createA')} ${t('knowledgeBase.mediaDataSet')}`}
        open={visible}
        onCancel={handleClose}
        okText={t('common.create')}
        onOk={handleSave}
        confirmLoading={loading}
        maskClosable={false}
        okButtonProps={{
          disabled: loading || !hasFiles
        }}
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
              fileSize={50} 
              multiple={true} 
              maxCount={99} 
              fileType={['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'mp3', 'mp4', 'mov', 'wav']} 
              customRequest={handleUpload}
              onChange={(fileList) => {
                // Update file status in real-time
                setHasFiles(fileList.length > 0);
              }}
              onRemove={async (file) => {
                // If file is uploading, cancel upload
                const fileUid = file.uid;
                const abortController = abortControllersRef.current.get(fileUid);
                if (abortController) {
                  abortController.abort();
                  abortControllersRef.current.delete(fileUid);
                }
                
                // If file is already uploaded successfully, delete file on server
                if (file.response?.id) {
                  await handleDeleteFile(file.response.id);
                }
                
                return true; // Allow file removal
              }}
            />
          </Form.Item>
        </Form>
      </RbModal>
    </>);
  }
);

export default CreateImageDataset;
