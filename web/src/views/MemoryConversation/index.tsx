import { type FC, type ReactNode, useEffect, useMemo, useState, useRef } from 'react'
import { App, Flex, Tooltip, Skeleton, Segmented } from 'antd'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { readServiceStream, userMemoryListUrl } from '@/api/memory'
import Chat from '@/components/Chat'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import type { ChatItem } from '@/components/Chat/types'
import { getMemoryStages } from './constants'
import { parseStreamEvents, getStageIndex } from './stream'
import type { LogItem, MemoryItem } from './types'
import StageContent from './components/StageContent'
import RbCard from '@/components/RbCard/Card';
import SearchInput from '@/components/SearchInput'
import Empty from '@/components/Empty'
import ConversationEmptyIcon from '@/assets/images/conversation/conversationEmpty.svg'
import AnalysisEmptyIcon from '@/assets/images/conversation/analysisEmpty.svg'
import { formatDateTime } from '@/utils/format'
import RequestSummaryCard from './components/RequestSummaryCard'
import styles from './index.module.css'
import Tag from '@/components/Tag'

/** Search mode configuration */
const searchSwitchList = [
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/deepThinking.svg')]"></div>,
    value: '0',
    key: 'deepThinking'
  },
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/normalReply.svg')]"></div>,
    value: '1',
    key: 'normalReply'
  },
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/quickReply.svg')]"></div>,
    value: '2',
    key: 'quickReply'
  },
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/quickReplyPlus.svg')]"></div>,
    value: '5',
    key: 'quickReplyPlus'
  },
]

const ContentWrapper: FC<{ children: ReactNode }> = ({ children }) => (
  <div className="rb-border-t rb:bg-white rb:px-3 rb:py-2.5 rb:text-[11px] rb:leading-[1.65] rb:text-[#697481] [&>p]:rb:m-0">
    {children}
  </div>
)

