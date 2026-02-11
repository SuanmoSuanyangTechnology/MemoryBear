/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:08:58 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:08:58 
 */
/**
 * SettingModal Component
 * 
 * A modal dialog for configuring application settings including language and timezone.
 * Uses forwardRef to expose open/close methods to parent components.
 * 
 * @component
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal'
import { useI18n } from '@/store/locale'
import { timezones } from '@/utils/timezones'

const FormItem = Form.Item;

/** Interface for SettingModal ref methods exposed to parent components */
export interface SettingModalRef {
  /** Open the settings modal */
  handleOpen: () => void;
  /** Close the settings modal */
  handleClose: () => void;
}

/** Settings modal component for language and timezone configuration */
const SettingModal = forwardRef<SettingModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const { changeLanguage, language, timeZone, changeTimeZone } = useI18n()
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();

  /** Close modal and reset form to initial state */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  /** Open modal and populate form with current settings */
  const handleOpen = () => {
    form.setFieldsValue({ language, timeZone })
    setVisible(true);
  };
  
  /** Validate and save settings, update language and timezone if changed */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        const { language: newLanguage, timeZone: newTimeZone } = values
        if (newLanguage !== language) {
          changeLanguage(newLanguage);
        }
        changeTimeZone(newTimeZone)
        handleClose()
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  /** Expose handleOpen and handleClose methods to parent component via ref */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  
  return (
    <RbModal
      title={t('header.setting')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
      >
        {/* Language selection dropdown */}
        <FormItem
          name="language"
          label={t('header.language')}
        >
          <Select
            options={['zh', 'en'].map(key => ({ label: t(`header.${key}`), value: key }))}
          />
        </FormItem>
        {/* Timezone selection dropdown */}
        <FormItem
          name="timeZone"
          label={t('header.timeZone')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Select
            options={timezones.map(key => ({ label: t(`timezones.${key}`), value: key }))}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default SettingModal;