/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:49:20 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-01 10:20:56
 */
/**
 * Sub-Model Modal
 * Modal for selecting models and API keys to add to group model
 * Uses cascader for hierarchical selection
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Space, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import type { SubModelModalForm, SubModelModalRef, SubModelModalProps } from './types';
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect'
import { modelProviderUrl, getModelNewList } from '@/api/models'
import type { ProviderModelItem, ModelListItem } from '../../types'
import Tag from '@/components/Tag';
import { formatModelType } from '../../utils'

/**
 * Sub-model modal component
 */
const SubModelModal = forwardRef<SubModelModalRef, SubModelModalProps>(({
  refresh,
  type,
  groupedByProvider,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<SubModelModalForm>();
  const [modelList, setModelList] = useState<ModelListItem[]>([])

  /** Close modal and reset state */
  const handleClose = () => {
    form.resetFields();
    setVisible(false);
    setModelList([])
  };

  /** Open modal */
  const handleOpen = () => {
    form.resetFields()
    setVisible(true);
  };
  /** Save selected models and API keys */
  const handleSave = () => {
    form
      .validateFields()
      .then(({ provider, model_names }) => {
        refresh?.(model_names.map(name => ({
          model_name: name,
          provider
        })))
        handleClose()
      })
  }

  /** Handle provider change and load models */
  const handleChangeProvider = (provider: string) => {
    form.setFieldValue('model_names', undefined)
    if (provider) {
      getModelNewList({
        provider: provider,
        is_composite: false,
        is_active: true,
        type
      })
        .then(res => {
          const response = res as ProviderModelItem[]
          const list = response[0]?.models || []
          setModelList(list)

          if (groupedByProvider?.[provider]?.length) {
            form.setFieldsValue({
              model_names: groupedByProvider?.[provider].map(item => item.model_name)
            })
          }
        })
    } else {
      setModelList([])
    }
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  console.log('modelList', modelList)

  return (
    <RbModal
      title={t('modelNew.implementConfig')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          name="provider"
          label={t('modelNew.provider')}
          rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('modelNew.provider') }) }]}
        >
          <CustomSelect
            placeholder={t('common.pleaseSelect')}
            url={modelProviderUrl}
            hasAll={false}
            format={(items) => items.map((item) => ({ label: String(item.provider).charAt(0).toUpperCase() + String(item.provider).slice(1), value: String(item.provider) }))}
            onChange={(value) => handleChangeProvider(value)}
          />
        </Form.Item>
        <Form.Item 
          name="model_names"
          label={t('modelNew.modelList')}
          rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('modelNew.model_names') }) }]}
        >
          <Select
            placeholder={t('common.pleaseSelect')}
            options={modelList.map(vo => ({
              label: (
                <Space>
                  {vo.name}
                  <Tag>{formatModelType(vo.type)}</Tag>
                  {vo.capability?.filter(item => item !== 'video').map(vo => <Tag key={vo}>{t(`modelNew.${vo}`)}</Tag>)}
                </Space>
              ),
              value: vo.name
            }))}
            mode="multiple"
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default SubModelModal;