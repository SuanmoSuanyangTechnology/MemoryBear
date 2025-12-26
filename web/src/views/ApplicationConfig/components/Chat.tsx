import { type FC, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'
import { Input, Form } from 'antd'
import ChatIcon from '@/assets/images/application/chat.png'
import ChatSendIcon from '@/assets/images/application/chatSend.svg'
import DebuggingEmpty from '@/assets/images/application/debuggingEmpty.png'
import type { ChatData, Config } from '../types'
import { runCompare, draftRun } from '@/api/application'
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import { type SSEMessage } from '@/utils/stream'

interface ChatProps {
  chatList: ChatData[];
  data: Config;
  updateChatList: React.Dispatch<React.SetStateAction<ChatData[]>>;
  handleSave: (flag?: boolean) => Promise<any>;
  source?: 'multi_agent' | 'agent';
}
const Chat: FC<ChatProps> = ({ chatList, data, updateChatList, handleSave, source = 'agent' }) => {
  const { t } = useTranslation();
  const [form] = Form.useForm<{ message: string }>()
  const [loading, setLoading] = useState(false)
  const [isCluster, setIsCluster] = useState(source === 'multi_agent')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [compareLoading, setCompareLoading] = useState(false)

  useEffect(() => {
    setIsCluster(source === 'multi_agent')
  }, [source])

  const addUserMessage = (message: string) => {
    const newUserMessage: ChatItem = {
      role: 'user',
      content: message,
      created_at: Date.now(),
    };
    updateChatList(prev => prev.map(item => ({
      ...item,
      list: [...(item.list || []), newUserMessage]
    })))
  }
  const addAssistantMessage = () => {
    const assistantMessage: ChatItem = {
      role: 'assistant',
      content: '',
      created_at: Date.now(),
    };
    
    if (isCluster) {
      updateChatList(prev => prev.map(item => ({
        ...item,
        list: [...(item.list || []), assistantMessage]
      })))
    } else {
      const assistantMessages: Record<string, ChatItem> = {}
      chatList.forEach(item => {
        assistantMessages[item.model_config_id as string] = assistantMessage
      })
      updateChatList(prev => prev.map(item => ({
        ...item,
        list: [...(item.list || []), assistantMessages[item.model_config_id as string]]
      })))
    }
  }
  const updateAssistantMessage = (content?: string, model_config_id?: string, conversation_id?: string) => {
    if (!content || !model_config_id) return
    updateChatList(prev => {
      const targetIndex = prev.findIndex(item => item.model_config_id === model_config_id);
      if (targetIndex !== -1) {
        const modelChatList = [...prev]
        const curModelChat = modelChatList[targetIndex]
        const curChatMsgList = curModelChat.list || []
        const lastMsg = curChatMsgList[curChatMsgList.length - 1]
        if (lastMsg.role === 'assistant') {
          modelChatList[targetIndex] = {
            ...modelChatList[targetIndex],
            conversation_id: conversation_id,
            list: [
              ...curChatMsgList.slice(0, curChatMsgList.length - 1),
              {
                ...lastMsg,
                content: lastMsg.content + content
              }
            ]
          }
        }
        return [...modelChatList]
      }
      return prev;
    })
  }
  const updateErrorAssistantMessage  = (message_length: number, model_config_id?: string) => {
    if (message_length > 0 || !model_config_id) return

    updateChatList(prev => {
      const targetIndex = prev.findIndex(item => item.model_config_id === model_config_id);
      if (targetIndex > -1) {
        const modelChatList = [...prev]
        const curModelChat = modelChatList[targetIndex]
        const curChatMsgList = curModelChat.list || []
        const lastMsg = curChatMsgList[curChatMsgList.length - 1]
        if (lastMsg.role === 'assistant') {
          modelChatList[targetIndex] = {
            ...modelChatList[targetIndex],
            list: [
              ...curChatMsgList.slice(0, curChatMsgList.length - 1),
              {
                ...lastMsg,
                content: null
              }
            ]
          }
        }
        return [...modelChatList]
      }

      return prev
    })
  }
  const handleSend = () => {
    if (loading) return
    setLoading(true)
    setCompareLoading(true)
    handleSave(false)
      .then(() => {
        const message = form.getFieldValue('message')
        if (!message?.trim()) return
        
        addUserMessage(message)
        form.setFieldsValue({ message: undefined })
        addAssistantMessage()

        const handleStreamMessage = (data: SSEMessage[]) => {
          setCompareLoading(false)

          data.map(item => {
            const { model_config_id, conversation_id, content, message_length } = item.data as { model_config_id: string; conversation_id: string; content: string; message_length: number };

            switch(item.event) {
              case 'model_message':
                updateAssistantMessage(content, model_config_id, conversation_id)
                break;
              case 'model_end':
                updateErrorAssistantMessage(message_length, model_config_id)
                break;
              case 'compare_end':
                setLoading(false);
                break;
            }
          })
        };

        setTimeout(() => {
          runCompare(data.app_id, {
            message,
            models: chatList.map(item => ({
              model_config_id: item.model_config_id,
              label: item.label,
              model_parameters: item.model_parameters,
              conversation_id: item.conversation_id
            })),
            variables: {},
            "parallel": true,
            "stream": true,
            "timeout": 60,
          }, handleStreamMessage)
            .finally(() => setLoading(false));
        }, 0)
      })
      .catch(() => {
        setLoading(false)
        setCompareLoading(false)
      })
  }

  const addClusterAssistantMessage = () => {
    const assistantMessage: ChatItem = {
      role: 'assistant',
      content: '',
      created_at: Date.now(),
    };
    updateChatList(prev => prev.map(item => ({
      ...item,
      list: [...(item.list || []), assistantMessage]
    })))
  }
  const updateClusterAssistantMessage = (content?: string) => {
    if (!content) return
    updateChatList(prev => {
      const modelChatList = [...prev]
      const curModelChat = modelChatList[0]
      const curChatMsgList = curModelChat.list || []
      const lastMsg = curChatMsgList[curChatMsgList.length - 1]
      if (lastMsg.role === 'assistant') {
        modelChatList[0] = {
          ...modelChatList[0],
          list: [
            ...curChatMsgList.slice(0, curChatMsgList.length - 1),
            {
              ...lastMsg,
              content: lastMsg.content + content
            }
          ]
        }
      }
      return [...modelChatList]
    })
  }
  const updateClusterErrorAssistantMessage  = (message_length: number) => {
    if (message_length > 0) return

    updateChatList(prev => {
      const modelChatList = [...prev]
      const curModelChat = modelChatList[0]
      const curChatMsgList = curModelChat.list || []
      const lastMsg = curChatMsgList[curChatMsgList.length - 1]
      if (lastMsg.role === 'assistant') {
        modelChatList[0] = {
          ...modelChatList[0],
          list: [
            ...curChatMsgList.slice(0, curChatMsgList.length - 1),
            {
              ...lastMsg,
              content: null
            }
          ]
        }
      }
      return [...modelChatList]
    })
  }
  const handleClusterSend = () => {
    if (loading) return
    setLoading(true)
    setCompareLoading(true)
    handleSave(false)
      .then(() => {
        const message = form.getFieldValue('message')
        if (!message || message.trim() === '') return
        addUserMessage(message)
        form.setFieldsValue({ message: undefined })
        addClusterAssistantMessage()

        const handleStreamMessage = (data: SSEMessage[]) => {
          setCompareLoading(false)

          data.map(item => {
            const { conversation_id, content, message_length } = item.data as { conversation_id: string, content: string, message_length: number };

            switch(item.event) {
              case 'start':
                if (conversation_id && conversationId !== conversation_id) {
                  setConversationId(conversation_id);
                }
                break
              case 'message':
                updateClusterAssistantMessage(content)
                if (conversation_id && conversationId !== conversation_id) {
                  setConversationId(conversation_id);
                }
                break;
              case 'model_end':
                updateClusterErrorAssistantMessage(message_length)
                break;
              case 'compare_end':
                setLoading(false);
                break;
            }
          })
        };

        setTimeout(() => {
            draftRun(
              data.app_id,
              { 
                message, 
                conversation_id: conversationId, 
                stream: true 
              }, 
              handleStreamMessage
            )
              .finally(() => setLoading(false))
        }, 0)
      })
      .catch(() => {
        setLoading(false)
        setCompareLoading(false)
      })
  }

  const handleDelete = (index: number) => {
    updateChatList(chatList.filter((_, voIndex) => voIndex !== index))
  }

  return (
    <div className="rb:relative rb:h-[calc(100vh-110px)]">
      {chatList.length === 0
        ? <Empty 
          url={DebuggingEmpty} 
          size={[300, 200]}
          title={t('application.debuggingEmpty')} 
          subTitle={t('application.debuggingEmptyDesc')} 
          className="rb:h-full"
        />
      : <>
        <div className={clsx(`rb:grid rb:grid-cols-${chatList.length} rb:overflow-hidden rb:w-full`, {
          'rb:h-[calc(100vh-236px)]': !isCluster,
          'rb:h-[calc(100%-76px)]': isCluster,
        })}>
          {chatList.map((chat, index) => (
            <div key={index} className={clsx('rb:h-full rb:flex rb:flex-col', {
              "rb:border-r rb:border-[#DFE4ED]": index !== chatList.length - 1 && chatList.length > 1,
            })}>
              {chat.label &&
                <div className={clsx(
                  "rb:grid rb:bg-[#F0F3F8] rb:text-center rb:flex-[0_0_auto]",
                  {
                    'rb:rounded-tr-xl': index === chatList.length - 1,
                    'rb:rounded-tl-xl': index === 0,
                  }
                )}>
                  <div className='rb:relative rb:p-[10px_12px] rb:overflow-hidden'>
                    <div className="rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:w-[calc(100%-24px)]">{chat.label}</div>
                    <div 
                      className="rb:w-4 rb:h-4 rb:cursor-pointer rb:absolute rb:top-3 rb:right-3 rb:bg-cover rb:bg-[url('@/assets/images/close.svg')] rb:hover:bg-[url('@/assets/images/close_hover.svg')]" 
                      onClick={() => handleDelete(index)}
                    ></div>
                  </div>
                </div>
              }
              <ChatContent
                classNames={{
                  'rb:mx-[16px] rb:pt-[24px]': true,
                  'rb:h-[calc(100vh-186px)]': isCluster,
                  'rb:h-[calc(100vh-286px)]': !isCluster,
                }} 
                contentClassNames={{
                  'rb:max-w-[400px]!': chatList.length === 1,
                  'rb:max-w-[260px]!': chatList.length === 2,
                  'rb:max-w-[150px]!': chatList.length === 3,
                  'rb:max-w-[108px]!': chatList.length === 4,
                }}
                empty={<Empty url={ChatIcon} title={t('application.chatEmpty')} isNeedSubTitle={false} size={[240, 200]} className="rb:h-full" />}
                data={chat.list || []}
                streamLoading={compareLoading}
                labelPosition="top"
                labelFormat={(item) => item.role === 'user' ? t('application.you') : chat.label}
                errorDesc={t('application.ReplyException')}
              />
              
            </div>
          ))}
        </div>
        <div className="rb:flex rb:items-center rb:gap-2.5 rb:p-4">
          <Form form={form} style={{width: 'calc(100% - 54px)'}}>
            <Form.Item name="message" className="rb:mb-0!">
              <Input 
                className="rb:h-11 rb:shadow-[0px_2px_8px_0px_rgba(33,35,50,0.1)]" 
                placeholder={t('application.chatPlaceholder')}
                onPressEnter={isCluster ? handleClusterSend : handleSend}
              />
            </Form.Item>
          </Form>
          <img src={ChatSendIcon} className={clsx("rb:w-11 rb:h-11 rb:cursor-pointer", {
            'rb:opacity-50': loading,
          })} onClick={isCluster ? handleClusterSend : handleSend} />
        </div>
      </>
      }
    </div>
  )
}

export default Chat;