import { type FC, useState, useEffect, useRef } from 'react'
import { useParams, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import InfiniteScroll from 'react-infinite-scroll-component';
import { Flex, Skeleton, Form } from 'antd'
import clsx from 'clsx'
import AnalysisEmptyIcon from '@/assets/images/conversation/analysisEmpty.svg'
import { getConversationHistory, sendConversation, getConversationDetail, getShareToken } from '@/api/application'
import type { HistoryItem, QueryParams } from './types'
import Empty from '@/components/Empty'
import { formatDateTime } from '@/utils/format';
import { randomString } from '@/utils/common'
import BgImg from '@/assets/images/conversation/bg.png'
import Chat from '@/components/Chat'
import type { ChatItem } from '@/components/Chat/types'
import ButtonCheckbox from '@/components/ButtonCheckbox'
import MemoryFunctionIcon from '@/assets/images/conversation/memoryFunction.svg'
import OnlineIcon from '@/assets/images/conversation/online.svg'
import OnlineCheckedIcon from '@/assets/images/conversation/onlineChecked.svg'
import MemoryFunctionCheckedIcon from '@/assets/images/conversation/memoryFunctionChecked.svg'
import dayjs from 'dayjs'
import { type SSEMessage } from '@/utils/stream'

const Conversation: FC = () => {
  const { t } = useTranslation()
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
  const [shareToken, setShareToken] = useState<string | null>(localStorage.getItem(`shareToken_${token}`))

  const [form] = Form.useForm<QueryParams>()
  const queryValues = Form.useWatch<QueryParams>([], form)
  useEffect(() => {
    const shareToken = localStorage.getItem(`shareToken_${token}`)
    setShareToken(shareToken)
    if (shareToken && shareToken !== '') return
    getShareToken(token as string, userId || randomString(12, false))
      .then(res => {
        const response = res as { access_token: string  } || {}
        localStorage.setItem(`shareToken_${token}`, response.access_token ?? '')
        setShareToken(response.access_token ?? '')
      })
  }, [token])

  useEffect(() => {
    if (token && page === 1 && hasMore && historyList.length === 0 && shareToken) {
      getHistory()
    }
  }, [token, shareToken, page, hasMore, historyList])

  // 按日期分组历史记录
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

  const getHistory = (flag: boolean = false) => {
    if (!token || (pageLoading || !hasMore) && !flag) {
      return
    }
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
      .finally(() => {
        setPageLoading(false);
      })
  }
  const handleChangeHistory = (id: string | null) => {
    if (id !== conversation_id) {
      setConversationId(id)
    }
    if (!id) {
      setMessage('')
    }
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

  const addUserMessage = (message: string = '') => {
    const newUserMessage: ChatItem = {
      conversation_id,
      role: 'user',
      content: message,
      created_at: Date.now()
    };
    setChatList(prev => [...prev, newUserMessage])
  }
  const addAssistantMessage = () => {
    const newAssistantMessage: ChatItem = {
      created_at: Date.now(),
      role: 'assistant',
      content: '',
    }
    setChatList(prev => [...prev, newAssistantMessage])
  }
  const updateAssistantMessage = (content: string = '') => {
    if (!content) return
    if (streamLoading) {
      setStreamLoading(false)
    }

    setChatList(prev => {
      const lastList = [...prev]
      const lastIndex = lastList.length - 1
      const lastMsg = lastList[lastIndex]
      if (lastMsg?.role === 'assistant') {
        return [
          ...lastList.slice(0, lastList.length - 1),
          {
            ...lastMsg,
            content: lastMsg.content + content
          }
        ]
      }
      return prev
    })
  }

  const handleSend = () => {
    if (!token || !shareToken) {
      return
    }
    setLoading(true)
    setStreamLoading(true)
    addUserMessage(message)
    addAssistantMessage()

    let currentConversationId: string | null = null
    const handleStreamMessage = (data: SSEMessage[]) => {
      data.forEach((item) => {
        switch(item.event) {
          case 'start':
            const { conversation_id: newId } = item.data as { conversation_id: string  }
            currentConversationId = newId
            break
          case 'message':
            const { content } = item.data as { content: string  }
            updateAssistantMessage(content)
            break
          case 'end':
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
      ...queryValues,
      message: message || '',
      stream: true,
      conversation_id: conversation_id || null,
    }, handleStreamMessage, shareToken)
      .finally(() => {
        setLoading(false)
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
              // endMessage={<Divider plain>It is all, nothing more 🤐</Divider>}
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
        <Chat
          empty={<Empty url={AnalysisEmptyIcon} className="rb:h-full" subTitle={t('memoryConversation.emptyDesc')} />}
          contentClassName="rb:h-[calc(100%-152px)]"
          data={chatList}
          streamLoading={streamLoading}
          loading={loading}
          onChange={setMessage}
          onSend={handleSend}
          labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
        >
          <Form form={form} initialValues={{ memory: false, web_search: false}}>
            <Flex gap={8}>
              <Form.Item name="web_search" valuePropName="checked" className="rb:mb-0!">
                <ButtonCheckbox
                  icon={OnlineIcon}
                  checkedIcon={OnlineCheckedIcon}
                >
                  {t(`memoryConversation.web_search`)}
                </ButtonCheckbox>
              </Form.Item>
              <Form.Item name="memory" valuePropName="checked" className="rb:mb-0!">
                <ButtonCheckbox
                  icon={MemoryFunctionIcon}
                  checkedIcon={MemoryFunctionCheckedIcon}
                >
                  {t(`memoryConversation.memory`)}
                </ButtonCheckbox>
              </Form.Item>
            </Flex>
          </Form>
        </Chat>
      </div>
    </Flex>
  )
}
export default Conversation