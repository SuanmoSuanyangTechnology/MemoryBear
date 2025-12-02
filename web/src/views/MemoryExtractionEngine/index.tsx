import { type FC, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Space, Switch, Select, InputNumber, Slider, Button, App, Skeleton, Form } from 'antd'
import { ExclamationCircleFilled, CheckCircleFilled } from '@ant-design/icons'
import clsx from 'clsx'
import Card from './components/Card'
import RbCard from '@/components/RbCard/Card'
import RbAlert from '@/components/RbAlert'
import Empty from '@/components/Empty'
import type { ConfigForm, ConfigVo, Variable, TestResult } from './types'
import { getMemoryExtractionConfig, updateMemoryExtractionConfig, pilotRunMemoryExtractionConfig } from '@/api/memory'
import Markdown from '@/components/Markdown'
import { getModelList } from '@/api/models';
import type { Model } from '@/views/ModelManagement/types'

const keys = [
  // 'example', 
  'storageLayerModule', 
  'arrangementLayerModule'
]

const configList: ConfigVo[] = [
  {
    type: 'storageLayerModule',
    data: [
      {
        title: 'entityDeduplicationDisambiguation',
        list: [
          {
            label: 'enableLlmDedupBlockwise',
            variableName: 'enable_llm_dedup_blockwise',
            control: 'button', // switch
            type: 'tinyint',
          },
          {
            label: 'enableLlmDisambiguation',
            variableName: 'enable_llm_disambiguation',
            control: 'button',
            type: 'tinyint',
          },
          {
            label: 'tNameStrict',
            control: 'slider',
            variableName: 't_name_strict',
            type: 'decimal',
          },
          {
            label: 'tTypeStrict',
            control: 'slider',
            variableName: 't_type_strict',
            type: 'decimal',
          },
          {
            label: 'tOverall',
            control: 'slider',
            variableName: 't_overall',
            type: 'decimal',
          },
        ]
      },
      // 语义锚点标注
      {
        title: 'semanticAnchorAnnotationModule',
        list: [
          // 句子提取颗粒度
          {
            label: 'statementGranularity',
            variableName: 'statement_granularity',
            control: 'slider',
            type: 'decimal',
            max: 3,
            min: 1,
            step: 1,
            meaning: 'statementGranularityDesc',
          },
          // 是否包含对话上下文
          {
            label: 'includeDialogueContext',
            variableName: 'include_dialogue_context',
            control: 'button', // switch
            type: 'tinyint',
            meaning: 'includeDialogueContextDesc'
          },
          // 上下文文字上限
          {
            label: 'maxDialogueContextChars',
            variableName: 'max_context',
            control: 'inputNumber',
            min: 100,
            type: 'decimal',
            meaning: 'maxDialogueContextCharsDesc',
          },
        ]
      },
    ]
  },
  {
    type: 'arrangementLayerModule',
    data: [
      {
        title: 'queryMode',
        list: [
          {
            label: 'deepRetrieval',
            variableName: 'deep_retrieval',
            control: 'button',
            type: 'tinyint',
            meaning: 'deepRetrievalMeaning',
          },
        ]
      },
      {
        title: 'dataPreprocessing',
        list: [
          {
            label: 'chunkerStrategy',
            variableName: 'chunker_strategy',
            control: 'select',
            type: 'enum',
            options: [
              { label: 'recursiveChunker', value: 'RecursiveChunker' }, // 递归分块
              { label: 'tokenChunker', value: 'TokenChunker' }, // token 分块
              { label: 'semanticChunker', value: 'SemanticChunker' }, // 语义分块
              { label: 'neuralChunker', value: 'NeuralChunker' }, // 神经网络分块
              { label: 'hybridChunker', value: 'HybridChunker' }, // 混合分块
              { label: 'llmChunker', value: 'LLMChunker' }, // LLM 分块
              { label: 'sentenceChunker', value: 'SentenceChunker' }, // 句子分块
              { label: 'lateChunker', value: 'LateChunker' }, // 延迟分块
            ],
            meaning: 'chunkerStrategyDesc',
          },
        ]
      },
      // 智能语义剪枝
      {
        title: 'intelligentSemanticPruning',
        list: [
          // 智能语义剪枝功能
          {
            label: 'intelligentSemanticPruningFunction',
            variableName: 'pruning_enabled',
            control: 'button',
            type: 'tinyint',
            meaning: 'intelligentSemanticPruningFunctionDesc',
          },
          // 智能语义剪枝场景
          {
            label: 'intelligentSemanticPruningScene',
            variableName: 'pruning_scene',
            control: 'select',
            type: 'enum',
            options: [
              { label: 'education', value: 'education' },
              { label: 'online_service', value: 'online_service' },
              { label: 'outbound', value: 'outbound' },
            ],
            meaning: 'intelligentSemanticPruningSceneDesc',
          },
          // 智能语义剪枝阈值
          {
            label: 'intelligentSemanticPruningThreshold',
            control: 'slider',
            variableName: 'pruning_threshold',
            type: 'decimal',
            max: 0.9,
            min: 0,
            step: 0.1,
            meaning: 'intelligentSemanticPruningThresholdDesc',
          },
        ]
      },
      // 自我反思引擎
      {
        title: 'selfReflexionEngine',
        list: [
          // 是否启用反思引擎
          {
            label: 'enableSelfReflexion',
            variableName: 'enable_self_reflexion',
            control: 'button',
            type: 'tinyint',
          },
          // 迭代周期
          {
            label: 'iterationPeriod',
            variableName: 'iteration_period',
            control: 'select',
            type: 'enum',
            options: [
              { label: 'oneHour', value: '1' },
              { label: 'threeHours', value: '3' },
              { label: 'sixHours', value: '6' },
              { label: 'twelveHours', value: '12' },
              { label: 'daily', value: '24' },
            ],
            meaning: 'iterationPeriodDesc',
          },
          // 反思范围
          {
            label: 'reflexionRange',
            variableName: 'reflexion_range',
            control: 'select',
            type: 'enum',
            options: [
              { label: 'retrieval', value: 'retrieval' },
              { label: 'database', value: 'database' },
            ],
            meaning: 'reflexionRangeDesc',
          },
          // 反思基线
          {
            label: 'reflectOnTheBaseline',
            variableName: 'baseline',
            control: 'select',
            type: 'enum',
            options: [
              { label: 'basedOnTime', value: 'TIME' },
              { label: 'basedOnFacts', value: 'FACT' },
              { label: 'basedOnFactsAndTime', value: 'TIME-FACT' },
            ],
          },
        ]
      },
    ]
  }
]