const MemoryConversation: FC = () => {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const pageScrollListRef = useRef<PageScrollListRef>(null)
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = useState<MemoryItem>()
  const [keyword, setKeyword] = useState('')
  const [search_switch, setSearchSwitch] = useState('2')
  const [input, setInput] = useState('')
  const [chatData, setChatData] = useState<ChatItem[]>([])
  const [logs, setLogs] = useState<LogItem[]>([])
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string>()
  const [layoutMode, setLayoutMode] = useState<'vertical' | 'mixed' | 'horizontal'>('horizontal')
  const abortRef = useRef<(() => void)>()
  const requestGenerationRef = useRef(0)
  console.log('layoutMode', layoutMode)

  const selectedId = selected?.end_user?.id || selected?.end_user_id
  const currentName = selected?.end_user?.other_name || selectedId || ''
  const query = useMemo(() => ({ keyword }), [keyword])
  const stageKeys = getMemoryStages(search_switch)
  const requestInput = logs[0]?.input
  const currentQuery =
    requestInput
      && typeof requestInput === 'object'
      && !Array.isArray(requestInput)
      && 'message' in requestInput
      && typeof requestInput.message === 'string'
      ? requestInput.message
      : null

  const cancelCurrentRequest = () => {
    requestGenerationRef.current += 1
    abortRef.current?.()
    abortRef.current = undefined
    setLoading(false)
  }

  useEffect(() => {
    const computeLayoutMode = () => {
      const width = window.innerWidth
      if (width >= 1280) return 'horizontal'
      if (width >= 1024) return 'mixed'
      return 'vertical'
    }
    const handleResize = () => setLayoutMode(computeLayoutMode())
    handleResize()
    window.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('resize', handleResize)
    }
  }, [])
  useEffect(() => () => {
    requestGenerationRef.current += 1
    abortRef.current?.()
  }, [])

  const handleSelect = (item: MemoryItem) => {
    cancelCurrentRequest()
    setSelected(item)
    setChatData([])
    setLogs([])
    setExpanded({})
    setSessionId(undefined)
  }

  const handleClearSelection = () => {
    setSelected(undefined)
    setChatData([])
    setLogs([])
    setExpanded({})
    setSessionId(undefined)
  }
  /** Handle keyword mode change */
  const handleChange = (value: string) => {
    cancelCurrentRequest()
    setSearchSwitch(value)
    setLogs([])
    setExpanded({})
    setChatData([])
    setSessionId(undefined)
  }

  const updateLogs = (log: LogItem) => {
    const stageIndex = getStageIndex(log, stageKeys)
    setLogs(previous => {
      if (stageIndex !== undefined) {
        const next = [...previous]
        const current = next[stageIndex] || {}
        const currentData = current.data && typeof current.data === 'object' && !Array.isArray(current.data)
          ? current.data as Record<string, unknown>
          : {}
        const incomingData = log.data && typeof log.data === 'object' && !Array.isArray(log.data)
          ? log.data as Record<string, unknown>
          : {}
        const data = { ...currentData, ...incomingData }
        if (log.append_answer && typeof incomingData.answer === 'string') {
          data.answer = `${typeof currentData.answer === 'string' ? currentData.answer : ''}${incomingData.answer}`
        }
        next[stageIndex] = {
          ...current,
          ...log,
          data,
        }
        return next
      }
      const key = log.stage || log.title || log.type
      const index = previous.findIndex(item => (item?.stage || item?.title || item?.type) === key)
      if (index < 0) return [...previous, log]
      const next = [...previous]
      next[index] = { ...next[index], ...log }
      return next
    })
    if (stageIndex !== undefined) {
      setExpanded(previous => ({ ...previous, [stageIndex]: true }))
    }
  }

  const updateStream = (
    events: Parameters<typeof parseStreamEvents>[0],
    generation: number,
  ) => {
    if (generation !== requestGenerationRef.current) return

    parseStreamEvents(events).forEach(update => {
      if (update.sessionId) setSessionId(update.sessionId)
      if (update.log) updateLogs(update.log)
      if (update.answer !== undefined) {
        setChatData(previous => {
          const last = previous[previous.length - 1]
          const content = update.appendAnswer
            ? `${last?.role === 'assistant' ? last.content || '' : ''}${update.answer}`
            : update.answer
          if (last?.role === 'assistant') {
            return [
              ...previous.slice(0, -1),
              { ...last, content },
            ]
          }
          return [
            ...previous,
            { role: 'assistant', content, created_at: Date.now() },
          ]
        })
      }
      if (update.completed) {
        setLoading(false)
        abortRef.current?.()
        abortRef.current = undefined
      }
    })
  }

  const handleSend = () => {
    if (!selectedId) {
      message.warning(t('memoryConversation.selectMemoryPlaceholder'))
      return
    }
    if (loading || !input.trim()) return

    const text = input.trim()
    const generation = requestGenerationRef.current + 1
    const request = {
      end_user_id: selectedId,
      message: text,
      search_switch,
      session_id: sessionId,
    }
    requestGenerationRef.current = generation
    setInput('')
    setChatData(previous => [...previous, { role: 'user', content: text, created_at: Date.now() }])
    setLogs([{
      type: 'start',
      stage: 'start',
      status: 'running',
      input: request,
      data: { search_switch, session_id: sessionId },
    }])
    setExpanded({ 0: true })
    setLoading(true)

    readServiceStream(
      request,
      events => updateStream(events, generation),
      abort => {
        if (generation === requestGenerationRef.current) {
          abortRef.current = abort
        } else {
          abort()
        }
      },
    ).catch(() => {
      if (generation !== requestGenerationRef.current) return
      updateLogs({
        type: 'final_answer',
        stage: 'final_answer',
        status: 'failed',
        data: { reason: t('memoryConversation.serverError') },
      })
      message.error(t('memoryConversation.serverError'))
    })
    .finally(() => {
      if (generation !== requestGenerationRef.current) return
      setLoading(false)
      abortRef.current = undefined
    })
  }

  return (
    <Flex gap={16} vertical={layoutMode === 'vertical'}
      className={clsx("rb:h-full! rb:w-full!", {
        'rb:overflow-hidden!': layoutMode !== 'vertical',
        'rb:overflow-y-auto!': layoutMode === 'vertical',
      })}
    >
      <RbCard
        avatar={
          <Flex align="center" justify="center" className="rb:bg-[#171719] rb:size-8 rb:rounded-lg">
            <div className="rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/userMemory_active.svg')]" />
          </Flex>
        }
        title={t('memoryConversation.selectMemory')}
        subTitle={t('memoryConversation.switchReset')}
        headerType="borderless"
        headerClassName="rb:py-2! rb:px-3! rb:min-h-[64px]!"
        className={clsx("rb:h-full! rb:shrink-0! rb:overflow-hidden!", {
          'rb:w-72!': layoutMode !== 'vertical',
          'rb:w-full!': layoutMode === 'vertical'
        })}
        bodyClassName="rb:h-[calc(100%-64px)]! rb:overflow-hidden! rb:px-3! rb:pt-1! rb:pb-3!"
      >
        <Flex vertical justify="space-between" gap={8} className="rb:h-full! rb:overflow-hidden">
          <div>
            <div className="rb:mb-2 rb:text-[12px] rb:text-[#5B6167]">
              {t('memoryConversation.memoryId')}
            </div>
            <SearchInput
              value={keyword}
              onChange={event => setKeyword(event.target.value)}
              placeholder={t('memoryConversation.searchSupport')}
              className="rb:w-full! rb:mb-2!  "
              size="small"
              variant="outlined"
            />
            <Flex align="center" justify="space-between" className="rb:text-[12px] rb:text-[#5B6167]">
              <span>{t('memoryConversation.availableMemory')}</span>
              <span>{t('memoryConversation.resultCount', { count: total ?? 0 })}</span>
            </Flex>
          </div>
          <PageScrollList<MemoryItem>
            ref={pageScrollListRef}
            url={userMemoryListUrl}
            query={query}
            column={1}
            gutter={0}
            heightClass="rb:flex-1! rb:min-h-0!"
            renderItem={item => {
              const name = item.end_user?.other_name || item.end_user?.id || item.end_user_id || t('memoryConversation.unnamedMemory')
              const id = item.end_user?.id || item.end_user_id || ''
              const active = id === selectedId
              return (
                <Flex
                  align="center"
                  gap={8}
                  className={clsx(`rb:w-full rb:cursor-pointer rb:rounded-lg rb:p-2!`, {
                    'rb:bg-[rgba(21,94,239,0.04)] rb:hover:bg-[rgba(21,94,239,0.08)]': active,
                    'rb:bg-[rgba(255,255,255,0.04)] rb:hover:bg-[#F6F6F6]': !active,
                  })}
                  onClick={() => handleSelect(item)}
                >
                  <div className={clsx("rb:size-6 rb:text-center rb:font-semibold rb:leading-6 rb:rounded-md rb:shrink-0", {
                    'rb:bg-[#171719] rb:text-white': active,
                    'rb:bg-[#EBEBEB]': !active,
                  })}>
                    {name[0]}
                  </div>
                  <div className="rb:flex-1 rb:overflow-hidden">
                    <Tooltip title={name}>
                      <div className="rb:overflow-hidden rb:text-xs rb:text-ellipsis rb:whitespace-nowrap">{name}</div>
                    </Tooltip>
                    <Tooltip title={id}>
                      <div className="rb:overflow-hidden rb:text-[10px] rb:text-ellipsis rb:whitespace-nowrap rb:text-[#5B6167]">{id}</div>
                    </Tooltip>
                  </div>
                  {active &&
                    <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/check_green.svg')] rb:shrink-0" />
                  }
                </Flex>
              )
            }}
            empty={<Empty size={88} />}
            pageLoading={
              <Skeleton paragraph={{ rows: 2 }} />
            }
            onTotalChange={setTotal}
          />
          {selected &&
            <div className="rb:cursor-pointer rb-border-t rb:pt-2 rb:text-[12px] rb:text-[#5B6167] rb:hover:text-[#171719] rb:text-center"
              onClick={handleClearSelection}
            >
              {t('memoryConversation.clearSelection')}
            </div>
          }
          <div className="rb-border-t rb:px-0.75 rb:pt-2 rb:text-[12px]">
            <Flex gap={8} align="center">
              <div className="rb:size-1.5 rb:rounded-full rb:bg-[#369F21]"></div>
              {t('memoryConversation.currentContext')}
            </Flex>
            <p className="rb:mt-1.5 rb:mb-0.5 rb:font-medium">
              {selected ? currentName : t('memoryConversation.noSelectMemory')}
            </p>
            <p className="rb:text-[10px] rb:mb-0.5">
              {selectedId || '-'}
            </p>
            <Flex align="center" gap={12} className="rb:text-[10px] rb:text-[#5B6167]">
              <span>{selected?.memory_num?.total ?? 0} {t('userMemory.memoryNum')}</span>
              <span>{selected?.memory_config?.memory_config_name || t('memoryConversation.memoryNotConnected')}</span>
            </Flex>
          </div>
        </Flex>
      </RbCard>
      
      <Flex vertical={layoutMode !== 'horizontal'}
        className={clsx({
          'rb:h-full! rb:min-h-0! rb:overflow-y-auto! rb:flex-1!': layoutMode === 'mixed',
          'rb:gap-x-4! rb:flex-1!': layoutMode === 'horizontal',
          'rb:gap-y-4!': layoutMode !== 'horizontal',
        })}
      >
        <RbCard
          title={t('memoryConversation.conversationContent')}
          subTitle={t('memoryConversation.sendQuery')}
          extra={
            <Tag color="default" className="rb:text-[10px] rb:w-[150px]!">
              {selectedId || t('memoryConversation.selectMemoryFirst')}
            </Tag>
          }
          headerType="borderless"
          headerClassName="rb:py-2! rb:min-h-[64px]!"
          className={clsx("rb:overflow-hidden!", {
            'rb:h-full! rb:flex-1!': layoutMode === 'horizontal',
            'rb:shrink-0!': layoutMode === 'mixed',
            'rb:h-[500px]! rb:w-full! ': layoutMode === 'vertical',
          })}
          bodyClassName="rb:h-[calc(100%-64px)]! rb:overflow-hidden! rb:px-0! rb:pt-1! rb:pb-0!"
        >
          <Chat
            empty={
              <Empty url={ConversationEmptyIcon} className="rb:h-full" size={[140, 100]} title={t('memoryConversation.conversationContentEmpty')} isNeedSubTitle={false} />
            }
            className="rb:pt-0!"
            contentClassName='rb:h-[calc(100%-144px)] rb:px-4!'
            data={chatData}
            message={input}
            onChange={setInput}
            onSend={handleSend}
            loading={loading}
            streamLoading={loading}
            labelFormat={(item) => formatDateTime(item.created_at, 'MMMM D, YYYY [at] h:mm A')}
          >
            <Segmented
              options={searchSwitchList.map(item => ({
                ...item,
                icon: <Tooltip title={t(`memoryConversation.${item.key}`)}>{item.icon}</Tooltip>
              }))}
              value={search_switch}
              shape="round"
              className={styles.segmented}
              onChange={handleChange}
            />
          </Chat>
        </RbCard>

        <RbCard
          title={t('memoryConversation.analysis')}
          subTitle={selectedId || t('memoryConversation.waitingValidation')}
          headerType="borderless"
          headerClassName="rb:py-2! rb:min-h-[64px]!"
          className={clsx("rb:shrink-0! rb:overflow-hidden!", {
            'rb:w-100! rb:h-full!': layoutMode === 'horizontal',
            'rb:shrink-0! rb:h-[500px]! rb:w-full!': layoutMode === 'mixed',
            'rb:h-[500px]! rb:w-full!': layoutMode === 'vertical',
          })}
          bodyClassName="rb:h-[calc(100%-64px)]! rb:overflow-hidden! rb:px-3! rb:pt-1! rb:pb-3!"
        >
          {selected
            ? (
              <Flex vertical gap={12} className="rb:h-full! rb:overflow-auto!">
                {currentQuery &&
                  <RequestSummaryCard
                    log={logs[0]}
                    query={currentQuery}
                    searchSwitch={search_switch}
                  />
                }
                <Flex vertical gap={8}>
                  {stageKeys.map((stageKey, index) => {
                    const stage = t(`memoryConversation.stages.${stageKey}`)
                    const log = stageKey === 'problemSplit' && index > 0 ? {
                      ...logs[index],
                      data: {
                        ...(logs[index]?.data || {}),
                        original_query: (logs[index-1]?.data as {original_query?: string})?.original_query,
                      }
                    } : logs[index]
                    const canExpand = stageKey !== 'hybridRetrieval'
                    const isOpen = canExpand && (expanded[index] ?? Boolean(log))
                    const statusKey = !log
                      ? loading
                        ? 'running'
                        : 'waiting'
                      : log.status === 'failed'
                        ? 'failed'
                        : log.status === 'completed'
                          ? 'completed'
                          : 'running'
                    const stageBadge = t(`memoryConversation.${statusKey}`)
                    return (
                      <Flex key={stageKey} gap={10}
                        className="rb:relative rb:after:absolute rb:after:top-8 rb:after:-bottom-2 rb:after:left-[11.5px] rb:after:content-[''] rb:after:w-px rb:after:bg-[#EBEBEB] rb:last:after:hidden"
                      >
                        <Flex
                          align="center"
                          justify="center"
                          className="rb:size-6 rb-border rb:rounded-full rb:mt-1! rb:relative rb:z-1"
                        >
                          <Flex
                            align="center"
                            justify="center"
                            className={clsx("rb:size-4 rb:rounded-full rb:text-[10px] rb:text-white", {
                              'rb:bg-[#B9BEC6]': statusKey === 'waiting',
                              'rb:bg-[#171719]': statusKey === 'running',
                              'rb:bg-[rgba(54,159,33)]': statusKey === 'completed',
                              'rb:bg-[rgba(255,138,76)]': statusKey === 'failed',
                            })}
                          >
                            {index + 1}
                          </Flex>
                        </Flex>
                        <div className="rb:flex-1 rb:overflow-hidden rb:rounded-lg rb-border rb:bg-[#F6F6F6]">
                          <Flex align="center" gap={8}
                            className={clsx('rb:min-h-10.5 rb:w-full rb:border-0 rb:bg-transparent rb:px-2.75! rb:text-left', {
                              'rb:cursor-pointer': canExpand,
                              'rb:cursor-default': !canExpand,
                            })}
                            onClick={() => {
                              if (canExpand) {
                                setExpanded(previous => ({ ...previous, [index]: !isOpen }))
                              }
                            }}
                          >
                            <b className="rb:flex-1 rb:text-xs rb:font-semibold">{log?.title || stage}</b>
                            <Tag
                              color={
                                statusKey === 'completed'
                                  ? 'success'
                                  : statusKey === 'failed'
                                    ? 'error'
                                    : statusKey === 'waiting'
                                      ? 'default'
                                      : 'processing'
                              }
                              size="small"
                              className="rb:shrink-0"
                            >
                              {stageBadge}
                            </Tag>
                            {canExpand && (
                              <div
                                className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:transition-transform", {
                                  'rb:rotate-180': !isOpen,
                                  'rb:rotate-0': isOpen,
                                })}
                              />
                            )}
                          </Flex>
                          {canExpand && isOpen && log &&
                            <ContentWrapper>
                              <StageContent stage={stageKey} log={log} />
                            </ContentWrapper>
                          }
                        </div>
                      </Flex>
                    )
                  })}
                </Flex>
              </Flex>
            )
            : <Empty url={AnalysisEmptyIcon} className="rb:h-full!" />
          }
        </RbCard>
      </Flex>
    </Flex>
  )
}

export default MemoryConversation
