/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:48:03 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-03 21:17:09
 */
/**
 * Space Configuration Page
 * Configures default models for workspace (LLM, embedding, rerank)
 */

import { type FC, useEffect, useState } from 'react';
import { Form, App, Button, Skeleton, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import type { SpaceConfigData } from './types'
import { getWorkspaceModels, updateWorkspaceModels, getDefaultWorkspaceModel, getCustomWorkspaceModels } from '@/api/workspaces'
import RadioGroupCard from '@/components/RadioGroupCard'
import type { Capability, ModelListItem } from '@/views/ModelManagement/types'

/** Required base model selectors */
const baseModelFields: { name: string; label: string; required?: boolean }[] = [
  { name: 'llm', label: 'llmModel', required: true },
  { name: 'embedding', label: 'embeddingModel', required: true },
  { name: 'rerank', label: 'rerankModel', required: true },
]

/** Optional multimodal model selectors */
const multimodalModelFields: { name: string; label: string; capability: Capability }[] = [
  { name: 'vision', label: 'visionModel', capability: 'vision' },
  { name: 'audio', label: 'audioModel', capability: 'audio' },
  { name: 'video', label: 'videoModel', capability: 'video' },
]

const SpaceConfig: FC = () => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [pageLoading, setPageLoading] = useState(false)
  const [form] = Form.useForm<SpaceConfigData>();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch([], form);

  const [defaultModels, setDefaultModels] = useState<Record<string, ModelListItem>>({})
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

  useEffect(() => {
    setPageLoading(true)
    getWorkspaceModels().then((res) => {
      const { is_default_config, llm, embedding, rerank, vision, audio, video } = res as SpaceConfigData
      form.setFieldsValue({
        is_default_config: is_default_config ? '1' : '0',
        llm,
        embedding,
        rerank,
        vision,
        audio,
        video,
      })
    })
    .finally(() => {
      setPageLoading(false)
    })

    handleGetDefaultModels()
    handleGetCustomModels()
  }, [])
  /** Save configuration */
  const handleSave = () => {
    form
      .validateFields()
      .then(({ is_default_config, ...rest }: SpaceConfigData) => {
        if (is_default_config === '1') {
          [...baseModelFields, ...multimodalModelFields].map(field => {
            (rest as Record<string, any>)[field.name] = undefined
          })
        }
        setLoading(true)
        updateWorkspaceModels({ ...rest, is_default_config: is_default_config === '1' })
          .then(() => {
            setLoading(false)
            message.success(t('common.updateSuccess'))
          })
          .catch(() => {
            setLoading(false)
          });
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  return (
    <div className="rb:bg-white rb:rounded-lg rb:p-6 rb:pb-8">
      <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[#212332] rb:leading-5">{t('menu.spaceConfig')}</div>
      <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:mt-2 rb:mb-6">{t('space.configAlert')}</div>
      {pageLoading
        ? <Skeleton active />
        : <Form
          form={form}
          layout="vertical"
          initialValues={{ is_default_config: '1' }}
        >
          <Form.Item name="is_default_config" className="rb:mb-6! rb:max-w-137.5">
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
            />
          </Form.Item>

          {values?.is_default_config === '0' ? (
            <>
              <div className="rb:flex rb:items-baseline rb:gap-2 rb:pb-3 rb:mb-6 rb:border-b rb:border-[#EBEBEB] rb:max-w-137.5">
                <span className="rb:font-medium rb:text-[#212332]">{t('space.baseModel')}</span>
                <span className="rb:text-[12px] rb:text-[#5B6167]">{t('space.baseModelDesc')}</span>
              </div>
              {baseModelFields.map(field => (
                <Form.Item
                  key={field.name}
                  label={t(`space.${field.label}`)}
                  className="rb:font-medium rb:text-[#212332] rb:mb-6!"
                  name={field.name}
                  rules={[{ required: true, message: t('common.pleaseSelect') }]}
                >
                  <Select
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    fieldNames={{ label: 'name', value: 'id' }}
                    placeholder={t('common.pleaseSelect')}
                    options={customModels[field.name]}
                    className="rb:w-137.5!"
                  />
                </Form.Item>
              ))}

              <div className="rb:flex rb:items-baseline rb:gap-2 rb:pb-3 rb:mb-6 rb:border-b rb:border-[#EBEBEB] rb:max-w-137.5">
                <span className="rb:font-medium rb:text-[#212332]">{t('space.multimodalModel')}</span>
                <span className="rb:text-[12px] rb:text-[#5B6167]">{t('space.multimodalModelDesc')}</span>
              </div>
              {multimodalModelFields.map(field => (
                <Form.Item
                  key={field.name}
                  label={<>{t(`space.${field.label}`)}<span className="rb:text-[#5B6167] rb:font-regular">{t('space.optional')}</span></>}
                  className="rb:font-medium rb:text-[#212332] rb:mb-6!"
                  name={field.name}
                >
                  <Select
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    fieldNames={{ label: 'name', value: 'id' }}
                    placeholder={t('common.pleaseSelect')}
                    options={customModels[field.name]}
                    className="rb:w-137.5!"
                  />
                </Form.Item>
              ))}
            </>
          ) : (
            <div className="rb:rounded-lg rb:bg-[#F6F6F6] rb:px-4 rb:mb-6 rb:max-w-137.5">
              {[...baseModelFields, ...multimodalModelFields].map(field => (
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

          <Button type="primary" className="rb:mt-1" onClick={handleSave} loading={loading}>
            {t('common.save')}
          </Button>
        </Form>
      }
    </div>
  );
};

export default SpaceConfig;