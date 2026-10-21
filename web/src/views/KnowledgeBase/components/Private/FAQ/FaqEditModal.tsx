import { forwardRef, useImperativeHandle, useState } from 'react';
import { App, Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import { createFaq, updateFaq } from '@/api/kbWiki';
import RbModal from '@/components/RbModal';
import type { FaqItem, FaqForm } from './types'

export interface FaqEditModalRef {
  handleOpen: (item?: FaqItem) => void;
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
  const [editing, setEditing] = useState<FaqItem | undefined>(undefined);
  const [form] = Form.useForm<FaqForm>();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);

  const handleClose = () => {
    setVisible(false);
    setEditing(undefined)
    form.resetFields()
  };

  const handleSave = () => {
    form.validateFields()
      .then((values) => {
        setLoading(true)
        const request = editing
          ? updateFaq(knowledgeBaseId, editing.id, {
            ...values,
            expected_row_version: editing.row_version
          })
          : createFaq(knowledgeBaseId, values)
        request
          .then(() => {
            if (editing) {
              message.success(t('common.updateSuccess'))
            } else {
              message.success(t('common.createSuccess'))
            }
            refresh?.()
            handleClose()
          })
          .finally(() => {
            setLoading(false)
          })
      })
  };

  useImperativeHandle(ref, () => ({
    handleOpen: (item) => {
      setVisible(true);
      if (item) {
        form.setFieldsValue(item)
        setEditing(item);
      }
    },
  }));

  return (
    <RbModal
      title={t(editing ? 'knowledgeBase.faq.editTitle' : 'knowledgeBase.faq.add')}
      open={visible}
      confirmLoading={loading}
      onOk={handleSave}
      onCancel={handleClose}
    >
      <Form
        form={form}
        key={editing?.id ?? 'new'}
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
