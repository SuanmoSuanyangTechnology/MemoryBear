/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-12 14:42:02 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 11:55:36
 */
import { type FC, useEffect, useState, useMemo, useRef } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Skeleton, Button, Divider, Tooltip, Flex } from 'antd'


import InfiniteScroll from 'react-infinite-scroll-component'
import RbCard from '@/components/RbCard/Card'
import {
  getConversations,
  getConversationMessages,
  getConversationDetail,
} from '@/api/memory'
import { formatDateTime } from '@/utils/format'
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import PageLoading from '@/components/Empty/PageLoading'

/** A conversation session entry in the sidebar list. */
interface Conversation {
  title: string;
  id: string;
}

/**
 * AI-generated insight for a conversation, including key takeaways,
 * open questions, and an overall summary.
 */
interface Detail {
  theme: string;
  theme_intro: string;
  /** Core insight summary of the conversation. */
  summary: string;
  /** Open questions or pitfalls identified during the conversation. */
  question: string[];
  /** Successful experiences / key takeaways extracted from the conversation. */
  takeaways: string[];
  /** Information quality score. */
  info_score: number;
}

/**
 * WorkingDetail – Three-column working-memory view for a user's conversations.
 *
 * Left column (360px): scrollable list of conversation sessions.
 * Centre column (fluid): real-time chat message stream for the selected conversation,
 *   with a refresh button and time-range indicator.
 * Right column (360px): AI-generated conversation insights – successful experiences
 *   (takeaways), open questions / pitfalls, and a core summary.
 *
 * Route param `id` is the end-user ID.
 */
