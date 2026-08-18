/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-12 14:42:02 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-14 14:51:27
 */
import { type FC, useEffect, useState, useMemo, useRef, Fragment } from 'react'
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
  getApiMcpDataSources,
  getMemoryInsightReport,
  getUserSummary,
} from '@/api/memory'
import { formatDateTime } from '@/utils/format'
import Empty from '@/components/Empty'
import RbAlert from '@/components/RbAlert'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import PageLoading from '@/components/Empty/PageLoading'
import type { Data as SummaryData } from '../components/AboutMe'
import { type Data as InsightData, INSIGHT_KEYS } from '../components/MemoryInsight'
import ApiMcpMessageList, {
  type ApiMcpMessageItem,
  type ApiMcpMessageListRef,
  type ApiMcpSource,
} from '../components/ApiMcpMessageList'

/** A conversation session entry in the sidebar list. */
export interface Conversation {
  title: string;
  id: string;
  /** Source/client type of the conversation, e.g. `mcp` / `api` / `mac`. */
  type?: string;
}

/**
 * AI-generated insight for a single conversation (used by `conversation`-type sessions),
 * including key takeaways, open questions, and an overall summary.
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

interface ApiMcpListItem {
  source: ApiMcpSource;
  message_count: number;
  latest_at: number;
}

/**
 * WorkingDetail – Three-column working-memory view for a user's conversations.
 *
 * Left column (360px): scrollable list of conversation sessions.
 * Centre column (fluid): real-time chat message stream for the selected conversation,
 *   with a refresh button and time-range indicator.
 * Right column (360px): AI-generated conversation insights – successful experiences
 *   and user-level memory insight (overview, key findings, behavior
 *   pattern, growth trajectory) followed by the user summary (about-me) data.
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
  const [messages, setMessages] = useState<ChatItem[] | ApiMcpMessageItem[]>([])
  const [detailLoading, setDetailLoading] = useState<boolean>(false)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [insightLoading, setInsightLoading] = useState<boolean>(false)
  const [insight, setInsight] = useState<InsightData>({} as InsightData)
  const [summaryLoading, setSummaryLoading] = useState<boolean>(false)
  const [summary, setSummary] = useState<SummaryData>({} as SummaryData)
  const [selected, setSelected] = useState<Conversation | ApiMcpListItem | null>(null)
  const apiMcpMessageListRef = useRef<ApiMcpMessageListRef>(null)

  /* Fetch conversation list + api/mcp data sources whenever the route user ID changes. */
  useEffect(() => {
    if (!id) return
    setLoading(true)
    setSelected(null)
    setDetail(null)
    setData([])
    setApiMcpList([])
    setHasMore(true)
    pageRef.current = 1
    Promise.all([getApiMcpList(), getData()])
      .then(([apiMcpItems, conversations]) => {
        // Prefer an api/mcp data source when available, otherwise fall back to the first conversation.
        setSelected(apiMcpItems.length > 0 ? apiMcpItems[0] : (conversations[0] || null))
      })
      .finally(() => {
        setLoading(false)
      })
  }, [id])

  const [apiMcpList, setApiMcpList] = useState<ApiMcpListItem[]>([])
  /** Load api/mcp data sources; resolves with the fetched list. */
  const getApiMcpList = () => {
    if (!id) return Promise.resolve([] as ApiMcpListItem[])
    return getApiMcpDataSources(id)
      .then((res) => {
        const response = (res as ApiMcpListItem[]) || []
        setApiMcpList(response)
        return response
      })
      .catch(() => [] as ApiMcpListItem[])
  }
  /** Load the first page of conversations; resolves with the fetched items. */
  const getData = () => {
    if (!id) return Promise.resolve([] as Conversation[])
    return getConversations(id, 1)
      .then((res) => {
        const response = res as { items: Conversation[], page: { hasnext: boolean } }
        setData(response.items)
        setHasMore(response.page.hasnext)
        return response.items
      })
      .catch(() => [] as Conversation[])
  }

  /**
   * Fetch user-level memory insight and user summary for the right column.
   * Both requests run in parallel and are independent of the selected conversation.
   */
  const getUserInsight = () => {
    if (!id) return
    setInsight({} as InsightData)
    setSummary({} as SummaryData)
    setInsightLoading(true)
    setSummaryLoading(true)
    getMemoryInsightReport(id)
      .then(res => {
        setInsight((res as InsightData) || {})
      })
      .finally(() => {
        setInsightLoading(false)
      })
    getUserSummary(id)
      .then(res => {
        setSummary((res as SummaryData) || {})
      })
      .finally(() => {
        setSummaryLoading(false)
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
    if (!id || !selected || (!(selected as Conversation)?.id && !(selected as ApiMcpListItem)?.source)) return
    getDetail(selected)
  }, [id, selected])

  /**
   * Fetch the chat messages for the selected conversation. For `conversation`-type
   * sessions, also fetch the per-conversation detail (takeaways / questions / summary).
   * `mcp` / `api` sessions instead reuse the user-level insight + summary.
   */
  const getDetail = (conversation: Conversation | ApiMcpListItem) => {
    if (!id || (!(conversation as Conversation).id && !(conversation as ApiMcpListItem).source)) return
    const conversationId = (conversation as Conversation).id

    setDetail(null)
    setMessages([])
    if (conversationId) {
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
    } else {
      setDetailLoading(false)
      setMessagesLoading(false)
      getUserInsight()
    }
  }

  const handleRefresh = () => {
    if ((selected as ApiMcpListItem)?.source) {
      apiMcpMessageListRef.current?.refresh()
      getUserInsight()
    } else if (selected) {
      getDetail(selected)
    }
  }
  /** Derive a human-readable date range (e.g. "2024.01 - 2024.03") from message timestamps. */
  const timeRange = useMemo(() => {
    const times = messages.filter(m => m.created_at).map(m => Number(m.created_at))
    if (times.length === 0) return ''
    const minTime = Math.min(...times)
    const maxTime = Math.max(...times)
    return `${formatDateTime(minTime, 'YYYY.MM')} - ${formatDateTime(maxTime, 'YYYY.MM')}`
  }, [messages])

  /** Whether the memory insight block has any renderable content. */
  const hasInsight = useMemo(
    () => INSIGHT_KEYS.some(key => {
      const value = insight[key]
      return Array.isArray(value) ? value.length > 0 : !!value
    }),
    [insight],
  )
  /** Whether the user summary block has any renderable content. */
  const hasSummary = useMemo(
    () => !!(summary.user_summary || summary.personality || summary.core_values || summary.one_sentence),
    [summary],
  )

  return (
    <>
      {loading
        ? <PageLoading />
        : data.length === 0 && apiMcpList.length === 0
        ? <Empty className="rb:h-full!" />
        :(
          <Row gutter={16} wrap={false} className="rb:h-full!">
            <Col span={5} className="rb:h-full!">
              <RbCard
                title={t('workingDetail.conversation')}
                headerType="borderless"
                headerClassName="rb:min-h-[58px]! rb:font-[MiSans-Bold] rb:font-bold"
                bodyClassName='rb:p-3! rb:pt-0! rb:h-[calc(100%-58px)]'
                className="rb:h-full!"
              >
                <div id="conversation-list" className="rb:h-full! rb:overflow-y-auto">
                  <InfiniteScroll
                    dataLength={data.length + apiMcpList.length}
                    next={loadMore}
                    hasMore={hasMore}
                    loader={null}
                    scrollableTarget="conversation-list"
                  >
                    <Flex vertical gap={8}>
                      {apiMcpList.map(item => (
                        <Flex
                          key={item.source}
                          gap={12}
                          align="center"
                          className={clsx("rb:cursor-pointer rb:rounded-xl rb:h-12 rb:py-1! rb:px-3! rb:hover:bg-[#F6F6F6]", {
                            'rb:bg-[#171719] rb:hover:bg-[#171719]! rb:text-white': item.source === (selected as ApiMcpListItem)?.source,
                          })}
                          onClick={() => setSelected(item)}
                        >
                          <div className="rb:leading-5 rb:break-all rb:line-clamp-2 rb:flex-1">
                            {t(`userMemory.${item.source}`)}
                          </div>
                        </Flex>
                      ))}
                      {data.map(item => (
                        <Flex
                          key={item.id}
                          gap={12}
                          align="center"
                          className={clsx("rb:cursor-pointer rb:rounded-xl rb:h-12 rb:py-1! rb:px-3! rb:hover:bg-[#F6F6F6]", {
                            'rb:bg-[#171719] rb:hover:bg-[#171719]! rb:text-white': item.id === (selected as Conversation)?.id,
                          })}
                          onClick={() => setSelected(item)}
                        >
                          <div className="rb:size-6 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/chat.svg')]"></div>
                          <Tooltip title={item.title}>
                            <div className="rb:leading-5 rb:break-all rb:line-clamp-2 rb:flex-1">
                              {item.title}
                            </div>
                          </Tooltip>
                          {item.type && (
                            <div className={clsx(
                              "rb:shrink-0 rb:uppercase rb:text-[10px] rb:leading-4 rb:px-1.5 rb:rounded-md rb:border",
                              item.id === (selected as Conversation)?.id
                                ? "rb:border-white/30 rb:text-white"
                                : "rb:border-[#E5E5E5] rb:text-[#5B6167]",
                            )}>
                              {item.type}
                            </div>
                          )}
                        </Flex>
                      ))}
                    </Flex>
                  </InfiniteScroll>
                </div>
              </RbCard>
            </Col>
            {selected && <>
              <Col flex="1" className="rb:h-full!">
                <RbCard
                  title={
                    (selected as Conversation).title
                    || ((selected as ApiMcpListItem).source ? t(`userMemory.${(selected as ApiMcpListItem).source}`) : undefined)
                  }
                  headerType="borderless"
                  headerClassName="rb:min-h-[42px]! rb:pt-4! rb:font-[MiSans-Bold] rb:font-bold"
                  bodyClassName='rb:p-4! rb:pt-0! rb:h-[calc(100%-42px)]! rb:overflow-hidden!'
                  className="rb:h-full!"
                >
                  <Flex vertical className="rb:h-full! rb:overflow-y-hidden!">
                    <div className="rb:text-[#5B6167] rb:leading-4.5 rb:text-[12px]">{timeRange}</div>
                    <Flex justify="space-between" align="center" className="rb:bg-[#F6F6F6] rb:rounded-lg rb:py-2.5! rb:pr-2.5! rb:pl-3.25! rb:mt-3!">
                      {t('workingDetail.conversationStream')}
                      <Button className="rb:h-6!" onClick={handleRefresh}>{t('workingDetail.refresh')}</Button>
                    </Flex>
                    {(selected as ApiMcpListItem).source && id
                      ? (
                        <ApiMcpMessageList
                          ref={apiMcpMessageListRef}
                          endUserId={id}
                          source={(selected as ApiMcpListItem).source}
                          onMessagesChange={setMessages}
                        />
                      )
                      : messagesLoading
                        ? <Skeleton active />
                        : messages.length === 0
                          ? <Empty />
                          : (
                            <ChatContent
                              classNames="rb:flex-1 rb:pt-5"
                              contentClassNames="rb:max-w-110!"
                              data={messages}
                              streamLoading={false}
                              labelFormat={(item) => formatDateTime(item.created_at)}
                            />
                          )
                    }
                  </Flex>
                </RbCard>
              </Col>
              <Col flex='360px' className="rb:h-full!">
                {(selected as ApiMcpListItem).source
                  ? (
                    <RbCard
                      headerType="borderless"
                      headerClassName="rb:min-h-0!"
                      bodyClassName='rb:p-4! rb:pt-0! rb:h-full rb:overflow-y-auto!'
                      className="rb:h-full!"
                    >
                      {insightLoading || summaryLoading
                        ? <Skeleton active />
                        : (!hasInsight && !hasSummary)
                          ? <Empty />
                          : <Flex vertical gap={16} className="rb:pt-4!">
                            {/* Memory insight */}
                            {INSIGHT_KEYS.filter(key => (Array.isArray(insight[key]) && insight[key].length > 0) || (!Array.isArray(insight[key]) && insight[key])).map((key, index) => {
                              const value = insight[key]
                              return (
                                <Fragment key={key}>
                                  {index > 0 && <Divider className="rb:my-0! rb:border-t-[0.5px]!" />}
                                  <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5 rb:mb-3">{t(`userMemory.${key}`)}</div>
                                  <div className="rb:leading-5 rb:text-[#5B6167]">
                                    {Array.isArray(value)
                                      ? value.map((vo, i) => <div key={i}>- {vo}</div>)
                                      : value}
                                  </div>
                                </Fragment>
                              )
                            })}
                            {/* User summary (about me) */}
                            {hasSummary && <>
                              {hasInsight && <Divider className="rb:my-0!" />}
                              {['user_summary', 'personality', 'core_values'].filter((key) => summary[key]).map((key, index) => {
                                return (
                                  <Fragment key={key}>
                                    {index > 0 && <Divider className="rb:my-0!" />}
                                    <div key={key}>
                                      <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5 rb:mb-3">{t(`userMemory.${key}`)}</div>
                                      {summary[key] &&
                                        <div className="rb:leading-5 rb:text-[#5B6167]">{summary[key]}</div>
                                      }
                                    </div>
                                  </Fragment>
                                )
                              })}
                              {summary.one_sentence &&
                                <RbAlert className="rb:text-[14px]!">{summary.one_sentence}</RbAlert>
                              }
                            </>}
                          </Flex>
                      }
                    </RbCard>
                  )
                  : (
                    <RbCard
                      title={t('workingDetail.successfulTitle')}
                      headerType="borderless"
                      headerClassName="rb:min-h-[50px]! rb:font-[MiSans-Bold] rb:font-bold rb:leading-5.5"
                      bodyClassName='rb:p-4! rb:pt-0! rb:h-[calc(100%-50px)] rb:overflow-y-auto!'
                      className="rb:h-full!"
                    >
                      {detailLoading
                        ? <Skeleton active />
                        : detail
                          ? <>
                            {detail.takeaways.length > 0
                              ? (
                                <ul className="rb:leading-5 rb:list-disc rb:ml-4">
                                  {detail.takeaways.map((vo, i) => <li key={i}>{vo}</li>)}
                                </ul>
                              )
                              : <Empty size={88} />
                            }

                            <Divider className="rb:my-4!" />
                            <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5 rb:mb-3">{t('workingDetail.question')}</div>
                            {detail.question.length > 0
                              ? (
                                <ul className="rb:leading-5 rb:list-disc rb:ml-4">
                                  {detail.question.map((vo, i) => <li key={i}>{vo}</li>)}
                                </ul>
                              )
                              : <Empty size={88} />
                            }

                            <Divider className="rb:my-4!" />
                            <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5 rb:mb-3">{t('workingDetail.summary')}</div>
                            {detail.summary
                              ? <div className="rb:leading-5.5">{detail.summary}</div>
                              : <Empty size={88} />
                            }
                          </>
                          : <Empty />
                      }
                    </RbCard>
                  )
                }
              </Col>
            </>}
          </Row>
        )
      }
    </>
  )
}
export default WorkingDetail