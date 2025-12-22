import { type FC, useEffect, useState, useRef, forwardRef, useImperativeHandle, type Key } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom';
import Card from './components/Card'
import { Form, Space, Row, Col, Button, Flex, App } from 'antd'
import type { DefaultOptionType } from 'antd/es/select'
import Tag, { type TagProps } from './components/Tag'
import CustomSelect from '@/components/CustomSelect';
import { getApplicationListUrl, getMultiAgentConfig, saveMultiAgentConfig } from '@/api/application';
import type { 
  Config,
  SubAgentModalRef,
  ChatData,
  SubAgentItem,
  ClusterRef
} from './types'
import Chat from './components/Chat'
import RbCard from '@/components/RbCard/Card'
import SubAgentModal from './components/SubAgentModal'
import Empty from '@/components/Empty'
import type { Application } from '@/views/ApplicationManagement/types'


const tagColors = ['processing', 'warning', 'default']
const MAX_LENGTH = 5;
const Cluster = forwardRef<ClusterRef, { application: Application }>(({application}, ref) => {
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
    const params = {
      ...values,
      sub_agents: (subAgents || []).map(item => ({
        ...item,
        priority: 1,
      }))
    }

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
  useEffect(() => {
    if (application) {
      form.setFieldsValue({
        name: application.name,
      })
    }
  }, [application])

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
    // setSubAgents(subAgents)
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
  const handleChange = (value: Key, option?: DefaultOptionType | DefaultOptionType[] | undefined) => {
    if (option && !Array.isArray(option)) {
      form.setFieldsValue({ master_agent_name: option.children })
    }
  }
  useImperativeHandle(ref, () => ({
    handleSave
  }))

  return (
    <Row className="rb:h-[calc(100vh-64px)]">
      <Col span={12} className="rb:h-full rb:overflow-x-auto rb:border-r rb:border-[#DFE4ED] rb:p-[20px_16px_24px_16px]">
        <div className="rb:flex rb:items-center rb:justify-end rb:mb-[20px]">
          <Button type="primary" onClick={() => handleSave()}>
            {t('common.save')}
          </Button>
        </div>
        <Form form={form} layout="vertical">
          <Space size={20} direction="vertical" style={{width: '100%'}}>
            <Card title={t('application.supervisorAgent')}>
              <Row gutter={18}>
                <Col span={24}>
                  <Form.Item 
                    name="master_agent_id" 
                    label={
                      <div className="rb:font-medium">
                        {t('application.agentName')}
                      </div>
                    } 
                    className="rb:mb-[20px]!"
                    rules={[{ required: true, message: t('common.pleaseSelect') }]}
                  >
                    <CustomSelect
                      url={getApplicationListUrl}
                      params={{ pagesize: 100, status: 'active', type: 'agent' }}
                      valueKey="id"
                      labelKey="name"
                      hasAll={false}
                      optionFilterProp="search"
                      showSearch={true}
                      onChange={handleChange}
                    />
                  </Form.Item>
                  <Form.Item name="master_agent_name" hidden />
                </Col>
              </Row>
            </Card>

            <Card title={t('application.subAgentsManagement')}>
              <Flex align="center" justify="space-between">
                <div className="rb:font-regular rb:text-[#5B6167] rb:leading-[20px]">{t('application.added')}: {subAgents.length}/{MAX_LENGTH}</div>
                <Button size="small" disabled={subAgents.length >= MAX_LENGTH} onClick={() => handleSubAgentModal()}>{t('application.addSubAgent')}</Button>
              </Flex>

              {subAgents.length === 0
                ? <Empty size={88} />
                : subAgents.map((agent, index) => (
                  <Flex key={index} align="center" justify="space-between" 
                    className="rb:mt-[16px]! rb:w-full! rb:border rb:border-[#DFE4ED] rb:rounded-[8px] rb:p-[20px_31px_20px_20px]!"
                  >
                    <Flex className="rb:w-[calc(100%-80px)]!">
                      <div className="rb:w-[48px] rb:h-[48px] rb:rounded-[8px] rb:mr-[13px] rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                        {agent.name?.[0]}
                      </div>
                      <div className="rb:flex rb:flex-col rb:justify-center rb:max-w-[calc(100%-60px)]">
                        {agent.name}
                        {agent.role && <div className="rb:font-regular rb:leading-[20px] rb:text-[#5B6167] rb:mt-[6px]">{agent.role || '-'}</div>}
                        {agent.capabilities && <Flex wrap gap={8} className="rb:mt-[16px]">{agent.capabilities.map((tag, tagIndex) => <Tag key={tagIndex} color={tagColors[tagIndex % tagColors.length] as TagProps['color']}>{tag}</Tag>)}</Flex>}
                      </div>
                    </Flex>

                    <Space>
                      <div 
                        className="rb:w-[32px] rb:h-[32px] rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]" 
                        onClick={() => handleSubAgentModal(agent)}
                      ></div>
                      <div 
                        className="rb:w-[32px] rb:h-[32px] rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]" 
                        onClick={() => handleDeleteSubAgent(agent)}
                      ></div>
                    </Space>
                  </Flex>
                ))}
            </Card>
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
    </Row>
  )
})

export default Cluster