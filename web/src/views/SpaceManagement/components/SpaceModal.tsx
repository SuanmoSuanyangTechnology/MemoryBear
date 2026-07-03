/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:49:09 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-03 21:10:45
 */
/**
 * Space Modal Component
 * Two-step modal for creating workspace with basic info and model configuration
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Steps, Button, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import type { SpaceModalData, SpaceModalRef, Space, StorageType } from '../types'
import RbModal from '@/components/RbModal'
import { createWorkspace, getDefaultWorkspaceModel, getCustomWorkspaceModels } from '@/api/workspaces'
import RadioGroupCard from '@/components/RadioGroupCard'
import UploadImages from '@/components/Upload/UploadImages'
import { getFileLink } from '@/api/fileStorage'
import ragIcon from '@/assets/images/space/rag.png'
import neo4jIcon from '@/assets/images/space/neo4j.png'
import { stringRegExp } from '@/utils/validator';
import type { ModelListItem } from '@/views/ModelManagement/types'

const FormItem = Form.Item;

/**
 * Component props
 */
interface SpaceModalProps {
  refresh: () => void;
}
/** Storage types */
const types: StorageType[] = [
  'neo4j',
  'rag',
]
/** Type icons mapping */
const typeIcons: Record<StorageType, string> = {
  rag: ragIcon,
  neo4j: neo4jIcon
}

/** Custom config model selectors */
const customModelFields: { name: 'llm' | 'embedding' | 'rerank' | 'vision' | 'audio' | 'video'; label: string; required?: boolean }[] = [
  { name: 'llm', label: 'llmModel', required: true },
  { name: 'embedding', label: 'embeddingModel', required: true },
  { name: 'rerank', label: 'rerankModel', required: true },
  { name: 'vision', label: 'visionModel' },
  { name: 'audio', label: 'audioModel' },
  { name: 'video', label: 'videoModel' },
]

