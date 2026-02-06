/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:51:40 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:51:40 
 */
/**
 * Create User Modal
 * Modal for creating new user with email, username, and password
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { CreateModalData, CreateModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { addUser } from '@/api/user'

const FormItem = Form.Item;

/**
 * Component props
 */
interface CreateModalProps {
  refreshTable: () => void;
}

const CreateModal = forwardRef<CreateModalRef, CreateModalProps>(({
  refreshTable
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<CreateModalData>();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch([], form);

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
  /** Save user */
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true)
        addUser(values)
          .then(() => {
            setLoading(false)
            refreshTable()
            handleClose()
            message.success(t('common.createSuccess'))
          })
          .catch(() => {
            setLoading(false)
          });
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
      title={t('user.createUser')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.create')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="email"
          label={t('user.usernameOrAccount')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        
        <FormItem
          name="username"
          label={t('user.displayName')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter',)} />
        </FormItem>

        <FormItem
          name="password"
          label={t('user.initialPassword')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default CreateModal;