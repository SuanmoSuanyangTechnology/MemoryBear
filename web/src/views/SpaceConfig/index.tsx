/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:48:03 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-17 10:30:48
 */
/**
 * Space Configuration Page
 * Configures default models for workspace (LLM, embedding, rerank)
 */

import { type FC, useEffect, useState } from 'react';
import { Form, App, Button, Skeleton, Flex, Tabs, type TabsProps, Descriptions, Row, Col } from 'antd';
import { useTranslation } from 'react-i18next';
import { MemoryLifecycle } from '@redbear/memory-brick';
import clsx from 'clsx';

import type { SpaceConfigData } from './types'
import {
  getWorkspaceModels,
  updateWorkspaceModels,
  getDefaultWorkspaceModel,
  getCustomWorkspaceModels,
} from '@/api/workspaces'
import RadioGroupCard from '@/components/RadioGroupCard'
import type { Modality, Model } from '@/views/ModelManagement/types'
import { isPrivateAvailable } from '@/utils/private'
import ModelSelect from '@/components/ModelSelect'
import { request } from '@/utils/request'
import EmbeddingAlert from './components/EmbeddingAlert';

import styles from './index.module.css'

/** Required base model selectors */
const baseModelFields: { name: string; label: string; required?: boolean }[] = [
  { name: 'llm', label: 'llmModel', required: true },
  { name: 'embedding', label: 'embeddingModel', required: true },
  { name: 'rerank', label: 'rerankModel', required: true },
]

/** Optional multimodal model selectors */
const multimodalModelFields: { name: string; label: string; modality: Modality }[] = [
  { name: 'vision', label: 'visionModel', modality: 'image' },
  { name: 'audio', label: 'audioModel', modality: 'audio' },
  { name: 'video', label: 'videoModel', modality: 'video' },
]

