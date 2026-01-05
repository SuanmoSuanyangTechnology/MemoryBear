import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Slider } from 'antd';
import { useTranslation } from 'react-i18next';

import type {  ApiKeyConfigModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { updateApiKey } from '@/api/apiKey';
import type { ApiKey } from '@/views/ApiKeyManagement/types'

interface ApiKeyConfigModalProps {
  refresh: () => void;
}
const ApiKeyConfigModal = forwardRef<ApiKeyConfigModalRef, ApiKeyConfigModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ApiKey>();
  const [loading, setLoading] = useState(false)
  const values = Form.useWatch<ApiKey>([], form)
  const [editVo, setEditVo] = useState<ApiKey | null>(null)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    form.resetFields();
    setLoading(false)
    setEditVo(null)
    setVisible(false);
  };

  const handleOpen = (apiKey: ApiKey) => {
    setVisible(true);
    setEditVo(apiKey)
    form.setFieldsValue({
      daily_request_limit: apiKey.daily_request_limit,
      rate_limit: apiKey.rate_limit
    });
  };
  // 封装保存方法，添加提交逻辑
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

  // 暴露给父组件的方法
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
        {/* QPS 限制（每秒请求数） */}
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
        {/* 日调用量限制 */}
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