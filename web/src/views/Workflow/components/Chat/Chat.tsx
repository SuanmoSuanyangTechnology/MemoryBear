import { forwardRef, useImperativeHandle, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { Input, Form, App, Space, Button, Collapse } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined } from '@ant-design/icons'
import CodeBlock from '@/components/Markdown/CodeBlock'

import ChatIcon from '@/assets/images/application/chat.png'
import RbDrawer from '@/components/RbDrawer';
import VariableConfigModal from './VariableConfigModal'
import { draftRun } from '@/api/application';
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import ChatSendIcon from '@/assets/images/application/chatSend.svg'
import dayjs from 'dayjs'
import type { ChatRef, VariableConfigModalRef, GraphRef } from '../../types'
import { type SSEMessage } from '@/utils/stream'
import type { Variable } from '../Properties/VariableList/types'
import styles from './chat.module.css'
import Markdown from '@/components/Markdown'

const Chat = forwardRef<ChatRef, { appId: string; graphRef: GraphRef }>(({ appId, graphRef }, ref) => {
  const { t } = useTranslation()
  const { message: messageApi } = App.useApp()
  const [form] = Form.useForm<{ message: string }>()
  const variableConfigModalRef = useRef<VariableConfigModalRef>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [chatList, setChatList] = useState<ChatItem[]>([])
  const [variables, setVariables] = useState<Variable[]>([])
  const [streamLoading, setStreamLoading] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)

  const handleOpen = () => {
    setOpen(true)
    getVariables()
  }
  const getVariables = () => {
    const nodes = graphRef.current?.getNodes()
    const list = nodes?.map(node => node.getData()) || []
    const startNodes = list.filter(vo => vo.type === 'start')
    if (startNodes.length) {
      const curVariables = startNodes[0].config.variables?.defaultValue

      curVariables.forEach((vo: Variable) => {
        if (typeof vo.default !== 'undefined') {
          vo.value = vo.default
        }
        const lastVo = variables.find(item => item.name === vo.name)
        if (lastVo?.value) {
          vo.value = lastVo.value
        }
      })
      setVariables(curVariables)
    }
  }
  const handleClose = () => {
    setOpen(false)
    setChatList([])
    setVariables([])
    setConversationId(null)
  }
  const handleEditVariables = () => {
    variableConfigModalRef.current?.handleOpen(variables)
  }
  const handleSave = (values: Variable[]) => {
    setVariables([...values])
  }
  const handleSend = () => {
    if (loading || !appId) return
    let isCanSend = true
    const params: Record<string, any> = {}
    if (variables.length > 0) {
      const needRequired: string[] = []
      variables.forEach(vo => {
        params[vo.name] = vo.value ?? vo.defaultValue

        if (vo.required && (params[vo.name] === null || params[vo.name] === undefined || params[vo.name] === '')) {
          isCanSend = false
          needRequired.push(vo.name)
        }
      })

      if (needRequired.length) {
        messageApi.error(`${needRequired.join(',')} ${t('workflow.variableRequired')}`)
      }
    }
    if (!isCanSend) {
      return
    }

    setLoading(true)
    const message = form.getFieldValue('message')
    setChatList(prev => [...prev, {
      role: 'user',
      content: message,
      created_at: Date.now(),
    }])
    setChatList(prev => [...prev, {
      role: 'assistant',
      content: '',
      created_at: Date.now(),
      subContent: [],
    }])

    const handleStreamMessage = (data: SSEMessage[]) => {
      data.forEach(item => {
        const { chunk, conversation_id, node_id, input, output, error, elapsed_time, status } = item.data as {
          chunk: string;
          conversation_id: string | null;
          node_id: string;
          node_name?: string;
          input?: any;
          output?: any;
          elapsed_time?: string;
          error?: any;
          state: Record<string, any>;
          status?: 'completed' | 'failed'
        };

        const node = graphRef.current?.getNodes().find(n => n.id === node_id);
        const { name, icon } = node?.getData() || {}

        console.log('node', node?.getData())

        switch(item.event) {
          case 'message':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  content: newList[lastIndex].content + chunk
                }
              }
              return newList
            })
            break
          case 'node_start':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                const newSubContent = newList[lastIndex].subContent || []
                const filterIndex = newSubContent.findIndex(vo => vo.id === node_id)
                if (filterIndex > -1) {
                  newSubContent[filterIndex] = {
                    ...newSubContent[filterIndex],
                    node_id: node_id,
                    node_name: name,
                    icon,
                    content: {},
                  }
                } else {
                  newSubContent.push({
                    id: node_id,
                    node_id: node_id,
                    node_name: name,
                    icon,
                    content: {},
                  })
                }
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  subContent: newSubContent
                }
              }
              return newList
            })
            break
          case 'node_end':
          case 'node_error':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                const newSubContent = newList[lastIndex].subContent || []
                const filterIndex = newSubContent.findIndex(vo => vo.node_id === node_id)
                if (filterIndex > -1 && newSubContent[filterIndex].content) {
                  newSubContent[filterIndex] = {
                    ...newSubContent[filterIndex],
                    content: {
                      input,
                      output,
                      error,
                    },
                    status: status || 'completed',
                    elapsed_time
                  }
                }
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  subContent: newSubContent
                }
              }
              return newList
            })
            break
          case 'workflow_end':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  status,
                  content: newList[lastIndex].content === '' ? null : newList[lastIndex].content
                }
              }
              return newList
            })
            setStreamLoading(false)
            break
        }

        if (conversation_id && conversationId !== conversation_id) {
          setConversationId(conversation_id)
        }
      })
    }

    form.setFieldValue('message', undefined)
    setStreamLoading(true)
    draftRun(appId, {
      message: message,
      variables: params,
      stream: true,
      conversation_id: conversationId
    }, handleStreamMessage)
      .catch((error) => {
        setChatList(prev => {
          const newList = [...prev]
          const lastIndex = newList.length - 1
          if (lastIndex >= 0) {
            newList[lastIndex] = {
              ...newList[lastIndex],
              status: 'failed',
              content: null,
              subContent: error.error
            }
          }
          return newList
        })
      })
      .finally(() => {
        setLoading(false)
        setStreamLoading(false)
      })
  }
  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  const getStatus = (status?: string) => {
    return status === 'completed' ? 'rb:text-[#369F21]' : status === 'failed' ? 'rb:text-[#FF5D34]' : 'rb:text-[#5B6167]'
  }

  console.log('chatList', chatList)
  return (
    <RbDrawer
      title={<div className="rb:flex rb:items-center rb:gap-2.5">
        {t('workflow.run')}
        {variables.length > 0 && <Space>
          <Button size="small" onClick={handleEditVariables}>变量</Button>
        </Space>}
      </div>}
      classNames={{
        body: 'rb:p-0!'
      }}
      open={open}
      onClose={handleClose}
    >
      <ChatContent
        classNames="rb:mx-[16px] rb:pt-[24px] rb:h-[calc(100%-76px)]"
        contentClassNames="rb:max-w-[400px]!'"
        empty={<Empty url={ChatIcon} title={t('application.chatEmpty')} isNeedSubTitle={false} size={[240, 200]} className="rb:h-full" />}
        data={chatList}
        streamLoading={streamLoading}
        labelPosition="bottom"
        labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
        errorDesc={t('application.ReplyException')}
        renderRuntime={(item, index) => {
          return (
            <div key={index} className="rb:w-100 rb:mb-2">
              <Collapse
                className={styles[item.status || 'default']}
                items={[{
                  key: 0,
                  label: <div className={getStatus(item.status)}>
                    {item.status === 'completed' ? <CheckCircleFilled className="rb:mr-1" /> : item.status === 'failed' ? <CloseCircleFilled className="rb:mr-1" /> : <LoadingOutlined className="rb:mr-1" />}
                    {t('application.workflow')}
                  </div>,
                  className: styles.collapseItem,
                  children: (
                    Array.isArray(item.subContent)
                    ? <Space size={8} direction="vertical" className="rb:w-full!">
                      {item.subContent?.map(vo => (
                        <Collapse
                          key={vo.node_id}
                          items={[{
                            key: vo.node_id,
                            label: <div className={clsx("rb:flex rb:justify-between rb:items-center", getStatus(vo.status))}>
                              <div className="rb:flex rb:items-center rb:gap-1 rb:flex-1">
                                {vo.icon && <img src={vo.icon} className="rb:size-4" />}
                                <div className="rb:wrap-break-word rb:line-clamp-1">{vo.node_name || vo.node_id}</div>
                              </div>
                              <span>
                                {typeof vo.elapsed_time == 'number' && <>{vo.elapsed_time?.toFixed(3)}ms</>}
                                {vo.status === 'completed' ? <CheckCircleFilled className="rb:ml-1" /> : vo.status === 'failed' ? <CloseCircleFilled className="rb:ml-1" /> : <LoadingOutlined className="rb:ml-1" />}
                              </span>
                            </div>,
                            className: styles.collapseItem,
                            children: (
                              <Space size={8} direction="vertical" className="rb:w-full!">
                                {vo.status === 'failed' &&
                                  <div className={clsx("rb:bg-[#F0F3F8] rb:rounded-md", getStatus(vo.status))}>
                                    <div className="rb:py-2 rb:px-3 rb:flex rb:justify-between rb:items-center rb:text-[12px]">
                                      {t(`workflow.error`)}
                                      <Button
                                        className="rb:py-0! rb:px-1! rb:text-[12px]!"
                                        size="small"
                                      >{t('common.copy')}</Button>
                                    </div>
                                    <div className="rb:pb-2 rb:px-3 rb:max-h-40 rb:overflow-auto">
                                      <Markdown content={vo.content?.error || ''} />
                                    </div>
                                  </div>
                                }
                                {['input', 'output'].map(key => (
                                  <div key={key} className="rb:bg-[#F0F3F8] rb:rounded-md">
                                    <div className="rb:py-2 rb:px-3 rb:flex rb:justify-between rb:items-center rb:text-[12px]">
                                      {t(`workflow.${key}`)}
                                      <Button
                                        className="rb:py-0! rb:px-1! rb:text-[12px]!"
                                        size="small"
                                      >{t('common.copy')}</Button>
                                    </div>
                                    <div className="rb:max-h-40 rb:overflow-auto">
                                      <CodeBlock
                                        size="small"
                                        value={typeof vo.content === 'object' && vo.content?.[key] ? JSON.stringify(vo.content[key], null, 2) : '{}'}
                                        needCopy={false}
                                        showLineNumbers={true}
                                      />
                                    </div>
                                  </div>
                                ))}
                              </Space>
                            )
                          }]}
                        />
                      ))}
                    </Space>
                      : <div className={clsx("rb:bg-[#FBFDFF] rb:rounded-md rb:py-2 rb:px-3 ", getStatus('failed'))}>
                      <Markdown content={item.subContent || ''}  />
                    </div>
                  )
                }]}
              />
            </div>
          )
        }}
      />
      <div className="rb:flex rb:items-center rb:gap-2.5 rb:p-4">
        <Form form={form} style={{width: 'calc(100% - 54px)'}}>
          <Form.Item name="message" className="rb:mb-0!">
            <Input 
              className="rb:h-11 rb:shadow-[0px_2px_8px_0px_rgba(33,35,50,0.1)]" 
              placeholder={t('application.chatPlaceholder')}
              onPressEnter={handleSend}
            />
          </Form.Item>
        </Form>
        <img src={ChatSendIcon} className={clsx("rb:w-11 rb:h-11 rb:cursor-pointer", {
          'rb:opacity-50': loading,
        })} onClick={handleSend} />
      </div>

      <VariableConfigModal
        ref={variableConfigModalRef}
        refresh={handleSave}
        variables={variables}
      />
    </RbDrawer>
  )
})

export default Chat
