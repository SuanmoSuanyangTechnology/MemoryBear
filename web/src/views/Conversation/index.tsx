import { type FC, useState, useEffect, useRef } from 'react'
import { useParams, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import InfiniteScroll from 'react-infinite-scroll-component';
import { Flex, Skeleton } from 'antd'
import clsx from 'clsx'
import Chat, { type ChatItem } from '@/views/MemoryConversation/components/Chat'
import AnalysisEmptyIcon from '@/assets/images/conversation/analysisEmpty.svg'
import { getConversationHistory, sendConversation, getConversationDetail, getShareToken } from '@/api/application'
import type { HistoryItem } from './types'
import Empty from '@/components/Empty'
import { formatDateTime } from '@/utils/format';
import { randomString } from '@/utils/common'
import BgImg from '@/assets/images/conversation/bg.png'

const Conversation: FC = () => {
  const { t } = useTranslation()
  const { token } = useParams()
  const location = useLocation()
  const searchParams = new URLSearchParams(location.search)
  const userId = searchParams.get('user_id')
  const [loading, setLoading] = useState(false)
  const [chatLoading, setChatLoading] = useState(false)
  const [query, setQuery] = useState<{
    message?: string;
    web_search?: boolean;
    memory?: boolean;
    conversation_id?: string;
  }>({})
  const [conversation_id, setConversationId] = useState<string | null>(null)
  const [historyList, setHistoryList] = useState<HistoryItem[]>([])
  const [groupHistoryList, setGroupHistoryList] = useState<Record<string, HistoryItem[]>>({})
  const [chatList, setChatList] = useState<ChatItem[]>([])
  const [pageLoading, setPageLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [shareToken, setShareToken] = useState<string | null>(localStorage.getItem(`shareToken_${token}`))
  useEffect(() => {
    const shareToken = localStorage.getItem(`shareToken_${token}`)
    setShareToken(shareToken)
    if (shareToken && shareToken !== '') return
    getShareToken(token as string, userId || randomString(12, false))
      .then(res => {
        localStorage.setItem(`shareToken_${token}`, res?.access_token || '')
        setShareToken(res?.access_token || '')
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
        const response = res as { items: HistoryItem[], page: { hasnext: boolean } }
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
      setQuery({})
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

  const handleSend = () => {
    if (!token || !shareToken) {
      return
    }
    // 添加必需的id和conversation_id属性
    const newUserMessage: ChatItem = {
      conversation_id,
      role: 'user',
      content: query?.message || '',
      created_at: Date.now()
    };
    setChatList(prev => [...prev, newUserMessage])
    
    setLoading(true)
    setChatLoading(true)
    setChatList(prev => [...prev, {
      created_at: Date.now(),
      role: 'assistant',
      content: '',
    }])
    let currentConversationId: string | null = null
    const handleStreamMessage = (data: string) => {
      setChatLoading(false)
      try {
        const lines = data.split('\n');
        let currentEvent = '';

        
        for (let i = 0; i < lines.length; i++) {
          const line = lines[i].trim();
          
          if (line.startsWith('event:')) {
            currentEvent = line.substring(6).trim();
          } else if (line.startsWith('data:') && currentEvent === 'message') {
            const jsonData = line.substring(5).trim();
            const parsed = JSON.parse(jsonData);
            
            if (parsed.content) {
              setChatList(prev => prev.map((msg, msgIndex) => {
                if (msgIndex === prev!.length - 1 && msg.role === 'assistant') {
                  return { ...msg, content: msg.content + parsed.content };
                }
                return msg;
              }))
            }
          } else if (line.startsWith('data:') && currentEvent === 'start') {
            const jsonData = line.substring(5).trim();
            const parsed = JSON.parse(jsonData);
            currentConversationId = parsed.conversation_id
          } else if (currentEvent === 'end') {
            setLoading(false);
            if (currentConversationId && currentConversationId !== conversation_id) {
              setConversationId(currentConversationId)
              getHistory(true)
            }
          }
        }
      } catch (e) {
        console.error('Parse stream data error:', e);
      }
    };
    
    sendConversation(token as string, {
      message: query?.message || '',
      web_search: query?.web_search || false,
      memory: query?.memory || false,
      stream: true,
      conversation_id: conversation_id || null,
    }, handleStreamMessage, shareToken)
      .finally(() => {
        setLoading(false)
      })
  }

  return (
    <Flex className="rb:w-full rb:p-[-16px]!">
      <div className="rb:w-[345px] rb:h-[100vh] rb:overflow-hidden rb:border-r rb:border-[#EAECEE] rb:p-[12px]">
        <div className="rb:group rb:flex rb:items-center rb:justify-center rb:font-regular rb:cursor-pointer rb:mb-[20px] rb:border rb:border-[#DFE4ED] rb:hover:border-[#155EEF] rb:hover:text-[#155EEF] rb:rounded-[8px] rb:py-[10px]"
          onClick={() => handleChangeHistory(null)}
        >
          <div 
            className="rb:w-[20px] rb:h-[20px] rb:cursor-pointer rb:mr-[8px] rb:bg-cover rb:bg-[url('@/assets/images/conversation/conversation.svg')] rb:group-hover:bg-[url('@/assets/images/conversation/conversation_hover.svg')]" 
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
                <div key={date} className="rb:mt-[24px] rb:first:mt-0">
                  <div className="rb:leading-[20px] rb:text-[#5B6167] rb:mb-[8px] rb:pl-[4px] rb:font-regular">{date.replace(/\u200e|\u200f/g, '')}</div>
                  {items.map(item => (
                    <div key={item.updated_at} className="rb:mb-[12px]">
                      <div className={clsx("rb:p-[8px_13px] rb:rounded-[8px] rb:leading-[20px] rb:cursor-pointer rb:hover:bg-[#F0F3F8]", {
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
        <img src={BgImg} className="rb:absolute rb:bottom-0 rb:left-0 rb:w-[345px]" />
      </div>

      <div className="rb:relative rb:h-[100vh] rb:px-[16px] rb:flex-[1_1_auto]">
        <Chat
          source="conversation"
          empty={
            <Empty url={AnalysisEmptyIcon} subTitle={t('memoryConversation.emptyDesc')} />
          }
          query={query}
          data={chatList}
          loading={loading}
          onChange={setQuery}
          onSend={handleSend}
        />
      </div>
    </Flex>
  )
}
export default Conversation