import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import clsx from 'clsx'
import { Divider, Flex, Skeleton } from 'antd'
import { useTranslation } from 'react-i18next'
import InfiniteScroll from 'react-infinite-scroll-component'

import { getApiMcpMessages } from '@/api/memory'
import Empty from '@/components/Empty'
import Markdown from '@/components/Markdown'
import { formatDateTime } from '@/utils/format'

export type ApiMcpSource = 'mcp' | 'service_api'

export interface ApiMcpMessageItem {
  role: 'user' | 'assistant';
  content: string;
  dialog_at: number;
  message_seq: number;
  created_at: number;
}

interface ApiMcpMessagesResponse {
  items: ApiMcpMessageItem[];
  page: {
    hasnext: boolean;
    total: number;
  };
}

export interface ApiMcpMessageListRef {
  refresh: () => void;
  total: number;
}

interface ApiMcpMessageListProps {
  endUserId: string;
  source: ApiMcpSource;
  onMessagesChange?: (messages: ApiMcpMessageItem[]) => void;
  filterParams?: {
    start_date?: number;
    end_date?: number;
    keyword?: string;
  }
}

const PAGE_SIZE = 20

const ApiMcpMessageList = forwardRef<ApiMcpMessageListRef, ApiMcpMessageListProps>(({
  endUserId,
  source,
  onMessagesChange,
  filterParams = {},
}, ref) => {
  const { t } = useTranslation()
  const [messages, setMessages] = useState<ApiMcpMessageItem[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [hasMore, setHasMore] = useState<boolean>(true)
  const messagesRef = useRef<ApiMcpMessageItem[]>([])
  const pageRef = useRef<number>(1)
  const loadingRef = useRef<boolean>(false)
  const requestRef = useRef<number>(0)
  const scrollableTarget = `api-mcp-message-list-${source}`
  const [total, setTotal] = useState<number>(0)

  const refresh = useCallback(() => {
    const requestId = ++requestRef.current
    pageRef.current = 1
    loadingRef.current = true
    messagesRef.current = []
    setMessages([])
    setHasMore(true)
    setLoading(true)
    onMessagesChange?.([])

    getApiMcpMessages(endUserId, {
      source,
      page: 1,
      pagesize: PAGE_SIZE,
      ...filterParams,
    })
      .then(res => {
        if (requestId !== requestRef.current) return
        const response = res as ApiMcpMessagesResponse
        const nextMessages = response.items || []
        messagesRef.current = nextMessages
        setMessages(nextMessages)
        setHasMore(Boolean(response.page?.hasnext))
        onMessagesChange?.(nextMessages)
        setTotal(response.page?.total || 0)
      })
      .catch(() => {
        if (requestId === requestRef.current) {
          setHasMore(false)
        }
      })
      .finally(() => {
        if (requestId !== requestRef.current) return
        loadingRef.current = false
        setLoading(false)
      })
  }, [endUserId, onMessagesChange, source, filterParams])

  const loadMore = useCallback(() => {
    if (loadingRef.current || !hasMore) return

    const requestId = requestRef.current
    const nextPage = pageRef.current + 1
    loadingRef.current = true

    getApiMcpMessages(endUserId, {
      source,
      page: nextPage,
      pagesize: PAGE_SIZE,
      ...filterParams,
    })
      .then(res => {
        if (requestId !== requestRef.current) return
        const response = res as ApiMcpMessagesResponse
        const nextMessages = [...messagesRef.current, ...(response.items || [])]
        messagesRef.current = nextMessages
        pageRef.current = nextPage
        setMessages(nextMessages)
        setHasMore(Boolean(response.page?.hasnext))
        onMessagesChange?.(nextMessages)
        setTotal(response.page?.total || 0)
      })
      .catch(() => {
        if (requestId === requestRef.current) {
          setHasMore(false)
        }
      })
      .finally(() => {
        if (requestId === requestRef.current) {
          loadingRef.current = false
        }
      })
  }, [endUserId, hasMore, onMessagesChange, source, filterParams])

  useImperativeHandle(ref, () => ({
    refresh,
    total,
  }), [refresh, total])

  useEffect(() => {
    refresh()
    return () => {
      requestRef.current += 1
      loadingRef.current = false
    }
  }, [refresh])

  if (loading) return <Skeleton active />
  if (messages.length === 0) return <Empty />

  return (
    <div
      id={scrollableTarget}
      className="rb:mt-5! rb:flex-1! rb:min-h-0! rb:overflow-y-auto! rb:w-full! rb:overflow-x-hidden!"
    >
      <InfiniteScroll
        dataLength={messages.length}
        next={loadMore}
        hasMore={hasMore}
        loader={<Skeleton active paragraph={{ rows: 1 }} />}
        scrollableTarget={scrollableTarget}
      >
        <Flex vertical gap={12}>
          {messages.map((item, index) => (
            <div key={`${item.message_seq}-${item.created_at}`}>
              {index !== 0 && <Divider className="rb:mt-1! rb:mb-3! rb:ml-11! rb:w-[calc(100%-44px)]!" />}
              <Flex align="start" gap={12}>
                <div className={clsx('rb:size-8 rb:bg-cover', {
                  'rb:bg-[url(@/assets/images/conversation/user.png)]': item.role === 'user',
                  'rb:bg-[url(@/assets/images/conversation/ai.png)]': item.role === 'assistant',
                })}></div>
                <div className="rb:flex-1">
                  <Flex gap={12} justify="space-between">
                    <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4.5 rb:mb-0.5">
                      {item.role === 'assistant' ? t('userMemory.assistant') : t('userMemory.user')}
                    </div>
                    <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4.5 rb:mb-0.5">
                      {formatDateTime(item.dialog_at)}
                    </div>
                  </Flex>
                  <Markdown content={item.content || ''} />
                </div>
              </Flex>
            </div>
          ))}
        </Flex>
      </InfiniteScroll>
    </div>
  )
})

ApiMcpMessageList.displayName = 'ApiMcpMessageList'

export default ApiMcpMessageList
