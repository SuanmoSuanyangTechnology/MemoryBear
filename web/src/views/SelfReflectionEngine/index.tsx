/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:46:47 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 11:44:16
 */
/**
 * Self Reflection Engine Configuration Page
 * Configures reflection period, range, baseline, quality assessment, and privacy audit
 * Supports pilot run with example data
 */

import React, { useState, useEffect } from 'react';
import { Row, Col, Form, App, Button, Space, Select, Flex } from 'antd';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import RbCard from '@/components/RbCard/Card';
import { getMemoryReflectionConfig, updateMemoryReflectionConfig, pilotRunMemoryReflectionConfig } from '@/api/memory'
import type { ConfigForm, Result, ReflexionData, MemoryVerify, QualityAssessment } from './types'
import Tag from '@/components/Tag'
import { useI18n } from '@/store/locale';
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import LabelWrapper from '@/components/FormItem/LabelWrapper'
import DescWrapper from '@/components/FormItem/DescWrapper'
import ModelSelect from '@/components/ModelSelect';

/** Configuration list */
const configList = [
  // Enable reflection engine
  {
    key: 'reflection_enabled',
    type: 'switch',
  },
  // Reflection model
  {
    key: 'reflection_model_id',
    type: 'modelSelect',
    params: { type: 'chat,llm' }, // chat,llm
  },
  // Iteration period
  {
    key: 'reflection_period_in_hours',
    type: 'select',
    options: [
      { label: 'oneHour', value: '1' },
      { label: 'threeHours', value: '3' },
      { label: 'sixHours', value: '6' },
      { label: 'twelveHours', value: '12' },
      { label: 'daily', value: '24' },
    ],
  },
  // Reflection scope
  {
    key: 'reflexion_range',
    type: 'select',
    hiddenDesc: true,
    options: [
      { label: 'partial', value: 'partial' },
      { label: 'all', value: 'all' },
    ],
  },
  // Reflection baseline
  {
    key: 'baseline',
    type: 'select',
    hiddenDesc: true,
    options: [
      { label: 'TIME', value: 'TIME' },
      { label: 'FACT', value: 'FACT' },
      { label: 'HYBRID', value: 'HYBRID' },
    ],
  },
  // Quality assessment
  {
    key: 'quality_assessment',
    type: 'switch',
  },
  // Quality assessment
  {
    key: 'memory_verify',
    type: 'switch',
  },
]

