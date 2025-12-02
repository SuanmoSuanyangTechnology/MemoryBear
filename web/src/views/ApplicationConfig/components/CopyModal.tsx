import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import type { CopyModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { copyApplication } from '@/api/application'
import type { Application } from '@/views/ApplicationManagement/types'

const FormItem = Form.Item;

interface CopyModalProps {
  data: Application
}

const CopyModal = forwardRef<CopyModalRef, CopyModalProps>(({
  data
}, ref) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
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
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    setVisible(false);
    setLoading(true)
    const values = form.getFieldsValue()
    copyApplication(data.id, values.new_name)
      .then((res) => {
        const resData = res as Application
        navigate(`/application/config/${resData.id}`)
      })
      .finally(() => {
        setLoading(false)
      })
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <>
      <RbModal
        title={t('application.copyApplication')}
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
          {/* 应用名 */}
          <FormItem
            name="new_name"
            label={t('application.applicationName')}
            rules={[
              { required: true, message: t('common.pleaseEnter') },
            ]}
          >
            <Input placeholder={t('common.enter')} />
          </FormItem>
        </Form>
      </RbModal>
    </>
  );
});

export default CopyModal;