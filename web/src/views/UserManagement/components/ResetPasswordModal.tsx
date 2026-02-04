/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:51:29 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:51:29 
 */
/**
 * Reset Password Modal
 * Modal for resetting user password with auto-generate option
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Button, Row, Col } from 'antd';
import { useTranslation } from 'react-i18next';
import copy from 'copy-to-clipboard'

import { randomString } from '@/utils/common'
import { useUser } from '@/store/user';
import type { ResetPasswordModalRef, User } from '../types'
import RbModal from '@/components/RbModal'
import { changePassword } from '@/api/user'

const ResetPasswordModal = forwardRef<ResetPasswordModalRef, { source?: 'resetPassword' | 'changePassword' }>(({ source = 'resetPassword' }, ref) => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const { logout } = useUser();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{ new_password?: string }>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<User>()

  const values = Form.useWatch([], form);

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /** Open modal with user data */
  const handleOpen = (user: User) => {
    form.resetFields();
    setEditVo(user)
    setVisible(true);
  };
  /** Save new password */
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true)
        changePassword({ user_id: editVo?.id as string, new_password: values.new_password as string })
          .then((res) => {
            handleClose()
            const password = typeof res === 'string' ? res : values.new_password as string
            if (source === 'changePassword') {
              logout()
            } else {
              modal.confirm({
                title: <>
                  {t('user.resetPasswordSuccess')}
                  <br />
                  【{password}】
                </>,
                cancelText: t('common.cancel'),
                okText: t('common.copy'),
                okType: 'danger',
                onOk: () => {
                  copy(password)
                  message.success(t('common.copySuccess'))
                }
              })
            }
          })
          .finally(() => {
            setLoading(false)
          })
      })
      .catch((err) => {
        console.log('err', err)
      });
  }
  /** Auto-generate random password (12 chars with letters, numbers, special chars) */
  const handleAutoGenerate = () => {
    form.setFieldValue('new_password', randomString());
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('user.resetPassword')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Row gutter={16}>
          <Col span={16}>
            <Form.Item
              name="new_password"
              rules={[
                { min: 6, message: t('user.passwordRule') }
              ]}
              className="rb:mb-0! rb:w-[calc(100%-)]"
            >
              <Input placeholder={t('user.newPasswordPlaceholder')} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Button onClick={handleAutoGenerate}>{t('user.autoGenerate')}</Button>
          </Col>
        </Row>
      </Form>
    </RbModal>
  );
});

export default ResetPasswordModal;