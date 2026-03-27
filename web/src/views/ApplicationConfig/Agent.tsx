/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:29:21 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 11:39:59
 */
import { useEffect, useRef, useState, forwardRef, useImperativeHandle, useMemo } from 'react';
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom';
import { Row, Col, Space, Form, Input, Button, App, Spin, Flex } from 'antd'

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
  ChatVariableConfigModalRef,
  FeaturesConfigForm
} from './types'
import type { Variable } from './components/VariableList/types'
import type { KnowledgeConfig } from './components/Knowledge/types'
import type { Model } from '@/views/ModelManagement/types'
import { getModelList } from '@/api/models';
import { saveAgentConfig } from '@/api/application'
import Knowledge from './components/Knowledge/Knowledge'
import VariableList from './components/VariableList/VariableList'
import { getApplicationConfig } from '@/api/application'
import { memoryConfigListUrl } from '@/api/memory'
import CustomSelect from '@/components/CustomSelect'
import AiPromptModal from './components/AiPromptModal'
import ToolList from './components/ToolList/ToolList'
import SkillList from './components/Skill'
import ChatVariableConfigModal from './components/ChatVariableConfigModal';
import type { Skill } from '@/views/Skills/types'
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import DescWrapper from '@/components/FormItem/DescWrapper'
import FeaturesConfig from './components/FeaturesConfig'
import { getListLogoUrl } from '@/views/ModelManagement/utils';

/**
 * Agent configuration component
 * Manages single agent configuration including prompts, knowledge, memory, variables, and tools
 */
