/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:18 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:26:18 
 */
/**
 * API Extension Modal
 * Allows configuring external API endpoints for dynamic variable options
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ApiExtensionModalData, ApiExtensionModalRef } from './types'
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

/**
 * Component props
 */
interface ApiExtensionModalProps {
  /** Callback to refresh API extension list */
  refresh: () => void;
}

/**
 * Modal for adding API extensions
 */
const ApiExtensionModal = forwardRef<ApiExtensionModalRef, ApiExtensionModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ApiExtensionModalData>();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch([], form);

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /** Open modal */
  const handleOpen = () => {
      form.resetFields();
    setVisible(true);
  };
  /** Save API extension configuration */
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true)
        console.log('values', values)
        refresh()
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('application.addApiExtension')}
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
          name="name"
          label={t('application.name')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>

        <FormItem
          name="apiEndpoint"
          label={t('application.apiEndpoint')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>

        <FormItem
          name="apiKey"
          label={t('application.apiKey')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ApiExtensionModal;