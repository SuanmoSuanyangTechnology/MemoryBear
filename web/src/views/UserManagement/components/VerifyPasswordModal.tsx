/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-25 10:51:17 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-25 11:46:11
 */
/**
 * VerifyPasswordModal Component
 * 
 * A modal dialog for verifying user's current login password before performing
 * sensitive operations (e.g., changing email address).
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';
import { ExclamationCircleFilled } from '@ant-design/icons';

import type { VerifyPasswordModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { verifyPassword } from '@/api/user'
import RbAlert from '@/components/RbAlert';

/**
 * VerifyPasswordModal component props
 */
interface VerifyPasswordModalProps {
  /** Callback function executed after successful password verification */
  refresh: () => void;
}

const VerifyPasswordModal = forwardRef<VerifyPasswordModalRef, VerifyPasswordModalProps>(({ refresh }, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{ password: string }>();
  const [loading, setLoading] = useState(false)

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
  /** Verify password and execute callback on success */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        verifyPassword(values)
          .then(() => {
            refresh()
            handleClose()
          })
          .catch(() => {
            form.setFields([{
              name: 'password',
              errors: [t('user.loginPasswordVerifyFailed')]
            }])
          })
          .finally(() => {
            setLoading(false)
          })
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
      title={t('user.authVerify')}
      open={visible}
      onCancel={handleClose}
      okText={t('user.verify')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <RbAlert icon={<ExclamationCircleFilled />} className="rb:mb-4!">{ t('user.authVerifyDesc') }</RbAlert>
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          name="password"
          label={t('user.loginPassword')}
          rules={[
            { required: true, message: t('user.loginPasswordPlaceholder') },
            { min: 6, message: t('user.passwordRule') }
          ]}
        >
          <Input placeholder={t('user.loginPasswordPlaceholder')} />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default VerifyPasswordModal;