const SpaceModal = forwardRef<SpaceModalRef, SpaceModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<SpaceModalData>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<Space | null>(null)
  const [currentStep, setCurrentStep] = useState(0)
  const [defaultModels, setDefaultModels] = useState<Record<string, ModelListItem>>({})

  const values = Form.useWatch([], form);

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditVo(null)
    setCurrentStep(0)
  };
  /** Go to previous step */
  const handlePrevStep = () => {
    setCurrentStep(prev => prev - 1)
  }
  const handleGetDefaultModels = () => {
    getDefaultWorkspaceModel().then(res => {
      setDefaultModels((res || {}) as Record<string, ModelListItem>)
    })
  }
  const [customModels, setCustomModels] = useState<Record<string, ModelListItem[]>>({})
  const handleGetCustomModels = () => {
    getCustomWorkspaceModels().then(res => {
      setCustomModels((res || {}) as Record<string, ModelListItem[]>)
    })
  }
  /** Open modal with optional data */
  const handleOpen = (space?: Space) => {
    if (space) {
      setEditVo(space || null)
      form.setFieldsValue({
        name: space.name,
        icon: space.icon
      })
    } else {
      form.resetFields();
    }
    handleGetDefaultModels()
    handleGetCustomModels()
    setVisible(true);
  };
  /** Save or proceed to next step */
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        if (currentStep === 0) {
          setCurrentStep(1)
        } else {
          const { icon, is_default_config, ...rest } = values
          let formData: SpaceModalData = {
            ...rest,
            is_default_config: is_default_config === '1',
          }
          if (is_default_config === '1') {
            customModelFields.forEach(field => {
              formData[field.name] = undefined
            })
          }
          if (icon?.response?.data.file_id) {
            getFileLink(icon?.response?.data.file_id).then(res => {
              const logoRes = res as { url: string }
              formData.icon = logoRes.url
              formData.iconType = 'remote'
              handleUpdate(formData)
            }).catch(() => {
              handleUpdate(formData)
            })
          } else {
            handleUpdate(formData)
          }
        }
      })
      .catch((err) => {
        console.log('err', err)
      });
  }
  /** Update workspace */
  const handleUpdate = (formData: SpaceModalData) => {
    setLoading(true)
    createWorkspace(formData)
      .then(() => {
        setLoading(false)
        refresh()
        handleClose()
        message.success(t('common.createSuccess'))
      })
      .catch(() => {
        setLoading(false)
      });
  }
  const handleChange = (value: string | null | undefined) => {
    const resetFields = {}
    if (value === '0') {
      customModelFields.forEach(field => {
        (resetFields as Record<string, any>)[field.name] = defaultModels[field.name]?.id
      })
    }
    form.setFieldsValue({
      ...resetFields,
    })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t(`space.${editVo?.id ? 'editSpace' : 'createSpace'}`)}
      open={visible}
      onCancel={handleClose}
      onOk={handleSave}
      footer={[
        <Button key="close" onClick={currentStep === 0 ? handleClose : handlePrevStep}>{t(currentStep === 0 ? 'common.cancel' : 'common.prevStep')}</Button>,
        <Button key="submit" type="primary" onClick={handleSave}>{t(currentStep === 0 ? 'common.nextStep' : 'common.save')}</Button>,
      ]}
      confirmLoading={loading}
    >
      <Steps
        size="small"
        current={currentStep}
        items={['basic', 'models'].map(key => ({ title: t(`space.${key}`) } ))}
        className="rb:mb-6!"
      />
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          storage_type: types[0],
          is_default_config: '1',
        }}
      >
        <Form.Item
          name="icon"
          label={t('space.spaceIcon')}
          valuePropName="fileList"
          hidden={currentStep === 1}
          rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('space.spaceIcon') }) }]}
          extra={t('common.logoTip')?.split('\n').map((vo, index) => <div key={index}>{vo}</div>)}
        >
          <UploadImages fileSize={2} />
        </Form.Item>
        <FormItem
          name="name"
          label={t('space.spaceName')}
          hidden={currentStep === 1}
          rules={[
            { required: true, message: t('common.inputPlaceholder', { title: t('space.spaceName') }) },
            { max: 50 },
            { pattern: stringRegExp, message: t('common.nameInvalid') },
          ]}
        >
          <Input placeholder={t('common.inputPlaceholder', { title: t('space.spaceName') })} />
        </FormItem>
        <FormItem
          name="storage_type"
          label={t('space.storageType')}
          hidden={currentStep === 1}
          rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('space.storageType') }) }]}
        >
          <RadioGroupCard
            options={types.map((type) => ({
              value: type,
              label: t(`space.${type}`),
              labelDesc: t(`space.${type}Desc`),
              icon: typeIcons[type],
              recommend: type === 'neo4j',
            }))}
            block={true}
          />
        </FormItem>


        {currentStep === 1 && <>
          <Form.Item name="is_default_config" className="rb:mb-6!">
            <RadioGroupCard
              allowClear={false}
              options={[
                {
                  value: '1',
                  label: t('space.defaultConfigPackage'),
                  labelDesc: t('space.defaultConfigPackageDesc'),
                  recommend: true,
                },
                {
                  value: '0',
                  label: t('space.customConfig'),
                  labelDesc: t('space.customConfigDesc'),
                },
              ]}
              onChange={handleChange}
            />
          </Form.Item>

          {values?.is_default_config === '0' ? (
            customModelFields.map(field => (
              <Form.Item
                key={field.name}
                label={t(`space.${field.label}`)}
                name={field.name}
                rules={[{ required: field.required, message: t('common.selectPlaceholder', { title: t(`space.${field.label}`) }) }]}
              >
                <Select
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  fieldNames={{ label: 'name', value: 'id' }}
                  placeholder={t('common.pleaseSelect')}
                  options={customModels[field.name]}
                />
              </Form.Item>
            ))
          ) : (
            <div className="rb:rounded-lg rb:bg-[#F6F6F6] rb:px-4">
              {customModelFields.map(field => (
                <div
                  key={field.name}
                  className="rb:flex rb:items-center rb:justify-between rb:py-3.5 rb:border-b rb:border-[#EBEBEB] rb:last:border-b-0"
                >
                  <span className="rb:text-[#5B6167]">{t(`space.${field.label}`)}</span>
                  <span className="rb:font-medium rb:text-[#212332]">{defaultModels[field.name]?.name || '-'}</span>
                </div>
              ))}
            </div>
          )}
        </>}
      </Form>
    </RbModal>
  );
});

export default SpaceModal;