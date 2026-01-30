import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { PromptSaveModalRef, PromptReleaseData } from '../types'
import RbModal from '@/components/RbModal'
import { savePrompt } from '@/api/prompt'

const FormItem = Form.Item;

interface PromptSaveModalProps {
  refresh: () => void;
}

const PromptSaveModal = forwardRef<PromptSaveModalRef, PromptSaveModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{ title?: string; }>();
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<PromptReleaseData | null>(null)
  const title = Form.useWatch(['title'], form)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setData(null)
  };

  const handleOpen = (vo: PromptReleaseData) => {
    setData(vo)
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    if (!title || title.trim() === '') {
      message.warning(t('common.inputPlaceholder', { title: t('prompt.saveTitle') }))
      return
    }
    setLoading(true)
    savePrompt({
      ...data,
      title
    } as PromptReleaseData)
      .then(() => {
        setLoading(false)
        refresh()
        handleClose()
        message.success(t('common.saveSuccess'))
      })
      .catch(() => {
        setLoading(false)
      });
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('prompt.saveTitle')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="title"
          noStyle
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default PromptSaveModal;