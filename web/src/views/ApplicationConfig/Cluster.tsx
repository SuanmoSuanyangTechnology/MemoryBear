import { useEffect, useState, useRef, forwardRef, useImperativeHandle, useCallback, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom';
import { Form, Space, Row, Col, Button, Flex, App, Select, Spin, type SelectProps } from 'antd'

import Card from './components/Card'
import Tag from './components/Tag'
import RbTag from '@/components/Tag';
import { getMultiAgentConfig, saveMultiAgentConfig, getApplicationList } from '@/api/application';
import type { 
  Config,
  SubAgentModalRef,
  ChatData,
  SubAgentItem,
  ClusterRef,
  ModelConfigModalRef,
  FeaturesConfigForm,
  ChatVariableConfigModalRef,
  ModelConfig
} from './types'
import Chat from './components/Chat'
import RbCard from '@/components/RbCard/Card'
import SubAgentModal from './components/SubAgentModal'
import Empty from '@/components/Empty'
import RadioGroupCard from '@/components/RadioGroupCard'
import ModelSelect from '@/components/ModelSelect'
import ModelConfigModal from './components/ModelConfigModal'
import { defaultThinkingBudgetTokens } from './components/ModelConfigModal/constants'
import { configFields } from './components/ModelConfigModal/constants';
import type { Model } from '@/views/ModelManagement/types'
import ChatVariableConfigModal from './components/ChatVariableConfigModal'
import SupervisorCapabilityConfig from './components/SupervisorCapabilityConfig'
import { useClusterFeatureSync } from './hooks/useClusterFeatureSync'
import type { Application } from '@/views/ApplicationManagement/types'
import {
  normalizeSupervisorConfigForForm,
  normalizeSupervisorConfigForSave,
  enrichMissingModelFields,
} from './utils/clusterConfig'
import FeaturesConfig from './components/FeaturesConfig'
import { colorObj } from './constants';

const MAX_LENGTH = 5;
/**
 * Multi-agent cluster configuration component
 * Manages multi-agent orchestration, sub-agents, and collaboration modes
 */
const Cluster = forwardRef<ClusterRef, { onFeaturesLoad?: (features: FeaturesConfigForm | undefined) => void }>(({ onFeaturesLoad }, ref) => {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const { id } = useParams()
  const subAgentModalRef = useRef<SubAgentModalRef>(null)
  const [data, setData] = useState<Config | null>(null)
  const [subAgents, setSubAgents] = useState<SubAgentItem[]>([])
  const [chatList, setChatList] = useState<ChatData[]>([
    {
      list: []
    },
  ])
  const [loading, setLoading] = useState(false)
  const chatVariableConfigModalRef = useRef<ChatVariableConfigModalRef>(null)
  const values = Form.useWatch([], form)
  const {
    chatVariables,
    handleSaveChatVariable,
    handleSaveFeaturesConfig,
  } = useClusterFeatureSync({
    form,
    values,
    chatListLength: chatList.length,
    setChatList,
    onFeaturesLoad,
  })

  const handleOpenVariableConfig = () => {
    chatVariableConfigModalRef.current?.handleOpen(chatVariables)
  }

  /**
   * Save cluster configuration
   * @param flag - Whether to show success message
   * @returns Promise that resolves when save is complete
   */
  const handleSave = (flag = true) => {
    if (!data) return Promise.resolve()
    if (!values.default_model_config_id && values.orchestration_mode !== 'collaboration') {
      message.warning(t('common.selectPlaceholder', { title: t('application.model') }))
      return Promise.resolve()
    }
    const params = {
      id: data.id,
      app_id: data.app_id,
      ...values,
      supervisor_config: normalizeSupervisorConfigForSave(
        values.supervisor_config,
        data.supervisor_config
      ),
      sub_agents: (subAgents || []).map(item => ({
        ...item,
        name: typeof item.name === 'string' ? item.name : (item.name as ReactElement)?.props?.['data-name']
      }))
    }

    return new Promise((resolve, reject) => {
      form.validateFields().then(() => {
        saveMultiAgentConfig(id as string, params)
          .then((res) => {
            if (flag) {
              message.success({ content: t('common.saveSuccess'), duration: 1 })
            }
            resolve(res)
          })
          .catch(error => {
            reject(error)
          })
      })
        .catch(error => {
          reject(error)
        })
    })
  }

  /**
   * Fetch cluster configuration data
   */
  const getData = useCallback(() => {
    if (!id) {
      return
    }
    setChatList([{ list: [] }])
    setLoading(true)
    getMultiAgentConfig(id as string).then(async res => {
      const response = await enrichMissingModelFields(res as Config)
      setData(response)
      form.setFieldsValue({
        ...response,
        supervisor_config: normalizeSupervisorConfigForForm(response.supervisor_config),
      })
      const sub_agents = response.sub_agents || []
      if (sub_agents.length > 0) {
        getApplicationList({ ids: sub_agents?.map(item => item.agent_id).join(',')})
          .then(res => {
            const applicationList = ((res as { items: Application[] }).items) || []

            setSubAgents(sub_agents.map(vo => {
              const filterVO = applicationList.find(item => item.id === vo.agent_id)
              if (filterVO) {
                return {
                  ...vo,
                  name: (
                    <Space align="center" size={8} data-name={filterVO.name}>
                      {filterVO.name}
                      <RbTag color={colorObj[filterVO.type]}>{t(`application.${filterVO.type}`)}</RbTag>
                    </Space>
                  ),
                  is_active: filterVO.is_active
                }
              }
              return vo
            }))
          })
      } else {
        setSubAgents(sub_agents)
      }
      onFeaturesLoad?.(response?.supervisor_config?.features)
    })
    .finally(() => {
      setLoading(false)
    })
  }, [onFeaturesLoad, form, id])
  useEffect(() => {
    getData()
  }, [id, getData])
  /**
   * Open sub-agent modal for add or edit
   * @param agent - Optional agent data for edit mode
   */
  const handleSubAgentModal = (agent?: SubAgentItem) => {
    subAgentModalRef.current?.handleOpen(agent)
  }
  /**
   * Refresh sub-agents list after add or edit
   * @param agent - Agent data to add or update
   */
  const refreshSubAgents = (agent: SubAgentItem) => {
    const index = subAgents.findIndex(item => item.agent_id === agent.agent_id)
      const newSubAgents = [...subAgents]
    if (index === -1) {
      if (subAgents.length >= MAX_LENGTH) {
        message.warning(t('application.subAgentMaxLength', {maxLength: MAX_LENGTH}))
        return
      }
      setSubAgents([...newSubAgents, agent])
    } else {
      newSubAgents[index] = agent
      setSubAgents(newSubAgents)
    }
  }
  /**
   * Delete sub-agent from list
   * @param agent - Agent to delete
   */
  const handleDeleteSubAgent = (agent: SubAgentItem) => {
    setSubAgents(prev => prev.filter(item => item.agent_id !== agent.agent_id))
  }
  useImperativeHandle(ref, () => ({
    handleSave,
    features: data?.supervisor_config?.features
  }))

  const modelConfigModalRef = useRef<ModelConfigModalRef>(null)
  /**
   * Open model configuration modal
   */
  const handleEditModelConfig = () => {
    modelConfigModalRef.current?.handleOpen('multi_agent', {
      ...(values?.model_parameters || {}),
      default_model_config_id: values?.default_model_config_id
    })
  }
  /**
   * Disable file upload capabilities when the selected model changes
   */
  const resetFileUploadWhenModelChanges = (nextModelConfigId?: string) => {
    if (nextModelConfigId === values?.default_model_config_id) return

    const fileUpload = { ...values?.supervisor_config?.features?.file_upload }
    Object.keys(fileUpload).forEach(key => {
      if (key.includes('enabled')) {
        (fileUpload as Record<string, unknown>)[key] = false
      }
    })
    form.setFieldValue(['supervisor_config', 'features', 'file_upload'], fileUpload)
    message.warning(t('application.resetFeaturesTip'))
  }

  /**
   * Save model configuration
   * @param vo - Model parameters
   */
  const handleSaveModelConfig = (vo: ModelConfig) => {
    const { default_model_config_id, input_modalities, output_modalities, ...rest } = vo
    resetFileUploadWhenModelChanges(default_model_config_id)
    form.setFieldsValue({
      default_model_config_id,
      input_modalities,
      output_modalities,
      model_parameters: {...rest}
    })
  }
  const handleChangeModel: SelectProps['onChange'] = (value, option) => {
    const model = option as Model | undefined
    const nextModelConfigId = typeof value === 'string' ? value : undefined
    const features = model?.features ?? []

    resetFileUploadWhenModelChanges(nextModelConfigId)
    form.setFieldsValue({
      input_modalities: model?.input_modalities,
      output_modalities: model?.output_modalities,
      model_parameters: {
        ...(values?.model_parameters || {}),
        features,
        thinking_budget_tokens: (features?.includes('thinking') || features?.includes('thinking_only')) ? defaultThinkingBudgetTokens : undefined,
      },
    })
  }
  const handleChangeMode = (value?: string | null) => {
    if (value === 'supervisor_loop') {
      const model_parameters: ModelConfig = {};
      configFields.forEach(item => {
        model_parameters[item.key] = item.defaultValue;
      })
      form.setFieldValue('model_parameters', model_parameters)
    }
    form.setFieldValue('supervisor_config', null)
  }
  console.log('chatList', chatList, values)
  return (
    <>
      {loading && <Spin fullscreen></Spin>}
      <Row className="rb:h-full!" gutter={12}>
        <Col span={12} className="rb:h-full! rb:overflow-y-hidden">
          <Form form={form} layout="vertical" className="rb:h-full! rb:overflow-hidden!">
            <Flex gap={12} vertical className="rb:h-full! rb:overflow-hidden!">
              <Flex gap={12} align="center" justify="end" className="rb:p-3! rb:bg-white rb:rounded-xl">
                {values?.orchestration_mode === 'supervisor_loop' &&
                  <>
                    <FeaturesConfig
                      value={values?.supervisor_config?.features as FeaturesConfigForm}
                      input_modalities={values?.input_modalities || []}
                      refresh={handleSaveFeaturesConfig}
                      chatVariables={chatVariables}
                    />
                    <Form.Item name="input_modalities" hidden noStyle />
                    <Form.Item name="output_modalities" hidden noStyle />
                    <Form.Item name={['supervisor_config', 'features']} hidden noStyle />
                  </>
                }
                <Button type="primary" onClick={() => handleSave()}>
                  {t('common.save')}
                </Button>
              </Flex>
              <Flex gap={12} vertical className="rb:flex-1! rb:overflow-y-auto!">
                <Card title={t('application.collaboration')}>
                  <Form.Item
                    name="orchestration_mode"
                    noStyle
                  >
                    <RadioGroupCard
                      options={['supervisor', 'supervisor_loop', 'collaboration'].map((type) => ({
                        value: type,
                        label: t(`application.${type}`),
                        labelDesc: t(`application.${type}Desc`),
                      }))}
                      allowClear={false}
                      block={true}
                      onChange={handleChangeMode}
                    />
                  </Form.Item>
                </Card>

                <Card
                  title={<>
                    {t('application.subAgentsManagement')}
                    <span className="rb:font-medium rb:font-[PingFangSC,PingFang_SC]! rb:text-[14px]!"> ({subAgents.length}/{MAX_LENGTH})</span>
                  </>}
                  extra={<Button className="rb:py-0! rb:px-2! rb:h-6!" disabled={subAgents.length >= MAX_LENGTH} onClick={() => handleSubAgentModal()}>+ {t('application.addSubAgent')}</Button>}
                >
                  {subAgents.length === 0
                    ? <Empty size={88} className="rb-border rb:rounded-xl rb:pt-4! rb:pb-6!" />
                    : <Flex vertical gap={12}>
                      {subAgents.map((agent, index) => (
                        <Flex key={index} align="center" justify="space-between"
                          className="rb:w-full! rb-border rb:rounded-xl rb:py-2.5! rb:pl-4! rb:pr-3!"
                        >
                          <Flex justify="center" vertical className="rb:max-w-[calc(100%-60px)]">
                            <div>
                              <span className="rb:text-gray-800 rb:leading-5">
                                {agent.name}
                              </span>
                              <Tag color={agent.is_active ? 'success' : 'warning'} className="rb:ml-2">
                                {agent.is_active ? t('common.enable') : t('common.deleted')}
                              </Tag>
                            </div>
                            {agent.role && <div className="rb:font-regular rb:leading-5 rb:text-gray-600 rb:text-[12px] rb:mt-1">{agent.role || '-'}</div>}
                            {agent.capabilities && <Flex wrap gap={8} className="rb:mt-2.5!">
                              {agent.capabilities.map((tag, tagIndex) => <Tag key={tagIndex} color="dark" className="rb:py-0!">{tag}</Tag>)}
                            </Flex>}
                          </Flex>

                          <Space>
                            <div
                              className="rb:size-6 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]"
                              onClick={() => handleSubAgentModal(agent)}
                            ></div>
                            <div
                              className="rb:size-6 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                              onClick={() => handleDeleteSubAgent(agent)}
                            ></div>
                          </Space>
                        </Flex>
                      ))}
                    </Flex>
                  }
                </Card>

                {values?.orchestration_mode !== 'collaboration' &&
                  <Card title={t('application.masterConfig')}>
                    <Form.Item
                      label={<span className="rb:text-gray-600">{t('application.model')}</span>}
                      required={true}
                      className={values?.orchestration_mode === 'supervisor' ? "rb:mb-4!" : 'rb:mb-0!'}
                    >
                      <Flex align="center" gap={12}>
                        <Form.Item name="default_model_config_id" noStyle>
                          <ModelSelect
                            params={{ type: 'llm' }}
                            className="rb:w-full!"
                            onChange={handleChangeModel}
                          />
                        </Form.Item>
                        <Form.Item name="model_parameters" noStyle>
                          <Button
                            className="rb:w-33"
                            icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/application/set.svg')]"></div>}
                            onClick={handleEditModelConfig}
                          >{t('application.modelConfig')}</Button>
                        </Form.Item>
                      </Flex>
                    </Form.Item>
                    {values?.orchestration_mode === 'supervisor' &&
                      <>
                        <Form.Item
                          name={['execution_config', "sub_agent_execution_mode"]}
                          label={<span className="rb:text-gray-600">{t('application.orchestrationMode')}</span>}
                          className="rb:mb-4!"
                        >
                          <Select
                            options={['sequential', 'parallel'].map((type) => ({
                              value: type,
                              label: t(`application.${type}`),
                            }))}
                            placeholder={t('common.pleaseSelect')}
                          />
                        </Form.Item>
                        <Form.Item
                          name="aggregation_strategy"
                          label={<span className="rb:text-gray-600">{t('application.aggregationStrategy')}</span>}
                          className="rb:mb-0!"
                        >
                          <Select
                            options={['merge', 'vote', 'priority'].map((type) => ({
                              value: type,
                              label: t(`application.${type}`),
                            }))}
                            placeholder={t('common.pleaseSelect')}
                          />
                        </Form.Item>
                      </>
                    }
                  </Card>
                }
                {values?.orchestration_mode === 'supervisor_loop' &&
                  <SupervisorCapabilityConfig />
                }
              </Flex>
            </Flex>
          </Form>
        </Col>
        <Col span={12} className="rb:h-full! rb:overflow-y-hidden">
          <RbCard
            title={t('application.debuggingAndPreview')}
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
              source="multi_agent"
              chatVariables={chatVariables}
              handleEditVariables={handleOpenVariableConfig}
            />
          </RbCard>
        </Col>

        <SubAgentModal
          ref={subAgentModalRef}
          refresh={refreshSubAgents}
        />
        <ModelConfigModal
          data={values as Config}
          ref={modelConfigModalRef}
          refresh={handleSaveModelConfig}
        />
        <ChatVariableConfigModal
          ref={chatVariableConfigModalRef}
          refresh={handleSaveChatVariable}
        />
      </Row>
    </>
  )
})

export default Cluster