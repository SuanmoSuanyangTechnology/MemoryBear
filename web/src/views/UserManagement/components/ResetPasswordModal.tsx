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

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = (user: User) => {
    form.resetFields();
    setEditVo(user)
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
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
  // 自动生成长度为12的随机密码，包含字母、数字、特殊字符
  const handleAutoGenerate = () => {
    form.setFieldValue('new_password', randomString());
  }

  // 暴露给父组件的方法
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