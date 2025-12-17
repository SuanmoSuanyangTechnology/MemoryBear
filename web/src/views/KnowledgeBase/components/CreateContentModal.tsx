import { forwardRef, useImperativeHandle, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';
import RbModal from '@/components/RbModal';
import { createDocumentAndUpload } from '@/api/knowledgeBase'
import type { CreateSetModalRef,CreateSetMoealRefProps } from '../types'
interface ContentFormData {
  title: string;
  content: string;
}

const CreateContentModal = forwardRef<CreateSetModalRef, CreateSetMoealRefProps>(
  ({ refreshTable }, ref) => {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const [visible, setVisible] = useState(false);
    const [form] = Form.useForm<ContentFormData>();
    const [loading, setLoading] = useState(false);
    const [kbId, setKbId] = useState<string>('');
    const [parentId, setParentId] = useState<string>('');

    const handleClose = () => {
      form.resetFields();
      setLoading(false);
      setVisible(false);
      setKbId('');
      setParentId('');
    };

    const handleOpen = (kb_id: string, parent_id: string) => {
      setKbId(kb_id);
      setParentId(parent_id);
      form.resetFields();
      setVisible(true);
    };

    const handleSave = async () => {
      try {
        const values = await form.validateFields();
        setLoading(true);

        // TODO: 这里需要调用相应的API来保存内容
        const params = {
          // ...values,
          kb_id: kbId,
          parent_id: parentId,
        };


        const response = await createDocumentAndUpload(values, params)
        if(response){
          handleChunking(response.kb_id,parentId,response.id)
        }
        handleClose();
      } catch (err) {
        console.error('创建内容失败:', err);
      } finally {
        setLoading(false);
      }
    };
    const handleChunking = (kb_id: string, parent_id: string, file_id: string) => {
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

    return (
      <RbModal
        title={t('knowledgeBase.createContent')}
        open={visible}
        onCancel={handleClose}
        okText={t('common.create')}
        onOk={handleSave}
        confirmLoading={loading}
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="title"
            label={t('knowledgeBase.title')}
            rules={[{ required: true, message: t('knowledgeBase.pleaseEnterTitle') }]}
          >
            <Input placeholder={t('knowledgeBase.pleaseEnterTitle')} />
          </Form.Item>

          <Form.Item
            name="content"
            label={t('knowledgeBase.content')}
            rules={[{ required: true, message: t('knowledgeBase.pleaseEnterContent') }]}
          >
            <Input.TextArea
              placeholder={t('knowledgeBase.pleaseEnterContent')}
              rows={8}
              showCount
              maxLength={5000}
            />
          </Form.Item>
        </Form>
      </RbModal>
    );
  }
);

export default CreateContentModal;