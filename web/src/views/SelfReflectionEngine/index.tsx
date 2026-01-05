import React, { useState, useEffect } from 'react';
import { Row, Col, Form, App, Button, Switch, Space, Select } from 'antd';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import RbCard from '@/components/RbCard/Card';
import strategyImpactSimulator from '@/assets/images/memory/strategyImpactSimulator.svg'
import { getMemoryReflectionConfig, updateMemoryReflectionConfig, pilotRunMemoryReflectionConfig } from '@/api/memory'
import type { ConfigForm, Result, ReflexionData, MemoryVerify, QualityAssessment } from './types'
import CustomSelect from '@/components/CustomSelect';
import { getModelListUrl } from '@/api/models'
import Tag from '@/components/Tag'
import { useI18n } from '@/store/locale';

const configList = [
  // 启用反思引擎
  {
    key: 'reflection_enabled',
    type: 'switch',
  },
  // 反思模型
  {
    key: 'reflection_model_id',
    type: 'customSelect',
    url: getModelListUrl,
    params: { type: 'chat,llm', page: 1, pagesize: 100 }, // chat,llm
  },
  // 迭代周期
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
  // 反思范围
  {
    key: 'reflexion_range',
    type: 'select',
    hiddenDesc: true,
    options: [
      { label: 'partial', value: 'partial' },
      { label: 'all', value: 'all' },
    ],
  },
  // 反思基线
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
  // 质量评估
  {
    key: 'quality_assessment',
    type: 'switch',
  },
  // 质量评估
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
  const handleReset = () => {
    form.setFieldsValue(configData);
  }
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
          title={
            <div className="rb:flex rb:items-center">
              <img src={strategyImpactSimulator} className="rb:w-5 rb:h-5 rb:mr-2" />
              {t('reflectionEngine.reflectionEngineConfig')}
            </div>
          }
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
            {configList.map(config => {
              if (config.type === 'customSelect') {
                return (
                  <div key={config.key}>
                    <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mb-2">
                      {t(`reflectionEngine.${config.key}`)}
                    </div>
                    <Form.Item
                      name={config.key}
                      extra={t(`reflectionEngine.${config.key}_desc`)}
                    >
                      <CustomSelect
                        url={config.url as string}
                        params={config.params}
                        valueKey='id'
                        labelKey='name'
                        hasAll={false}
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
                    <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mb-2">
                      {t(`reflectionEngine.${config.key}`)}
                    </div>
                    <Form.Item
                      name={config.key}
                      extra={t(`reflectionEngine.${config.key}_desc`)}
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
                <div className="rb:flex rb:items-center rb:justify-between rb:mb-6">
                  <div>
                    <span className="rb:text-[14px] rb:font-medium rb:leading-5">{t(`reflectionEngine.${config.key}`)}</span>
                    {(config as any).hasSubTitle && <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">{t(`reflectionEngine.${config.key}_subTitle`)}</div>}
                    <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">{t(`reflectionEngine.${config.key}_desc`)}</div>
                  </div>
                  <Form.Item
                    name={config.key}
                    valuePropName="checked"
                    className="rb:ml-2 rb:mb-0!"
                  >
                    <Switch
                      disabled={!values?.reflection_enabled && config.key !== 'reflection_enabled'} />
                  </Form.Item>
                </div>
              )
            })}
            <Row gutter={16} className="rb:mt-3">
              <Col span={12}>
                <Button block onClick={handleReset}>{t('common.reset')}</Button>
              </Col>
              <Col span={12}>
                <Button type="primary" loading={loading} block onClick={handleSave}>{t('common.save')}</Button>
              </Col>
            </Row>
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

            <Button type="primary" block loading={runLoading} onClick={handleRun}>{t('reflectionEngine.run')}</Button>
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
