/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:28:11 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:28:11 
 */
/**
 * Release Modal
 * Allows publishing a new version of the application
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ReleaseModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { publishRelease } from '@/api/application'
import type { Application } from '@/views/ApplicationManagement/types'

const FormItem = Form.Item;

/**
 * Component props
 */
interface ReleaseModalProps {
  /** Callback to refresh release list */
  refreshTable: () => void;
  /** Application data */
  data: Application
}

/**
 * Modal for publishing new application versions
 */
const ReleaseModal = forwardRef<ReleaseModalRef, ReleaseModalProps>(({
  refreshTable,
  data
}, ref) => {
  const { t } = useTranslation();
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
  };
  /** Publish new release */
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

  /** Expose methods to parent component */
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
          {/* Version name */}
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
          {/* Version description */}
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