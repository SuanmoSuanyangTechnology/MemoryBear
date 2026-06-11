/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:28:11 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-11 11:01:51
 */
/**
 * Release Modal
 * Allows publishing a new version of the application
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ReleaseModalData, ReleaseModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { publishRelease, getCurrentRelease } from '@/api/application'
import { getFileLink } from '@/api/fileStorage'
import type { Application } from '@/views/ApplicationManagement/types'
import UploadImages from '@/components/Upload/UploadImages'
import { stringRegExp } from '@/utils/validator';

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
  const [form] = Form.useForm<ReleaseModalData>();
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
    getCurrentRelease(data.id)
      .then(res => {
        const response = res as { name: string; icon: any }
        form.setFieldsValue({
          name: response.name,
          icon: response.icon ? { url: response.icon, uid: response.icon, status: 'done', name: 'icon' } : undefined,
        })
      })
  };
  /** Publish new release */
  const handleSave = () => {
    form.validateFields().then(() => {
      setLoading(true)
      const { icon, ...rest } = form.getFieldsValue()
      const formData: ReleaseModalData = {
        ...rest
      }
      if (icon?.response?.data.file_id) {
        getFileLink(icon?.response?.data.file_id).then(res => {
          const logoRes = res as { url: string }
          formData.icon = logoRes.url
          handleUpdate(formData)
        }).catch(() => {
          handleUpdate(formData)
        })
      } else {
        handleUpdate(formData)
      }
    })
  }
  const handleUpdate = (formData: ReleaseModalData) => {
    publishRelease(data.id, formData)
      .then(() => {
        handleClose()
        refreshTable()
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

          <FormItem
            name="name"
            label={t('application.customTitle')}
            tooltip={t('application.customTitleTip')}
            extra={t('application.customTitleTip')}
            rules={[
              { max: 50 },
              { pattern: stringRegExp, message: t('common.nameInvalid') },
            ]}
          >
            <Input placeholder={t('application.customTitlePlaceholder')} />
          </FormItem>
          <Form.Item
            name="icon"
            label={t('application.customIcon')}
            valuePropName="fileList"
            tooltip={t('application.customIconTip')}
            extra={t('application.customIconDesc')?.split('\n').map((vo, index) => <div key={index}>{vo}</div>)}
          >
            <UploadImages fileSize={2} />
          </Form.Item>
        </Form>
      </RbModal>
    </>
  );
});

export default ReleaseModal;