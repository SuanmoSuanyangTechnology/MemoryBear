/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:46:47 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-26 18:57:08
 */
/**
 * Self Reflection Engine Configuration Page
 * Configures reflection period, range, baseline, quality assessment, and privacy audit
 * Supports pilot run with example data
 */

import React, { useState, useEffect } from 'react';
import { Row, Col, Form, App, Button, Space, Select, Flex, Divider } from 'antd';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import RbCard from '@/components/RbCard/Card';
import { getMemoryReflectionConfig, updateMemoryReflectionConfig, pilotRunMemoryReflectionConfig } from '@/api/memory'
import type { ConfigForm, Result, ReflexionData } from './types'
import { useI18n } from '@/store/locale';
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import LabelWrapper from '@/components/FormItem/LabelWrapper'
import DescWrapper from '@/components/FormItem/DescWrapper'
import ModelSelect from '@/components/ModelSelect';
import BtnTabs from '@/components/BtnTabs'

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
  const [activeTabMap, setActiveTabMap] = useState<Record<number, string>>({});
  const [expanded, setExpanded] = useState({ conflict: true, quality: true, privacy: true });
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
            setExpanded({ conflict: true, quality: true, privacy: true })
            setActiveTabMap({})
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
    <Row gutter={[16, 16]} className="rb:h-full!">
      <Col span={12} className="rb:h-full!">
        <RbCard
          title={t('reflectionEngine.reflectionEngineConfig')}
          extra={<Space>
            <Button onClick={handleReset}>{t('common.reset')}</Button>
            <Button type="primary" loading={loading} onClick={handleSave}>{t('common.save')}</Button>
          </Space>}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          className="rb:h-full!"
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
      <Col span={12} className="rb:h-full!">
        <RbCard
          title={t('memoryExtractionEngine.example')}
          extra={<Space>
            <Button type="primary" loading={runLoading} disabled={!values?.reflection_enabled} onClick={handleRun}>{t('reflectionEngine.run')}</Button>
          </Space>}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          className="rb:h-full!"
          bodyClassName="rb:h-[calc(100%-54px)] rb:overflow-y-auto! rb:p-4! rb:pt-0!"
        >
          <Flex vertical gap={12}>
            <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:py-2.5 rb:px-3 rb:leading-5.5">
              {t('reflectionEngine.exampleText')}
            </div>

            {result && <>
              <Flex justify="space-between" className="rb:bg-[#F6F6F6] rb:rounded-xl rb:py-2.5! rb:px-3! rb:leading-5">
                <span className="rb:font-medium rb:text-[#212332]">{t('reflectionEngine.runTitle')}</span>
                <span className="rb:text-[#5B6167]">{t(`reflectionEngine.baseline`)}: {t(`reflectionEngine.${result.baseline}`)}</span>
              </Flex>

              {result.reflexion_data.length > 0 &&
                <Flex vertical gap={12} className="rb:bg-[#F6F6F6] rb:rounded-xl rb:py-2.5! rb:px-3! rb:leading-5.5">
                  <Flex justify="space-between" className="rb:font-medium rb:text-[#212332] rb:cursor-pointer" onClick={() => setExpanded(p => ({ ...p, conflict: !p.conflict }))}>
                    {t('reflectionEngine.conflictDetection')}
                    <div className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:transition-transform", {
                      'rb:rotate-180': !expanded.conflict,
                    })}></div>
                  </Flex>

                  {expanded.conflict && result.reflexion_data.map((item, index) => (
                    <div key={index} className="rb:bg-white rb:rounded-xl rb:py-2.5! rb:px-3!">
                      <BtnTabs
                        className="rb:mb-3!"
                        variant="outline"
                        activeKey={activeTabMap[index] ?? 'reason'}
                        items={['reason', 'solution'].map(key => ({
                          label: t(`reflectionEngine.${key}`),
                          key
                        }))}
                        onChange={(key) => setActiveTabMap(prev => ({ ...prev, [index]: key }))}
                      />
                      <div className="rb:leading-5.5">{item[(activeTabMap[index] ?? 'reason') as keyof ReflexionData]}</div>
                    </div>
                  ))}

                </Flex>
              }
              {result.quality_assessments.length > 0 &&
                <Flex vertical gap={12} className="rb:bg-[#F6F6F6] rb:rounded-xl rb:py-2.5! rb:px-3! rb:leading-5.5">
                  <Flex justify="space-between" className="rb:font-medium rb:text-[#212332] rb:cursor-pointer" onClick={() => setExpanded(p => ({ ...p, quality: !p.quality }))}>
                    {t('reflectionEngine.qualityAssessment')}
                    <div className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:transition-transform", {
                      'rb:rotate-180': !expanded.quality,
                    })}></div>
                  </Flex>

                  {expanded.quality && result.quality_assessments.map((item, index) => (
                    <div key={index} className="rb:bg-white rb:rounded-xl rb:py-2.5! rb:px-3!">
                      <div>
                        <span className="rb:font-medium rb:text-[#212332] rb:leading-5 rb:mr-4.5">{t(`reflectionEngine.qualityAssessmentObj.score`)}</span>
                        <span className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[#155EEF] rb:leading-5">{item.score}</span>
                      </div>
                      <Divider className="rb:my-3!" />
                      <div className="rb:font-medium rb:text-[#212332] rb:leading-5 rb:mb-2">{t(`reflectionEngine.qualityAssessmentObj.summary`)}</div>
                      <div className="rb:mt-1 rb:leading-5.5">{item.summary}</div>
                    </div>
                  ))}

                </Flex>
              }
              {result.memory_verifies.length > 0 &&
                <Flex vertical gap={12} className="rb:bg-[#F6F6F6] rb:rounded-xl rb:py-2.5! rb:px-3! rb:leading-5.5">
                  <Flex justify="space-between" className="rb:font-medium rb:text-[#212332] rb:cursor-pointer" onClick={() => setExpanded(p => ({ ...p, privacy: !p.privacy }))}>
                    {t('reflectionEngine.privacyAudit')}
                    <div className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:transition-transform", {
                      'rb:rotate-180': !expanded.privacy,
                    })}></div>
                  </Flex>

                  {expanded.privacy && result.memory_verifies.map((item, index) => (
                    <div key={index} className="rb:bg-white rb:rounded-xl rb:py-2.5! rb:px-3!">
                      <div>
                        <span className="rb:font-medium rb:text-[#212332] rb:leading-5 rb:mr-4.5">{t(`reflectionEngine.privacyAuditObj.has_privacy`)}</span>
                        <span className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[#155EEF] rb:leading-5">{item.has_privacy}</span>
                      </div>

                      <Divider className="rb:my-3!" />

                      <div className="rb:font-medium rb:text-[#212332] rb:leading-5 rb:mb-2">{t(`reflectionEngine.privacyAuditObj.privacy_types`)}</div>
                      <div className="rb:mt-1 rb:leading-5.5">{item.privacy_types.join(', ')}</div>

                      <Divider className="rb:my-3!" />

                      <div className="rb:font-medium rb:text-[#212332] rb:leading-5 rb:mb-2">{t(`reflectionEngine.privacyAuditObj.summary`)}</div>
                      <div className="rb:mt-1 rb:leading-5.5">{item.summary}</div>
                    </div>
                  ))}

                </Flex>
              }
            </>}
          </Flex>
        </RbCard>
      </Col>
    </Row>
  );
};

export default SelfReflectionEngine;
