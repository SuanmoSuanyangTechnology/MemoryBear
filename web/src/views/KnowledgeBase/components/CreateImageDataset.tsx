import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';
import type { UploadFile } from 'antd';
import type { CreateImageModalRef, CreateImageMoealRefProps,UploadFileResponse } from '../types';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import RbModal from '@/components/RbModal';
import UploadFiles from '@/components/Upload/UploadFiles';
import { uploadFile } from '../service';

interface ImageDatasetFormData {
  name: string;
  images: UploadFile[];
}

const CreateImageDataset = forwardRef<CreateImageModalRef, CreateImageMoealRefProps>(
  ({ refreshTable }, ref) => {
    const { t } = useTranslation();
    const [visible, setVisible] = useState(false);
    const [form] = Form.useForm<ImageDatasetFormData>();
    const [loading, setLoading] = useState(false);
    const [kbId, setKbId] = useState<string>('');
    const [parentId, setParentId] = useState<string>('');
    const uploadRef = useRef<{ fileList: UploadFile[]; clearFiles: () => void }>(null);

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

        // 上传所有图片
        const uploadPromises = fileList.map(async (file) => {
          if (file.originFileObj) {
            const formData = new FormData();
            formData.append('file', file.originFileObj);
            
            return uploadFile(formData, {
              kb_id: kbId,
              parent_id: parentId,
            });
          }
          return null;
        });

        await Promise.all(uploadPromises);

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

    useImperativeHandle(ref, () => ({
      handleOpen,
    }));
    // 上传文件
    const handleUpload = (options: UploadRequestOption) => {
      const { file, onSuccess, onError, onProgress, filename = 'file' } = options;
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
          }
        })
        .catch((error) => {
          onError?.(error as Error);
        });
    };
    return (
      <RbModal
        title={`${t('knowledgeBase.createA')} ${t('knowledgeBase.imageDataSet')}`}
        open={visible}
        onCancel={handleClose}
        okText={t('common.create')}
        onOk={handleSave}
        confirmLoading={loading}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label={t('knowledgeBase.datasetName')}
            rules={[{ required: true, message: t('knowledgeBase.pleaseEnterDatasetName') }]}
          >
            <Input placeholder={t('knowledgeBase.pleaseEnterDatasetName')} />
          </Form.Item>

          <Form.Item label={t('knowledgeBase.uploadImages')}>
            <UploadFiles 
              isCanDrag={true} 
              fileSize={50} 
              multiple={true} 
              maxCount={99} 
              fileType={['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']} 
              customRequest={handleUpload} 
            />
          </Form.Item>
        </Form>
      </RbModal>
    );
  }
);

export default CreateImageDataset;
