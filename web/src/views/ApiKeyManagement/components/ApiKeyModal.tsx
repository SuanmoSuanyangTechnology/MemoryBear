/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:52:47 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-04 10:00:01
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Switch, App, DatePicker } from 'antd';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs'

import type { ApiKey, ApiKeyModalRef } from '../types';
import RbModal from '@/components/RbModal'
import { createApiKey, updateApiKey  } from '@/api/apiKey';

const FormItem = Form.Item;

/**
 * Props for ApiKeyModal component
 */
interface CreateModalProps {
  /** Callback to refresh parent list after save */
  refresh: () => void;
}

/**
 * Modal component for creating or editing API keys
 * Handles API key configuration including permissions and expiration
 */
const ApiKeyModal = forwardRef<ApiKeyModalRef, CreateModalProps>(({
  refresh,
}, ref) => {
  // Hooks
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<ApiKey>();
  
  // State
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [editVo, setEditVo] = useState<ApiKey | null>(null);

  /**
   * Close modal and reset form state
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setEditVo(null);
  };

  /**
   * Open modal for creating or editing
   * @param apiKey - Optional API key data for edit mode
   */
  const handleOpen = (apiKey?: ApiKey) => {
    if (apiKey?.id) {
      const { scopes = [], expires_at } = apiKey
      // Edit mode - populate form with existing data
      form.setFieldsValue({
        name: apiKey.name,
        description: apiKey.description,
        memory: scopes.includes('memory'),
        rag: scopes.includes('rag'),
        expires_at: expires_at ? dayjs(expires_at) : undefined
      });
      setEditVo(apiKey);
    }
    setVisible(true);
  };

  /**
   * Validate and submit form data
   * Creates new API key or updates existing one
   */
  const handleSave = async () => {
    form.validateFields()
      .then((values) => {
        const { memory, rag, expires_at, ...rest } = values
        let scopes = []

        if (memory) {
          scopes.push('memory')
        }
        if (rag) {
          scopes.push('rag')
        }
        // Prepare new/updated API key data
        const apiKeyData = {
          ...rest,
          scopes,
          expires_at: expires_at ? dayjs(expires_at.valueOf()).endOf('day').valueOf() : null,
          type: 'service'
        };
        setLoading(true)
        const req = editVo?.id ? updateApiKey(editVo.id, apiKeyData as ApiKey) : createApiKey(apiKeyData as ApiKey)
        
        req.then(() => {
            refresh();
            handleClose();
            message.success(t(editVo ? 'common.updateSuccess' : 'common.createSuccess'));
          })
          .finally(() => setLoading(false))
      })
  }

  /**
   * Expose methods to parent component via ref
   */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={editVo ? t('apiKey.updateApiKey') : t('apiKey.createApiKey')}
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
        <div className="rb:text-[#5B6167] rb:font-medium rb:leading-5 rb:mb-4">{t('apiKey.baseInfo')}</div>
        <FormItem
          name="name"
          label={t('apiKey.name')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        
        <FormItem
          name="description"
          label={t('apiKey.description')}
        >
          <Input.TextArea placeholder={t('common.pleaseEnter')} rows={3} />
        </FormItem>

        <div className="rb:text-[#5B6167] rb:font-medium rb:leading-5 rb:mb-4">{t('apiKey.permissionInfo')}</div>

        <FormItem
          name="memory"
          label={t('apiKey.memoryEngine')}
          layout="horizontal"
          valuePropName="checked"
        >
          <Switch />
        </FormItem>

        <FormItem
          name="rag"
          label={t('apiKey.knowledgeBase')}
          layout="horizontal"
          valuePropName="checked"
        >
          <Switch />
        </FormItem>

        <div className="rb:text-[#5B6167] rb:font-medium rb:leading-5 rb:mb-4">{t('apiKey.advancedSettings')}</div>

        <FormItem
          name="expires_at"
          label={t('apiKey.expires_at')}
        >
          <DatePicker
            className="rb:w-full"
            disabledDate={(current) => current && current < dayjs().subtract(1, 'day').endOf('day')}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ApiKeyModal;