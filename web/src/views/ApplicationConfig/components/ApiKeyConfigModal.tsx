/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:22 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:27:22 
 */
/**
 * API Key Configuration Modal
 * Allows configuring rate limits and daily usage limits for API keys
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Slider } from 'antd';
import { useTranslation } from 'react-i18next';

import type {  ApiKeyConfigModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { updateApiKey } from '@/api/apiKey';
import type { ApiKey } from '@/views/ApiKeyManagement/types'

/**
 * Component props
 */
interface ApiKeyConfigModalProps {
  /** Callback to refresh API key list */
  refresh: () => void;
}

/**
 * Modal for configuring API key limits
 */const ApiKeyConfigModal = forwardRef<ApiKeyConfigModalRef, ApiKeyConfigModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ApiKey>();
  const [loading, setLoading] = useState(false)
  const values = Form.useWatch<ApiKey>([], form)
  const [editVo, setEditVo] = useState<ApiKey | null>(null)

  /** Close modal and reset state */
  const handleClose = () => {
    form.resetFields();
    setLoading(false)
    setEditVo(null)
    setVisible(false);
  };

  /** Open modal with API key data */
  const handleOpen = (apiKey: ApiKey) => {
    setVisible(true);
    setEditVo(apiKey)
    form.setFieldsValue({
      daily_request_limit: apiKey.daily_request_limit,
      rate_limit: apiKey.rate_limit
    });
  };
  /** Save API key configuration */
  const handleSave = () => {
    if (!editVo?.id) return
    form.validateFields()
      .then((values) => {
        updateApiKey(editVo.id, {
          ...editVo,
          ...values
        })
        handleClose()
        setTimeout(() => {
          refresh()
        }, 50)
      })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('application.apiLimitConfig')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        className="rb:px-2.5!"
        scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
      >
        {/* QPS limit (requests per second) */}
        <>
          <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mb-2">
            {t(`application.qpsLimit`)}({t('application.qpsLimitTip')})
          </div>
          <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4 rb:mb-2">
            {t('application.qpsLimitDesc')}
          </div>
          <div className="rb:pl-2">
            <Form.Item
              name="rate_limit"
            >
              <Slider 
                style={{ margin: '0' }} 
                min={1} 
                max={100} 
                step={1}
              />
            </Form.Item>
            <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:leading-5 rb:-mt-6.5">
              1
              <span>{t('application.currentValue')}: {values?.rate_limit}{t('application.qpsLimitUnit')}</span>
            </div>
          </div>
        </>
        {/* Daily usage limit */}
        <>
          <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mt-6 rb:mb-2">
            {t(`application.dailyUsageLimit`)}
          </div>
          <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4 rb:mb-2">
            {t('application.dailyUsageLimitDesc')}
          </div>
          <div className="rb:pl-2">
            <Form.Item
              name="daily_request_limit"
            >
              <Slider 
                style={{ margin: '0' }} 
                min={100} 
                max={100000} 
                step={100}
              />
            </Form.Item>
            <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:leading-5 rb:-mt-6.5">
              100
              <span>{t('application.currentValue')}: {values?.daily_request_limit}{t('application.dailyUsageLimitUnit')}</span>
            </div>
          </div>
        </>
      </Form>
    </RbModal>
  );
});

export default ApiKeyConfigModal;