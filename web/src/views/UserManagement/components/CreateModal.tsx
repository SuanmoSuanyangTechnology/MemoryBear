import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { CreateModalData, CreateModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { addUser } from '@/api/user'

const FormItem = Form.Item;

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

  // 暴露给父组件的方法
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