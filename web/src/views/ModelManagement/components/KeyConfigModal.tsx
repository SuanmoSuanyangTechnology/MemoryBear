/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:49:40 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-11 15:12:17
 */
/**
 * Key Configuration Modal
 * Modal for configuring API keys for model providers
 * Allows setting API key and base URL
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import type { KeyConfigModalForm, ProviderModelItem, KeyConfigModalRef, KeyConfigModalProps } from '../types';
import RbModal from '@/components/RbModal'
import { updateProviderApiKeys } from '@/api/models'

/**
 * Key configuration modal component
 */
const KeyConfigModal = forwardRef<KeyConfigModalRef, KeyConfigModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [model, setModel] = useState<ProviderModelItem>({} as ProviderModelItem);
  const [form] = Form.useForm<KeyConfigModalForm>();
  const [loading, setLoading] = useState(false)
  const [abortController, setAbortController] = useState<AbortController | null>(null)

  /** Close modal and reset state */
  const handleClose = () => {
    abortController?.abort()
    setAbortController(null)
    setModel({} as ProviderModelItem);
    form.resetFields();
    setLoading(false)
    setVisible(false);
  };

  /** Open modal with provider model data */
  const handleOpen = (vo: ProviderModelItem) => {
    setVisible(true);
    setModel(vo);
  };
  /** Save API key configuration */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        setLoading(true)

        const controller = new AbortController()
        setAbortController(controller)

        updateProviderApiKeys({
          ...values,
          provider: model.provider
        }, controller.signal).then((res) => {
            if (refresh) {
              refresh();
            }
            handleClose()
            message.success(res as string)
          })
          .catch(() => {
            setLoading(false)
          });
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
      title={`${model.provider} - ${t('modelNew.keyConfig')}`}
      open={visible}
      onCancel={handleClose}
      footer={[
        <Button key="cancel" onClick={handleClose}>{t('common.cancel')}</Button>,
        <Button key="confirm" type="primary" loading={loading} onClick={handleSave}>{t(`common.save`)}</Button>,
      ]}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          name="api_key"
          label={t('modelNew.api_key')}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('modelNew.api_key') }) }]}
        >
          <Input.Password placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        <Form.Item
          name="api_base"
          label={t('modelNew.api_base')}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('modelNew.api_base') }) }]}
        >
          <Input placeholder="https://api.example.com/v1" />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default KeyConfigModal;