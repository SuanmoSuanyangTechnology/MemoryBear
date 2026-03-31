/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:58:03 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-31 16:24:47
 */
/**
 * Conversation Page
 * Public conversation interface for shared applications
 * Supports conversation history, streaming responses, and memory/web search features
 */

import { type FC, useState, useEffect, useRef } from 'react'
import { useParams, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import InfiniteScroll from 'react-infinite-scroll-component';
import { Flex, Skeleton, App, Tooltip } from 'antd'
import clsx from 'clsx'
import dayjs from 'dayjs'

import { getConversationHistory, sendConversation, getConversationDetail, getShareToken, getExperienceConfig } from '@/api/application'
import type { HistoryItem } from './types'
import Empty from '@/components/Empty'
import { formatDateTime } from '@/utils/format';
import { randomString } from '@/utils/common'
import ChatEmpty from '@/assets/images/empty/chatEmpty.png'
import Chat from '@/components/Chat'
import type { ChatItem } from '@/components/Chat/types'
import { type SSEMessage } from '@/utils/stream'
import { shareFileUploadUrlWithoutApiPrefix, getFileStatusById } from '@/api/fileStorage'
import ChatToolbar, { type ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import type { Variable } from '@/views/Workflow/components/Properties/VariableList/types'
import type { Variable as AppVariable } from '@/views/ApplicationConfig/components/VariableList/types'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';
import { replaceVariables } from '@/views/ApplicationConfig/Agent'

const Conversation: FC = () => {
  const { t } = useTranslation()
  const { message: messageApi, modal } = App.useApp()
  const { token } = useParams()
  const location = useLocation()
  const searchParams = new URLSearchParams(location.search)
  const userId = searchParams.get('user_id')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState<string>('')
  const [conversation_id, setConversationId] = useState<string | null>(null)
  const [historyList, setHistoryList] = useState<HistoryItem[]>([])
  const [groupHistoryList, setGroupHistoryList] = useState<Record<string, HistoryItem[]>>({})
  const [chatList, setChatList] = useState<ChatItem[]>([])
  const [pageLoading, setPageLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<ChatToolbarRef>(null)
  const audioPollingRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map())
  const [shareToken, setShareToken] = useState<string | null>(localStorage.getItem(`shareToken_${token}`))
  const [fileList, setFileList] = useState<any[]>([])
  const [webSearch, setWebSearch] = useState(false)
  const [isHasMemory, setIsHasMemory] = useState(false)
  const [memory, setMemory] = useState(true)
  const [features, setFeatures] = useState<FeaturesConfigForm>({} as FeaturesConfigForm)
  const [config, setConfig] = useState<Record<string, any>>({})
  const [audioStatusMap, setAudioStatusMap] = useState<Record<string, string>>({})
  const streamLoadingRef = useRef(false)
  const [isDeepThinking, setIsDeepThinking] = useState<Record<string, any>>({})
  const [thinking, setThinking] = useState(false)

  useEffect(() => {
    const shareToken = localStorage.getItem(`shareToken_${token}`)
    setShareToken(shareToken)
    if (shareToken && shareToken !== '') return
    getShareToken(token as string, userId || randomString(12, false))
      .then(res => {
        const response = res as { access_token: string } || {}
        localStorage.setItem(`shareToken_${token}`, response.access_token ?? '')
        setShareToken(response.access_token ?? '')
      })
  }, [token])

  useEffect(() => {
    if (token && page === 1 && hasMore && historyList.length === 0 && shareToken) {
      getHistory()
    }
  }, [token, shareToken, page, hasMore, historyList])

  useEffect(() => {
    if (shareToken && token) {
      getExperienceConfig(token)
        .then(res => {
          const response = res as { variables: Variable[]; features: FeaturesConfigForm; model_parameters?: Record<string, any>; app_type: string; memory: boolean; }
          toolbarRef.current?.setVariables(response.variables || [])
          setConfig(response)
          setFeatures(response.features)
          setIsHasMemory((response.app_type === 'workflow' && response.memory) || response.memory)
          setIsDeepThinking(response.model_parameters?.deep_thinking || false)
        })
    } else {
      setChatList([])
    }
  }, [shareToken, token])

  /** Group conversation history by date */
  const groupHistoryByDate = (items: HistoryItem[]): Record<string, HistoryItem[]> => {
    return items.reduce((groups: Record<string, HistoryItem[]>, item) => {
      const date = formatDateTime(item.created_at, 'YYYY-MM-DD')

      if (!groups[date]) {
        groups[date] = [];
      }
      groups[date].push(item);
      return groups;
    }, {});
  }

  /** Fetch conversation history with pagination */
  const getHistory = (flag: boolean = false) => {
    if (!token || (pageLoading || !hasMore) && !flag) return
    setPageLoading(true);
    getConversationHistory(token, { page: flag ? 1 : page, pagesize: 20 })
      .then(res => {
        const response = res as { items: HistoryItem[], page: { hasnext: boolean; page: number; pagesize: number; total: number } }
        const results = response?.items || []
        let list = []
        if (flag) {
          setHistoryList(results);
          list = [...results]
        } else {
          setHistoryList(historyList.concat(results));
          list = [...historyList, ...results]
        }
        setHistoryList(list)
        setGroupHistoryList(groupHistoryByDate(list))
        if (page === 1 && !flag) {
          setConversationId(list[0]?.id || '')
        }
        setPage(response.page.page + 1);
        setHasMore(response.page.hasnext);
        setLoading(false);
      })
      .finally(() => setPageLoading(false))
  }
  /** Switch to different conversation or start new one */
  const handleChangeHistory = (id: string | null) => {
    if (id !== conversation_id) setConversationId(id)
    if (!id) setMessage('')
  }

  useEffect(() => {
    audioPollingRef.current.forEach((timer) => clearInterval(timer))
    audioPollingRef.current.clear()
    if (conversation_id) {
      getConversationDetail(token as string, conversation_id)
        .then(res => {
          const response = res as { messages: ChatItem[] }
          setChatList(response?.messages || [])
        })
    } else {
      if (features?.opening_statement?.statement) {
        setChatList([{
          role: 'assistant',
          content: features.opening_statement.statement,
          created_at: Date.now(),
          meta_data: {
            suggested_questions: features.opening_statement?.suggested_questions
          }
        }])
      } else {
        setChatList([])
      }
    }
  }, [conversation_id, features?.opening_statement?.statement])

  const addUserMessage = (message: string = '', files?: any[]) => {
    setChatList(prev => [...prev, {
      conversation_id,
      role: 'user',
      content: message,
      created_at: Date.now(),
      meta_data: {
        files
      },
    }])
  }

  const addAssistantMessage = () => {
    setChatList(prev => [...prev, {
      created_at: Date.now(),
      role: 'assistant',
      content: ''
    }])
  }

  const updateAssistantMessage = (content: string = '', audio_url?: string, audio_status?: string, citations?: any[]) => {
    if (!content && !audio_url && (!citations || citations?.length < 1)) return
    if (streamLoadingRef.current) streamLoadingRef.current = false
    setChatList(prev => {
      const lastList = [...prev]
      const lastIndex = lastList.length - 1
      const lastMsg = lastList[lastIndex]
      if (lastMsg?.role === 'assistant') {
        return [
          ...lastList.slice(0, lastIndex),
          {
            ...lastMsg,
            content: lastMsg.content + content,
            meta_data: {
              ...(lastMsg.meta_data || {}),
              audio_url: audio_url || lastMsg.meta_data?.audio_url,
              audio_status: audio_status || lastMsg.meta_data?.audio_status,
              citations: citations || lastMsg.meta_data?.citations
            }
          }
        ]
      }
      return prev
    })
  }
  const updateAssistantReasoningMessage = (content: string = '') => {
    if (!content) return
    if (streamLoadingRef.current) streamLoadingRef.current = false
    setChatList(prev => {
      const lastList = [...prev]
      const lastIndex = lastList.length - 1
      const lastMsg = lastList[lastIndex]
      if (lastMsg?.role === 'assistant') {
        return [
          ...lastList.slice(0, lastIndex),
          {
            ...lastMsg,
            meta_data: {
              ...(lastMsg.meta_data || {}),
              reasoning_content: (lastMsg.meta_data?.reasoning_content || '') + content
            }
          }
        ]
      }
      return prev
    })
  }
  useEffect(() => {
    if (!Object.keys(audioStatusMap).length) return
    setChatList(prev => prev.map(msg => {
      if (msg.role === 'assistant' && msg.meta_data?.audio_url && audioStatusMap[msg.meta_data.audio_url]) {
        return {
          ...msg,
          meta_data: {
            ...msg.meta_data,
            audio_status: audioStatusMap[msg.meta_data.audio_url]
          }
        }
      }
      return msg
    }))
  }, [audioStatusMap, chatList.length])

  /** Send message and handle streaming response */
  const handleSend = (msg?: string) => {
    if (!token || !shareToken) return
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))
    const variables = toolbarRef.current?.getVariables() || []
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
    if (!isCanSend) return

    setLoading(true)
    streamLoadingRef.current = true
    addUserMessage(msg || message, files)
    addAssistantMessage()
    toolbarRef.current?.setFiles([])
    setFileList([])

    let currentConversationId: string | null = null
    const handleStreamMessage = (data: SSEMessage[]) => {
      data.forEach((item) => {
        const { content, conversation_id: curId, audio_url, citations } = item.data as {
          content: string; conversation_id: string; audio_url?: string;
          citations?: {
            document_id: string;
            file_name: string;
            knowledge_id: string;
            score: string;
          }[]
        }
        switch (item.event) {
          case 'start':
          case 'node_start':
            const { conversation_id: newId } = item.data as { conversation_id: string }
            currentConversationId = newId
            break
          case 'reasoning':
            updateAssistantReasoningMessage(content)
            if (curId) currentConversationId = curId;
            break
          case 'message':
            updateAssistantMessage(content, audio_url, audio_url ? 'pending' : undefined)
            if (curId) currentConversationId = curId;
            break
          case 'end':
          case 'workflow_end':
            if (audio_url) {
              updateAssistantMessage(content, audio_url, 'pending', citations)
              const { file_id } = item.data as { file_id?: string }
              const idToPoll = file_id || audio_url || ''
              const fileId = audio_url.split('/').pop()
              if (fileId && idToPoll && !audioPollingRef.current.has(idToPoll)) {

                const timer = setInterval(() => {
                  getFileStatusById(fileId)
                    .then(res => {
                      const { status } = res as { status: string }
                      if (status && status !== 'pending') {
                        setAudioStatusMap(prev => ({
                          ...prev,
                          [idToPoll]: status
                        }))
                        clearInterval(audioPollingRef.current.get(idToPoll))
                        audioPollingRef.current.delete(idToPoll)
                        getHistory(true)
                        if (currentConversationId && currentConversationId !== conversation_id) {
                          setConversationId(currentConversationId)
                        }
                      }
                    })
                    .catch(() => {
                      clearInterval(audioPollingRef.current.get(idToPoll))
                      audioPollingRef.current.delete(idToPoll)
                      getHistory(true)
                      if (currentConversationId && currentConversationId !== conversation_id) {
                        setConversationId(currentConversationId)
                      }
                    })
                }, 2000)
                audioPollingRef.current.set(idToPoll, timer)
              }
            } else {
              getHistory(true)
              if (currentConversationId && currentConversationId !== conversation_id) {
                setConversationId(currentConversationId)
              }
            }
            if (citations && citations.length > 0) {
              updateAssistantMessage(content, audio_url, undefined, citations)
            }
            setLoading(false)
            break
        }
      })
    };

    sendConversation({
      web_search: webSearch,
      memory,
      message: msg || message || '',
      stream: true,
      conversation_id: conversation_id || null,
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
      variables: params,
      thinking,
    }, handleStreamMessage, shareToken)
      .catch(() => {
        setLoading(false)
        streamLoadingRef.current = false
      })
      .finally(() => {
        setLoading(false)
        streamLoadingRef.current = false
      })
  }

  const handleChangeMemory = () => {
    if (config.app_type === 'workflow') return;
    let value = !memory
    modal.confirm({
      title: value ? t('memoryConversation.memoryTipTitle') : t('memoryConversation.memoryCancelTipTitle'),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: () => {
        setMemory(value)
      },
      onCancel: () => {
        setMemory(!value)
      }
    })
  }
  const handleChangeDeepThinking = () => {
    setThinking(prev => !prev)
  }

  const handleChangeVariables = (variables: Variable[]) => {
    setChatList(prev => {
      const firstMsg = prev[0]
      console.log('firstMsg', firstMsg)
      if (firstMsg && firstMsg.role === 'assistant' && firstMsg.content && features?.opening_statement.enabled && features?.opening_statement.statement && variables.length > 0) {
        firstMsg.content = replaceVariables(features?.opening_statement.statement, variables as unknown as AppVariable[])
      }
      return [firstMsg, ...prev.slice(1)]
    })
  }

  console.log('chatList', chatList, streamLoadingRef.current)

  return (
    <Flex className="rb:w-full rb:p-[-16px]!">
      <div className="rb:w-80 rb:h-screen rb:bg-[#F6F6F6] rb:overflow-hidden">
        <Flex align="center" gap={8} className="rb:p-5!">
          <div className="rb:size-6 rb:bg-cover rb:bg-[url('@/assets/images/conversation/redbear.png')]"></div>
          <div className="rb:text-[16px] rb:leading-5 rb:font-[Gilroy-Extrabold] rb:font-extrabold">{t('memoryConversation.chatTitle')}</div>
        </Flex>

        <Flex align="center" gap={12}
          className="rb:cursor-pointer rb:border rb:border-[#155EEF] rb:rounded-xl rb:p-3! rb:mx-4! rb:text-[16px] rb:font-medium rb:text-[#155EEF] rb:h-12! rb:mb-5!"
          onClick={() => handleChangeHistory(null)}
        >
          <div
            className="rb:w-5 rb:h-5 rb:cursor-pointer rb:mr-2 rb:bg-cover rb:bg-[url('@/assets/images/conversation/conversation.svg')] rb:group-hover:bg-[url('@/assets/images/conversation/conversation_hover.svg')]"
          ></div>
          {t('memoryConversation.startANewConversation')}
        </Flex>
        {historyList.length > 0 &&
          <div
            ref={scrollRef}
            id="scrollableDiv"
            className="rb:overflow-y-auto rb:h-[calc(100vh-144px)] rb:px-3!"
          >
            <InfiniteScroll
              dataLength={historyList.length}
              next={getHistory}
              hasMore={hasMore}
              loader={<Skeleton active />}
              scrollableTarget="scrollableDiv"
            >
              {Object.entries(groupHistoryList).map(([date, items]) => (
                <div key={date} className="rb:mt-6 rb:first:mt-0">
                  <div className="rb:leading-5 rb:text-[#5B6167] rb:mb-2 rb:pl-1 rb:font-regular">{date.replace(/\u200e|\u200f/g, '')}</div>

                  <Flex vertical gap={4}>
                    {items.map(item => (
                    <div key={item.updated_at} className="rb:mb-3">
                      <div className={clsx("rb:p-[8px_13px] rb:rounded-lg rb:leading-5 rb:cursor-pointer rb:hover:bg-[#F0F3F8]", {
                        'rb:bg-[#FFFFFF] rb:shadow-[0px_2px_4px_0px_rgba(0,0,0,0.15)] rb:font-medium rb:hover:bg-[#FFFFFF]!': item.id === conversation_id,
                      })}
                        onClick={() => handleChangeHistory(item.id)}
                      >
                        {item.title}
                      </div>
                    </div>
                    ))}
                  </Flex>
                </div>
              ))}
            </InfiniteScroll>
          </div>
        }
      </div>

      <div className="rb:relative rb:h-screen rb:px-4 rb:flex-[1_1_auto]">
        <div className='rb:w-190  rb:h-screen rb:mx-auto rb:pt-10 rb:pb-3'>
          <Chat
            empty={<Empty url={ChatEmpty} className="rb:h-full" size={[320,180]} title={t('memoryConversation.chatEmpty')} subTitle={t('memoryConversation.emptyDesc')} />}
            contentClassName={!fileList.length ? "rb:h-[calc(100%-144px)] rb:w-full" : "rb:h-[calc(100%-208px)] rb:w-full"}
            data={chatList}
            streamLoading={streamLoadingRef.current}
            loading={loading}
            onChange={setMessage}
            onSend={handleSend}
            labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
            conversationId={conversation_id}
            fileList={fileList}
            fileChange={(list) => {
              setFileList(list || [])
              toolbarRef.current?.setFiles(list || [])
            }}
          >
          <ChatToolbar
            ref={toolbarRef}
            features={features}
            onFilesChange={setFileList}
            uploadAction={shareFileUploadUrlWithoutApiPrefix}
            uploadRequestConfig={{
              headers: {
                'Content-Type': 'multipart/form-data',
                Authorization: `Bearer ${shareToken || ''}`,
              }
            }}
            rightExtra={
              (features?.web_search?.enabled || isHasMemory || isDeepThinking)
              ? <Flex align="center" justify="end" gap={8}>
                {isDeepThinking &&
                  <Tooltip title={t('memoryConversation.deepThinking')}>
                    <Flex justify="center" align="center"
                      className={clsx("rb:size-7 rb:cursor-pointer rb:border rb:hover:bg-[#F6F6F6] rb:rounded-full rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]", {
                        'rb:bg-[rgba(21,94,239,0.06)] rb:border-[rgba(21,94,239,0.25)]': thinking,
                        'rb:border-[#EBEBEB]': !thinking,
                      })}
                      onClick={handleChangeDeepThinking}
                    >
                      <div className={clsx("rb:size-4 rb:bg-cover", {
                        "rb:bg-[url('@/assets/images/conversation/deepThinking.svg')]": !thinking,
                        "rb:bg-[url('@/assets/images/conversation/deepThinkingChecked.svg')]": thinking
                      })} />
                    </Flex>
                  </Tooltip>
                }
                {features?.web_search?.enabled &&
                  <Tooltip title={t('memoryConversation.web_search')}>
                    <Flex justify="center" align="center"
                      className={clsx("rb:size-7 rb:border rb:cursor-pointer rb:hover:bg-[#F6F6F6] rb:rounded-full rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]", {
                        'rb:bg-[rgba(21,94,239,0.06)] rb:border-[rgba(21,94,239,0.25)]': webSearch,
                        'rb:border-[#EBEBEB]': !webSearch,
                      })}
                      onClick={() => setWebSearch(prev => !prev)}
                    >
                      <div className={clsx("rb:size-4 rb:bg-cover", {
                        "rb:bg-[url('@/assets/images/conversation/online.svg')]": !webSearch,
                        "rb:bg-[url('@/assets/images/conversation/onlineChecked.svg')]": webSearch
                      })} />
                    </Flex>
                  </Tooltip>
                }
                {isHasMemory &&
                  <Tooltip title={t('memoryConversation.memory')}>
                    <Flex justify="center" align="center"
                      className={clsx("rb:size-7 rb:border rb:hover:bg-[#F6F6F6] rb:rounded-full rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]", {
                        'rb:bg-[rgba(21,94,239,0.06)] rb:border-[rgba(21,94,239,0.25)]': memory,
                        'rb:border-[#EBEBEB]': !memory,
                        'rb:cursor-pointer': config.app_type !== 'workflow',
                        'rb:cursor-not-allowed rb:opacity-65': config.app_type === 'workflow',
                      })}
                      onClick={handleChangeMemory}
                    >
                      <div className={clsx("rb:size-4 rb:bg-cover", {
                        "rb:bg-[url('@/assets/images/conversation/memoryFunction.svg')]": !memory,
                        "rb:bg-[url('@/assets/images/conversation/memoryFunctionChecked.svg')]": memory
                      })} />
                    </Flex>
                  </Tooltip>
                }
              </Flex>
              : undefined
            }
            onVariablesChange={handleChangeVariables}
          />
          </Chat>
        </div>
      </div>
    </Flex>
  )
}
export default Conversation
