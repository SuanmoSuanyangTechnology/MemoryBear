import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, DatePicker } from 'antd';
import { useTranslation } from 'react-i18next';

import type { EndUser, EndUserProfileModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { updatedEndUserProfile, } from '@/api/memory'
import dayjs from 'dayjs';

const FormItem = Form.Item;

interface EndUserProfileModalProps {
  refresh: () => void;
}

const EndUserProfileModal = forwardRef<EndUserProfileModalRef, EndUserProfileModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<EndUser>();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = (user: EndUser) => {
    form.setFieldsValue({
      ...user,
      end_user_id: user.id,
      hire_date: user.hire_date ? dayjs(user.hire_date) : undefined
    });
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true)
        updatedEndUserProfile({
          ...values,
          hire_date: values.hire_date?.valueOf() || null
        })
          .then(() => {
            setLoading(false)
            refresh()
            handleClose()
            message.success(t('common.saveSuccess'))
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
      title={t('common.edit')}
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
        <FormItem name="end_user_id" hidden></FormItem>
        <FormItem
          name="other_name"
          label={t('userMemory.other_name')}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        <FormItem
          name="position"
          label={t('userMemory.position')}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        <FormItem
          name="department"
          label={t('userMemory.department')}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        <FormItem
          name="contact"
          label={t('userMemory.contact')}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        <FormItem
          name="phone"
          label={t('userMemory.phone')}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        <FormItem
          name="hire_date"
          label={t('userMemory.hire_date')}
        >
          <DatePicker className="rb:w-full" allowClear />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default EndUserProfileModal;