/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:30:02 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-26 15:45:42
 */
/**
 * Memory Extraction Engine Configuration Page
 * Configures entity deduplication, disambiguation, semantic anchoring, and pruning
 * Supports real-time testing with example data
 */

import { type FC, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Space, Select, InputNumber, App, Form, Input, Flex, Tooltip } from 'antd'
import clsx from 'clsx'

import Card from './components/Card'
import type { ConfigForm, Variable } from './types'
import { getMemoryExtractionConfig, updateMemoryExtractionConfig } from '@/api/memory'
import Markdown from '@/components/Markdown'
import { configList, modelConfigList } from './constant'
import Result from './components/Result'
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import ModelSelect from '@/components/ModelSelect'
import RbSlider from '@/components/RbSlider';
import DescWrapper from '@/components/FormItem/DescWrapper'
import LabelWrapper from '@/components/FormItem/LabelWrapper'

/** Available configuration section keys */
const keys = [
  'modelConfig',
  'storageLayerModule', 
  'arrangementLayerModule'
]

/**
 * Configuration description component
 */
const Desc: FC<{ config: Variable, className?: string; onlyMeaning?: boolean; }> = ({ config, className, onlyMeaning = false}) => {
  const { t } = useTranslation();
  return (
    <div className={className}>
      {!onlyMeaning && <Space size={8} className={clsx("rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4 ")}>
        {config.variableName && <span className="rb:font-regular">{t('memoryExtractionEngine.variableName')}: {config.variableName}</span>}
        {config.control && <span className="rb:font-regular">{t('memoryExtractionEngine.control')}: {t(`memoryExtractionEngine.${config.control}`)}</span>}
        {config.type && <span className="rb:font-regular">{t('memoryExtractionEngine.type')}: {config.type}</span>}
      </Space>}
    </div>
  )
}
const MemoryExtractionEngine: FC = () => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { id } = useParams()
  const [expandedKeys, setExpandedKeys] = useState<string[]>(keys)
  const [form] = Form.useForm<ConfigForm>()
  const values = Form.useWatch<ConfigForm>([], form)
  const [loading, setLoading] = useState(false)
  const [iterationPeriodDisabled, setIterationPeriodDisabled] = useState(false)

  useEffect(() => {
    if (values?.reflexion_range === 'database') {
      form.setFieldValue('iteration_period', 24)
      setIterationPeriodDisabled(true)
    } else {
      setIterationPeriodDisabled(false)
    }
  }, [values])

  /** Fetch configuration data */
  const getConfig = () => {
    if (!id) {
      return
    }
    getMemoryExtractionConfig(id).then(res => {
      const response = res as ConfigForm
      const initialValues: ConfigForm = {
        ...response,
        t_name_strict: Number(response.t_name_strict || 0),
        t_type_strict: Number(response.t_type_strict || 0),
        t_overall: Number(response.t_overall || 0),
      }
      form.setFieldsValue(initialValues)
    })
  }
  useEffect(() => {
    if (id) {
      getConfig()
    }
  }, [id])

  /** Toggle section expansion */
  const handleExpand = (key: string) => {
    const newKeys = expandedKeys.includes(key) ? expandedKeys.filter(item => item !== key) : [...expandedKeys, key]

    setExpandedKeys(newKeys)
  }
  /** Save configuration */
  const handleSave = () => {
    if (!id) {
      return
    }
    setLoading(true)
    updateMemoryExtractionConfig({
      ...values,
      config_id: id,
    }).then(() => {
      message.success(t('common.saveSuccess'))
    })
    .finally(() => {
      setLoading(false)
    })
  }

  return (
    <>
      <Flex align="center" gap={4} className="rb:font-[MiSans-Bold] rb:text-[16px] rb:font-bold rb:leading-5.5 rb:mb-4!">
        {t('memoryExtractionEngine.title')}
        <Tooltip title={t('memoryExtractionEngine.subTitle')}>
          <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
        </Tooltip>
      </Flex>

      <Row gutter={12} className="rb:h-[calc(100%-38px)]!">
        <Col span={12} className="rb:h-full!">
          <Form form={form} className="rb:h-full!">
            <Flex vertical gap={12} className="rb:h-full! rb:overflow-y-auto">
              <div className="rb:bg-white rb:rounded-xl rb:py-2.5 rb:px-4">
                <Flex
                  align="center"
                  justify="space-between"
                  className="rb:font-[MiSans-Bold] rb:font-bold rb:cursor-pointer"
                  onClick={() => handleExpand('example')}
                >
                  {t('memoryExtractionEngine.example')}
                  <div className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')]", {
                    'rb:rotate-180': !expandedKeys.includes('example'),
                    'rb:rotate-0': expandedKeys.includes('example'),
                  })}></div>
                </Flex>

                {expandedKeys.includes('example') &&
                  <div className="rb:text-[14px] rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2.5 rb:mb-1.5">
                    <Markdown content={t('memoryExtractionEngine.exampleText')} />
                  </div>
                }
              </div>

              <Card
                title={t('memoryExtractionEngine.modelConfig')}
                type="modelConfig"
                expanded={expandedKeys.includes('modelConfig')}
                handleExpand={handleExpand}
              >
                {/* <Form form={modelForm}> */}
                  <Flex vertical gap={12}>
                    <Flex gap={12} vertical>
                      {modelConfigList.map(config => (
                        <div key={config.key} className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3">
                          <LabelWrapper title={t(`memoryExtractionEngine.${config.key}`)} className="rb:mb-3" />
                          <Form.Item
                            name={config.key}
                            className="rb:mb-0!"
                          >
                            <ModelSelect
                              params={config.params}
                            />
                          </Form.Item>
                        </div>
                      ))}
                    </Flex>
                  </Flex>
                {/* </Form> */}
              </Card>

              <Flex vertical gap={16}>
                {configList.map((item, index) => (
                  <Card
                    type={item.type}
                    title={t(`memoryExtractionEngine.${item.type}`)}
                    key={index}
                    expanded={expandedKeys.includes(item.type)}
                    handleExpand={handleExpand}
                  >
                    <Flex gap={16} vertical>
                      {item.data.map(vo => (
                        <Flex
                          key={vo.title}
                          vertical
                          gap={10}
                          className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3! rb:pt-2.5!"
                        >
                          <Space size={4} className="rb:text-[#212332] rb:font-medium rb:leading-5">
                            {t(`memoryExtractionEngine.${vo.title}`)}
                            <Tooltip title={t(`memoryExtractionEngine.${vo.title}SubTitle`)}>
                              <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
                            </Tooltip>
                          </Space>

                          {vo.list.map(config => (
                            <div key={config.label} className="rb:bg-white rb:rounded-xl rb:p-3 rb:pr-2.5">
                              {config.control === 'button'
                                ? <SwitchFormItem
                                  title={t(`memoryExtractionEngine.${config.label}`)}
                                  name={config.variableName}
                                  desc={<DescWrapper desc={<Desc config={config} />} />}
                                  className="rb:mt-6"
                                />
                                : <>
                                  {config.meaning
                                    ? <Space size={4} className="rb:text-[#212332] rb:font-medium rb:leading-5">
                                      {t(`memoryExtractionEngine.${config.label}`)}
                                      <Tooltip title={<>{t('memoryExtractionEngine.Meaning')}: {t(`memoryExtractionEngine.${config.meaning}`)}</>}>
                                        <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
                                      </Tooltip>
                                    </Space>
                                    : <div className="rb:text-[#212332] rb:font-medium rb:leading-5">
                                      {t(`memoryExtractionEngine.${config.label}`)}
                                    </div>
                                  }
                                  {config.control !== 'text' && <DescWrapper desc={<Desc config={config} />} />}
                                  <Form.Item
                                    name={config.variableName}
                                    className="rb:mb-0! rb:mt-2!"
                                  >
                                    {config.control === 'select'
                                      ? <Select
                                        disabled={config.variableName === 'iteration_period' && iterationPeriodDisabled}
                                        options={config.options ? config.options.map(item => ({ ...item, label: t(`memoryExtractionEngine.${item.label}`) })) : []}
                                      />
                                      : config.control === 'slider'
                                      ? <RbSlider
                                        min={config.min || 0}
                                        max={config.max || 1}
                                        step={config.step || 0.01}
                                        isInput={true}
                                        prefix={<span className="rb:text-[#5B6167]">{t('emotionEngine.currentValue')}:</span>}
                                        inputClassName="rb:w-[155px]!"
                                      />
                                      : config.control === 'inputNumber'
                                      ? <InputNumber min={config.min || 0} style={{ width: '100%' }} placeholder={t('common.pleaseEnter')} />
                                      : config.control === 'text'
                                      ? <Input placeholder={t('common.pleaseEnter')} disabled />
                                      : null
                                    }
                                  </Form.Item>
                                </>
                              }
                            </div>
                          ))}
                        </Flex>
                      ))}
                    </Flex>
                  </Card>
                ))}
              </Flex>
            </Flex>
          </Form>
        </Col>
        <Col span={12} className="rb:h-full!">
          <Result
            loading={loading}
            handleSave={handleSave}
          />
        </Col>
      </Row>
    </>
  )
}
export default MemoryExtractionEngine