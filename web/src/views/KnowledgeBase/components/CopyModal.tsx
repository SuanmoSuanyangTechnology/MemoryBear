import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal'
import { copyKnowledgeBase } from '@/api/knowledgeBase.ts'
import type { V1KnowledgeListItem } from '../typesV1'

const FormItem = Form.Item;

export interface CopyModalRef {
  /** Open copy modal */
  handleOpen: (data: V1KnowledgeListItem) => void;
}
interface CopyModalProps {
  refresh: () => void;
}

const CopyModal = forwardRef<CopyModalRef, CopyModalProps>(({ refresh }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{ name?: string; }>();
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<V1KnowledgeListItem | null>(null)

  useEffect(() => {
    form.resetFields();
  }, [visible, form])

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setData(null)
  };

  /** Open modal */
  const handleOpen = (data: V1KnowledgeListItem) => {
    setVisible(true);
    setData(data)
  };
  /** Copy knowledgeBase with new name */
  const handleSave = () => {
    if (!data) return

    form.validateFields()
      .then(values => {
        setVisible(false);
        setLoading(true)
        copyKnowledgeBase(data.id, values)
          .then(() => {
            handleClose()
            refresh();
            message.success(t('common.copySuccess'))
          })
          .finally(() => {
            setLoading(false)
          })
      })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('knowledgeBase.copyKnowledgeBase')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.copy')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        {/* V1KnowledgeListItem name */}
        <FormItem
          name="name"
          label={t('knowledgeBase.knowledgeBaseName')}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default CopyModal;