import { forwardRef, useImperativeHandle, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { Input, Form, App } from 'antd'
import { Space, Button } from 'antd'

import ChatIcon from '@/assets/images/application/chat.png'
import RbDrawer from '@/components/RbDrawer';
import VariableConfigModal from './VariableConfigModal'
import { draftRun } from '@/api/application';
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import ChatSendIcon from '@/assets/images/application/chatSend.svg'
import dayjs from 'dayjs'
import type { ChatRef, VariableConfigModalRef, StartVariableItem, GraphRef } from '../../types'
import { type SSEMessage } from '@/utils/stream'

const Chat = forwardRef<ChatRef, { appId: string; graphRef: GraphRef }>(({ appId, graphRef }, ref) => {
  const { t } = useTranslation()
  const { message: messageApi } = App.useApp()
  const [form] = Form.useForm<{ message: string }>()
  const variableConfigModalRef = useRef<VariableConfigModalRef>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [chatList, setChatList] = useState<ChatItem[]>([])
  const [variables, setVariables] = useState<StartVariableItem[]>([])
  const [streamLoading, setStreamLoading] = useState(false)

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

      curVariables.forEach((vo: StartVariableItem) => {
        if (vo.default) {
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
  }
  const handleEditVariables = () => {
    variableConfigModalRef.current?.handleOpen(variables)
  }
  const handleSave = (values: StartVariableItem[]) => {
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
    }])

    const handleStreamMessage = (data: SSEMessage[]) => {
      setStreamLoading(false)

      data.forEach(item => {
        const { chunk } = item.data as { chunk: string; };

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
          case 'workflow_end':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  content: newList[lastIndex].content === '' ? null : newList[lastIndex].content
                }
              }
              return newList
            })
            setStreamLoading(false)
            break
        }
      })
    }

    form.setFieldValue('message', undefined)
    draftRun(appId, {
      message: message,
      variables: params,
      stream: true
    }, handleStreamMessage)
      .finally(() => {
        setLoading(false)
      })
  }
  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

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
        classNames={{
          'rb:mx-[16px] rb:pt-[24px] rb:h-[calc(100%-76px)]': true,
          
        }}
        contentClassNames="rb:max-w-[400px]!'"
        empty={<Empty url={ChatIcon} title={t('application.chatEmpty')} isNeedSubTitle={false} size={[240, 200]} className="rb:h-full" />}
        data={chatList}
        streamLoading={streamLoading}
        labelPosition="bottom"
        labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
        errorDesc={t('application.ReplyException')}
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
