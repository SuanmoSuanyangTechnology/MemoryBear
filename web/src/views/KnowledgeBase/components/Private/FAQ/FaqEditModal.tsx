import { forwardRef, useImperativeHandle, useState } from 'react';
import { App, Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import { createFaq } from '@/api/kbWiki';
import RbModal from '@/components/RbModal';
import type { FaqForm } from './types'

export interface FaqEditModalRef {
  handleOpen: () => void;
}

interface FaqEditModalProps {
  knowledgeBaseId: string;
  refresh: () => void;
}

const FaqEditModal = forwardRef<FaqEditModalRef, FaqEditModalProps>(({
  knowledgeBaseId, refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<FaqForm>();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);

  const handleClose = () => {
    setVisible(false);
    form.resetFields()
  };

  const handleSave = () => {
    form.validateFields()
      .then((values) => {
        setLoading(true)
        createFaq(knowledgeBaseId, values)
          .then(() => {
            message.success(t('common.createSuccess'))
            refresh?.()
            handleClose()
          })
          .finally(() => {
            setLoading(false)
          })
      })
  };

  useImperativeHandle(ref, () => ({
    handleOpen: () => {
      setVisible(true);
    },
  }));

  return (
    <RbModal
      title={t('knowledgeBase.faq.add')}
      open={visible}
      confirmLoading={loading}
      onOk={handleSave}
      onCancel={handleClose}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          name="standard_question"
          label={t('knowledgeBase.faq.question')}
          rules={[{ required: true, whitespace: true, message: t('knowledgeBase.faq.questionRequired') }]}
        >
          <Input.TextArea rows={2} maxLength={200} showCount />
        </Form.Item>
        <Form.Item
          name="standard_answer"
          label={t('knowledgeBase.faq.answer')}
          rules={[{ required: true, whitespace: true, message: t('knowledgeBase.faq.answerRequired') }]}
        >
          <Input.TextArea rows={7} />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default FaqEditModal;
