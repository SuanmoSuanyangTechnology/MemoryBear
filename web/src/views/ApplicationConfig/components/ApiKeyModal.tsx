/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:25 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:27:25 
 */
/**
 * API Key Creation Modal
 * Allows creating new API keys for application access
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { Application } from '@/views/ApplicationManagement/types'
import type { ApiKeyModalRef } from '../types'
import { createApiKey  } from '@/api/apiKey';
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

/**
 * Component props
 */
interface ApiKeyModalProps {
  /** Callback to refresh API key list */
  refresh: () => void;
  /** Application data */
  application?: Application | null;
}

/**
 * Modal for creating new API keys
 */
const ApiKeyModal = forwardRef<ApiKeyModalRef, ApiKeyModalProps>(({
  refresh,
  application
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false)

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /** Open modal */
  const handleOpen = () => {
    setVisible(true);
      form.resetFields();
  };
  /** Create new API key */
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

  /** Expose methods to parent component */
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
        {/* Key name */}
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
        {/* Description */}
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