import { useEffect, useState, useRef, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom';
import Card from './components/Card'
import { Form, Space, Row, Col, Button, Flex, App, Select } from 'antd'
import Tag, { type TagProps } from './components/Tag'
import CustomSelect from '@/components/CustomSelect';
import { getMultiAgentConfig, saveMultiAgentConfig } from '@/api/application';
import type { 
  Config,
  SubAgentModalRef,
  ChatData,
  SubAgentItem,
  ClusterRef,
  ModelConfigModalRef
} from './types'
import Chat from './components/Chat'
import RbCard from '@/components/RbCard/Card'
import SubAgentModal from './components/SubAgentModal'
import Empty from '@/components/Empty'
import RadioGroupCard from '@/components/RadioGroupCard'
import { getModelListUrl } from '@/api/models'
import ModelConfigModal from './components/ModelConfigModal'


const tagColors = ['processing', 'warning', 'default']
const MAX_LENGTH = 5;
const Cluster = forwardRef<ClusterRef>((_props, ref) => {
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

    console.log('params', params)

    return new Promise((resolve, reject) => {
      form.validateFields().then(() => {
        saveMultiAgentConfig(id as string, params)
          .then(() => {
            if (flag) {
              message.success(t('common.saveSuccess'))
            }
            resolve(true)
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

  const getData = () => {
    if (!id) {
      return
    }
    getMultiAgentConfig(id as string).then(res => {
      const response = res as Config
      setData(response)
      form.setFieldsValue({
        ...response,
      })
      setSubAgents(response.sub_agents || [])
    })
  }
  const handleSubAgentModal = (agent?: SubAgentItem) => {
    subAgentModalRef.current?.handleOpen(agent)
  }
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
  const handleDeleteSubAgent = (agent: SubAgentItem) => {
    setSubAgents(prev => prev.filter(item => item.agent_id !== agent.agent_id))
  }
  useImperativeHandle(ref, () => ({
    handleSave
  }))

  const modelConfigModalRef = useRef<ModelConfigModalRef>(null)
  const handleEditModelConfig = () => {
    modelConfigModalRef.current?.handleOpen('multi_agent', values.model_parameters)
  }
  const handleSaveModelConfig = (values: Config['model_parameters']) => {
    form.setFieldsValue({
      model_parameters: values
    })
  }

  return (
    <Row className="rb:h-[calc(100vh-64px)]">
      <Col span={12} className="rb:h-full rb:overflow-x-auto rb:border-r rb:border-[#DFE4ED] rb:p-[20px_16px_24px_16px]">
        <div className="rb:flex rb:items-center rb:justify-end rb:mb-5">
          <Button type="primary" onClick={() => handleSave()}>
            {t('common.save')}
          </Button>
        </div>
        <Form form={form} layout="vertical">
          <Space size={20} direction="vertical" style={{width: '100%'}}>
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
                />
              </Form.Item>
            </Card>

            <Card title={t('application.subAgentsManagement')}>
              <Flex align="center" justify="space-between">
                <div className="rb:font-regular rb:text-[#5B6167] rb:leading-5">{t('application.added')}: {subAgents.length}/{MAX_LENGTH}</div>
                <Button size="small" disabled={subAgents.length >= MAX_LENGTH} onClick={() => handleSubAgentModal()}>{t('application.addSubAgent')}</Button>
              </Flex>

              {subAgents.length === 0
                ? <Empty size={88} />
                : subAgents.map((agent, index) => (
                  <Flex key={index} align="center" justify="space-between"
                    className="rb:mt-4! rb:w-full! rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:p-[20px_31px_20px_20px]!"
                  >
                    <Flex className="rb:w-[calc(100%-80px)]!">
                      <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                        {agent.name?.[0]}
                      </div>
                      <div className="rb:flex rb:flex-col rb:justify-center rb:max-w-[calc(100%-60px)]">
                        {agent.name}
                        {agent.role && <div className="rb:font-regular rb:leading-5 rb:text-[#5B6167] rb:mt-1.5">{agent.role || '-'}</div>}
                        {agent.capabilities && <Flex wrap gap={8} className="rb:mt-4">{agent.capabilities.map((tag, tagIndex) => <Tag key={tagIndex} color={tagColors[tagIndex % tagColors.length] as TagProps['color']}>{tag}</Tag>)}</Flex>}
                      </div>
                    </Flex>

                    <Space>
                      <div
                        className="rb:w-8 rb:h-8 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]"
                        onClick={() => handleSubAgentModal(agent)}
                      ></div>
                      <div
                        className="rb:w-8 rb:h-8 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                        onClick={() => handleDeleteSubAgent(agent)}
                      ></div>
                    </Space>
                  </Flex>
                ))}
            </Card>

            {values?.orchestration_mode !== 'collaboration' && <Card title={t('application.masterConfig')}>
              <Form.Item
                label={t('application.model')}
                required={true}
              >
                <Row gutter={16}>
                  <Col span={16}>
                    <Form.Item name="default_model_config_id" noStyle>
                      <CustomSelect
                        url={getModelListUrl}
                        params={{ type: 'llm,chat', pagesize: 100 }}
                        valueKey="id"
                        labelKey="name"
                        hasAll={false}
                        style={{ width: '100%' }}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="model_parameters" noStyle>
                      <Button onClick={handleEditModelConfig}>{t('application.modelConfig')}</Button>
                    </Form.Item>
                  </Col>
                </Row>
              </Form.Item>
              <Form.Item
                name={['execution_config',"sub_agent_execution_mode"]}
                label={t('application.orchestrationMode')}
              >
                <Select
                  options={['sequential', 'parallel'].map((type) => ({
                    value: type,
                    label: t(`application.${type}`),
                  }))}
                />
              </Form.Item>
              <Form.Item
                name="aggregation_strategy"
                label={t('application.aggregationStrategy')}
              >
                <Select
                  options={['merge', 'vote', 'priority'].map((type) => ({
                    value: type,
                    label: t(`application.${type}`),
                  }))}
                />
              </Form.Item>
            </Card>}
          </Space>
        </Form>
      </Col>
      <Col span={12} className="rb:h-full rb:overflow-x-hidden rb:p-[20px_16px_24px_16px]">
        <RbCard height="100%" bodyClassName="rb:p-[0]! rb:h-full rb:overflow-hidden">
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
  )
})

export default Cluster