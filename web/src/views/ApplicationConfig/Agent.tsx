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
  MemoryConfig,
  AiPromptModalRef,
  Source,
  ChatVariableConfigModalRef
} from './types'
import type { Variable } from './components/VariableList/types'
import type { KnowledgeConfig } from './components/Knowledge/types'
import type { ModelListItem } from '@/views/ModelManagement/types'
import { getModelList } from '@/api/models';
import { saveAgentConfig } from '@/api/application'
import Knowledge from './components/Knowledge/Knowledge'
import VariableList from './components/VariableList/VariableList'
import { getApplicationConfig } from '@/api/application'
import { memoryConfigListUrl } from '@/api/memory'
import CustomSelect from '@/components/CustomSelect'
import aiPrompt from '@/assets/images/application/aiPrompt.png'
import AiPromptModal from './components/AiPromptModal'
import ToolList from './components/ToolList/ToolList'
import ChatVariableConfigModal from './components/ChatVariableConfigModal';

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
const SwitchWrapper: FC<{ title: string, desc?: string, name: string | string[]; needTransition?: boolean; }> = ({ title, desc, name, needTransition = true }) => {
  const { t } = useTranslation();
  return (
    <div className="rb:flex rb:items-center rb:justify-between">
      <LabelWrapper title={needTransition ? t(`application.${title}`) : title}>
        {desc && <DescWrapper desc={needTransition ? t(`application.${desc}`) : desc} className="rb:mt-2" />}
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
const SelectWrapper: FC<{ title: string, desc: string, name: string | string[], url: string }> = ({ title, desc, name, url }) => {
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
          placeholder={t('common.pleaseSelect')}
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
  const [modelList, setModelList] = useState<ModelListItem[]>([])
  const [defaultModel, setDefaultModel] = useState<ModelListItem | null>(null)
  const [chatList, setChatList] = useState<ChatData[]>([])
  const values = Form.useWatch<Config>([], form) 
  const [isSave, setIsSave] = useState(false)
  const initialized = useRef(false)
  
  // 初始化完成标记
  useEffect(() => {
    if (data) {
      initialized.current = true
    }
  }, [data])

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
      let allTools = Array.isArray(response.tools) ? response.tools : []
      form.setFieldsValue({
        ...response,
        tools: allTools,
        memory: {
          ...response.memory,
          memory_content: response.memory?.memory_content ? Number(response.memory?.memory_content) : undefined
        }
      })
      setData({
        ...response,
        tools: allTools
      })
    }).finally(() => {
      setLoading(false)
    })
  }

  const refresh = (vo: ModelConfig, type: Source) => {
    if (type === 'model') {
      const { default_model_config_id, ...rest } = vo
      form.setFieldsValue({
        default_model_config_id,
        model_parameters: {...rest}
      })
      if (default_model_config_id === values?.default_model_config_id) {
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
    const { memory, knowledge_retrieval, tools, ...rest } = values
    const { knowledge_bases = [], ...knowledgeRest } = knowledge_retrieval || {}
    const { memory_content } = memory || {}
    // 从原数据中获取memory的其他必要属性
    const originalMemory = data.memory || ({} as MemoryConfig)
    
    const params: Config = {
      ...data,
      ...rest,
      memory: {
        ...originalMemory,
        ...memory,
        memory_content: memory_content ? String(memory_content) : '',
      },
      knowledge_retrieval: knowledge_bases.length > 0 ? {
        ...data.knowledge_retrieval,
        ...knowledgeRest,
        knowledge_bases: knowledge_bases.map(item => ({
          kb_id: item.kb_id || item.id,
          ...(item.config || {})
        }))
      } as KnowledgeConfig : null,
      tools: tools.map(vo => ({
        tool_id: vo.tool_id,
        operation: vo.operation,
        enabled: vo.enabled
      }))
    }
    
    return new Promise((resolve, reject) => {
      saveAgentConfig(data.app_id, params)
      .then((res) => {
        if (flag) {
          message.success(t('common.saveSuccess'))
        }
        setIsSave(false)
        resolve(res)
      }).catch(error => {
        reject(error)
      })
    })
  }
  const getModels = () => {
    getModelList({ type: 'llm,chat', pagesize: 100, page: 1, is_active: true })
      .then(res => {
        const response = res as { items: ModelListItem[] }
        setModelList(response.items)
      })
  }
  const handleAddModel = () => {
    modelConfigModalRef.current?.handleOpen('chat')
  }
  useEffect(() => {
    if (values?.default_model_config_id && modelList.length > 0) {
      const filterValue = modelList.find(item => item.id === values.default_model_config_id)
      setDefaultModel(filterValue as ModelListItem | null)
      setChatList([{
        label: filterValue?.name || '',
        model_config_id: filterValue?.id || '',
        model_parameters: {...(filterValue?.config || {})} as unknown as ModelConfig,
        list: []
      }])
    }
  }, [modelList, values?.default_model_config_id])

  useImperativeHandle(ref, () => ({
    handleSave
  }))

  const aiPromptModalRef = useRef<AiPromptModalRef>(null)
  const handlePrompt = () => {
    aiPromptModalRef.current?.handleOpen()
  }
  const updatePrompt = (value: string) => {
    form.setFieldValue('system_prompt', value)
    const variables = value.match(/\{\{([^}]+)\}\}/g)?.map(match => match.slice(2, -2)) || []
    const uniqueVariables = [...new Set(variables)]
    const newVariableList: Variable[] = uniqueVariables.map((name, index) => ({
      index,
      type: 'text',
      name,
      display_name: name,
      required: false
    }))
    updateVariableList(newVariableList)
  }

  const updateVariableList = (list: Variable[]) => {
    form.setFieldValue('variables', [...list])
    setChatVariables([...list])
  }
  const chatVariableConfigModalRef = useRef<ChatVariableConfigModalRef>(null)
  const [chatVariables, setChatVariables] = useState<Variable[]>([])
  const handleOpenVariableConfig = () => {
    chatVariableConfigModalRef.current?.handleOpen(chatVariables)
  }
  const handleSaveChatVariable = (values: Variable[]) => {
    setChatVariables(values)
  }
  console.log('values', values)
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
            <Form.Item name="default_model_config_id" hidden noStyle></Form.Item>
            <Form.Item name="model_parameters" hidden noStyle></Form.Item>
            <Space size={16} direction="vertical" style={{ width: '100%' }}>
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

              <Form.Item name="knowledge_retrieval" noStyle>
                <Knowledge />
              </Form.Item>

              {/* 记忆配置 */}
              <Card title={t('application.memoryConfiguration')}>
                <Space size={24} direction='vertical' style={{ width: '100%' }}>
                  <SwitchWrapper title="dialogueHistoricalMemory" desc="dialogueHistoricalMemoryDesc" name={['memory', 'enabled']} />
                  <SelectWrapper 
                    title="selectMemoryContent" 
                    desc="selectMemoryContentDesc" 
                    name={['memory', 'memory_content']}
                    url={memoryConfigListUrl}
                  />
                </Space>
              </Card>

              <Form.Item name="variables">
                <VariableList />
              </Form.Item>
              
              {/* 工具配置 */}
              <Form.Item name="tools">
                <ToolList />
              </Form.Item>
            </Space>
          </Form>
        </Col>
        <Col span={12} className="rb:h-full rb:overflow-x-hidden rb:p-[20px_16px_24px_16px]">
          <div className="rb:flex rb:items-center rb:justify-between rb:mb-5">
            {t('application.debuggingAndPreview')}

            <Space size={10}>
              <Button type="primary" ghost onClick={handleOpenVariableConfig}>
                {t('application.variableConfig')}
              </Button>
              <Button type="primary" ghost onClick={handleAddModel}>
                + {t('application.addModel')}
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
        data={values}
        ref={modelConfigModalRef}
        refresh={refresh}
      />
      <AiPromptModal
        ref={aiPromptModalRef}
        defaultModel={defaultModel}
        refresh={updatePrompt}
      />
      <ChatVariableConfigModal
        ref={chatVariableConfigModalRef}
        refresh={handleSaveChatVariable}
      />
    </>
  );
});

export default Agent;
