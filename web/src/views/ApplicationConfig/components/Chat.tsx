import { type FC, useRef, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'
import { Input, Form } from 'antd'
import ChatIcon from '@/assets/images/application/chat.svg'
import ChatSendIcon from '@/assets/images/application/chatSend.svg'
import DebuggingEmpty from '@/assets/images/application/debuggingEmpty.svg'
import type { ChatItem, ChatData, Config } from '../types'
import { runCompare, draftRun } from '@/api/application'
import Empty from '@/components/Empty'
import Markdown from '@/components/Markdown'

interface ChatProps {
  chatList: ChatData[];
  data: Config;
  updateChatList: (list: ChatData[]) => void;
  handleSave: (flag?: boolean) => Promise<any>;
  source?: 'cluster' | 'agent';
}
const Chat: FC<ChatProps> = ({ chatList, data, updateChatList, handleSave, source = 'agent' }) => {
  const { t } = useTranslation();
  const [form] = Form.useForm<{ message: string }>()
  const scrollContainerRefs = useRef<(HTMLDivElement | null)[]>([])
  const [loading, setLoading] = useState(false)
  const [isCluster, setIsCluster] = useState(source === 'cluster')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [compareLoading, setCompareLoading] = useState(false)
  
  // 当聊天列表更新时，自动滚动到底部
  useEffect(() => {
    // 延迟一下，确保DOM已经更新
    setTimeout(() => {
      scrollContainerRefs.current.forEach(container => {
        if (container) {
          container.scrollTop = container.scrollHeight;
        }
      });
    }, 0);
  }, [chatList]);
  useEffect(() => {
    setIsCluster(source === 'cluster')
  }, [source])

  const handleSend = () => {
    if (loading) return
    setLoading(true)
    setCompareLoading(true)
    handleSave(false)
      .then(() => {
        const message = form.getFieldValue('message')
        if (!message || message.trim() === '') return
        const newUserMessage: ChatItem = {
          role: 'question',
          content: message,
          time: Date.now(),
        };
        updateChatList((prev: ChatData[]) => {
          return prev.map(item => ({
            ...item,
            list: [
              ...(item.list || []),
              newUserMessage
            ]
          }))
        })
        form.setFieldsValue({ message: undefined })
        // 添加空的助手消息用于流式更新
        const assistantMessages: Record<string, ChatItem> = {};
        if (isCluster) {
          const assistantMessage: ChatItem = {
            role: 'answer',
            content: '',
            time: Date.now(),
          };
          assistantMessages['cluster'] = assistantMessage;
          updateChatList((prev: ChatData[]) => prev.map(item => ({
            ...item,
            list: [...(item.list || []), assistantMessage]
          })))
        } else {
          chatList.forEach(item => {
            const assistantMessage: ChatItem = {
              role: 'answer',
              content: '',
              time: Date.now(),
            };
            assistantMessages[item.model_config_id] = assistantMessage;
          });
          updateChatList((prev: ChatData[]) => prev.map(item => ({
            ...item,
            list: [...(item.list || []), assistantMessages[item.model_config_id]]
          })))
        }

        const handleStreamMessage = (data: string) => {
          setCompareLoading(false)
          try {
            const lines = data.split('\n');
            let currentEvent = '';
            
            for (let i = 0; i < lines.length; i++) {
              const line = lines[i].trim();
              
              if (line.startsWith('event:')) {
                currentEvent = line.substring(6).trim();
              } else if (line.startsWith('data:') && (!isCluster && currentEvent === 'model_message')) {
                const jsonData = line.substring(5).trim();
                const parsed = JSON.parse(jsonData);
                
                if (parsed.content && parsed.model_config_id) {
                  const targetIndex = chatList.findIndex(item => item.model_config_id === parsed.model_config_id);
                  if (targetIndex !== -1) {
                    updateChatList((prev: ChatData[]) => prev.map((item, index) => {
                      if (index === targetIndex) {
                        return {
                          ...item,
                          conversation_id: parsed.conversation_id,
                          list: item.list?.map((msg, msgIndex) => {
                            if (msgIndex === item.list!.length - 1 && msg.role === 'answer') {
                              return { ...msg, content: msg.content + parsed.content };
                            }
                            return msg;
                          }) || []
                        };
                      }
                      return item;
                    }))
                  }
                }
              } else if (line.startsWith('data:') && (isCluster && currentEvent === 'message')) {
                const jsonData = line.substring(5).trim();
                const parsed = JSON.parse(jsonData);
                if (parsed.content) {
                  updateChatList((prev: ChatData[]) => prev.map((item, index) => {
                    if (index === 0) {
                      return {
                        ...item,
                        list: item.list?.map((msg, msgIndex) => {
                          if (msgIndex === item.list!.length - 1 && msg.role === 'answer') {
                            return { ...msg, content: (msg.content || '') + parsed.content };
                          }
                          return msg;
                        }) || []
                      };
                    }
                    return item;
                  }))
                }
                if (parsed.conversation_id) {
                  setConversationId(parsed.conversation_id);
                }
              } else if (line.startsWith('data:') && (!isCluster && currentEvent === 'model_end')) {
                const jsonData = line.substring(5).trim();
                const parsed = JSON.parse(jsonData);
                
                if (parsed.message_length === 0 && parsed.model_config_id) {
                  const targetIndex = chatList.findIndex(item => item.model_config_id === parsed.model_config_id);
                  if (targetIndex !== -1) {
                    updateChatList((prev: ChatData[]) => prev.map((item, index) => {
                      if (index === targetIndex) {
                        return {
                          ...item,
                          list: item.list?.map((msg, msgIndex) => {
                            if (msgIndex === item.list!.length - 1 && msg.role === 'answer') {
                              return { ...msg, content: null };
                            }
                            return msg;
                          }) || []
                        };
                      }
                      return item;
                    }))
                  }
                }
              } else if (line.startsWith('data:') && (isCluster && currentEvent === 'model_end')) {
                const jsonData = line.substring(5).trim();
                const parsed = JSON.parse(jsonData);
                if (parsed.message_length === 0) {
                  updateChatList((prev: ChatData[]) => prev.map((item, index) => {
                    if (index === 0) {
                      return {
                        ...item,
                        list: item.list?.map((msg, msgIndex) => {
                          if (msgIndex === item.list!.length - 1 && msg.role === 'answer') {
                            return { ...msg, content: null };
                          }
                          return msg;
                        }) || []
                      };
                    }
                    return item;
                  }))
                }

                if (parsed.conversation_id) {
                  setConversationId(parsed.conversation_id);
                }
              } else if (currentEvent === 'compare_end') {
                setLoading(false);
              }
            }
          } catch (e) {
            console.error('Parse stream data error:', e);
          }
        };

        setTimeout(() => {
          if (isCluster) {
            draftRun(data.app_id, { message, conversation_id: conversationId, stream: true }, handleStreamMessage)
              .finally(() => setLoading(false))
          } else {
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
          }
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
                    'rb:rounded-tr-[12px]': index === chatList.length - 1,
                    'rb:rounded-tl-[12px]': index === 0,
                  }
                )}>
                  <div className='rb:relative rb:p-[10px_12px] rb:overflow-hidden'>
                    <div className="rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:w-[calc(100%-24px)]">{chat.label}</div>
                    <div 
                      className="rb:w-[16px] rb:h-[16px] rb:cursor-pointer rb:absolute rb:top-[12px] rb:right-[12px] rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/close.svg')] rb:hover:bg-[url('@/assets/images/close_hover.svg')]" 
                      onClick={() => handleDelete(index)}
                    ></div>
                  </div>
                </div>
              }
              {!chat.list || chat.list.length === 0
                ? <Empty url={ChatIcon} title={t('application.chatEmpty')} className="rb:h-full" />
                : (
                  <div ref={el => scrollContainerRefs.current[index] = el} className={clsx(`rb:relative rb:overflow-y-auto rb:overflow-x-hidden`, {
                    'rb:h-[calc(100vh-186px)]': isCluster,
                    'rb:h-[calc(100vh-286px)]': !isCluster,
                  })}>
                    {chat.list?.map((vo, voIndex) => {
                      if (compareLoading && voIndex === chat.list?.length - 1) {
                        return null
                      }
                      return (
                        <div key={voIndex} className={clsx("rb:relative rb:mt-[24px]", {
                          'rb:right-[16px] rb:text-right': vo.role === 'question',
                          'rb:left-[16px] rb:text-left': vo.role !== 'question',
                        })}>
                          <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-[16px] rb:font-regular">{vo.role === 'question' ? 'You' : chat.label}</div>
                          <div className={clsx('rb:border rb:text-left rb:rounded-[8px] rb:mt-[6px] rb:leading-[18px] rb:p-[10px_12px_2px_12px] rb:inline-block', {
                            'rb:border-[rgba(255,93,52,0.30)] rb:bg-[rgba(255,93,52,0.08)] rb:text-[#FF5D34]': vo.role !== 'question' && vo.content === null,
                            'rb:bg-[rgba(21,94,239,0.08)] rb:border-[rgba(21,94,239,0.30)]': vo.role === 'question' && vo.content,
                            'rb:bg-[#ffffff] rb:border-[rgba(235,235,235,1)]': vo.role !== 'question' && (vo.content || vo.content === ''),
                            'rb:max-w-[400px]': chatList.length === 1,
                            'rb:max-w-[260px]': chatList.length === 2,
                            'rb:max-w-[150px]': chatList.length === 3,
                            'rb:max-w-[108px]': chatList.length === 4,
                          })}>
                            <Markdown content={vo.content === null ? t('application.ReplyException') : vo.content} />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )
              }
            </div>
          ))}
        </div>
        <div className="rb:flex rb:items-center rb:gap-[10px] rb:p-[16px]">
          <Form form={form} style={{width: 'calc(100% - 54px)'}}>
            <Form.Item name="message" className="rb:mb-[0]!">
              <Input 
                className="rb:h-[44px] rb:shadow-[0px_2px_8px_0px_rgba(33,35,50,0.1)]" 
                placeholder={t('application.chatPlaceholder')}
                onPressEnter={handleSend}
              />
            </Form.Item>
          </Form>
          <img src={ChatSendIcon} className={clsx("rb:w-[44px] rb:h-[44px] rb:cursor-pointer", {
            'rb:opacity-50': loading,
          })} onClick={handleSend} />
        </div>
      </>
      }
    </div>
  )
}

export default Chat;