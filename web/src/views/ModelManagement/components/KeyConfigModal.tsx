import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';
import type { KeyConfigModalForm, ProviderModelItem, KeyConfigModalRef, KeyConfigModalProps } from '../types';
import RbModal from '@/components/RbModal'
import { updateProviderApiKeys } from '@/api/models'

const KeyConfigModal = forwardRef<KeyConfigModalRef, KeyConfigModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [model, setModel] = useState<ProviderModelItem>({} as ProviderModelItem);
  const [form] = Form.useForm<KeyConfigModalForm>();
  const [loading, setLoading] = useState(false)

  const handleClose = () => {
    setModel({} as ProviderModelItem);
    form.resetFields();
    setLoading(false)
    setVisible(false);
  };

  const handleOpen = (vo: ProviderModelItem) => {
    setVisible(true);
    setModel(vo);
  };
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        setLoading(true)

        updateProviderApiKeys({
          ...values,
          provider: model.provider
        }).then((res) => {
            if (refresh) {
              refresh();
            }
            handleClose()
            message.success(res as string)
          })
          .catch(() => {
            setLoading(false)
          });
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={`${model.provider} - ${t('modelNew.keyConfig')}`}
      open={visible}
      onCancel={handleClose}
      okText={t(`common.save`)}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          name="api_key"
          label={t('modelNew.api_key')}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('modelNew.api_key') }) }]}
        >
          <Input.Password placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        <Form.Item
          name="api_base"
          label={t('modelNew.api_base')}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('modelNew.api_base') }) }]}
        >
          <Input placeholder="https://api.example.com/v1" />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default KeyConfigModal;