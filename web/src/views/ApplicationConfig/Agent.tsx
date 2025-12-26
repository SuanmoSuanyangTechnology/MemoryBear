import { type FC, type ReactNode, useEffect, useRef, useState, forwardRef, useImperativeHandle } from 'react';
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom';
import { Row, Col, Space, Form, Input, Switch, Button, App, Spin } from 'antd'
import Chat from './components/Chat'
import RbCard from '@/components/RbCard/Card'
import Card from './components/Card'
import ModelConfigModal from './components/ModelConfigModal'
import type { 
  ModelConfigModalRef,
  ChatData,
  Config,
  ModelConfig,
  AgentRef,
  KnowledgeBase,
  KnowledgeConfig,
  Variable,
  MemoryConfig,
  AiPromptModalRef
} from './types'
import type { Model } from '@/views/ModelManagement/types'
import { getModelList } from '@/api/models';
import { saveAgentConfig } from '@/api/application'
import Knowledge from './components/Knowledge'
import VariableList from './components/VariableList'
import { getApplicationConfig } from '@/api/application'
import { getKnowledgeBaseList } from '@/api/knowledgeBase'
import { memoryConfigListUrl } from '@/api/memory'
import CustomSelect from '@/components/CustomSelect'
import aiPrompt from '@/assets/images/application/aiPrompt.png'
import AiPromptModal from './components/AiPromptModal'

const DescWrapper: FC<{desc: string, className?: string}> = ({desc, className}) => {
  return (
    <div className={clsx(className, "rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4 ")}>
      {desc}
    </div>
  )
}
const LabelWrapper: FC<{title: string, className?: string; children?: ReactNode}> = ({title, className, children}) => {
  return (
    <div className={clsx(className, "rb:text-[14px] rb:font-medium rb:leading-5")}>
      {title}
      {children}
    </div>
  )
}
const SwitchWrapper: FC<{ title: string, desc: string, name: string }> = ({ title, desc, name }) => {
  const { t } = useTranslation();
  return (
    <div className="rb:flex rb:items-center rb:justify-between">
      <LabelWrapper title={t(`application.${title}`)}>
        <DescWrapper desc={t(`application.${desc}`)} className="rb:mt-2" />
      </LabelWrapper>
      <Form.Item
        name={name}
        valuePropName="checked"
        className="rb:mb-0!"
      >
        <Switch />
      </Form.Item>
    </div>
  )
}
const SelectWrapper: FC<{ title: string, desc: string, name: string, url: string }> = ({ title, desc, name, url }) => {
  const { t } = useTranslation();
  return (
    <>
      <LabelWrapper title={t(`application.${title}`)} className="rb:mb-2">
      </LabelWrapper>
      <Form.Item
        name={name}
        className="rb:mb-0!"
      >
        <CustomSelect
          url={url}
          hasAll={false}
          valueKey='config_id'
          labelKey="config_name"
        />
      </Form.Item>
      <DescWrapper desc={t(`application.${desc}`)} className="rb:mt-2" />
    </>
  )
}

