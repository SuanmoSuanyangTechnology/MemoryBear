import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import type { CustomModelForm, ModelPlazaItem, CustomModelModalRef, CustomModelModalProps } from '../types';
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect'
import UploadImages from '@/components/Upload/UploadImages'
import { updateCustomModel, addCustomModel, modelTypeUrl, modelProviderUrl } from '@/api/models'
import { getFileLink } from '@/api/fileStorage'

const CustomModelModal = forwardRef<CustomModelModalRef, CustomModelModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [model, setModel] = useState<ModelPlazaItem>({} as ModelPlazaItem);
  const [isEdit, setIsEdit] = useState(false);
  const [form] = Form.useForm<CustomModelForm>();
  const [loading, setLoading] = useState(false)
  const formValues = Form.useWatch([], form)

  const handleClose = () => {
    setModel({} as ModelPlazaItem);
    form.resetFields();
    setLoading(false)
    setVisible(false);
  };

  const handleOpen = (model?: ModelPlazaItem) => {
    if (model) {
      setIsEdit(true);
      setModel(model);
      form.setFieldsValue({
        ...model,
        logo: model.logo ? { url: model.logo, uid: model.logo, status: 'done', name: 'logo' } : undefined
      });
    } else {
      setIsEdit(false);
      form.resetFields();
    }
    setVisible(true);
  };
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        values.is_official = false;
        const logo = values.logo as any;
        if (typeof logo === 'object' && logo?.response?.data.file_id) {
          getFileLink(logo?.response?.data.file_id).then(res => {
            const logoRes = res as { url: string }
            values.logo = logoRes.url
            addCustomModel(values).then(() => {
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
        } else {
          values.logo = typeof logo === 'string' ? logo : logo.url
          updateCustomModel(model.id, values).then(() => {
            if (refresh) {
              refresh();
            }
            handleClose()
            message.success(isEdit ? t('common.updateSuccess') : t('common.createSuccess'))
          })
            .catch(() => {
              setLoading(false)
            });
        }
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  console.log('formValues', formValues)

  return (
    <RbModal
      title={isEdit ? `${model.name} - ${t('modelNew.modelConfiguration')}` : t('modelNew.createCustomModel')}
      open={visible}
      onCancel={handleClose}
      okText={t(`common.${isEdit ? 'save' : 'create'}`)}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        {!isEdit && <Form.Item
          name="logo"
          label={t('modelNew.logo')}
          valuePropName="fileList"
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <UploadImages />
        </Form.Item>}
        <Form.Item
          name="name"
          label={t('modelNew.model_name')}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('modelNew.model_name') }) }]}
        >
          <Input placeholder={t('common.pleaseEnter')} />
        </Form.Item>
        
        <Form.Item
          name="type"
          label={t('modelNew.type')}
          rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('modelNew.type') }) }]}
        >
          <CustomSelect
            url={modelTypeUrl}
            hasAll={false}
            format={(items) => items.map((item) => ({ label: t(`modelNew.${item}`), value: String(item) }))}
          />
        </Form.Item>

        <Form.Item
          name="provider"
          label={t('modelNew.provider')}
          rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('modelNew.provider') }) }]}
        >
          <CustomSelect
            url={modelProviderUrl}
            hasAll={false}
            format={(items) => items.map((item) => ({ label: t(`modelNew.${item}`), value: String(item) }))}
          />
        </Form.Item>

        <Form.Item
          name="description"
          label={t('modelNew.description')}
        >
          <Input.TextArea placeholder={t('common.pleaseEnter')} />
        </Form.Item>
        <Form.Item
          name="tags"
          label={t('modelNew.tags')}
        >
          <Select mode="tags" placeholder={t('common.pleaseEnter')} />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default CustomModelModal;