const SpaceConfig: FC = () => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const [pageLoading, setPageLoading] = useState(false)
  const [form] = Form.useForm<SpaceConfigData>();
  const [loading, setLoading] = useState(false)
  const [reembedJobId, setReembedJobId] = useState<string | null | undefined>(null)

  const values = Form.useWatch([], form);

  const [defaultModels, setDefaultModels] = useState<Record<string, Model>>({})
  const handleGetDefaultModels = () => {
    if (!isPrivateAvailable) {
      return
    }
    getDefaultWorkspaceModel().then(res => {
      setDefaultModels((res || {}) as Record<string, Model>)
    })
  }
  const [customModels, setCustomModels] = useState<Record<string, Model[]>>({})
  const [lastConfig, setLastConfig] = useState<SpaceConfigData>({} as SpaceConfigData)
  const handleGetCustomModels = () => {
    getCustomWorkspaceModels().then(res => {
      setCustomModels((res || {}) as Record<string, Model[]>)
    })
  }

  useEffect(() => {
    const allFields = [...baseModelFields, ...multimodalModelFields]
    
    allFields.forEach(field => {
      const currentValue = lastConfig[field.name as keyof SpaceConfigData]
      if (currentValue) {
        form.setFieldsValue({ [field.name]: currentValue })
      }
    })
  }, [customModels, lastConfig])

  useEffect(() => {
    setPageLoading(true)
    getWorkspaceModels().then((res) => {
      const { is_default_config, reembed_job_id } = res as SpaceConfigData
      form.setFieldValue('is_default_config', is_default_config && isPrivateAvailable ? '1' : '0')
      setLastConfig(res as SpaceConfigData)
      setReembedJobId(reembed_job_id)
    })
    .finally(() => {
      setPageLoading(false)
    })

    handleGetDefaultModels()
    handleGetCustomModels()
  }, [])

  const getFormData = ({ is_default_config, ...rest }: SpaceConfigData) => {
    const isDefaultConfig = is_default_config === '1' && isPrivateAvailable && Object.keys(defaultModels).length > 0
    if (isDefaultConfig) {
      [...baseModelFields, ...multimodalModelFields].map(field => {
        (rest as Record<string, any>)[field.name] = undefined
      })
    }
    return { ...rest, is_default_config: isDefaultConfig }
  }
  const saveConfig = (values: SpaceConfigData) => {
    setLoading(true)
    const rest = getFormData(values)
    return updateWorkspaceModels(rest)
      .then((res) => {
        const { workspace } = res as {workspace: SpaceConfigData};
        setLastConfig({ ...workspace })
        setReembedJobId(workspace.reembed_job_id)
        message.success(t('common.updateSuccess'))
      })
      .catch(() => {})
      .finally(() => {
        setLoading(false)
      })
  }
  /** Save model configuration */
  const handleSave = () => {
    form
      .validateFields()
      .then((values: SpaceConfigData) => {
        const embeddingChanged = values.embedding !== lastConfig.embedding
        if (!embeddingChanged) {
          return saveConfig(values)
        }
        const modelName = customModels.embedding?.find(model => model.id === values.embedding)?.name || values.embedding
        modal.confirm({
          width: 540,
          centered: true,
          icon: null,
          classNames: {
            content: 'rb:p-6 rb:rounded-xl rb:overflow-hidden'
          },
          title: (
            <div>
              <div className="rb:text-[18px] rb:leading-6.5 rb:font-semibold">
                {t('space.embeddingSwitchConfirmTitle')}
              </div>
              <div className="rb:mt-1 rb:text-[13px] rb:leading-5 rb:font-normal rb:text-gray-600">
                {t('space.embeddingSwitchConfirmContent')}
              </div>
            </div>
          ),
          content: (
            <div className="rb:p-4 rb:rounded-xl rb:bg-gray-100 rb:text-gray-600 rb:text-[13px] rb:leading-5.5">
              <div>{t('space.embeddingSwitchTask', { model: modelName })}</div>
              <div className="rb:mt-5.5">{t('space.embeddingSwitchDuring')}</div>
              <ul className="rb:pl-3 rb:list-disc">
                <li>{t('space.embeddingSwitchSearchImpact')}</li>
                <li>{t('space.embeddingSwitchLocked')}</li>
                <li>{t('space.embeddingSwitchRecovery')}</li>
              </ul>
              <div className="rb:mt-5.5">{t('space.embeddingSwitchEstimate')}</div>
              <div className="rb:mt-5.5">{t('space.embeddingSwitchContinue')}</div>
            </div>
          ),
          footer: (_, { OkBtn, CancelBtn }) => (
            <Flex align="center" justify="flex-end" gap={12}>
              <CancelBtn />
              <OkBtn />
            </Flex>
          ),
          okText: t('space.embeddingSwitchConfirmAction'),
          cancelText: t('common.cancel'),
          okButtonProps: { style: { height: 36, margin: 0, paddingInline: 20, borderRadius: 8, background: '#191919', borderColor: '#191919', fontWeight: 600 } },
          cancelButtonProps: { style: { height: 36, margin: 0, paddingInline: 16, borderRadius: 8, borderColor: '#D0D5DD', color: '#344054' } },
          onOk: () => saveConfig(values),
        })
      })
  }
  const [activeTab, setActiveTab] = useState<'models' | 'memoryConfig'>('models')
  /** Handle tab change */
  const handleChangeTab: TabsProps['onChange'] = (value) => {
    setActiveTab(value as ('models' | 'memoryConfig'))
  }

  return (
    <div className="rb:bg-white rb:rounded-lg rb:p-6! rb:h-full rb:overflow-auto">
      <Flex vertical className="rb:max-w-205">
        <Flex vertical gap={6} className="rb:mb-2!">
          <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5">{t('menu.spaceConfig')}</div>
          <div className="rb:text-gray-600 rb:text-[12px] rb:leading-4">{t('space.configAlert')}</div>
        </Flex>
        {isPrivateAvailable &&
          <Tabs
            items={['models', 'memoryConfig'].map(key => ({
              label: t(`space.${key}`),
              key
            }))}
            activeKey={activeTab}
            onChange={handleChangeTab}
            className={clsx("rb:mb-1!", styles.tabs)}
          />
        }
        {activeTab === 'models' &&
          <>
            {pageLoading
              ? <Skeleton active />
              : (
                <Form
                  form={form}
                  layout="vertical"
                  initialValues={{ is_default_config: isPrivateAvailable ? '1' : '0' }}
                  className="rb:flex-1! rb:overflow-hidden!"
                > 
                  <Flex vertical gap={4} className="rb:h-full! rb:overflow-hidden!">
                    <EmbeddingAlert reembedJobId={reembedJobId} onChange={setReembedJobId} className="rb:mb-4!" />
                    <div className="rb:flex-1! rb:overflow-x-hidden rb:overflow-y-auto">
                      {isPrivateAvailable && Object.keys(defaultModels).length > 0 &&
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
                            className="rb:text-left! rb:px-5!"
                          />
                        </Form.Item>
                      }

                      {!isPrivateAvailable || Object.keys(defaultModels).length === 0 || values?.is_default_config === '0' ? (
                        <>
                          <Flex align="baseline" justify="space-between" gap={8} className="rb:pb-3! rb:mb-5! rb-border-b">
                            <div className="rb:font-semibold rb:leading-4.5 rb:border-l-4 rb:border-l-blue-500 rb:pl-2">{t('space.baseModel')}</div>
                            <span className="rb:text-[12px] rb:text-gray-600">{t('space.baseModelDesc')}</span>
                          </Flex>
                          <Row gutter={20}>
                            {baseModelFields.map(field => (
                              <Col key={field.name} span={12}>
                                <Form.Item
                                  label={t(`space.${field.label}`)}
                                  className="rb:font-medium rb:mb-5!"
                                  name={field.name}
                                  rules={[{ required: true, message: t('common.pleaseSelect') }]}
                                  extra={!!reembedJobId && field.name === 'embedding'
                                    ? <span className="rb:text-orange-500">{t('space.reembedding.switchLockedNotice')}</span>
                                    : undefined
                                  }
                                >
                                  <ModelSelect
                                    fieldNames={{ label: 'name', value: 'id' }}
                                    placeholder={t('common.pleaseSelect')}
                                    isAutoFetch={false}
                                    initialData={customModels[field.name]}
                                    disabled={!!reembedJobId && field.name === 'embedding'}
                                  />
                                </Form.Item>
                              </Col>
                            ))}
                          </Row>

                          <Flex align="baseline" justify="space-between" gap={8} className="rb:pb-3! rb:mb-5! rb-border-b">
                            <div className="rb:font-semibold rb:leading-4.5 rb:border-l-4 rb:border-l-blue-500 rb:pl-2">{t('space.multimodalModel')}</div>
                            <span className="rb:text-[12px] rb:text-gray-600">{t('space.multimodalModelDesc')}</span>
                          </Flex>
                          <Row gutter={20}>
                            {multimodalModelFields.map(field => (
                              <Col key={field.name} span={12}>
                                <Form.Item
                                  label={t(`space.${field.label}`)}
                                  className="rb:font-medium rb:mb-5!"
                                  name={field.name}
                                >
                                  <ModelSelect
                                    fieldNames={{ label: 'name', value: 'id' }}
                                    placeholder={t('common.pleaseSelect')}
                                    isAutoFetch={false}
                                    initialData={customModels[field.name]}
                                  />
                                </Form.Item>
                              </Col>
                            ))}
                          </Row>
                        </>
                      ) : (
                        <Descriptions
                          bordered
                          column={2}
                          items={[...baseModelFields, ...multimodalModelFields].map(field => ({
                            key: field.name,
                            label: t(`space.${field.label}`),
                            children: defaultModels[field.name]?.name || '-'
                          }))}
                          size="small"
                          className={styles.descriptions}
                        />
                      )}
                    </div>

                    <Flex gap={12} className="rb:shrink-0 rb:pt-10!">
                      <Button type="primary" onClick={handleSave} loading={loading}>
                        {t('common.save')}
                      </Button>
                    </Flex>
                  </Flex>
                </Form>
              )
            }
          </>
        }
        {isPrivateAvailable && activeTab === 'memoryConfig' && (
          <MemoryLifecycle
            request={request}
          />
        )}
      </Flex>
    </div>
  );
};

export default SpaceConfig;
