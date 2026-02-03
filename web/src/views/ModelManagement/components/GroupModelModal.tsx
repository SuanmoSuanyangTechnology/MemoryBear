/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:49:33 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:49:33 
 */
/**
 * Group Model Modal
 * Modal for creating and editing composite/group models
 * Supports multiple API key configuration and load balancing
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ModelListItem, CompositeModelForm, GroupModelModalRef, GroupModelModalProps, ModelApiKey } from '../types';
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect'
import { updateCompositeModel, modelTypeUrl, addCompositeModel } from '@/api/models'
import UploadImages from '@/components/Upload/UploadImages'
import ModelImplement from './ModelImplement'
import { getFileLink } from '@/api/fileStorage'

/**
 * Group model modal component
 */
const GroupModelModal = forwardRef<GroupModelModalRef, GroupModelModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [model, setModel] = useState<ModelListItem>({} as ModelListItem);
  const [isEdit, setIsEdit] = useState(false);
  const [form] = Form.useForm<CompositeModelForm>();
  const [loading, setLoading] = useState(false)
  const type = Form.useWatch(['type'], form)

  /** Close modal and reset state */
  const handleClose = () => {
    setModel({} as ModelListItem);
    form.resetFields();
    setLoading(false)
    setVisible(false);
  };

  /** Open modal with optional model data for editing */
  const handleOpen = (model?: ModelListItem) => {
    if (model) {
      setIsEdit(true);
      setModel(model);
      form.setFieldsValue({
        ...model,
        api_key_ids: model.api_keys,
        logo: model.logo ? { url: model.logo, uid: model.logo, status: 'done', name: 'logo' } : undefined
      })
    } else {
      setIsEdit(false);
      form.resetFields();
    }
    setVisible(true);
  };
  /** Validate and save group model */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        const { api_key_ids = [], logo, ...rest } = values

        const formData: CompositeModelForm = {
          ...rest,
          api_key_ids: api_key_ids.map(vo => (vo as ModelApiKey).id)
        }

        if (logo?.response?.data.file_id) {
          getFileLink(logo?.response?.data.file_id).then(res => {
            const logoRes = res as { url: string }
            formData.logo = logoRes.url
            handleUpdate(formData)
          }).catch(() => {
            handleUpdate(formData)
          })
        } else {
          formData.logo = typeof logo === 'string' ? logo : logo.url
          handleUpdate(formData)
        }
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  /** Update or create group model */
  const handleUpdate = (data: CompositeModelForm) => {
    setLoading(true)
    const { type, ...rest } = data
    const res = isEdit
      ? updateCompositeModel(model.id, { ...rest })
      : addCompositeModel(data)

    res.then(() => {
      refresh?.();
        handleClose()
        message.success(isEdit ? t('common.updateSuccess') : t('common.createSuccess'))
      })
      .catch(() => {
        setLoading(false)
      });
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={isEdit ? `${model.name} - ${t('modelNew.modelConfiguration')}` : t('modelNew.createGroupModel')}
      open={visible}
      onCancel={handleClose}
      okText={t(`common.${isEdit ? 'save' : 'create'}`)}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{ balance_strategy: 'none' }}
      >
        <Form.Item 
          name="logo" 
          label={t('modelNew.logo')}
          valuePropName="fileList"
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <UploadImages />
        </Form.Item>

        <Form.Item 
          name="name" 
          label={t('modelNew.name')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
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
            format={(items) => items.map((item) => ({ 
              label: t(`modelNew.${typeof item === 'object' ? item.value : item}`), 
              value: typeof item === 'object' ? item.value : item 
            }))}
            disabled={isEdit}
          />
        </Form.Item>

        <Form.Item
          name="description"
          label={t('modelNew.description')}
        >
          <Input.TextArea placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        <Form.Item
          name="load_balance_strategy"
          label={t('modelNew.load_balance_strategy')}
        >
          <Select
            options={['round_robin', 'none'].map(key => ({
              label: t(`modelNew.${key}`),
              value: key
            }))}
            placeholder={t('common.pleaseSelect')}
          />
        </Form.Item>

        <Form.Item name="api_key_ids">
          <ModelImplement type={type} />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default GroupModelModal;