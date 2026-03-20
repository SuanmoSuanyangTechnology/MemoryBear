/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:39 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 11:38:45
 */
/**
 * Chat debugging component for application testing
 * Supports both single agent and multi-agent cluster modes
 * Provides real-time streaming responses and conversation history
 */

import { type FC, useEffect, useState, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom'
import clsx from 'clsx'
import { App, Flex } from 'antd';
import { SettingOutlined } from '@ant-design/icons'

import ChatIcon from '@/assets/images/application/chat.png'
import DebuggingEmpty from '@/assets/images/application/debuggingEmpty.png'
import type { ChatData, Config, FeaturesConfigForm } from '../types'
import { runCompare, draftRun } from '@/api/application'
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import { type SSEMessage } from '@/utils/stream'
import ChatInput from '@/components/Chat/ChatInput'
import ChatToolbar from '@/components/Chat/ChatToolbar'
import type { ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import type { Variable } from './VariableList/types'


/**
 * Component props
 */
interface ChatProps {
  /** List of chat configurations for comparison */
  chatList: ChatData[];
  /** Application configuration data */
  data: Config;
  /** Update chat list state */
  updateChatList: React.Dispatch<React.SetStateAction<ChatData[]>>;
  /** Save configuration before running */
  handleSave: (flag?: boolean) => Promise<unknown>;
  /** Source type: multi-agent cluster or single agent */
  source?: 'multi_agent' | 'agent';
  /** chatVariables prop */
  chatVariables?: Variable[];
  handleEditVariables?: () => void;
}


/**
 * Chat debugging component
 * Allows testing application with different model configurations side-by-side
 */
const Chat: FC<ChatProps> = ({
  chatList, data, updateChatList, handleSave, source = 'agent', chatVariables,
  handleEditVariables
}) => {
  const { t } = useTranslation();
  const { id } = useParams()
  const { message: messageApi } = App.useApp()
  const toolbarRef = useRef<ChatToolbarRef>(null)
  const [loading, setLoading] = useState(false)
  const [isCluster, setIsCluster] = useState(source === 'multi_agent')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [compareLoading, setCompareLoading] = useState(false)
  const [fileList, setFileList] = useState<any[]>([])
  const [message, setMessage] = useState<string | undefined>(undefined)
  const [features, setFeatures] = useState<FeaturesConfigForm>({} as FeaturesConfigForm)

  useEffect(() => {
    setCompareLoading(false)
    setLoading(false)
  }, [chatList.map(item => item.label).join(',')])

  useEffect(() => {
    if (data?.features) setFeatures(data.features)
  }, [data?.features])

  useEffect(() => {
    setIsCluster(source === 'multi_agent')
    toolbarRef.current?.setFiles([])
    setMessage(undefined)
  }, [source])

  /** Add user message to all chat lists */
  const addUserMessage = (message: string, files: any[]) => {
    const newUserMessage: ChatItem = {
      role: 'user',
      content: message,
      created_at: Date.now(),
      meta_data: {
        files
      },
    };
    updateChatList(prev => prev.map(item => ({
      ...item,
      list: [...(item.list || []), newUserMessage]
    })))
  }
  /** Add empty assistant message placeholder */
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
  /** Update assistant message with streaming content */
  const updateAssistantMessage = (content?: string, model_config_id?: string, conversation_id?: string, audio_url?: string) => {
    if ((!content && !audio_url) || !model_config_id) return
    updateChatList(prev => {
      const targetIndex = prev.findIndex(item => item.model_config_id === model_config_id);
      if (targetIndex !== -1) {
        const modelChatList = [...prev]
        const curModelChat = modelChatList[targetIndex]
        const curChatMsgList = curModelChat.list || []
        const lastMsg = curChatMsgList[curChatMsgList.length - 1]
        if (lastMsg && lastMsg.role === 'assistant') {
          modelChatList[targetIndex] = {
            ...modelChatList[targetIndex],
            conversation_id,
            list: [
              ...curChatMsgList.slice(0, curChatMsgList.length - 1),
              {
                ...lastMsg,
                content: lastMsg.content + (content || ''),
                meta_data: { audio_url }
              }
            ]
          }
        }
        return [...modelChatList]
      }
      return prev;
    })
  }
  /** Update assistant message when error occurs */
  const updateErrorAssistantMessage = (message_length: number, model_config_id?: string) => {
    if (message_length > 0 || !model_config_id) return

    updateChatList(prev => {
      const targetIndex = prev.findIndex(item => item.model_config_id === model_config_id);
      if (targetIndex > -1) {
        const modelChatList = [...prev]
        const curModelChat = modelChatList[targetIndex]
        const curChatMsgList = curModelChat.list || []
        const lastMsg = curChatMsgList[curChatMsgList.length - 2]
        modelChatList[targetIndex] = {
          ...modelChatList[targetIndex],
          list: [
            ...curChatMsgList.slice(0, curChatMsgList.length - 2),
            {
              ...lastMsg,
              ...(lastMsg.role === 'user' ? { status: 'error' } : { content: null })
            }
          ]
        }
        return [...modelChatList]
      }

      return prev
    })
  }
  /** Send message for agent comparison mode */
  const handleSend = (msg?: string) => {
    if (loading || !id) return
    setLoading(true)
    setCompareLoading(true)
    handleSave(false)
      .then(() => {
        const message = msg
        if (!message?.trim()) return
        const files = toolbarRef.current?.getFiles() || []
        // Validate required variables before sending
        let isCanSend = true
        const params: Record<string, any> = {}
        if (chatVariables && chatVariables.length > 0) {
          const needRequired: string[] = []
          chatVariables.forEach(vo => {
            params[vo.name] = vo.value

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
          setLoading(false)
          setCompareLoading(false)
          return
        }

        addUserMessage(message, files)
        setMessage(message)
        toolbarRef.current?.setFiles([])
        setFileList([])
        addAssistantMessage()

        const handleStreamMessage = (data: SSEMessage[]) => {
          setCompareLoading(false)

          data.map(item => {
            const { model_config_id, conversation_id, content, message_length, audio_url } = item.data as { model_config_id: string; conversation_id: string; content: string; message_length: number; audio_url: string };
            
            switch (item.event) {
              case 'model_message':
                updateAssistantMessage(content, model_config_id, conversation_id, audio_url)
                break;
              case 'model_end':
                if (audio_url) {
                  updateAssistantMessage(content, model_config_id, conversation_id, audio_url)
                }
                updateErrorAssistantMessage(message_length, model_config_id)
                break;
              case 'compare_end':
                setLoading(false);
                break;
            }
          })
        };

        setTimeout(() => {
          runCompare(id, {
            message,
            files: files.map(file => {
              if (file.url) {
                return file
              } else {
                return {
                  type: file.type,
                  transfer_method: 'local_file',
                  upload_file_id: file.response.data.file_id
                }
              }
            }),
            models: chatList.map(item => ({
              model_config_id: item.model_config_id,
              label: item.label,
              model_parameters: item.model_parameters,
              conversation_id: item.conversation_id
            })),
            variables: params,
            parallel: true,
            stream: true,
            timeout: 60,
          }, handleStreamMessage)
            .catch(() => {
              setLoading(false)
              setCompareLoading(false)
              updateClusterErrorAssistantMessage(0)
            })
            .finally(() => {
              setLoading(false)
              setCompareLoading(false)
            })
        }, 0)
      })
      .catch(() => {
        setLoading(false)
        setCompareLoading(false)
      })
  }

  /** Add assistant message for cluster mode */
  const addClusterAssistantMessage = () => {
    const assistantMessage: ChatItem = {
      role: 'assistant',
      content: '',
      created_at: Date.now()
    };
    updateChatList(prev => prev.map(item => ({
      ...item,
      list: [...(item.list || []), assistantMessage]
    })))
  }
  /** Update cluster assistant message with content */
  const updateClusterAssistantMessage = (content?: string) => {
    if (!content) return
    updateChatList(prev => {
      const modelChatList = [...prev]
      const curChatMsgList = modelChatList[0].list || []
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
  /** Update cluster message when error occurs */
  const updateClusterErrorAssistantMessage = (message_length: number) => {
    if (message_length > 0) return
    updateChatList(prev => {
      const modelChatList = [...prev]
      const curChatMsgList = modelChatList[0].list || []
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
  /** Send message for cluster mode */
  const handleClusterSend = (msg?: string) => {
    if (loading || !id) return
    setLoading(true)
    setCompareLoading(true)
    handleSave(false)
      .then(() => {
        const message = msg
        if (!message || message.trim() === '') return
        const files = toolbarRef.current?.getFiles() || []
        addUserMessage(message, files)
        setMessage(undefined)
        toolbarRef.current?.setFiles([])
        setFileList([])
        addClusterAssistantMessage()

        const handleStreamMessage = (data: SSEMessage[]) => {
          setCompareLoading(false)

          data.map(item => {
            const { conversation_id, content, message_length } = item.data as { conversation_id: string, content: string, message_length: number };
            
            switch (item.event) {
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
          draftRun(id,
            {
              message,
              conversation_id: conversationId,
              stream: true,
              files: files.map(file => {
                if (file.url) {
                  return file
                } else {
                  return {
                    type: file.type,
                    transfer_method: 'local_file',
                    upload_file_id: file.response.data.file_id
                  }
                }
              }),
            },
            handleStreamMessage
          )
            .catch(() => {
              setLoading(false)
              setCompareLoading(false)
              updateClusterErrorAssistantMessage(0)
            })
            .finally(() => {
              setLoading(false)
              setCompareLoading(false)
            })
        }, 0)
      })
      .catch(() => {
        setLoading(false)
        setCompareLoading(false)
      })
  }

  /** Delete chat configuration from list */
  const handleDelete = (index: number) => {
    updateChatList(chatList.filter((_, voIndex) => voIndex !== index))
  }
  const isHasLabel = useMemo(() => chatList.some(item => item.label), [chatList])

  return (
    <Flex vertical className="rb:relative rb:h-full">
      {chatList.length === 0
        ? <Empty
          url={DebuggingEmpty}
          size={[300, 200]}
          title={t('application.debuggingEmpty')} 
          subTitle={t('application.debuggingEmptyDesc')} 
          className="rb:h-[calc(100vh-159px)]"
        />
        : <>
          <div className={clsx(`rb:relative rb:grid rb:grid-cols-${chatList.length} rb:overflow-hidden rb:w-full rb:flex-1 rb:min-h-0`)}>
            {chatList.map((chat, index) => (
              <Flex key={index} vertical className={clsx({
                "rb:border-r rb:border-[#DFE4ED]": index !== chatList.length - 1 && chatList.length > 1,
              })}>
                {chat.label &&
                  <div className={clsx(
                    "rb:grid rb:bg-[#F6F6F6] rb:text-center rb:flex-[0_0_auto]"
                  )}>
                    <div className='rb:relative rb:py-2.5 rb:px-3 rb:overflow-hidden'>
                      <div className="rb:text-[#212332] rb:font-medium rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:w-[calc(100%-24px)]">{chat.label}</div>
                      <div
                        className="rb:w-4 rb:h-4 rb:cursor-pointer rb:absolute rb:top-3 rb:right-3 rb:bg-cover rb:bg-[url('@/assets/images/close.svg')] rb:hover:bg-[url('@/assets/images/close_hover.svg')]"
                        onClick={() => handleDelete(index)}
                      ></div>
                    </div>
                  </div>
                }
                <ChatContent
                  classNames={{
                    'rb:mb-3 rb:mt-5': isHasLabel,
                    'rb:mb-3': !isHasLabel,
                    'rb:h-[calc(100vh-292px)]': isCluster,
                    'rb:h-[calc(100vh-353px)]': !isCluster,
                    "rb:pr-4": index !== chatList.length - 1 && chatList.length > 1,
                    "rb:pl-4": index !== 0 && chatList.length > 1,
                  }} 
                  contentClassNames={{
                    'rb:max-w-100!': chatList.length === 1,
                    'rb:max-w-70!': chatList.length === 2,
                    'rb:max-w-45!': chatList.length === 3,
                    'rb:max-w-24!': chatList.length === 4,
                  }}
                  empty={<Empty
                    url={ChatIcon}
                    title={t('application.chatEmpty')}
                    isNeedSubTitle={false}
                    size={[240, 200]}
                    className={clsx({
                      "rb:h-[calc(100vh-353px)]": isHasLabel,
                      "rb:h-[calc(100vh-292px)]": !isHasLabel,
                    })}
                  />}
                  data={chat.list || []}
                  streamLoading={compareLoading}
                  labelPosition="top"
                  labelFormat={(item) => item.role === 'user' ? t('application.you') : chat.label || t(`application.ai`)}
                  errorDesc={t('application.ReplyException')}
                />
              </Flex>
            ))}
          </div>
          <div className="rb:relative rb:flex rb:items-center rb:gap-2.5 rb:mt-4 rb:mb-1">
            <ChatInput
              message={message}
              className="rb:relative!"
              loading={loading}
              fileChange={(list) => {
                setFileList(list || [])
                toolbarRef.current?.setFiles(list || [])
              }}
              fileList={fileList}
              onSend={isCluster ? handleClusterSend : handleSend}
              onChange={setMessage}
            >
              <ChatToolbar
                ref={toolbarRef}
                features={features}
                onFilesChange={setFileList}
                extra={
                  chatVariables && chatVariables.length > 0 ? (
                    <div
                      className={clsx('rb:flex rb:items-center rb:border rb:rounded-lg rb:px-2 rb:text-[12px] rb:h-6 rb:cursor-pointer rb:hover:bg-[#F0F3F8] rb:text-[#212332]', {
                        'rb:border-[#FF5D34] rb:text-[#FF5D34]': chatVariables.some(vo => vo.required && !vo.value),
                        'rb:border-[#DFE4ED]': !chatVariables.some(vo => vo.required && !vo.value),
                      })}
                      onClick={handleEditVariables}
                    >
                      <SettingOutlined className="rb:mr-1" />
                      {t('memoryConversation.variableConfig')}
                    </div>
                  ) : null
                }
              />
            </ChatInput>
          </div>
        </>
      }
    </Flex>
  )
}

export default Chat;