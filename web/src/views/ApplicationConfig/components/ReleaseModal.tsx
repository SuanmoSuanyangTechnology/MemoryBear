import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ReleaseModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { publishRelease } from '@/api/application'
import type { Application } from '@/views/ApplicationManagement/types'

const FormItem = Form.Item;

interface ReleaseModalProps {
  refreshTable: () => void;
  data: Application
}

const ReleaseModal = forwardRef<ReleaseModalRef, ReleaseModalProps>(({
  refreshTable,
  data
}, ref) => {
  const { t } = useTranslation();
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
    form.validateFields().then(() => {
      setLoading(true)
      const values = form.getFieldsValue()
      publishRelease(data.id, values)
        .then(() => {
          handleClose()
          refreshTable()
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
    <>
      <RbModal
        title={t('application.releaseNewVersion')}
        open={visible}
        onCancel={handleClose}
        okText={t('application.release')}
        onOk={handleSave}
        confirmLoading={loading}
      >
        <Form
          form={form}
          layout="vertical"
        >
          {/* 版本名 */}
          <FormItem
            name="version_name"
            label={t('application.versionName')}
            rules={[
              { required: true, message: t('common.pleaseEnter') },
            ]}
            extra={t('application.versionNameTip')}
          >
            <Input placeholder={t('common.enter')} />
          </FormItem>
          {/* 版本描述 */}
          <FormItem
            name="release_notes"
            label={t('application.versionDescription')}
            rules={[
              { required: true, message: t('common.pleaseEnter') },
            ]}
            extra={t('application.versionDescriptionTip')}
          >
            <Input.TextArea placeholder={t('common.enter')} />
          </FormItem>
        </Form>
      </RbModal>
    </>
  );
});

export default ReleaseModal;