/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:58:03 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-17 15:39:17
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
import { Flex, Skeleton, App } from 'antd'
import clsx from 'clsx'
import dayjs from 'dayjs'

import { getConversationHistory, sendConversation, getConversationDetail, getShareToken, getExperienceConfig } from '@/api/application'
import type { HistoryItem } from './types'
import Empty from '@/components/Empty'
import { formatDateTime } from '@/utils/format';
import { randomString } from '@/utils/common'
import BgImg from '@/assets/images/conversation/bg.png'
import ChatEmpty from '@/assets/images/empty/chatEmpty.png'
import Chat from '@/components/Chat'
import type { ChatItem } from '@/components/Chat/types'
import ButtonCheckbox from '@/components/ButtonCheckbox'
import MemoryFunctionIcon from '@/assets/images/conversation/memoryFunction.svg'
import OnlineIcon from '@/assets/images/conversation/online.svg'
import OnlineCheckedIcon from '@/assets/images/conversation/onlineChecked.svg'
import MemoryFunctionCheckedIcon from '@/assets/images/conversation/memoryFunctionChecked.svg'
import { type SSEMessage } from '@/utils/stream'
import { shareFileUploadUrlWithoutApiPrefix } from '@/api/fileStorage'
import ChatToolbar, { type ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import type { Variable } from '@/views/Workflow/components/Properties/VariableList/types'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';

const Conversation: FC = () => {
  const { t } = useTranslation()
  const { message: messageApi, modal } = App.useApp()
  const { token } = useParams()
  const location = useLocation()
  const searchParams = new URLSearchParams(location.search)
  const userId = searchParams.get('user_id')
  const [loading, setLoading] = useState(false)
  const [streamLoading, setStreamLoading] = useState(false)
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
  const [shareToken, setShareToken] = useState<string | null>(localStorage.getItem(`shareToken_${token}`))
  const [fileList, setFileList] = useState<any[]>([])
  const [webSearch, setWebSearch] = useState(false)
  const [memory, setMemory] = useState(true)
  const [features, setFeatures] = useState<FeaturesConfigForm>({} as FeaturesConfigForm)

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
          const response = res as { variables: Variable[]; features: FeaturesConfigForm }
          toolbarRef.current?.setVariables(response.variables || [])
          setFeatures(response.features)
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
    if (conversation_id) {
      getConversationDetail(token as string, conversation_id)
        .then(res => {
          const response = res as { messages: ChatItem[] }
          setChatList(response?.messages || [])
        })
    } else {
      setChatList([])
    }
  }, [conversation_id])

  const addUserMessage = (message: string = '', files?: any[]) => {
    setChatList(prev => [...prev, {
      conversation_id,
      role: 'user',
      content: message,
      created_at: Date.now(),
      files
    }])
  }

  const addAssistantMessage = () => {
    setChatList(prev => [...prev, {
      created_at: Date.now(),
      role: 'assistant',
      content: ''
    }])
  }

  const updateAssistantMessage = (content: string = '', audio_url?: string) => {
    if (!content && !audio_url) return
    if (streamLoading) setStreamLoading(false)
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
            audioUrl: audio_url
          }
        ]
      }
      return prev
    })
  }

  /** Send message and handle streaming response */
  const handleSend = () => {
    if (!token || !shareToken) return
    const files = toolbarRef.current?.getFiles() || []
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
    setStreamLoading(true)
    addUserMessage(message, files)
    addAssistantMessage()
    toolbarRef.current?.setFiles([])
    setFileList([])

    let currentConversationId: string | null = null
    const handleStreamMessage = (data: SSEMessage[]) => {
      data.forEach((item) => {
        const { content, conversation_id: curId, audio_url } = item.data as { content: string; conversation_id: string; audio_url?: string; }
        switch (item.event) {
          case 'start':
          case 'node_start':
            const { conversation_id: newId } = item.data as { conversation_id: string }
            currentConversationId = newId
            break
          case 'message':
            updateAssistantMessage(content, audio_url)
            if (curId) currentConversationId = curId;
            break
          case 'end':
          case 'workflow_end':
            if (audio_url) {
              updateAssistantMessage(content, audio_url)
            }
            setLoading(false)
            if (currentConversationId && currentConversationId !== conversation_id) {
              setConversationId(currentConversationId)
            }
            getHistory(true)
            break
        }
      })
    };

    sendConversation({
      web_search: webSearch,
      memory,
      message: message || '',
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
      variables: params
    }, handleStreamMessage, shareToken)
      .catch(() => {
        setLoading(false)
        setStreamLoading(false)
      })
      .finally(() => {
        setLoading(false)
        setStreamLoading(false)
      })
  }

  const handleChangeMemory = (value: boolean) => {
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

  return (
    <Flex className="rb:w-full rb:p-[-16px]!">
      <div className="rb:w-86.25 rb:h-screen rb:overflow-hidden rb:border-r rb:border-[#EAECEE] rb:p-3">
        <div className="rb:group rb:flex rb:items-center rb:justify-center rb:font-regular rb:cursor-pointer rb:mb-5 rb:border rb:border-[#DFE4ED] rb:hover:border-[#155EEF] rb:hover:text-[#155EEF] rb:rounded-lg rb:py-2.5"
          onClick={() => handleChangeHistory(null)}
        >
          <div
            className="rb:w-5 rb:h-5 rb:cursor-pointer rb:mr-2 rb:bg-cover rb:bg-[url('@/assets/images/conversation/conversation.svg')] rb:group-hover:bg-[url('@/assets/images/conversation/conversation_hover.svg')]"
          ></div>
          {t('memoryConversation.startANewConversation')}
        </div>
        {historyList.length > 0 &&
          <div
            ref={scrollRef}
            id="scrollableDiv"
            className="rb:overflow-y-auto rb:h-[calc(100vh-255px)]"
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
                </div>
              ))}
            </InfiniteScroll>
          </div>
        }
        <img src={BgImg} className="rb:absolute rb:bottom-0 rb:left-0 rb:w-86.25" />
      </div>

      <div className="rb:relative rb:h-screen rb:px-4 rb:flex-[1_1_auto]">
        <div className='rb:w-190 rb:h-screen rb:mx-auto rb:pt-10'>
          <Chat
            empty={<Empty url={ChatEmpty} className="rb:h-full" size={[320, 180]} title={t('memoryConversation.chatEmpty')} subTitle={t('memoryConversation.emptyDesc')} />}
            contentClassName={!fileList.length ? "rb:h-[calc(100%-144px)]" : "rb:h-[calc(100%-208px)]"}
            data={chatList}
            streamLoading={streamLoading}
            loading={loading}
            onChange={setMessage}
            onSend={handleSend}
            labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
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
            extra={
              <>
                {features.web_search?.enabled &&
                  <ButtonCheckbox
                    icon={OnlineIcon}
                    checkedIcon={OnlineCheckedIcon}
                    checked={webSearch}
                    onChange={setWebSearch}
                  >
                    {t('memoryConversation.web_search')}
                  </ButtonCheckbox>
                }
                <ButtonCheckbox
                  icon={MemoryFunctionIcon}
                  checkedIcon={MemoryFunctionCheckedIcon}
                  checked={memory}
                  onChange={handleChangeMemory}
                >
                  {t('memoryConversation.memory')}
                </ButtonCheckbox>
              </>
            }
          />
          </Chat>
        </div>
      </div>
    </Flex>
  )
}
export default Conversation
