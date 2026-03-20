/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:49:28 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-11 15:08:24
 */
/**
 * Custom Model Modal
 * Modal for creating and editing custom models in the model square
 * Supports logo upload, type/provider selection, and tagging
 */

import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Checkbox, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import type { CustomModelForm, ModelListItem, CustomModelModalRef, CustomModelModalProps } from '../types';
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect'
import UploadImages from '@/components/Upload/UploadImages'
import { updateCustomModel, addCustomModel, modelTypeUrl, modelProviderUrl } from '@/api/models'
import { getFileLink } from '@/api/fileStorage'
import { validateSquareImage, stringRegExp } from '@/utils/validator'

/**
 * Custom model modal component
 */
const CustomModelModal = forwardRef<CustomModelModalRef, CustomModelModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [model, setModel] = useState<ModelListItem>({} as ModelListItem);
  const [isEdit, setIsEdit] = useState(false);
  const [form] = Form.useForm<CustomModelForm>();
  const [loading, setLoading] = useState(false)
  const [abortController, setAbortController] = useState<AbortController | null>(null)
  const modelType = Form.useWatch(['type'], form);
  const isOmni = Form.useWatch(['is_omni'], form);

  useEffect(() => {
    if (isOmni) {
      form.setFieldsValue({ is_vision: true })
    }
  }, [isOmni])

  /** Close modal and reset state */
  const handleClose = () => {
    abortController?.abort()
    setAbortController(null)
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
      const { capability, is_omni, ...rest} = model
      form.setFieldsValue({
        ...rest,
        logo: model.logo && model.logo.startsWith('http') ? { url: model.logo, uid: model.logo, status: 'done', name: 'logo' } : undefined,
        is_omni,
        is_vision: capability?.includes('vision') || false,
      });
    } else {
      setIsEdit(false);
      form.resetFields();
    }
    setVisible(true);
  };
  /** Update or create custom model */
  const handleUpdate = (data: CustomModelForm) => {
    setLoading(true)
    const controller = new AbortController()
    setAbortController(controller)
    const { type, provider, ...rest} = data
    const res = isEdit ? updateCustomModel(model.id, rest, controller.signal) : addCustomModel(data, controller.signal)

    res.then(() => {
      refresh?.(isEdit)
      handleClose()
      message.success(isEdit ? t('common.updateSuccess') : t('common.createSuccess'))
    })
      .catch(() => {
        setLoading(false)
      });
  }
  /** Validate and save custom model */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        const { logo, type, is_vision, is_omni, ...rest } = values;
        const formData: CustomModelForm = {
          ...rest,
          type,
        }
        if (!['embedding', 'rerank'].includes(type as string)) {
          formData.capability = is_omni ? ["vision", "audio"] : is_vision ? ['vision'] : []
          formData.is_omni = is_omni
        }

        if (typeof logo === 'object' && logo?.response?.data.file_id) {
          getFileLink(logo?.response?.data.file_id)
            .then(res => {
              const logoRes = res as { url: string }
              formData.logo = logoRes.url
              handleUpdate(formData)
            })
            .catch(() => {
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

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));
  return (
    <RbModal
      title={isEdit ? `${model.name} - ${t('modelNew.modelConfiguration')}` : t('modelNew.createCustomModel')}
      open={visible}
      onCancel={handleClose}
      footer={[
        <Button key="cancel" onClick={handleClose}>{t('common.cancel')}</Button>,
        <Button key="confirm" type="primary" loading={loading} onClick={handleSave}>{t(`common.${isEdit ? 'save' : 'create'}`)}</Button>,
      ]}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          name="logo"
          label={t('modelNew.logo')}
          valuePropName="fileList"
          rules={[
            { required: true, message: t('common.pleaseSelect') },
            { validator: validateSquareImage(t('common.imageSquareRequired')) }
          ]}
          extra={t('common.logoTip')?.split('\n').map((vo, index) => <div key={index}>{vo}</div>)}
        >
          <UploadImages fileSize={2} />
        </Form.Item>
        <Form.Item
          name="name"
          label={t('modelNew.name')}
          rules={[
            { required: true, message: t('common.inputPlaceholder', { title: t('modelNew.name') }) },
            { max: 50 },
            { pattern: stringRegExp, message: t('common.nameInvalid') },
          ]}
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
            disabled={isEdit}
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
            disabled={isEdit}
            format={(items) => items.map((item) => ({ label: t(`modelNew.${item}`), value: String(item) }))}
          />
        </Form.Item>

        <Form.Item
          name="description"
          label={t('modelNew.description')}
          rules={[{ max: 500 }]}
        >
          <Input.TextArea placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        <Form.Item
          name={["api_keys", 0, "api_key"]}
          label={t('modelNew.api_key')}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('modelNew.api_key') }) }]}
        >
          <Input.Password placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        <Form.Item
          name={["api_keys", 0, "api_base"]}
          label={t('modelNew.api_base')}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('modelNew.api_base') }) }]}
        >
          <Input placeholder="https://api.example.com/v1" />
        </Form.Item>

        {!['embedding', 'rerank'].includes(modelType as string) &&
          <>
            <Form.Item name="is_omni" valuePropName="checked" className="rb:mb-2!">
              <Checkbox>{t('modelNew.is_omni')}</Checkbox>
            </Form.Item>
            <Form.Item name="is_vision" valuePropName="checked" className="rb:mb-0!">
              <Checkbox disabled={isOmni}>{t('modelNew.is_vision')}</Checkbox>
            </Form.Item>
          </>
        }
      </Form>
    </RbModal>
  );
});

export default CustomModelModal;