import { forwardRef, useImperativeHandle, useState } from 'react';
import { useParams } from 'react-router-dom'
import { Form, Slider } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal'
import { forgetTrigger } from '@/api/memory'
import type { ForgetRefreshModalRef } from '../pages/ForgetDetail'

interface ForgetRefreshModalProps {
  refresh: (flag: boolean) => void;
}

const ForgetRefreshModal = forwardRef<ForgetRefreshModalRef, ForgetRefreshModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { id } = useParams()
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{ max_merge_batch_size: number; min_days_since_access: number; }>();
  const [loading, setLoading] = useState(false)
  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = () => {
    form.resetFields();
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    if(!id) return
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        forgetTrigger({
          ...values,
          end_user_id: id
        })
          .then(() => {
            refresh(true)
            handleClose()
          })
          .finally(() => {
            setLoading(false)
          })
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('common.refresh')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.refresh')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <div className="rb:pl-3">
          <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mb-2">
            {t(`forgettingEngine.max_merge_batch_size`)}
          </div>

          <Form.Item
            name="max_merge_batch_size"
          >
            <Slider tooltip={{ open: false }} max={1000} min={1} step={1} style={{ margin: '0' }} />
          </Form.Item>
          <div className="rb:flex rb:text-[12px] rb:items-center rb:justify-between rb:text-[#5B6167] rb:leading-5 rb:-mt-6.5">
            <span>{t(`forgettingEngine.range`)}: {[1, 1000]?.join('-')}</span>
            {t('forgettingEngine.CurrentValue')}: {values?.min_days_since_access || 0}
          </div>
        </div>
        <div className="rb:pl-3 rb:mt-4">
          <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mb-2">
            {t(`forgettingEngine.min_days_since_access`)}
          </div>

          <Form.Item
            name="min_days_since_access"
          >
            <Slider tooltip={{ open: false }} max={365} min={1} step={1} style={{ margin: '0' }} />
          </Form.Item>
          <div className="rb:flex rb:text-[12px] rb:items-center rb:justify-between rb:text-[#5B6167] rb:leading-5 rb:-mt-6.5">
            <span>{t(`forgettingEngine.range`)}: {[1, 365]?.join('-')}</span>
            {t('forgettingEngine.CurrentValue')}: {values?.min_days_since_access || 0}
          </div>
        </div>
      </Form>
    </RbModal>
  );
});

export default ForgetRefreshModal;