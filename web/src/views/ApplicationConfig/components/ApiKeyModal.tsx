import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { Application } from '@/views/ApplicationManagement/types'
import type { ApiKeyModalRef } from '../types'
import { createApiKey  } from '@/api/apiKey';
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

interface ApiKeyModalProps {
  refresh: () => void;
  application?: Application | null;
}

const ApiKeyModal = forwardRef<ApiKeyModalRef, ApiKeyModalProps>(({
  refresh,
  application
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = () => {
    setVisible(true);
      form.resetFields();
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    if (!application) return
    form.validateFields()
      .then((values) => {
        setLoading(true)
        createApiKey({
          ...values,
          type: application.type,
          resource_id: application.id,
          scopes: ['app']
        })
        .then(() => {
          handleClose()
          refresh()
          message.success(t('common.createSuccess'))
        })
        .finally(() => {
          setLoading(false)
        })
      })
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('application.addApiKey')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.create')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
      >
        {/* Key 名称 */}
        <FormItem
          name="name"
          label={t('application.apiKeyName')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
            { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: t('application.invalidVariableName') },
          ]}
        >
          <Input placeholder={t('application.apiKeyNamePlaceholder')} />
        </FormItem>
        {/* 描述 */}
        <FormItem
          name="description"
          label={t('application.description')}
        >
          <Input.TextArea placeholder={t('application.apiKeyDescPlaceholder')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ApiKeyModal;