/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:49:09 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:49:09 
 */
/**
 * Space Modal Component
 * Two-step modal for creating workspace with basic info and model configuration
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Steps, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import type { SpaceModalData, SpaceModalRef, Space, StorageType } from '../types'
import RbModal from '@/components/RbModal'
import { createWorkspace } from '@/api/workspaces'
import RadioGroupCard from '@/components/RadioGroupCard'
import { getModelListUrl } from '@/api/models'
import CustomSelect from '@/components/CustomSelect'
import UploadImages from '@/components/Upload/UploadImages'
import { getFileLink } from '@/api/fileStorage'
import ragIcon from '@/assets/images/space/rag.png'
import neo4jIcon from '@/assets/images/space/neo4j.png'

const FormItem = Form.Item;

/**
 * Component props
 */
interface SpaceModalProps {
  refresh: () => void;
}
/** Storage types */
const types: StorageType[] = [
  'rag',
  'neo4j',
]
/** Type icons mapping */
const typeIcons: Record<StorageType, string> = {
  rag: ragIcon,
  neo4j: neo4jIcon
}

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
          const { icon, ...rest } = values
          let formData: SpaceModalData = {
            ...rest
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
      >
        <Form.Item
          name="icon"
          label={t('space.spaceIcon')}
          valuePropName="fileList"
          hidden={currentStep === 1}
          rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('space.spaceIcon') }) }]}
        >
          <UploadImages />
        </Form.Item>
        <FormItem
          name="name"
          label={t('space.spaceName')}
          hidden={currentStep === 1}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('space.spaceName') }) }]}
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
              icon: typeIcons[type]
            }))}
            block={true}
          />
        </FormItem>


        {currentStep === 1 && <>
          <Form.Item
            label={t('space.llmModel')}
            name="llm"
            rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('space.llmModel') }) }]}
          >
            <CustomSelect
              url={getModelListUrl}
              params={{ type: 'llm,chat', pagesize: 100, is_active: true }}
              valueKey="id"
              labelKey="name"
              hasAll={false}
              placeholder={t('common.selectPlaceholder', { title: t('space.llmModel') })}
              className="rb:w-full!"
            />
          </Form.Item>
          <Form.Item
            label={t('space.embeddingModel')}
            name="embedding"
            rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('space.embeddingModel') }) }]}
          >
            <CustomSelect
              url={getModelListUrl}
              params={{ type: 'embedding', pagesize: 100, is_active: true }}
              valueKey="id"
              labelKey="name"
              hasAll={false}
              placeholder={t('common.selectPlaceholder', { title: t('space.embeddingModel') })}
              className="rb:w-full!"
            />
          </Form.Item>
          <Form.Item
            label={t('space.rerankModel')}
            name="rerank"
            rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('space.rerankModel') }) }]}
          >
            <CustomSelect
              url={getModelListUrl}
              params={{ type: 'rerank', pagesize: 100, is_active: true }}
              valueKey="id"
              labelKey="name"
              hasAll={false}
              placeholder={t('common.selectPlaceholder', { title: t('space.rerankModel') })}
              className="rb:w-full!"
            />
          </Form.Item>
        </>}
      </Form>
    </RbModal>
  );
});

export default SpaceModal;