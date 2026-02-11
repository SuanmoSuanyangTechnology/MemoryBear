/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:33:21 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:33:21 
 */
/**
 * End User Profile Modal
 * Modal for editing end user profile information
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, DatePicker } from 'antd';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs';

import type { EndUser, EndUserProfileModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { updatedEndUserProfile, } from '@/api/memory'

const FormItem = Form.Item;

/**
 * Component props
 */
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

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /** Open modal with user data */
  const handleOpen = (user: EndUser) => {
    form.setFieldsValue({
      ...user,
      end_user_id: user.id,
      hire_date: user.hire_date ? dayjs(user.hire_date) : undefined
    });
    setVisible(true);
  };
  /** Save profile changes */
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

  /** Expose methods to parent component */
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