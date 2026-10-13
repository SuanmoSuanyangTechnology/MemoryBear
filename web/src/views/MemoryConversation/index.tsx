import { type FC, useEffect, useMemo, useState, useRef } from 'react'
import { App, Flex, Tooltip, Skeleton, Segmented } from 'antd'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { readServiceStream, userMemoryListUrl } from '@/api/memory'
import Chat from '@/components/Chat'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import type { ChatItem } from '@/components/Chat/types'
import { getMemoryStages } from './constants'
import { markIncompleteStagesFailed, markLastAssistantMessageFailed } from './state'
import { parseStreamEvents, getStageIndex } from './stream'
import type { LogItem, MemoryItem } from './types'
import RbCard from '@/components/RbCard/Card';
import SearchInput from '@/components/SearchInput'
import Empty from '@/components/Empty'
import ConversationEmptyIcon from '@/assets/images/conversation/conversationEmpty.svg'
import { formatDateTime } from '@/utils/format'
import RequestSummaryCard from './components/RequestSummaryCard'
import styles from './index.module.css'
import StageCard from './components/StageCard'

/** Search mode configuration */
const searchSwitchList = [
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/deepThinking.svg')]" />,
    value: '0',
    key: 'deepThinking'
  },
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/normalReply.svg')]" />,
    value: '1',
    key: 'normalReply'
  },
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/quickReply.svg')]" />,
    value: '2',
    key: 'quickReply'
  },
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/quickReplyPlus.svg')]" />,
    value: '5',
    key: 'quickReplyPlus'
  },
]

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
  const abortRef = useRef<(() => void)>()
  const requestGenerationRef = useRef(0)

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
    cancelCurrentRequest()
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
      if (update.error) {
        const errorMessage = update.error.message?.trim() || t('memoryConversation.serverError')
        setChatData(previous => markLastAssistantMessageFailed(previous, errorMessage))
        setLogs(previous => markIncompleteStagesFailed(previous, stageKeys))
      }
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
    )
    .finally(() => {
      if (generation !== requestGenerationRef.current) return
      setLoading(false)
      abortRef.current = undefined
    })
  }

  return (
    <Flex gap={12}
      className="rb:h-full! rb:w-full! rb:overflow-hidden!"
    >
      <RbCard
        title={t('memoryConversation.selectMemory')}
        subTitle={t('memoryConversation.switchReset')}
        headerClassName="rb:py-2! rb:px-0! rb:m-[0_16px]! rb:min-h-[64px]!"
        className="rb:h-full! rb:shrink-0! rb:overflow-hidden! rb:w-[24%]"
        bodyClassName="rb:h-[calc(100%-64px)]! rb:overflow-hidden! rb:py-3! rb:px-2!"
      >
        <Flex vertical justify="space-between" gap={12} className="rb:h-full! rb:overflow-hidden">
          <div className="rb:mx-2 rb-border-b">
            <Flex align="center" justify="space-between" className="rb:text-gray-600 rb:leading-5">
              {t('memoryConversation.currentContext')}
              {selected &&
                <div className="rb:size-4.5 rb:bg-cover rb:bg-[url('@/assets/images/common/delete_red.svg')]" onClick={handleClearSelection} />
              }
            </Flex>

            {selected
              ? <div>
                <Flex align="center" justify="space-between" gap={24} className="rb:mt-3! rb:leading-4">
                  <Tooltip title={currentName}>
                    <span className="rb:font-medium rb:overflow-hidden rb:text-ellipsis rb:whitespace-nowrap rb:flex-1">
                      {currentName}
                    </span>
                  </Tooltip>
                  <span className="rb:text-gray-500 rb:text-[12px] rb:shrink-0">{selected?.memory_num?.total ?? 0} {t('userMemory.memoryNum')}</span>
                </Flex>
                <p className="rb:text-[12px] rb:text-gray-600 rb:overflow-hidden rb:text-ellipsis rb:whitespace-nowrap rb:mt-3">
                  ID: <Tooltip title={selectedId || '-'}>{selectedId || '-'}</Tooltip>
                </p>
                <p className="rb:text-[12px] rb:text-gray-600 rb:overflow-hidden rb:text-ellipsis rb:whitespace-nowrap rb:mt-1 rb:mb-3">
                  {t('memoryConversation.memoryConfig')}: <Tooltip title={selected?.memory_config?.memory_config_name || t('memoryConversation.memoryNotConnected')}>{selected?.memory_config?.memory_config_name || t('memoryConversation.memoryNotConnected')}</Tooltip>
                </p>
              </div>
              : <div className="rb:my-9.5 rb:px-3 rb:text-gray-500 rb:text-[12px] rb:text-center">{t('memoryConversation.noSelectMemory')}</div>
            }

          </div>
          
          <div className="rb:mx-2">
            <SearchInput
              value={keyword}
              onChange={event => setKeyword(event.target.value)}
              placeholder={t('memoryConversation.searchSupport')}
              className="rb:w-full! rb:h-7! rb:mb-3"
              size="small"
              variant="outlined"
            />
            <Flex align="center" justify="space-between" className="rb:text-[12px] rb:leading-4 rb:text-gray-600">
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
                    'rb:bg-[rgba(21,94,239,0.08)]': active,
                    'rb:hover:bg-gray-100': !active,
                  })}
                  onClick={() => handleSelect(item)}
                >
                  <div className={clsx("rb:size-7 rb:text-center rb:font-medium rb:leading-7 rb:rounded-md rb:shrink-0", {
                    'rb:bg-blue-500 rb:text-white': active,
                    'rb:bg-gray-200': !active,
                  })}>
                    {name[0]}
                  </div>
                  <div className="rb:flex-1 rb:overflow-hidden">
                    <Tooltip title={name}>
                      <div className="rb:overflow-hidden rb:text-[12px] rb:font-medium rb:text-ellipsis rb:whitespace-nowrap">{name}</div>
                    </Tooltip>
                    <Tooltip title={id}>
                      <div className="rb:overflow-hidden rb:mt-0.5 rb:text-[10px] rb:text-ellipsis rb:whitespace-nowrap rb:text-gray-600">{id}</div>
                    </Tooltip>
                  </div>
                  {active &&
                    <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/check_blue.svg')] rb:shrink-0" />
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
        </Flex>
      </RbCard>
      <RbCard
        title={t('memoryConversation.conversationContent')}
        subTitle={t('memoryConversation.sendQuery')}
        headerType="borderless"
        headerClassName="rb:py-2! rb:min-h-[64px]!"
        className="rb:overflow-hidden! rb:flex-1! rb:h-full!"
        bodyClassName="rb:h-[calc(100%-64px)]! rb:overflow-hidden! rb:px-0! rb:pt-1! rb:pb-0!"
      >
        <Chat
          empty={
            <Empty
              url={ConversationEmptyIcon}
              className="rb:h-full"
              subClassName="rb:text-gray-500 rb:mt-1!"
              size={[148, 104]}
              title={t('memoryConversation.conversationContentEmpty')}
              subTitle={t('memoryConversation.conversationContentSubEmpty')}
            />
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
        className="rb:shrink-0! rb:overflow-hidden! rb:w-[33%]! rb:h-full!"
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
              <Flex vertical gap={12}>
                {stageKeys.map((stageKey, index) => (
                  <StageCard
                    key={stageKey}
                    stageKey={stageKey}
                    index={index}
                    log={stageKey === 'problemSplit' && index > 0 && logs[index] ? {
                      ...logs[index],
                      data: {
                        ...(logs[index]?.data || {}),
                        original_query: (logs[index - 1]?.data as { original_query?: string })?.original_query,
                      },
                    } : logs[index]}
                    loading={loading}
                    expanded={expanded[index]}
                    onToggle={() => setExpanded(previous => ({ ...previous, [index]: !(previous[index] ?? Boolean(logs[index])) }))}
                  />
                ))}
              </Flex>
            </Flex>
          )
          : <Empty size={104} className="rb:h-full!" />
        }
      </RbCard>
    </Flex>
  )
}

export default MemoryConversation
