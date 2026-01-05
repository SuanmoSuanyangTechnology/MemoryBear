import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ModelFormData, Model, ConfigModalRef, ConfigModalProps } from '../types';
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect'
import { updateModel, addModel, modelTypeUrl, modelProviderUrl } from '@/api/models'

const ConfigModal = forwardRef<ConfigModalRef, ConfigModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [model, setModel] = useState<Model>({} as Model);
  const [isEdit, setIsEdit] = useState(false);
  const [form] = Form.useForm<ModelFormData>();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch<ModelFormData>([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setModel({} as Model);
    form.resetFields();
    setLoading(false)
    setVisible(false);
  };

  const handleOpen = (model?: Model) => {
    if (model) {
      setIsEdit(true);
      setModel(model);
      // 设置表单值
      const apiKeyInfo = model.api_keys[0]
      form.setFieldsValue({
        provider: apiKeyInfo.provider,
        model_name: apiKeyInfo.model_name,
        api_key: apiKeyInfo.api_key,
        api_base: apiKeyInfo.api_base
    });
    } else {
      setIsEdit(false);
      form.resetFields();
    }
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        const data = {
          name: values.name,
          type: values.type,
          api_keys: {
            provider: values.provider,
            model_name: values.model_name,
            api_key: values.api_key,
            api_base: values.api_base
          },
        }
        setLoading(true)
        const res = isEdit
          ? updateModel(model.api_keys[0].id, {
              provider: values.provider,
              model_name: values.model_name,
              api_key: values.api_key,
              api_base: values.api_base
            } as ModelFormData)
          : addModel(data as ModelFormData)

        res.then(() => {
            if (refresh) {
              refresh();
            }
            handleClose()
            message.success(isEdit ? t('common.updateSuccess') : t('common.createSuccess'))
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
      title={isEdit ? `${model.name} - ${t('model.modelConfiguration')}` : t('model.createModel')}
      open={visible}
      onCancel={handleClose}
      okText={t(`common.${isEdit ? 'save' : 'create'}`)}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{}}
      >
        {!isEdit && (
          <>
            <Form.Item
              name="name"
              label={t('model.displayName')}
              rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('model.displayName') }) }]}
            >
              <Input placeholder={t('common.pleaseEnter')} />
            </Form.Item>
            <Form.Item
              name="type"
              label={t('model.type')}
              rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('model.type') }) }]}
            >
              <CustomSelect
                url={modelTypeUrl}
                hasAll={false}
                format={(items) => items.map((item) => ({ label: t(`model.${item}`), value: item }))}
              />
            </Form.Item>
          </>
        )}


        <Form.Item
          name="provider"
          label={t('model.provider')}
          rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('model.provider') }) }]}
        >
          <CustomSelect
            url={modelProviderUrl}
            hasAll={false}
            format={(items) => items.map((item) => ({ label: t(`model.${item}`), value: item }))}
          />
        </Form.Item>
        <Form.Item
          name="model_name"
          label={t('model.modelName')}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('model.modelName') }) }]}
        >
          <Input placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        <Form.Item
          name="api_key"
          label={t('model.apiKey')}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('model.apiKey') }) }]}
        >
          <Input.Password placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        <Form.Item
          name="api_base"
          label={t('model.apiEndpoint')}
        >
          <Input placeholder="https://api.example.com/v1" />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default ConfigModal;