const resultObj = {
  extractTheNumberOfEntities: 'entities.extracted_count',
  numberOfEntityDisambiguation: 'disambiguation.block_count',
  memoryFragments: 'memory.chunks',
  numberOfRelationalTriples: 'triplets.count'
}

const ConfigDesc: FC<{ config: Variable, className?: string }> = ({config, className}) => {
  const { t } = useTranslation();
  return (
    <div className={className}>
      <Space size={8} className={clsx("rb:mt-[4px] rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-[16px] ")}>
        {config.variableName && <span className="rb:font-regular">{t('memoryExtractionEngine.variableName')}: {config.variableName}</span>}
        {config.control && <span className="rb:font-regular">{t('memoryExtractionEngine.control')}: {t(`memoryExtractionEngine.${config.control}`)}</span>}
        {config.type && <span className="rb:font-regular">{t('memoryExtractionEngine.type')}: {config.type}</span>}
      </Space>
      {config.meaning && <div className={clsx("rb:mt-[4px] rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-[16px] ")}>{t('memoryExtractionEngine.Meaning')}: {t(`memoryExtractionEngine.${config.meaning}`)}</div>}
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
  // const [data, setData] = useState<ConfigForm>()
  const modelValues = Form.useWatch([], modelForm)
  const values = Form.useWatch<ConfigForm>([], form)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [runLoading, setRunLoading] = useState(false)
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
    const requests = [getModelList({ type: 'llm', pagesize: 100, page: 1 }), getModelList({ type: 'chat', pagesize: 100, page: 1 })]
    Promise.all(requests)
      .then(responses => {
        const [chatRes, modelRes] = responses as { items: Model[] }[]
        const chatList = chatRes.items || []
        const modelList = modelRes.items || []
        setModelList([...chatList, ...modelList])
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
      const lastResult = localStorage.getItem(`${id}_testResult`)
      setTestResult(lastResult ? JSON.parse(lastResult) : null)
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
  const handleRun = () => {
    if (!id) {
      return
    }
    setRunLoading(true)
    updateMemoryExtractionConfig({
      ...values,
      ...modelValues,
      config_id: id,
    }).then(() => {
      pilotRunMemoryExtractionConfig({
        config_id: id,
        dialogue_text: t('memoryExtractionEngine.exampleText'),
      }).then((res) => {
        message.success(t('common.testSuccess'))
        const response = res as { extracted_result: TestResult }
        setTestResult(response.extracted_result || {})
        localStorage.setItem(`${id}_testResult`, JSON.stringify(response.extracted_result || {}))
      })
      .finally(() => {
        setRunLoading(false)
      })
    })
  }

  return (
    <>
      <div className="rb:text-[24px] rb:font-semibold rb:leading-[32px] rb:mb-[8px]">{t('memoryExtractionEngine.title')}</div>
      <div className="rb:text-[#5B6167] rb:leading-[20px] rb:mb-[24px]">{t('memoryExtractionEngine.subTitle')}</div>

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
          <div className="rb:text-[14px] rb:text-[#5B6167] rb:font-regular rb:leading-[20px]">
            <Markdown content={t('memoryExtractionEngine.exampleText')} />
          </div>
        }
      </Card>
      <Row gutter={[16, 16]} className="rb:mt-[16px]">
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
                          `rb:p-[16px_24px] rb:rounded-[8px]`,
                          'rb:border-[1px] rb:border-[#DFE4ED]',
                          {
                            'rb:shadow-[inset_4px_0px_0px_0px_#155EEF]': index % 2 === 0,
                            'rb:shadow-[inset_4px_0px_0px_0px_#369F21]': index % 2 !== 0,
                          }
                        )}
                      >
                        <div className="rb:text-[16px] rb:font-medium rb:leading-[22px]">{t(`memoryExtractionEngine.${vo.title}`)}</div>
                        <div className="rb:mt-[4px] rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-[16px]">{t(`memoryExtractionEngine.${vo.title}SubTitle`)}</div>

                        {vo.list.map(config => (
                          <div key={config.label}>
                            {config.control === 'button' &&
                              <div className="rb:flex rb:items-center rb:justify-between rb:mt-[24px]">
                                <div>
                                  <span className="rb:text-[14px] rb:font-medium rb:leading-[20px]">-{t(`memoryExtractionEngine.${config.label}`)}</span>
                                  <ConfigDesc config={config} className="rb:ml-[8px]" />
                                </div>
                                <Form.Item
                                  name={config.variableName}
                                  valuePropName="checked"
                                  className="rb:ml-[8px] rb:mb-[0px]!"
                                >
                                  <Switch />
                                </Form.Item>
                              </div>
                            }
                            {config.control === 'select' &&
                              <>
                                <div className="rb:text-[14px] rb:font-medium rb:leading-[20px] rb:mt-[24px] rb:mb-[8px]">
                                  -{t(`memoryExtractionEngine.${config.label}`)}
                                </div>
                                <div className="rb:pl-[8px]">
                                  <Form.Item
                                    name={config.variableName}
                                  >
                                    <Select 
                                      disabled={config.variableName === 'iteration_period' && iterationPeriodDisabled}
                                      options={config.options ? config.options.map(item => ({ ...item, label: t(`memoryExtractionEngine.${item.label}`) })) : []}
                                    />
                                  </Form.Item>
                                  <ConfigDesc config={config} className="rb:mt-[-16px]!" />
                                </div>
                              </>
                            }
                            {config.control === 'slider' &&
                              <>
                                <div className="rb:text-[14px] rb:font-medium rb:leading-[20px] rb:mt-[24px] rb:mb-[8px]">
                                  -{t(`memoryExtractionEngine.${config.label}`)}
                                </div>
                                <div className="rb:pl-[8px]">
                                  <ConfigDesc config={config} className="rb:mb-[10px]" />
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
                                  <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:leading-[20px] rb:mt-[-26px]">
                                    {config.min || 0}
                                    <span>{t('memoryExtractionEngine.CurrentValue')}: {values?.[config.variableName as keyof ConfigForm]}</span>
                                  </div>
                                </div>
                              </>
                            }
                            {config.control === 'inputNumber' &&
                              <>
                                <div className="rb:text-[14px] rb:font-medium rb:leading-[20px] rb:mt-[24px] rb:mb-[8px]">
                                  -{t(`memoryExtractionEngine.${config.label}`)}
                                </div>
                                <div className="rb:pl-[8px]">
                                  <Form.Item
                                    name={config.variableName}
                                  >
                                    <InputNumber min={config.min || 0} style={{ width: '100%' }} placeholder={t('common.pleaseEnter')} />
                                  </Form.Item>
                                  <ConfigDesc config={config} className="rb:mt-[-16px]!" />
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
          <Card
            title={t('memoryExtractionEngine.exampleMemoryExtractionResults')}
            subTitle={t('memoryExtractionEngine.exampleMemoryExtractionResultsSubTitle')}
            className="rb:min-h-[calc(100vh-330px)]!"
            bodyClassName="rb:min-h-[calc(100vh-388px)]"
          >
            <div 
            className="rb:min-h-[calc(100vh-480px)] rb:overflow-y-auto"
            >
              {testResult && Object.keys(testResult).length > 0
                ? <>
                  <RbAlert color="orange" icon={<ExclamationCircleFilled />} className="rb:mb-[14px]">
                    {t('memoryExtractionEngine.warning')}
                  </RbAlert>

                  <Space size={16} direction="vertical" style={{ width: '100%' }}>
                    {resultObj && Object.keys(resultObj).length > 0 &&
                      <RbCard>
                        <div className="rb:grid rb:grid-cols-2 rb:gap-[40px_57px]">
                          {Object.keys(resultObj).map(key => {
                            const keys = (resultObj as Record<string, string>)[key].split('.')
                            return (
                            <div key={key}>
                              <div className="rb:text-[24px] rb:leading-[30px] rb:font-extrabold">{testResult?.[keys[0] as keyof TestResult]?.[keys[1]]}</div>
                              <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-[16px] rb:font-regular">{t(`memoryExtractionEngine.${key}`)}</div>
                              <div className="rb:mt-[4px] rb:text-[12px] rb:text-[#369F21] rb:leading-[14px] rb:font-regular">
                                {}
                                {key === 'extractTheNumberOfEntities'
                                  ? t(`memoryExtractionEngine.${key}Desc`, {
                                    num: testResult.dedup.total_merged_count,
                                    exact: testResult.dedup.breakdown.exact,
                                    fuzzy: testResult.dedup.breakdown.fuzzy,
                                    llm: testResult.dedup.breakdown.llm,
                                  })
                                  : key === 'numberOfEntityDisambiguation'
                                  ? t(`memoryExtractionEngine.${key}Desc`, { num: testResult.disambiguation.effects?.length, block_count: testResult.disambiguation.block_count })
                                  : key === 'numberOfRelationalTriples'
                                  ? t(`memoryExtractionEngine.${key}Desc`, { num: testResult.triplets.count })
                                  :t(`memoryExtractionEngine.${key}Desc`)
                                }
                              </div>
                            </div>
                          )})}
                        </div>
                      </RbCard>
                    }
                    
                    {testResult?.dedup?.impact && testResult.dedup.impact?.length > 0 &&
                      <RbCard
                        title={t('memoryExtractionEngine.entityDeduplicationImpact')}
                        headerType="borderL"
                        headerClassName="rb:before:bg-[#155EEF]!"
                      >
                        <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-medium rb:leading-[16px]">{t('memoryExtractionEngine.identifyDuplicates')}</div>
                        {testResult.dedup.impact.map((item, index) => (
                          <div key={index} className="rb:pl-[8px] rb:mt-[8px] rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-[16px]">
                            -{t('memoryExtractionEngine.identifyDuplicatesDesc', { ...item })}
                          </div>
                        ))}

                        <RbAlert color="blue" icon={<CheckCircleFilled />} className="rb:mt-[12px]">
                          {t('memoryExtractionEngine.entityDeduplicationImpactDesc', { count: testResult.dedup.impact.length })}
                        </RbAlert>
                      </RbCard>
                    }
                    
                    {testResult?.disambiguation && testResult.disambiguation?.effects?.length > 0 &&
                      <RbCard
                        title={t('memoryExtractionEngine.theEffectOfEntityDisambiguationLLMDriven')}
                        headerType="borderL"
                        headerClassName="rb:before:bg-[#155EEF]!"
                      >
                        {testResult.disambiguation.effects.map((item, index) => (
                          <div key={index} className={clsx("rb:text-[12px] rb:text-[#5B6167] rb:leading-[16px]", {
                            'rb:mt-[16px]': index > 0,
                          })}>
                            <div className="rb:font-medium rb:mb-[8px]">Disagreement Case {index +1}:</div>
                            -{item.left.name}({item.left.type}) vs {item.right.name}({item.right.type}) → <span className="rb:text-[#369F21]">{item.result}</span>
                          </div>
                        ))}

                        <RbAlert color="blue" icon={<CheckCircleFilled />} className="rb:mt-[12px]">
                          {t('memoryExtractionEngine.entityDeduplicationImpactDesc', { count: testResult.dedup.impact.length })}
                        </RbAlert>
                      </RbCard>
                    }
                    
                    {testResult?.core_entities && testResult?.core_entities.length > 0 &&
                      <RbCard
                        title={t('memoryExtractionEngine.coreEntitiesAfterDedup')}
                        headerType="borderL"
                        headerClassName="rb:before:bg-[#369F21]!"
                      >
                        <div className="rb:grid rb:grid-cols-2 rb:gap-[24px]">
                          {testResult.core_entities.map(item => (
                            <div key={item.type} className="rb:text-[12px]">
                              <div className="rb:text-[#369F21] rb:font-medium">{item.type}({item.count})</div>

                              <div>
                                {item.entities.map((entity, index) => (
                                  <div key={index} className="rb:text-[#5B6167] rb:font-regular rb:leading-[16px]">
                                    -{entity}
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </RbCard>
                    }
                    
                    {testResult?.triplet_samples && testResult?.triplet_samples.length > 0 &&
                      <RbCard
                        title={t('memoryExtractionEngine.extractRelationalTriples')}
                        headerType="borderL"
                        headerClassName="rb:before:bg-[#9C6FFF]!"
                      >
                        <Space size={8} direction="vertical" className="rb:w-full">
                          {testResult.triplet_samples.map((item, index) => (
                            <div key={index} className="rb:text-[12px]">
                              -({item.subject}, <span className="rb:text-[#9C6FFF] rb:font-medium">{item.predicate}</span>, {item.object})
                            </div>
                          ))}
                        </Space>
                        <RbAlert color="purple" icon={<CheckCircleFilled />} className="rb:mt-[12px]">
                          {t('memoryExtractionEngine.extractRelationalTriplesDesc', { count: testResult.triplet_samples.length })}
                        </RbAlert>
                      </RbCard>
                    }
                  </Space>
                </>
                : loading
                ? <Skeleton />
                : <Empty className="rb:h-full" />
              }
            </div>

            <div className="rb:grid rb:grid-cols-2 rb:gap-[16px] rb:mt-[20px]">
              <Button block loading={loading} onClick={handleSave}>{t('common.save')}</Button>
              <Button block type="primary" loading={runLoading} onClick={handleRun}>{t('memoryExtractionEngine.debug')}</Button>
            </div>
          </Card>
        </Col>
      </Row>
    </>
  )
}
export default MemoryExtractionEngine