const WorkingDetail: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<Conversation[]>([])
  const [hasMore, setHasMore] = useState<boolean>(true)
  const pageRef = useRef<number>(1)
  const [messagesLoading, setMessagesLoading] = useState<boolean>(false)
  const [messages, setMessages] = useState<ChatItem[]>([])
  const [detailLoading, setDetailLoading] = useState<boolean>(false)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [selected, setSelected] = useState<Conversation | null>(null)

  /* Fetch conversation list whenever the route user ID changes. */
  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  /** Load all conversations for the current user and auto-select the first one. */
  const getData = () => {
    if (!id) return
    setLoading(true)
    setSelected(null)
    setDetail(null)
    setData([])
    setHasMore(true)
    pageRef.current = 1
    getConversations(id, 1).then((res) => {
      const response = res as { items: Conversation[], page: { hasnext: boolean } }
      setData(response.items)
      setSelected(response.items[0] || null)
      setHasMore(response.page.hasnext)
    })
    .finally(() => {
      setLoading(false)
    })
  }

  const loadMore = () => {
    if (!id) return
    const nextPage = pageRef.current + 1
    getConversations(id, nextPage).then((res) => {
      const response = res as {items: Conversation[], page: { hasnext: boolean }}
      setData(prev => [...prev, ...response.items])
      pageRef.current = nextPage
      setHasMore(response.page.hasnext)
    })
  }

  useEffect(() => {
    if (!id || !selected || !selected.id) return
    getDetail(selected.id)
  }, [id, selected])

  /**
   * Fetch both the chat messages and the AI-generated insight for a conversation.
   * Both requests run in parallel.
   */
  const getDetail = (conversationId: string) => {
    if (!id || !conversationId) return

    setDetail(null)
    setMessages([])
    setDetailLoading(true)
    setMessagesLoading(true)
    getConversationMessages(id, conversationId)
      .then(res => {
        setMessages(res as ChatItem[])
      })
      .finally(() => {
        setMessagesLoading(false)
      })
    getConversationDetail(id, conversationId)
      .then(res => {
        setDetail(res as Detail)
      })
      .finally(() => {
        setDetailLoading(false)
      })
  }
  /** Derive a human-readable date range (e.g. "2024.01 - 2024.03") from message timestamps. */
  const timeRange = useMemo(() => {
    const times = messages.filter(m => m.created_at).map(m => Number(m.created_at))
    if (times.length === 0) return ''
    const minTime = Math.min(...times)
    const maxTime = Math.max(...times)
    return `${formatDateTime(minTime, 'YYYY.MM')} - ${formatDateTime(maxTime, 'YYYY.MM')}`
  }, [messages])

  return (
    <>
      {loading
        ? <PageLoading />
        : data.length === 0
        ? <Empty />
        :(
          <Row gutter={16}>
            <Col span={5}>
              <RbCard
                title={t('workingDetail.conversation')}
                headerType="borderless"
                headerClassName="rb:min-h-[58px]! rb:font-[MiSans-Bold] rb:font-bold"
                bodyClassName='rb:p-3! rb:pt-0! rb:h-[calc(100%-58px)]'
                className="rb:h-[calc(100vh-88px)]!"
              >
                <div id="conversation-list" className="rb:h-full! rb:overflow-y-auto">
                  <InfiniteScroll
                    dataLength={data.length}
                    next={loadMore}
                    hasMore={hasMore}
                    loader={null}
                    scrollableTarget="conversation-list"
                  >
                    <Flex vertical gap={8}>
                      {data.map(item => (
                        <Flex
                          key={item.id}
                          gap={12}
                          align="center"
                          className={clsx("rb:cursor-pointer rb:rounded-xl rb:h-12 rb:py-1! rb:px-3! rb:hover:bg-[#F6F6F6]", {
                            'rb:bg-[#171719] rb:hover:bg-[#171719]! rb:text-white': item.id === selected?.id,
                          })}
                          onClick={() => setSelected(item)}
                        >
                          <div className="rb:size-6 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/chat.svg')]"></div>
                          <Tooltip title={item.title}>
                            <div className="rb:leading-5 rb:break-all rb:line-clamp-2 rb:flex-1">
                              {item.title}
                            </div>
                          </Tooltip>
                        </Flex>
                      ))}
                    </Flex>
                  </InfiniteScroll>
                </div>
              </RbCard>
            </Col>
            {selected && <>
              <Col flex="auto" className="rb:h-full">
                <RbCard
                  title={selected.title}
                  headerType="borderless"
                  headerClassName="rb:min-h-[42px]! rb:pt-4! rb:font-[MiSans-Bold] rb:font-bold"
                  bodyClassName='rb:p-4! rb:pt-0! rb:h-[calc(100%-42px)]'
                  className="rb:h-[calc(100vh-88px)]!"
                >
                  <div className="rb:text-[#5B6167] rb:leading-4.5 rb:text-[12px]">{timeRange}</div>
                  <Flex justify="space-between" align="center" className="rb:bg-[#F6F6F6] rb:rounded-lg rb:py-2.5! rb:pr-2.5! rb:pl-3.25! rb:mt-3!">
                    {t('workingDetail.conversationStream')}
                    <Button className="rb:h-6!" onClick={() => getDetail(selected.id)}>{t('workingDetail.refresh')}</Button>
                  </Flex>
                  {messagesLoading
                    ? <Skeleton active />
                    : messages.length === 0
                      ? <Empty />
                      : (
                        <ChatContent
                          classNames="rb:h-[calc(100%-77px)] rb:pt-5"
                          contentClassNames="rb:max-w-110!"
                          data={messages}
                          streamLoading={false}
                          labelFormat={(item) => formatDateTime(item.created_at)}
                        />
                      )
                  }
                </RbCard>
              </Col>
              <Col flex='360px' className="rb:h-full">
                <RbCard
                  title={t('workingDetail.successfulTitle')}
                  headerType="borderless"
                  headerClassName="rb:min-h-[50px]! rb:font-[MiSans-Bold] rb:font-bold rb:leading-5.5"
                  bodyClassName='rb:p-4! rb:pt-0! rb:h-[calc(100%-50px)] rb:overflow-y-auto!'
                  className="rb:h-[calc(100vh-88px)]!"
                >
                  {detailLoading
                    ? <Skeleton active />
                    : detail
                      ? <>
                        {detail.takeaways.length > 0
                          ? (
                            <ul className="rb:leading-5 rb:list-disc rb:ml-4">
                              {detail.takeaways.map(vo => <li>{vo}</li>)}
                            </ul>
                          )
                          : <Empty size={88} />
                        }

                        <>
                          <Divider className="rb:my-4!" />
                          <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5 rb:mb-3">{t('workingDetail.question')}</div>

                          {detail.question.length > 0
                            ? (
                              <ul className="rb:leading-5 rb:list-disc rb:ml-4">
                                {detail.question.map(vo => <li>{vo}</li>)}
                              </ul>
                            )
                            : <Empty size={88} />
                          }
                        </>

                        <>
                          <Divider className="rb:my-4!" />
                          <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5 rb:mb-3">{t('workingDetail.summary')}</div>
                          {detail.summary
                            ? <div className="rb:leading-5.5">{detail.summary}</div>
                            : <Empty size={88} />
                          }
                        </>
                      </>
                      : <Empty />
                  }
                </RbCard>
              </Col>
            </>}
          </Row>
        )
      }
    </>
  )
}
export default WorkingDetail