const Agent = forwardRef<AgentRef>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams();
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<Config | null>(null);
  const modelConfigModalRef = useRef<ModelConfigModalRef>(null)
  const [modelList, setModelList] = useState<Model[]>([])
  const [defaultModel, setDefaultModel] = useState<Model | null>(null)
  const [chatList, setChatList] = useState<ChatData[]>([])
  const [formData, setFormData] = useState<{
    default_model_config_id?: string,
    model_parameters?: Config['model_parameters'],
  } | null>(null)
  const values = Form.useWatch<{
    memoryEnabled: boolean;
    memory_content?: string | number;
    webSearch: boolean;
  } & Config>([], form)

  const [knowledgeConfig, setKnowledgeConfig] = useState<KnowledgeConfig>({ knowledge_bases: [] })  
  const [variableList, setVariableList] = useState<Variable[]>([])  
  const [isSave, setIsSave] = useState(false)
  const initialized = useRef(false)
  
  // 初始化完成标记
  useEffect(() => {
    if (data && values && formData) {
      initialized.current = true
    }
  }, [data, values, formData])

  useEffect(() => {
    if (!initialized.current) return
    if (isSave) return
    setIsSave(true)
  }, [knowledgeConfig])
  useEffect(() => {
    if (!initialized.current) return
    if (isSave) return
    setIsSave(true)
  }, [variableList])
  useEffect(() => {
    if (!initialized.current) return
    if (isSave) return
    setIsSave(true)
  }, [formData])
  useEffect(() => {
    if (!initialized.current) return
    if (isSave) return
    setIsSave(true)
  }, [values])

  useEffect(() => {
    getModels()
    getData()
  }, [])

  const getData = () => {
    setLoading(true)
    getApplicationConfig(id as string).then(res => {
      const response = res as Config
      setData(response)
      const { memory, tools } = response
      form.setFieldsValue({
        ...response,
        memoryEnabled: memory?.enabled || false,
        memory_content: memory?.memory_content ? Number(memory?.memory_content) : undefined,
        webSearch: tools?.web_search?.enabled || false,
      })
      setFormData({
        default_model_config_id: response.default_model_config_id,
        model_parameters: response.model_parameters || {},
      })
      if (response?.knowledge_retrieval?.knowledge_bases?.length) {
        getDefaultKnowledgeList(response)
      }
    }).finally(() => {
      setLoading(false)
    })
  }
  const getDefaultKnowledgeList = (data: Config) => {
    if (!data || !data.knowledge_retrieval || !data.knowledge_retrieval?.knowledge_bases?.length) {
      return
    }
    const initialList = [...(data?.knowledge_retrieval?.knowledge_bases || [])]
    getKnowledgeBaseList(undefined, {
      kb_ids: initialList.map(vo => vo.kb_id).join(','),
      page: 1,
      pagesize: 100,
    })
      .then(res => {
        const list = res.items || []
        const knowledge_bases: KnowledgeBase[] = list.map(item => {
          const filterItem = initialList.find(vo => vo.kb_id === item.id)
          return {
            ...item,
            ...filterItem
          }
        }) 
        setData((prev) => {
          prev = prev as Config
          const knowledge_retrieval: KnowledgeConfig = {
            ...(prev?.knowledge_retrieval || {}),
            knowledge_bases: [...knowledge_bases]
          }
          return {
            ...(prev || {}),
            knowledge_retrieval
          }
        })
      })
  }

  const refresh = (vo: ModelConfig, type: 'model' | 'chat') => {
    if (type === 'model') {
      const { default_model_config_id, ...rest } = vo
      form.setFieldsValue({
        default_model_config_id,
        model_parameters: {...rest}
      })
      setFormData((prevState) => {
        const prev = prevState as Config
        return {
          ...(prev || {}),
          default_model_config_id,
          model_parameters: {...rest}
        };
      })
      if (default_model_config_id === formData?.default_model_config_id) {
        setChatList([{
          label: vo.label || '',
          model_config_id: default_model_config_id || '',
          model_parameters: {...rest},
          list: []
        }])
      }
    } else if (type === 'chat') {
      if (chatList.length >= 4) {
        message.warning(t('application.maxChatCount'))
        return
      }
      const { label, default_model_config_id, ...reset } = vo

      setChatList((prev: ChatData[]) => {
        const newChatItem: ChatData = {
          label,
          model_config_id: default_model_config_id || '',
          model_parameters: {...reset},
          list: []
        };
        return [
          ...(prev || []).map(item => ({
            ...item,
            conversation_id: undefined,
            list: []
          })),
          newChatItem
        ];
      })
    }
  }

  const handleModelConfig = () => {
    modelConfigModalRef.current?.handleOpen('model')
  }
  const handleClearDebugging = () => {
    setChatList([])
  }

  // 保存Agent配置
  const handleSave = (flag = true) => {
    if (!isSave || !data) return Promise.resolve()
    const { memoryEnabled, memory_content, webSearch, ...rest } = values
    const { knowledge_bases = [], ...knowledgeRest } = knowledgeConfig || {}
    
    // 从原数据中获取memory的其他必要属性
    const originalMemory = data.memory || ({} as MemoryConfig)
    
    const params: Config = {
      ...data,
      ...rest,
      ...(formData || {}),
      memory: {
        ...originalMemory,
        enabled: memoryEnabled,
        memory_content: memory_content ? String(memory_content) : '',
        max_history: originalMemory.max_history || '',
      },
      variables: variableList || [],
      knowledge_retrieval: knowledge_bases.length > 0 ? {
        ...data.knowledge_retrieval,
        ...knowledgeRest,
        knowledge_bases: knowledge_bases.map(item => ({
          kb_id: item.id,
          ...(item.config || {})
        }))
      } as KnowledgeConfig : null,
      tools: {
        web_search: {
          enabled: webSearch,
          config: {
            web_search: webSearch
          }
        }
      }
    }
    
    return new Promise((resolve, reject) => {
      saveAgentConfig(data.app_id, params)
      .then(() => {
        if (flag) {
          message.success(t('common.saveSuccess'))
        }
        setIsSave(false)
        resolve(true)
      }).catch(error => {
        reject(error)
      })
    })
  }
  const getModels = () => {
    getModelList({ type: 'llm,chat', pagesize: 100, page: 1 })
      .then(res => {
        const response = res as { items: Model[] }
        setModelList(response.items)
      })
  }
  const handleAddModel = () => {
    modelConfigModalRef.current?.handleOpen('chat')
  }
  useEffect(() => {
    if (formData?.default_model_config_id && modelList.length > 0) {
      const filterValue = modelList.find(item => item.id === formData.default_model_config_id)
      setDefaultModel(filterValue as Model | null)
      setChatList([{
        label: filterValue?.name || '',
        model_config_id: filterValue?.id || '',
        model_parameters: {...(filterValue?.config || {})} as unknown as ModelConfig,
        list: []
      }])
    }
  }, [modelList, formData?.default_model_config_id])

  useImperativeHandle(ref, () => ({
    handleSave
  }))

  const aiPromptModalRef = useRef<AiPromptModalRef>(null)
  const handlePrompt = () => {
    aiPromptModalRef.current?.handleOpen()
  }
  const updatePrompt = (value: string) => {
    form.setFieldValue('system_prompt', value)
  }
  return (
    <>
      {loading && <Spin fullscreen></Spin>}
      <Row className="rb:h-[calc(100vh-64px)]">
        <Col span={12} className="rb:h-full rb:overflow-x-auto rb:border-r rb:border-[#DFE4ED] rb:p-[20px_16px_24px_16px]">
          <div className="rb:flex rb:items-center rb:justify-end rb:mb-5">
            <Space size={10}>
              <Button onClick={handleModelConfig} className="rb:group">
                {defaultModel?.name ? <div className="rb:w-4 rb:h-4 rb:bg-[url('@/assets/images/application/model.svg')] rb:group-hover:bg-[url('@/assets/images/application/model_hover.svg')]"></div> : null}
                {defaultModel?.name || t('application.chooseModel')}
              </Button>
              <Button type="primary" onClick={() => handleSave()}>
                {t('common.save')}
              </Button>
            </Space>
          </div>
          <Form form={form}>
            <Space size={16} direction="vertical" style={{ width: '100%' }}>
              {/* 提示词 */}
              <Card title={t('application.promptConfiguration')}>
                <div className="rb:flex rb:items-center rb:justify-between rb:mb-2.75">
                  <div className="rb:font-medium rb:leading-5">
                    {t('application.configuration')}
                    <span className="rb:font-regular rb:text-[12px] rb:text-[#5B6167]"> ({t('application.configurationDesc')})</span>
                  </div>
                  <Button style={{ padding: '0 8px', height: '24px' }} onClick={handlePrompt}>
                    <img src={aiPrompt} className="rb:size-5" />
                    {t('application.aiPrompt')}
                  </Button>
                </div>

                <Form.Item name="system_prompt" className="rb:mb-0!">
                  <Input.TextArea
                    placeholder={t('application.promptPlaceholder')}
                    styles={{
                      textarea: {
                        minHeight: '200px',
                        borderRadius: '8px'
                      },
                    }}
                  />
                </Form.Item>
              </Card>

              {/* 知识库 */}
              <Knowledge
                data={data?.knowledge_retrieval || { knowledge_bases: [] }} 
                onUpdate={setKnowledgeConfig}
              />

              {/* 记忆配置 */}
              <Card title={t('application.memoryConfiguration')}>
                <Space size={24} direction='vertical' style={{ width: '100%' }}>
                  <SwitchWrapper title="dialogueHistoricalMemory" desc="dialogueHistoricalMemoryDesc" name="memoryEnabled" />
                  <SelectWrapper 
                    title="selectMemoryContent" 
                    desc="selectMemoryContentDesc" 
                    name="memory_content"
                    url={memoryConfigListUrl}
                  />
                </Space>
              </Card>

              {/* 变量配置 */}
              <VariableList
                data={data?.variables}
                onUpdate={setVariableList}
              />
              {/* 工具配置 */}
              <Card title={t('application.toolConfiguration')}>
                <Space size={24} direction='vertical' style={{ width: '100%' }}>
                  <SwitchWrapper title="webSearch" desc="webSearchDesc" name="webSearch" />
                  {/* <SwitchWrapper title="codeExecutor" desc="codeExecutorDesc" name="codeExecutor" />
                  <SwitchWrapper title="imageGeneration" desc="imageGenerationDesc" name="imageGeneration" /> */}
                </Space>
              </Card>
            </Space>
          </Form>
        </Col>
        <Col span={12} className="rb:h-full rb:overflow-x-hidden rb:p-[20px_16px_24px_16px]">
          <div className="rb:flex rb:items-center rb:justify-between rb:mb-5">
            {t('application.debuggingAndPreview')}

            <Space size={10}>
              <Button type="primary" ghost onClick={handleAddModel}>
                +{t('application.addModel')}
              </Button>
              <div className="rb:w-8 rb:h-8 rb:cursor-pointer rb:bg-[url('@/assets/images/application/clean.svg')]" onClick={handleClearDebugging}></div>
            </Space>
          </div>
          <RbCard height="calc(100vh - 160px)" bodyClassName="rb:p-[0]! rb:h-full rb:overflow-hidden">
            <Chat
              data={data as Config}
              chatList={chatList}
              updateChatList={setChatList}
              handleSave={handleSave}
            />
          </RbCard>
        </Col>
      </Row>

      <ModelConfigModal
        modelList={modelList}
        data={formData as Config}
        chatList={chatList}
        ref={modelConfigModalRef}
        refresh={refresh}
      />
      <AiPromptModal
        ref={aiPromptModalRef}
        defaultModel={defaultModel}
        refresh={updatePrompt}
      />
    </>
  );
});

export default Agent;
