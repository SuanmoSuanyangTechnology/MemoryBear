import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { Form, Select, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import type { AuthConfigModalRef, HttpRequestConfigForm } from './types'
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

interface AuthConfigModalProps {
  refresh: (values: HttpRequestConfigForm['auth']) => void;
}

const AuthConfigModal = forwardRef<AuthConfigModalRef, AuthConfigModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<HttpRequestConfigForm['auth']>();

  const values = Form.useWatch<HttpRequestConfigForm['auth']>([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  const handleOpen = (data?: HttpRequestConfigForm['auth']) => {
    if (data) {
      const initialValues = {
        auth: !data.auth_type || data.auth_type === 'none' ? 'none' : 'api_key',
        auth_type: !data.auth_type || data.auth_type === 'none' ? undefined : data.auth_type,
        header: data.header,
        api_key: data.api_key
      }
      form.setFieldValue('auth', initialValues.auth)
      if (initialValues.auth !== 'none') {
        setTimeout(() => {
          form.setFieldsValue(initialValues)
        }, 1)
      }
    }
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        const { auth, auth_type, ...rest } = values ?? {}
        refresh({
          auth_type: auth === 'none' ? 'none' : auth_type,
          ...rest
        })
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

  useEffect(() => {
    if (values?.auth === 'api_key') {
      form.setFieldValue('auth_type', 'basic')
    } else {
      form.setFieldsValue({
        auth_type: undefined,
        header: undefined,
        api_key: undefined
      })
    }
  }, [values?.auth])


  return (
    <RbModal
      title={t('workflow.config.http-request.auth')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          auth: 'none'
        }}
        size="middle"
      >
        <FormItem
          name="auth"
          label={t('workflow.config.http-request.authType')}
          rules={[
            { required: true, message: t('common.pleaseSelect') }
          ]}
        >
          <Select
            size="middle"
            options={[
              { value: 'none', label: t('workflow.config.http-request.none') },
              { value: 'api_key', label: t('workflow.config.http-request.apiKey') },
            ]}
          />
        </FormItem>
        {values?.auth !== 'none' && <>
          <FormItem
            name="auth_type"
            label={t('workflow.config.http-request.authType')}
            rules={[
              { required: true, message: t('common.pleaseSelect') }
            ]}
          >
            <Select
              size="middle"
              options={[
                { value: 'basic', label: t('workflow.config.http-request.basic') },
                { value: 'bearer', label: t('workflow.config.http-request.bearer') },
                { value: 'custom', label: t('workflow.config.http-request.custom') },
              ]}
            />
          </FormItem>
          {values?.auth_type === 'custom' &&
            <FormItem 
              name="header"
              label={t('workflow.config.http-request.header')}
              rules={[
                { required: true, message: t('common.pleaseEnter') }
              ]}
            >
              <Input size="middle" placeholder={t('common.pleaseEnter')} />
            </FormItem>
          }
          <FormItem 
            name="api_key"
            label={t('workflow.config.http-request.api_key')}
            rules={[
              { required: true, message: t('common.pleaseEnter') }
            ]}
          >
            <Input size="middle" placeholder={t('common.pleaseEnter')} />
          </FormItem>
        </>}
      </Form>
    </RbModal>
  );
});

export default AuthConfigModal;