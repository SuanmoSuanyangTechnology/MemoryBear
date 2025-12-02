import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal'
import { useI18n } from '@/store/locale'
import { timezones } from '@/utils/timezones'

const FormItem = Form.Item;
export interface SettingModalRef {
  handleOpen: () => void;
  handleClose: () => void;
}

const SettingModal = forwardRef<SettingModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const { changeLanguage, language, timeZone, changeTimeZone } = useI18n()
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();

  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  const handleOpen = () => {
    form.setFieldsValue({ language, timeZone })
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
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

  // 暴露给父组件的方法
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
        {/* 中英文切换 */}
        <FormItem
          name="language"
          label={t('header.language')}
        >
          <Select
            options={['zh', 'en'].map(key => ({ label: t(`header.${key}`), value: key }))}
          />
        </FormItem>
        {/* 时区切换 */}
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