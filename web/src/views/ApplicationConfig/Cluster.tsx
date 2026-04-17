/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:29:33 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-07 20:37:43
 */
import { useEffect, useState, useRef, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom';
import { Form, Space, Row, Col, Button, Flex, App, Select, Spin } from 'antd'

import Card from './components/Card'
import Tag from './components/Tag'
import { getMultiAgentConfig, saveMultiAgentConfig, getApplicationList } from '@/api/application';
import type { 
  Config,
  SubAgentModalRef,
  ChatData,
  SubAgentItem,
  ClusterRef,
  ModelConfigModalRef,
  FeaturesConfigForm
} from './types'
import Chat from './components/Chat'
import RbCard from '@/components/RbCard/Card'
import SubAgentModal from './components/SubAgentModal'
import Empty from '@/components/Empty'
import RadioGroupCard from '@/components/RadioGroupCard'
import ModelSelect from '@/components/ModelSelect'
import ModelConfigModal from './components/ModelConfigModal'
import type { Application } from '@/views/ApplicationManagement/types'
// import FeaturesConfig from './components/FeaturesConfig'

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
  const values = Form.useWatch([], form)
  const [subAgents, setSubAgents] = useState<SubAgentItem[]>([])
  const [chatList, setChatList] = useState<ChatData[]>([
    {
      list: []
    },
  ])
  const [loading, setLoading] = useState(false)

  /**
   * Save cluster configuration
   * @param flag - Whether to show success message
   * @returns Promise that resolves when save is complete
   */
  const handleSave = (flag = true) => {
    if (!data) return Promise.resolve()
    if (!values.default_model_config_id && values.orchestration_mode === 'supervisor') {
      message.warning(t('common.selectPlaceholder', { title: t('application.model') }))
      return Promise.resolve()
    }

    const params = {
      id: data.id,
      app_id: data.app_id,
      ...values,
      sub_agents: (subAgents || []).map(item => ({
        ...item,
        priority: 1,
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
  useEffect(() => {
    getData()
  }, [id])

  /**
   * Fetch cluster configuration data
   */
  const getData = () => {
    if (!id) {
      return
    }
    setLoading(true)
    getMultiAgentConfig(id as string).then(res => {
      const response = res as Config
      setData(response)
      form.setFieldsValue({
        ...response,
      })
      let sub_agents = response.sub_agents || []
      if (sub_agents.length > 0) {
        console.log({ ids: sub_agents?.map(item => item.agent_id) })
        getApplicationList({ ids: sub_agents?.map(item => item.agent_id).join(',')})
          .then(res => {
            const applicationList = ((res as { items: Application[] }).items) || []

            setSubAgents(sub_agents.map(vo => {
              const filterVO = applicationList.find(item => item.id === vo.agent_id)
              if (filterVO) {
                return {
                  ...vo,
                  name: filterVO.name,
                  is_active: filterVO.is_active
                }
              }
              return vo
            }))
          })
      } else {
        setSubAgents(sub_agents)
      }
      onFeaturesLoad?.(response.features)
    })
    .finally(() => {
      setLoading(false)
    })
  }
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
    features: data?.features
  }))

  const modelConfigModalRef = useRef<ModelConfigModalRef>(null)
  /**
   * Open model configuration modal
   */
  const handleEditModelConfig = () => {
    modelConfigModalRef.current?.handleOpen('multi_agent', values.model_parameters)
  }
  /**
   * Save model configuration
   * @param values - Model parameters
   */
  const handleSaveModelConfig = (values: Config['model_parameters']) => {
    form.setFieldsValue({
      model_parameters: values
    })
  }
  // const handleSaveFeaturesConfig = (value: FeaturesConfigForm) => {
  //   form.setFieldValue('features', value)
  // }


  console.log('subAgents', subAgents)

  return (
    <>
      {loading && <Spin fullscreen></Spin>}
      <Row className="rb:h-full!" gutter={12}>
        <Col span={12}>
          <Form form={form} layout="vertical">
            <Flex gap={12} vertical>
              <Flex align="center" justify="end" className="rb:p-3! rb:bg-white rb:rounded-xl">
                {/* <FeaturesConfig value={values?.features as FeaturesConfigForm} refresh={handleSaveFeaturesConfig} /> */}
                <Button type="primary" onClick={() => handleSave()}>
                  {t('common.save')}
                </Button>
              </Flex>
              <Flex gap={12} vertical className="rb:h-[calc(100vh-158px)]! rb:overflow-y-auto!">
                <Form.Item name="features" hidden noStyle></Form.Item>
                <Card title={t('application.collaboration')}>
                  <Form.Item
                    name="orchestration_mode"
                    noStyle
                  >
                    <RadioGroupCard
                      options={['supervisor', 'collaboration'].map((type) => ({
                        value: type,
                        label: t(`application.${type}`),
                        labelDesc: t(`application.${type}Desc`),
                      }))}
                      allowClear={false}
                      block={true}
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
                    ? <div className="rb-border rb:rounded-xl rb:pt-4 rb:pb-6"><Empty size={88} /></div>
                    : <Flex vertical gap={12}>
                      {subAgents.map((agent, index) => (
                        <Flex key={index} align="center" justify="space-between"
                          className="rb:w-full! rb-border rb:rounded-xl rb:py-2.5! rb:pl-4! rb:pr-3!"
                        >
                          <Flex justify="center" vertical className="rb:max-w-[calc(100%-60px)]">
                            <div>
                              <span className="rb:text-[#212332] rb:leading-5">{agent.name}</span>
                              <Tag color={agent.is_active ? 'success' : 'warning'} className="rb:ml-2">
                                {agent.is_active ? t('common.enable') : t('common.deleted')}
                              </Tag>
                            </div>
                            {agent.role && <div className="rb:font-regular rb:leading-5 rb:text-[#5B6167] rb:text-[12px] rb:mt-1">{agent.role || '-'}</div>}
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

                {values?.orchestration_mode !== 'collaboration' && <Card title={t('application.masterConfig')}>
                  <Form.Item
                    label={<span className="rb:text-[#5B6167]">{t('application.model')}</span>}
                    required={true}
                    className="rb:mb-4!"
                  >
                    <Flex align="center" gap={12}>
                      <Form.Item name="default_model_config_id" noStyle>
                        <ModelSelect
                          params={{ type: 'llm,chat' }}
                          className="rb:w-full!"
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
                  <Form.Item
                    name={['execution_config', "sub_agent_execution_mode"]}
                    label={<span className="rb:text-[#5B6167]">{t('application.orchestrationMode')}</span>}
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
                    label={<span className="rb:text-[#5B6167]">{t('application.aggregationStrategy')}</span>}
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
                </Card>}
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
              data={data as Config}
              chatList={chatList}
              updateChatList={setChatList}
              handleSave={handleSave}
              source="multi_agent"
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
      </Row>
    </>
  )
})

export default Cluster