const Agent = forwardRef<AgentRef, { onFeaturesLoad?: (features: FeaturesConfigForm | undefined) => void }>(({ onFeaturesLoad }, ref) => {
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
  const values = Form.useWatch<Config>([], form) 
  const [isSave, setIsSave] = useState(false)
  const initialized = useRef(false)
  
  // Initialization flag
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
  }, [id])

  /**
   * Fetch agent configuration data
   */
  const getData = () => {
    setLoading(true)
    getApplicationConfig(id as string).then(res => {
      const response = res as Config
      const { skills, variables } = response
      const allSkills = Array.isArray(skills?.skill_ids) ? skills?.skill_ids.map(vo => ({ id: vo })) : []
      const allTools = Array.isArray(response.tools) ? response.tools : []
      const memoryContent = response.memory?.memory_config_id
      const parsedMemoryContent = memoryContent === null || memoryContent === ''
        ? undefined
        : !isNaN(Number(memoryContent)) ? Number(memoryContent) : memoryContent
      const variableList = variables?.map((item, index) => ({
        ...item,
        index
      })) || []
      form.setFieldsValue({
        ...response,
        tools: allTools,
        memory: {
          ...response.memory,
          memory_config_id: parsedMemoryContent
        },
        skills: {
          ...skills,
          skill_ids: allSkills
        },
        variables: [...variableList]
      })
      updateVariableList([...variableList])
      setData({
        ...response,
        tools: allTools
      })
      onFeaturesLoad?.(response.features)
    }).finally(() => {
      setLoading(false)
    })
  }

  /**
   * Refresh configuration after model changes
   * @param vo - Model configuration
   * @param type - Source type (model or chat)
   */
  const refresh = (vo: ModelConfig, type: Source) => {
    const opening_statement = form.getFieldValue(['features', 'opening_statement'])
    if (type === 'model') {
      const { default_model_config_id, capability, ...rest } = vo
      if (default_model_config_id !== values.default_model_config_id) {
        const fileUpload = { ...values.features?.file_upload }
        Object.keys(fileUpload).forEach(key => {
          if (key.includes('enabled')) {
            (fileUpload as Record<string, any>)[key] = false
          }
        })
        form.setFieldValue(['features', 'file_upload'], fileUpload)
        message.warning(t('application.resetFeaturesTip'))
      }
      form.setFieldsValue({
        default_model_config_id,
        capability,
        model_parameters: {...rest}
      })
      if (default_model_config_id === values?.default_model_config_id) {
        const label = defaultModel?.id === default_model_config_id && defaultModel?.name ? defaultModel.name : vo.label || ''
        setChatList([{
          label: defaultModel?.id === default_model_config_id && defaultModel?.name ? defaultModel.name :  vo.label || '',
          model_config_id: default_model_config_id || '',
          model_parameters: {...rest},
          list: label !== '' ? [{
            role: 'assistant',
            content: opening_statement?.statement,
            meta_data: {
              suggested_questions: opening_statement?.suggested_questions || []
            }
          }] : []
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

  /**
   * Open model configuration modal
   */
  const handleModelConfig = () => {
    modelConfigModalRef.current?.handleOpen('model')
  }
  /**
   * Clear all debugging chat sessions
   */
  const handleClearDebugging = () => {
    setChatList([])
  }

  /**
   * Save agent configuration
   * @param flag - Whether to show success message
   * @returns Promise that resolves when save is complete
   */
  const handleSave = (flag = true) => {
    if (!isSave || !data) return Promise.resolve()
    const { memory, knowledge_retrieval, tools, skills, ...rest } = values
    const { knowledge_bases = [], ...knowledgeRest } = knowledge_retrieval || {}
    const { memory_config_id } = memory || {}
    // Get other necessary properties of memory from original data
    const originalMemory = data.memory || ({} as MemoryConfig)
    
    const params: Config = {
      ...data,
      ...rest,
      memory: {
        ...originalMemory,
        ...memory,
        memory_config_id: memory_config_id ? String(memory_config_id) : '',
      },
      knowledge_retrieval: knowledge_bases.length > 0 ? {
        ...data.knowledge_retrieval,
        ...knowledgeRest,
        knowledge_bases: knowledge_bases.map(item => ({
          kb_id: item.kb_id || item.id,
          ...(item.config || {})
        }))
      } as KnowledgeConfig : null,
      tools: tools.map(vo => {
        if (!vo.operation) {
          return {
            tool_id: vo.tool_id,
            enabled: vo.enabled
          }
        }
        return {
          tool_id: vo.tool_id,
          operation: vo.operation,
          enabled: vo.enabled
        }
      }),
      skills: {
        ...skills,
        skill_ids: (skills?.skill_ids as Skill[])?.map(vo => vo.id)
      }
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
  /**
   * Fetch available models list
   */
  const getModels = () => {
    getModelList({ type: 'llm,chat', pagesize: 100, page: 1, is_active: true })
      .then(res => {
        const response = res as { items: Model[] }
        setModelList(response.items)
      })
  }
  /**
   * Add new model for debugging
   */
  const handleAddModel = () => {
    modelConfigModalRef.current?.handleOpen('chat')
  }
  useEffect(() => {
    if (values?.default_model_config_id && modelList.length > 0) {
      const filterValue = modelList.find(item => item.id === values.default_model_config_id)
      setDefaultModel(filterValue as Model | null)
      const opening_statement = form.getFieldValue(['features', 'opening_statement'])
      setChatList([{
        label: filterValue?.name || '',
        model_config_id: filterValue?.id || '',
        model_parameters: {...(filterValue?.config || {})} as unknown as ModelConfig,
        list: filterValue?.name ? [{
          role: 'assistant',
          content: opening_statement?.statement,
          meta_data: {
            suggested_questions: opening_statement?.suggested_questions || []
          }
        }] : []
      }])
      form.setFieldValue('capability', filterValue?.capability)
    }
  }, [modelList, values?.default_model_config_id])

  useImperativeHandle(ref, () => ({
    handleSave,
    features: values?.features
  }))

  const aiPromptModalRef = useRef<AiPromptModalRef>(null)
  /**
   * Open AI prompt generation modal
   */
  const handlePrompt = () => {
    aiPromptModalRef.current?.handleOpen()
  }
  /**
   * Update prompt and extract variables
   * @param value - New prompt value
   */
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

  /**
   * Update variable list
   * @param list - New variable list
   */
  const updateVariableList = (list: Variable[]) => {
    form.setFieldValue('variables', [...list])
    setChatVariables([...list])
  }
  const chatVariableConfigModalRef = useRef<ChatVariableConfigModalRef>(null)
  const [chatVariables, setChatVariables] = useState<Variable[]>([])
  /**
   * Open chat variable configuration modal
   */
  const handleOpenVariableConfig = () => {
    chatVariableConfigModalRef.current?.handleOpen(chatVariables)
  }
  /**
   * Save chat variable configuration
   * @param values - Variable values
   */
  const handleSaveChatVariable = (variables: Variable[]) => {
    setChatVariables(variables)
    const opening_statement = form.getFieldValue(['features', 'opening_statement'])

    if (opening_statement?.statement && opening_statement?.statement.trim() !== '') {
      const statement = opening_statement.statement as string
      const replacedContent = statement.replace(/\{\{([^}]+)\}\}/g, (match, name) => {
        const v = variables.find(item => item.name === name)
        return v?.value != null && v.value !== '' ? String(v.value) : match
      })
      setChatList(prev => prev.map(item => {
        const list = [...(item.list || [])]
        if (list.length > 0 && list[0].role === 'assistant') {
          list[0] = { ...list[0], content: replacedContent }
        }
        return { ...item, list }
      }))
    }
  }
  useEffect(() => {
    setChatVariables(values?.variables || [])
  }, [values?.variables])

  const handleSaveFeaturesConfig = (value: FeaturesConfigForm) => {
    form.setFieldValue('features', value)

    if (value?.opening_statement?.statement && value?.opening_statement?.statement.trim() !== '') {
      setChatList(prev => (prev.map(item => {
        const firstMsg = item.list?.[0]

        if (firstMsg?.role === 'assistant') {
          firstMsg.meta_data = {
            suggested_questions: value.opening_statement?.suggested_questions || []
          }
          return item
        } else {
          return {
            ...item,
            list: [{
              role: 'assistant',
              content: value.opening_statement?.statement,
              meta_data: {
                suggested_questions: value.opening_statement?.suggested_questions || []
              }
            }, ...(item.list || [])]
          }
        }
      })))
    }
  }
  const modelLogo = useMemo(() => {
    return defaultModel?.name && getListLogoUrl(defaultModel.provider, defaultModel.logo as string)
  }, [defaultModel])
  
  console.log('agent values', values)
  return (
    <>
      {loading && <Spin fullscreen></Spin>}
      <Row className="rb:h-full!" gutter={12}>
        <Col span={12} className="rb:h-full!">
          <Form form={form}>
            <Flex gap={12} vertical>
              <Flex align="center" justify="space-between" className="rb:p-3! rb:bg-white rb:rounded-xl">
                <Button type="primary" ghost onClick={handleModelConfig} className="rb:group">
                  {modelLogo
                    ? <img src={modelLogo} className="rb:size-4 rb:rounded-md" alt="" />
                    : defaultModel?.name
                    ? <div className="rb:size-4 rb:bg-[url('@/assets/images/application/model.svg')]"></div> : null}
                  {defaultModel?.name || t('application.chooseModel')}
                </Button>
                <Space size={12}>
                  <FeaturesConfig
                    value={values?.features as FeaturesConfigForm}
                    capability={values?.capability || []}
                    refresh={handleSaveFeaturesConfig}
                    chatVariables={chatVariables}
                  />
                  <Button type="primary" onClick={() => handleSave()}>
                    {t('common.save')}
                  </Button>
                </Space>
              </Flex>

              <Flex gap={12} vertical className="rb:h-[calc(100vh-156px)]! rb:overflow-y-auto!">
                <Form.Item name="default_model_config_id" hidden noStyle></Form.Item>
                <Form.Item name="capability" hidden noStyle></Form.Item>
                <Form.Item name="model_parameters" hidden noStyle></Form.Item>
                <Form.Item name="features" hidden noStyle></Form.Item>
                <Card
                  title={t('application.promptConfiguration')}
                  extra={
                    <Space
                      size={1}
                      className="rb:px-2 rb:h-5.5 rb:rounded-md rb:cursor-pointer rb:border rb:border-[rgba(21,94,239,0.3)] rb:text-[#155EEF]"
                      onClick={handlePrompt}
                    >
                      <div className="rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/application/aiPrompt.png')]"></div>
                      <span className="rb:font-[PingFangSC, PingFang_SC]!">{t('application.aiPrompt')}</span>
                    </Space>
                  }
                >
                  <div className="rb:leading-4.5 rb:text-[12px] rb:mb-2">
                    <span className="rb:font-medium">{t('application.configuration')}</span>
                    <span className="rb:font-regular rb:text-[#5B6167]"> ({t('application.configurationDesc')})</span>
                  </div>

                  <Form.Item name="system_prompt" className="rb:mb-0!">
                    <Input.TextArea
                      placeholder={t('application.promptPlaceholder')}
                      styles={{
                        textarea: {
                          minHeight: '200px',
                          borderRadius: '8px',
                          padding: '12px'
                        },
                      }}
                    />
                  </Form.Item>
                </Card>

                <Form.Item name="knowledge_retrieval" noStyle>
                  <Knowledge />
                </Form.Item>

                  {/* Memory Configuration */}
                <Card title={t('application.memoryConfiguration')}>
                  <Flex gap={16} vertical className="rb:bg-[#FAFAFA] rb:rounded-xl rb:p-3!">
                    <SwitchFormItem
                      title={t('application.dialogueHistoricalMemory')}
                      name={['memory', 'enabled']}
                      desc={t('application.dialogueHistoricalMemoryDesc')}
                    />
                    <Form.Item
                      name={['memory', 'memory_config_id']}
                      label={t('application.selectMemoryContent')}
                      extra={<DescWrapper desc={t('application.selectMemoryContentDesc')} className="rb:mt-1" />}
                      layout="vertical"
                      className="rb:mb-0!"
                    >
                      <CustomSelect
                        placeholder={t('common.pleaseSelect')}
                        url={memoryConfigListUrl}
                        hasAll={false}
                        valueKey='config_id'
                        labelKey="config_name"
                        disabled={!values?.memory?.enabled}
                      />
                    </Form.Item>
                  </Flex>
                </Card>

                <Form.Item name="variables" noStyle>
                  <VariableList />
                </Form.Item>

                <Form.Item name="skills" noStyle>
                  <SkillList />
                </Form.Item>

                {/* Tool Configuration */}
                <Form.Item name="tools" noStyle>
                  <ToolList />
                </Form.Item>
              </Flex>
            </Flex>
          </Form>
        </Col>
        <Col span={12} className="rb:h-full! rb:overflow-y-hidden">
          <RbCard
            title={t('application.debuggingAndPreview')}
            extra={
              <Space size={10}>
                <Button type="primary" ghost onClick={handleAddModel}>
                  + {t('application.addModel')}
                </Button>
                <div className="rb:w-8 rb:h-8 rb:cursor-pointer rb:bg-[url('@/assets/images/application/clean.svg')]" onClick={handleClearDebugging}></div>
              </Space>
            }
            headerType="borderless"
            headerClassName="rb:h-[56px]! rb:leading-[22px]!"
            titleClassName="rb:font-[MiSans-Bold] rb:font-bold"
            bodyClassName="rb:p-4! rb:pt-0! rb:h-[calc(100%-56px)]!"
            className="rb:h-full!"
          >
            <Chat
              data={values as Config}
              chatList={chatList}
              updateChatList={setChatList}
              handleSave={handleSave}
              chatVariables={chatVariables}
              handleEditVariables={handleOpenVariableConfig}
            />
          </RbCard>
        </Col>
      </Row>

      <ModelConfigModal
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
