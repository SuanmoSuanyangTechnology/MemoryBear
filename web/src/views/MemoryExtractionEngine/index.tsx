import { type FC, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Space, Switch, Select, InputNumber, Slider, App, Form } from 'antd'
import clsx from 'clsx'
import Card from './components/Card'
import type { ConfigForm, Variable } from './types'
import { getMemoryExtractionConfig, updateMemoryExtractionConfig } from '@/api/memory'
import Markdown from '@/components/Markdown'
import { getModelList } from '@/api/models';
import type { Model } from '@/views/ModelManagement/types'
import { configList } from './constant'
import Result from './components/Result'

const keys = [
  // 'example', 
  'storageLayerModule', 
  'arrangementLayerModule'
]

const ConfigDesc: FC<{ config: Variable, className?: string }> = ({config, className}) => {
  const { t } = useTranslation();
  return (
    <div className={className}>
      <Space size={8} className={clsx("rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4 ")}>
        {config.variableName && <span className="rb:font-regular">{t('memoryExtractionEngine.variableName')}: {config.variableName}</span>}
        {config.control && <span className="rb:font-regular">{t('memoryExtractionEngine.control')}: {t(`memoryExtractionEngine.${config.control}`)}</span>}
        {config.type && <span className="rb:font-regular">{t('memoryExtractionEngine.type')}: {config.type}</span>}
      </Space>
      {config.meaning && <div className={clsx("rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4 ")}>{t('memoryExtractionEngine.Meaning')}: {t(`memoryExtractionEngine.${config.meaning}`)}</div>}
    </div>
  )
}
const MemoryExtractionEngine: FC = () => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { id } = useParams()
  const [expandedKeys, setExpandedKeys] = useState<string[]>(keys)
  const [form] = Form.useForm<ConfigForm>()
  const [modelForm] = Form.useForm()
  const modelValues = Form.useWatch([], modelForm)
  const values = Form.useWatch<ConfigForm>([], form)
  const [loading, setLoading] = useState(false)
  const [iterationPeriodDisabled, setIterationPeriodDisabled] = useState(false)
  const [modelList, setModelList] = useState<Model[]>([])

  useEffect(() => {
    if (values?.reflexion_range === 'database') {
      form.setFieldValue('iteration_period', 24)
      setIterationPeriodDisabled(true)
    } else {
      setIterationPeriodDisabled(false)
    }
  }, [values])

  const getModels = () => {
    getModelList({ type: 'llm,chat', pagesize: 100, page: 1 })
      .then(res => {
        const response = res as { items: Model[] }
        setModelList(response.items)
      })
  }

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
      // setData(initialValues)
      form.setFieldsValue(initialValues)
      modelForm.setFieldsValue({
        llm_id: response.llm_id,
      })
    })
  }
  useEffect(() => {
    if (id) {
      getConfig()
      getModels()
    }
  }, [id])

  const handleExpand = (key: string) => {
    const newKeys = expandedKeys.includes(key) ? expandedKeys.filter(item => item !== key) : [...expandedKeys, key]

    setExpandedKeys(newKeys)
  }
  const handleSave = () => {
    if (!id) {
      return
    }
    console.log('values', values)
    setLoading(true)
    updateMemoryExtractionConfig({
      ...values,
      ...modelValues,
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
      <div className="rb:text-[24px] rb:font-semibold rb:leading-8 rb:mb-2">{t('memoryExtractionEngine.title')}</div>
      <div className="rb:text-[#5B6167] rb:leading-5 rb:mb-6">{t('memoryExtractionEngine.subTitle')}</div>

      <Row gutter={[16, 16]}>
        <Col span={12}>
          <Form form={modelForm}>
            <Form.Item 
              label={t('memoryExtractionEngine.model')} 
              name="llm_id"
            >
              <Select
                placeholder={t('common.pleaseSelect')}
                fieldNames={{
                  label: 'name',
                  value: 'id',
                }}
                options={modelList}
              />
            </Form.Item>
          </Form>
        </Col>
      </Row>
      <Card
        type="example"
        title={t('memoryExtractionEngine.example')}
        expanded={expandedKeys.includes('example')}
        handleExpand={handleExpand}
      >
        {expandedKeys.includes('example') &&
          <div className="rb:text-[14px] rb:text-[#5B6167] rb:font-regular rb:leading-5">
            <Markdown content={t('memoryExtractionEngine.exampleText')} />
          </div>
        }
      </Card>
      <Row gutter={[16, 16]} className="rb:mt-4">
        <Col span={14}>
          <Form
            form={form}
          >
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              {configList.map((item, index) => (
                <Card
                  type={item.type}
                  title={t(`memoryExtractionEngine.${item.type}`)}
                  key={index}
                  expanded={expandedKeys.includes(item.type)}
                  handleExpand={handleExpand}
                >
                  <Space size={20} direction="vertical" style={{width: '100%'}}>
                    {item.data.map(vo => (
                      <div 
                        key={vo.title} 
                        className={clsx(
                          `rb:p-[16px_24px] rb:rounded-lg`,
                          'rb:border rb:border-[#DFE4ED]',
                          {
                            'rb:shadow-[inset_4px_0px_0px_0px_#155EEF]': index % 2 === 0,
                            'rb:shadow-[inset_4px_0px_0px_0px_#369F21]': index % 2 !== 0,
                          }
                        )}
                      >
                        <div className="rb:text-[16px] rb:font-medium rb:leading-[22px]">{t(`memoryExtractionEngine.${vo.title}`)}</div>
                        <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">{t(`memoryExtractionEngine.${vo.title}SubTitle`)}</div>

                        {vo.list.map(config => (
                          <div key={config.label}>
                            {config.control === 'button' &&
                              <div className="rb:flex rb:items-center rb:justify-between rb:mt-6">
                                <div>
                                  <span className="rb:text-[14px] rb:font-medium rb:leading-5">-{t(`memoryExtractionEngine.${config.label}`)}</span>
                                  <ConfigDesc config={config} className="rb:ml-2" />
                                </div>
                                <Form.Item
                                  name={config.variableName}
                                  valuePropName="checked"
                                  className="rb:ml-2 rb:mb-0!"
                                >
                                  <Switch />
                                </Form.Item>
                              </div>
                            }
                            {config.control === 'select' &&
                              <>
                                <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mt-6 rb:mb-2">
                                  -{t(`memoryExtractionEngine.${config.label}`)}
                                </div>
                                <div className="rb:pl-2">
                                  <Form.Item
                                    name={config.variableName}
                                  >
                                    <Select 
                                      disabled={config.variableName === 'iteration_period' && iterationPeriodDisabled}
                                      options={config.options ? config.options.map(item => ({ ...item, label: t(`memoryExtractionEngine.${item.label}`) })) : []}
                                    />
                                  </Form.Item>
                                  <ConfigDesc config={config} className="rb:-mt-4!" />
                                </div>
                              </>
                            }
                            {config.control === 'slider' &&
                              <>
                                <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mt-6 rb:mb-2">
                                  -{t(`memoryExtractionEngine.${config.label}`)}
                                </div>
                                <div className="rb:pl-2">
                                  <ConfigDesc config={config} className="rb:mb-2.5" />
                                  <Form.Item
                                    name={config.variableName}
                                  >
                                    <Slider 
                                      style={{ margin: '0' }} 
                                      min={config.min || 0} 
                                      max={config.max || 1} 
                                      step={config.step || 0.01}
                                    />
                                  </Form.Item>
                                  <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:leading-5 rb:mt-[-26px]">
                                    {config.min || 0}
                                    <span>{t('memoryExtractionEngine.CurrentValue')}: {values?.[config.variableName as keyof ConfigForm]}</span>
                                  </div>
                                </div>
                              </>
                            }
                            {config.control === 'inputNumber' &&
                              <>
                                <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:mt-6 rb:mb-2">
                                  -{t(`memoryExtractionEngine.${config.label}`)}
                                </div>
                                <div className="rb:pl-2">
                                  <Form.Item
                                    name={config.variableName}
                                  >
                                    <InputNumber min={config.min || 0} style={{ width: '100%' }} placeholder={t('common.pleaseEnter')} />
                                  </Form.Item>
                                  <ConfigDesc config={config} className="rb:-mt-4!" />
                                </div>
                              </>
                            }
                          </div>
                        ))}
                      </div>
                    ))}
                  </Space>
                </Card>
              ))}
            </Space>
          </Form>
        </Col>
        <Col span={10}>
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