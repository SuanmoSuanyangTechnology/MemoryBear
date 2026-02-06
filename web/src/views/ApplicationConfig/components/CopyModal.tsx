/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:56 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:27:56 
 */
/**
 * Copy Application Modal
 * Allows users to duplicate an existing application with a new name
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import type { CopyModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { copyApplication } from '@/api/application'
import type { Application } from '@/views/ApplicationManagement/types'

const FormItem = Form.Item;

/**
 * Component props
 */
interface CopyModalProps {
  /** Application data to copy */
  data: Application
}

/**
 * Modal for copying applications
 */
const CopyModal = forwardRef<CopyModalRef, CopyModalProps>(({
  data
}, ref) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
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
  /** Copy application with new name */
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

  /** Expose methods to parent component */
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
          {/* Application name */}
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