const SelfReflectionEngine: React.FC = () => {
  const { t } = useTranslation();
  const { id } = useParams();
  const [configData, setConfigData] = useState<ConfigForm>({} as ConfigForm);
  const [form] = Form.useForm<ConfigForm>();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false)
  const [runLoading, setRunLoading] = useState(false)
  const [result, setResult] = useState<Result | null>(null)
  const { language } = useI18n()

  const values = Form.useWatch([], form);

  useEffect(() => {
    getConfigData()
  }, [id])

  /** Fetch configuration data */
  const getConfigData = () => {
    if (!id) {
      return
    }
    getMemoryReflectionConfig(id)
      .then((res) => {
        const response = res as ConfigForm
        const initialValues = {
          ...response,
        }
        console.log('initialValues', initialValues)
        setConfigData(initialValues);
        form.setFieldsValue(initialValues);
      })
      .catch(() => {
        console.error('Failed to load data');
      })
  }
  /** Reset form to saved values */
  const handleReset = () => {
    form.setFieldsValue(configData);
  }
  /** Save configuration */
  const handleSave = () => {
    if (!id) {
      return
    }
    setLoading(true)
    updateMemoryReflectionConfig({
      ...values,
      config_id: id
    })
      .then(() => {
        message.success(t('common.saveSuccess'))
        setConfigData({...(values || {})})
      })
      .finally(() => {
        setLoading(false)
      })
  }
  /** Run pilot test */
  const handleRun = () => {
    if (!id) {
      return
    }
    setRunLoading(true)
    updateMemoryReflectionConfig({
      ...values,
      config_id: id
    })
      .then(() => {
        pilotRunMemoryReflectionConfig({
          config_id: id,
          language_type: language
        })
          .then((res) => {
            setResult(res as Result)
          })
          .finally(() => {
            setRunLoading(false)
          })
      })
      .catch(() => {
        setRunLoading(false)
      })
  }

  return (
    <Row gutter={[16, 16]}>
      <Col span={12}>
        <RbCard
          title={t('reflectionEngine.reflectionEngineConfig')}
          extra={<Space>
            <Button block onClick={handleReset}>{t('common.reset')}</Button>
            <Button type="primary" loading={loading} block onClick={handleSave}>{t('common.save')}</Button>
          </Space>}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          className="rb:h-[calc(100vh-76px)]!"
          bodyClassName="rb:h-[calc(100%-54px)] rb:overflow-y-auto! rb:p-4! rb:pt-0!"
        >
          <Form 
            form={form}
            layout="vertical"
            initialValues={{
              offset: 0,
              lambda_time: 0.03,
              lambda_mem: 0.03,
            }}
          >
            <Flex vertical gap={24}>
              {configList.map(config => {
                if (config.type === 'modelSelect') {
                  return (
                    <div key={config.key}>
                      <LabelWrapper title={t(`reflectionEngine.${config.key}`)} className="rb:mb-3">
                        <DescWrapper desc={t(`reflectionEngine.${config.key}_desc`)} className="rb:mt-1" />
                      </LabelWrapper>
                      <Form.Item
                        name={config.key}
                        className="rb:mb-0!"
                      >
                        <ModelSelect
                          params={config.params}
                          placeholder={t('common.pleaseSelect')}
                          disabled={!values?.reflection_enabled && config.key !== 'reflection_enabled'}
                        />
                      </Form.Item>
                    </div>
                  )
                }
                if (config.type === 'select') {
                  return (
                    <div key={config.key}>
                      <LabelWrapper title={t(`reflectionEngine.${config.key}`)} className="rb:mb-3">
                        <DescWrapper desc={t(`reflectionEngine.${config.key}_desc`)} className="rb:mt-1" />
                      </LabelWrapper>
                      <Form.Item
                        name={config.key}
                        className="rb:mb-0!"
                      >
                        <Select
                          options={config.options?.map(vo => ({
                            ...vo,
                            label: t(`reflectionEngine.${vo.label}`),
                          }))}
                          placeholder={t('common.pleaseSelect')}
                          disabled={!values?.reflection_enabled && config.key !== 'reflection_enabled'}
                        />
                      </Form.Item>
                    </div>
                  )
                }

                return (
                  <SwitchFormItem
                    title={t(`reflectionEngine.${config.key}`)}
                    name={config.key}
                    desc={<>
                      {(config as any).hasSubTitle && <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">{t(`reflectionEngine.${config.key}_subTitle`)}</div>}
                      <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">{t(`reflectionEngine.${config.key}_desc`)}</div>
                    </>}
                    className="rb:mb-6"
                    disabled={!values?.reflection_enabled && config.key !== 'reflection_enabled'}
                  />
                )
              })}
            </Flex>
          </Form>
        </RbCard>
      </Col>
      <Col span={12}>
        <Space size={16} direction="vertical" className="rb:w-full">
          <RbCard
            title={t('memoryExtractionEngine.example')}
          >
            <div className="rb:text-[14px] rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mb-6">
              {t('reflectionEngine.exampleText')}
            </div>

            <Button type="primary" block loading={runLoading} disabled={!values?.reflection_enabled} onClick={handleRun}>{t('reflectionEngine.run')}</Button>
          </RbCard>
          {result && <>
            <RbCard
              title={t('reflectionEngine.runTitle')}
            >
              <div 
                className="rb:flex rb:gap-4 rb:justify-start rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:mb-3"
              >
                <div className="rb:whitespace-nowrap rb:w-45 rb:font-medium">{t(`reflectionEngine.baseline`)}</div>
                <div className='rb:flex-inline rb:text-left rb:py-px rb:rounded rb:text-[#5B6167] rb:flex-1'>
                  {result.baseline}
                </div>
              </div>
            </RbCard>
            {result.reflexion_data.length > 0 && (
              <RbCard
                title={t('reflectionEngine.conflictDetection')}
              >
                <Space size={12} direction="vertical" className="rb:w-full">
                  {result.reflexion_data.map((item, index) => (
                    <div key={index} className="rb:bg-[#F0F3F8] rb:px-3 rb:py-2.5 rb:rounded-md rb:text-[12px]">
                      {['reason', 'solution'].map(key => (
                        <div
                          key={key}
                          className="rb:flex rb:gap-4 rb:justify-start rb:text-[14px] rb:leading-5 rb:mb-3"
                        >
                          <div className="rb:whitespace-nowrap rb:w-45 rb:font-medium">{t(`reflectionEngine.${key}`)}</div>
                          <div className='rb:flex-inline rb:text-left rb:py-px rb:rounded rb:text-[#5B6167] rb:flex-1'>
                            {item[key as keyof ReflexionData]}
                          </div>
                        </div>
                      ))}
                    </div>
                  ))}
                </Space>
              </RbCard>
            )}
            {result.quality_assessments.length > 0 && (
              <RbCard
                title={t('reflectionEngine.qualityAssessment')}
              >
                {result.quality_assessments.map((item, index) => (
                  <div key={index} className="rb:bg-[#F0F3F8] rb:px-3 rb:py-2.5 rb:rounded-md rb:text-[12px]">
                    {['score', 'summary'].map(key => (
                      <div
                        key={key}
                        className="rb:flex rb:gap-4 rb:justify-start rb:text-[14px] rb:leading-5 rb:mb-3"
                      >
                        <div className="rb:whitespace-nowrap rb:w-45 rb:font-medium">{t(`reflectionEngine.qualityAssessmentObj.${key}`)}</div>
                        <div className='rb:flex-inline rb:text-left rb:py-px rb:rounded rb:text-[#5B6167] rb:flex-1'>
                          {item[key as keyof QualityAssessment]}
                        </div>
                      </div>
                    ))}
                  </div>
                ))}
              </RbCard>
            )}
            {result.memory_verifies.length > 0 && (
              <RbCard
                title={t('reflectionEngine.privacyAudit')}
              >
                {result.memory_verifies.map((item, index) => (
                  <div key={index} className="rb:bg-[#F0F3F8] rb:px-3 rb:py-2.5 rb:rounded-md rb:text-[12px]">
                    {['has_privacy', 'privacy_types', 'summary'].map(key => (
                      <div
                        key={key}
                        className="rb:flex rb:gap-4 rb:justify-start rb:text-[14px] rb:leading-5 rb:mb-3"
                      >
                        <div className="rb:whitespace-nowrap rb:w-45 rb:font-medium">{t(`reflectionEngine.privacyAuditObj.${key}`)}</div>
                        <div className='rb:flex-inline rb:text-left rb:py-px rb:rounded rb:text-[#5B6167] rb:flex-1'>
                          {key === 'has_privacy'
                            ? <Tag color={item[key as keyof MemoryVerify] ? 'success' : 'error'}>{t(`reflectionEngine.privacyAuditObj.${item[key as keyof MemoryVerify]}`)}</Tag>
                            : key === 'privacy_types' ? (item[key as keyof MemoryVerify] as string[]).join('、')
                            : item[key as keyof MemoryVerify]
                          }
                        </div>
                      </div>
                    ))}
                  </div>
                ))}
              </RbCard>
            )}
          </>}
        </Space>
      </Col>
    </Row>
  );
};

export default SelfReflectionEngine;
