/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:39 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:27:39 
 */
/**
 * Chat debugging component for application testing
 * Supports both single agent and multi-agent cluster modes
 * Provides real-time streaming responses and conversation history
 */

import { type FC, useEffect, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'
import { Flex, Dropdown, type MenuProps } from 'antd'

import ChatIcon from '@/assets/images/application/chat.png'
import DebuggingEmpty from '@/assets/images/application/debuggingEmpty.png'
import type { ChatData, Config } from '../types'
import { runCompare, draftRun } from '@/api/application'
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import { type SSEMessage } from '@/utils/stream'
import ChatInput from '@/components/Chat/ChatInput'
import UploadFiles from '@/views/Conversation/components/FileUpload'
// import AudioRecorder from '@/components/AudioRecorder'
import UploadFileListModal from '@/views/Conversation/components/UploadFileListModal'
import type { UploadFileListModalRef } from '@/views/Conversation/types'

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
}

/**
 * Chat debugging component
 * Allows testing application with different model configurations side-by-side
 */
const Chat: FC<ChatProps> = ({ chatList, data, updateChatList, handleSave, source = 'agent' }) => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false)
  const [isCluster, setIsCluster] = useState(source === 'multi_agent')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [compareLoading, setCompareLoading] = useState(false)
  const [fileList, setFileList] = useState<any[]>([])
  const [message, setMessage] = useState<string | undefined>(undefined)
  const uploadFileListModalRef = useRef<UploadFileListModalRef>(null)

  useEffect(() => {
    setIsCluster(source === 'multi_agent')
  }, [source])

  /** Add user message to all chat lists */
  const addUserMessage = (message: string, files: any[]) => {
    const newUserMessage: ChatItem = {
      role: 'user',
      content: message,
      created_at: Date.now(),
      files
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
  const updateAssistantMessage = (content?: string, model_config_id?: string, conversation_id?: string) => {
    if (!content || !model_config_id) return
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
  /** Update assistant message when error occurs */
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
  /** Send message for agent comparison mode */
  const handleSend = (msg?: string) => {
    if (loading) return
    setLoading(true)
    setCompareLoading(true)
    handleSave(false)
      .then(() => {
        const message = msg
        if (!message?.trim()) return

        addUserMessage(message, fileList)
        setMessage(message)
        setFileList([])
        addAssistantMessage()

        const handleStreamMessage = (data: SSEMessage[]) => {
          setCompareLoading(false)

          data.map(item => {
            const { model_config_id, conversation_id, content, message_length } = item.data as { model_config_id: string; conversation_id: string; content: string; message_length: number };

            switch (item.event) {
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
            files: fileList.map(file => {
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

  /** Add assistant message for cluster mode */
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
  /** Update cluster assistant message with content */
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
  /** Update cluster message when error occurs */
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
  /** Send message for cluster mode */
  const handleClusterSend = (msg?: string) => {
    if (loading) return
    setLoading(true)
    setCompareLoading(true)
    handleSave(false)
      .then(() => {
        const message = msg
        if (!message || message.trim() === '') return
        addUserMessage(message, fileList)
        setMessage(undefined)
        setFileList([])
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
                stream: true,
                files: fileList.map(file => {
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
              .finally(() => setLoading(false))
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
  const handleMessageChange = (message: string) => {
    setMessage(message)
  }
  const [update, setUpdate] = useState(false)
  const fileChange = (file?: any) => {
    setFileList([...fileList, file])
    setUpdate(prev => !prev)
  }
  // const handleRecordingComplete = async (file: any) => {
  //   console.log('file', file)
  // }

  const handleShowUpload: MenuProps['onClick'] = ({ key }) => {
    switch(key) {
      case 'define':
        uploadFileListModalRef.current?.handleOpen()
        break
    }
  }
  const addFileList = (list?: any[]) => {
    if (!list || list.length <= 0) return
    setFileList([...fileList, ...(list || [])])
  }
  const updateFileList = (list?: any[]) => {
    setFileList([...list || []])
  }

  console.log('chatList', chatList, fileList)
  return (
    <div className="rb:relative rb:h-full rb:flex rb:flex-col">
      {chatList.length === 0
        ? <Empty 
          url={DebuggingEmpty} 
          size={[300, 200]}
          title={t('application.debuggingEmpty')} 
          subTitle={t('application.debuggingEmptyDesc')} 
          className="rb:h-full"
        />
      : <>
        <div className={clsx(`rb:relative rb:grid rb:grid-cols-${chatList.length} rb:overflow-hidden rb:w-full rb:flex-1 rb:min-h-0`)}>
          {chatList.map((chat, index) => (
            <div key={index} className={clsx('rb:flex rb:flex-col', {
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
                  'rb:h-[calc(100vh-258px)]': isCluster,
                  'rb:h-[calc(100vh-356px)]': !isCluster,
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
        <div className="rb:relative rb:flex rb:items-center rb:gap-2.5 rb:m-4 rb:mb-1">
          <ChatInput
            message={message}
            className="rb:relative!"
            loading={loading}
            fileChange={updateFileList}
            fileList={fileList}
            onSend={isCluster ? handleClusterSend : handleSend}
            onChange={handleMessageChange}
          >
            <Flex justify="space-between" className="rb:flex-1">
                <Flex gap={8} align="center">
                  <Dropdown
                    menu={{
                      items: [
                        { key: 'define', label: t('memoryConversation.addRemoteFile') },
                        {
                          key: 'upload', label: (
                            <UploadFiles
                              fileType={['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg']}
                              onChange={fileChange}
                              fileList={[]}
                              update={update}
                            />
                          )
                        },
                      ],
                      onClick: handleShowUpload
                    }}
                  >
                    <div
                      className="rb:size-6 rb:cursor-pointer rb:bg-cover rb:bg-[url('src/assets/images/conversation/link.svg')] rb:hover:bg-[url('src/assets/images/conversation/link_hover.svg')]"
                    ></div>
                  </Dropdown>
              </Flex>
              {/* <Flex align="center">
                <AudioRecorder onRecordingComplete={handleRecordingComplete} />
                <Divider type="vertical" className="rb:ml-1.5! rb:mr-3!" />
              </Flex> */}
            </Flex>
          </ChatInput>
        </div>
      </>
      }

      <UploadFileListModal
        ref={uploadFileListModalRef}
        refresh={addFileList}
      />
    </div>
  )
}

export default Chat;