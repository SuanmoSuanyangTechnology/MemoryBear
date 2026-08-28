import { type FC, useEffect, useMemo, useRef, useState } from 'react'
import InfiniteScroll from 'react-infinite-scroll-component'
import { useParams } from 'react-router-dom'
import { Flex, Skeleton, Tooltip, Select, Form, Button, Progress, DatePicker } from 'antd'
import dayjs from 'dayjs'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import {
  getEmotionOverview,
  getEmotionTimeline,
} from '@/api/memory'
import BtnTabs from '@/components/BtnTabs'
import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty'
import Tag from '@/components/Tag'
import OverflowTags from '@/components/OverflowTags'
import StatusTag from '@/components/StatusTag'
import { formatDateTime } from '@/utils/format'
import DonutChart from './DonutChart'

import type {
  DayGroup,
  EmotionConclusion,
  EmotionDailyItem,
  EmotionOverviewResponse,
  EmotionTimelineResponse,
  ViewType,
} from './types'
import { emotionColor, emotionColorClass } from './constants'

const toDayGroups = (items: EmotionDailyItem[]): DayGroup[] => items
  .map(item => ({
    key: item.date,
    date: dayjs(item.date),
    dialogueCount: item.dialogue_count,
    dataQuality: item.data_quality,
    summary: item.summary,
    stats: item.emotions.map(emotion => ({
      type: emotion.type,
      displayName: emotion.display_name,
      count: emotion.count,
      ratio: emotion.percentage,
      color: emotionColor(emotion.type),
    })),
  }))
  .filter(group => group.date.isValid())
  .sort((a, b) => a.date.valueOf() - b.date.valueOf())

const EmotionTimeAnalysis: FC = () => {
  const { t, i18n } = useTranslation()
  const { id } = useParams()
  const [overview, setOverview] = useState<EmotionOverviewResponse | null>(null)
  const [timeline, setTimeline] = useState<EmotionTimelineResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [view, setView] = useState<ViewType>('overview')
  const [expandedDayKeys, setExpandedDayKeys] = useState<string[]>([])
  const [timelineForm] = Form.useForm()
  const { ascending } = Form.useWatch([], timelineForm) || { ascending: true }
  const [page, setPage] = useState(1)
  const [timelineLoading, setTimelineLoading] = useState(false)
  const timelineLoadingRef = useRef(false)

  const loadOverview = () => {
    if (!id) return
    setLoading(true)
    getEmotionOverview({ end_user_id: id })
      .then(response => setOverview(response as EmotionOverviewResponse))
      .finally(() => setLoading(false))
  }

  const loadTimeline = (nextPage = page, nextAscending = ascending, values?: { start_date?: string; end_date?: string }, append = false) => {
    if (!id || timelineLoadingRef.current) return
    timelineLoadingRef.current = true
    setTimelineLoading(true)
    getEmotionTimeline({
      end_user_id: id,
      page: nextPage,
      pagesize: 20,
      sort: nextAscending ? 'asc' : 'desc',
      ...values,
    })
      .then(response => {
        const nextTimeline = response as EmotionTimelineResponse
        setTimeline(previous => append && previous
          ? {
              ...nextTimeline,
              items: [...previous.items, ...nextTimeline.items],
            }
          : nextTimeline)
        setPage(nextPage)
      })
      .finally(() => {
        timelineLoadingRef.current = false
        setTimelineLoading(false)
        setLoading(false)
      })
  }

  useEffect(() => {
    setPage(1)
    if (!id) {
      setOverview(null)
      setTimeline(null)
      setLoading(false)
      return
    }

    if (view === 'overview') {
      setOverview(null)
      loadOverview()
    } else {
      setTimeline(null)
      loadTimeline(1, ascending)
    }
  }, [id, view])

  const groups = useMemo(() => toDayGroups(timeline?.items || []), [timeline])
  const overviewGroups = useMemo(() => toDayGroups(overview?.items || []), [overview])
  const displayGroups = view === 'overview' ? overviewGroups : groups
  const orderedGroups = useMemo(
    () => ascending ? displayGroups : [...displayGroups].reverse(),
    [ascending, displayGroups]
  )
  const firstOrderedGroupKey = orderedGroups[0]?.key

  useEffect(() => {
    setExpandedDayKeys(current => {
      const next = firstOrderedGroupKey ? [firstOrderedGroupKey] : []
      const unchanged = current.length === next.length && current.every((key, index) => key === next[index])
      return unchanged ? current : next
    })
  }, [view, firstOrderedGroupKey])

  const summary = overview?.summary
  const conclusion: EmotionConclusion | null = overview?.conclusion || null
  const emotionCount = summary?.emotion_type_count || 0
  const dialogueCount = summary?.dialogue_count || 0
  const allExpanded = displayGroups.length > 0 && displayGroups.every(group => expandedDayKeys.includes(group.key))
  const toggleDay = (key: string) => {
    setExpandedDayKeys(keys => keys.includes(key) ? keys.filter(item => item !== key) : [...keys, key])
  }
  const toggleAllDays = () => {
    setExpandedDayKeys(allExpanded ? [] : displayGroups.map(group => group.key))
  }

  const emotionName = (type: string) => {
    const key = `userMemory.emotionTime.emotions.${type.toLowerCase()}`
    return i18n.exists(key) ? t(key) : type
  }

  const handleSortChange = (values: { ascending: boolean }) => {
    setPage(1)
    loadTimeline(1, values.ascending)
  }

  const handleDateChange = (dates: [dayjs.Dayjs | null, dayjs.Dayjs | null] | null) => {
    setPage(1)
    loadTimeline(1, ascending, {
      start_date: dates?.[0]?.format('YYYY-MM-DD'),
      end_date: dates?.[1]?.format('YYYY-MM-DD'),
    })
  }

  const loadNextTimelinePage = () => {
    if (timeline?.page?.hasnext && !timelineLoading) {
      loadTimeline(page + 1, ascending, undefined, true)
    }
  }
  const first = displayGroups[0]
  const last = displayGroups.at(-1)
  const leading = displayGroups[0]?.stats[0]
  const latestLeading = displayGroups.at(-1)?.stats[0]

  return (
    <RbCard
      headerType="borderless"
      headerClassName="rb:min-h-0!"
      bodyClassName="rb:px-4! rb:pb-3! rb:pt-0! rb:h-full"
      className="rb:h-full!"
    >
      <Flex vertical justify="center" gap={2} className="rb:h-18">
        <Flex align="center" justify="space-between" gap={12} className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px]">
          {t('userMemory.emotionTime.title')}

          <Tooltip title={t('userMemory.emotionTime.description')}>
            <div className="rb:size-4 rb:cursor-help rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
          </Tooltip>
        </Flex>
        <div className="rb:text-gray-600 rb:text-[12px] rb:leading-4 rb:font-regular">{t('userMemory.emotionTime.summary', { dialogues: dialogueCount, emotions: emotionCount, days: displayGroups.length })}</div>
      </Flex>

      <Flex vertical gap={12} className="rb:mb-3!">
        <BtnTabs
          className="rb:mx-4 rb:mt-2 rb:mb-2 rb:shrink-0!"
          activeKey={view}
          items={([
            ['overview', t('userMemory.emotionTime.overview')],
            ['timeline', t('userMemory.emotionTime.timeline')],
          ] as const).map(([key, label]) => ({ key, label }))}
          onChange={key => setView(key as ViewType)}
        />

        {view === 'timeline' &&
          <Form
            form={timelineForm}
            initialValues={{ ascending: true }}
          >
            <Flex align="center" justify="space-between" gap={8}>
              <Flex align="center" gap={8}>
                <Form.Item name="ascending" noStyle>
                  <Select
                    options={[
                      { value: true, label: t('userMemory.emotionTime.chronological') },
                      { value: false, label: t('userMemory.emotionTime.latestFirst') },
                    ]}
                    size="small"
                    labelRender={(props) => <span className="rb:text-[12px]">{props.label}</span>}
                    className="rb:w-27"
                    onChange={value => handleSortChange({ ascending: value })}
                    disabled={!displayGroups.length}
                  />
                </Form.Item>
                <Form.Item name="dateRange" noStyle>
                  <DatePicker.RangePicker
                    size="small"
                    onChange={handleDateChange}
                    className="rb:w-52"
                    disabled={!displayGroups.length}
                  />
                </Form.Item>
              </Flex>
              {!!displayGroups.length &&
                <Button
                  type="text"
                  size="small"
                  className="rb:text-[12px]!"
                  onClick={toggleAllDays}
                >
                  {t(`userMemory.emotionTime.${allExpanded ? 'collapseAll' : 'expandAll'}`)}
                </Button>
              }
            </Flex>
          </Form>
        }
      </Flex>

      {loading
        ? <Skeleton active paragraph={{ rows: 8 }} className="rb:p-4" />
        : view === 'overview'
        ? (
          <>
            {!displayGroups.length
              ? <Empty
                size={88}
                title={t('userMemory.emotionTime.noData')}
                subTitle={t('userMemory.emotionTime.noDataDescription')}
                className="rb:text-[12px]!"
                subClassName="rb:text-[10px]!"
              />
              : (
                <>
                  <div className="rb:rounded-xl rb-border rb:p-3">
                    <Flex align="center" justify="space-between">
                      <span className="rb:text-[11px] rb:text-gray-600">{t('userMemory.emotionTime.coreInsight')}</span>
                      <Tag color={overview?.data_quality === 'too_few' ? 'warning' : 'default'} circle={true} variant="borderless" className="rb:text-[10px]!">
                        {t(`userMemory.emotionTime.${overview?.data_quality === 'too_few'
                          ? 'sparseWarning'
                          : displayGroups.length === 1
                          ? 'singleDayData'
                          : 'dominantEmotionShift'}`)
                        }
                      </Tag>
                    </Flex>
                    <div className="rb:mt-3 rb:text-[12px] rb:text-gray-600">
                      <span className="rb:mr-2 rb:inline-block rb:size-2 rb:rounded-full" style={{ backgroundColor: leading?.color }} />
                      <b>{emotionName(leading?.type || '')}</b>
                      <span className="rb:mx-1 rb:text-gray-500">
                        {first && formatDateTime(first.date.valueOf(), 'MM-DD')}
                      </span>
                      {displayGroups.length > 1 &&
                        <>
                          <span className="rb:mx-2 rb:text-gray-600">→</span>
                          <span className="rb:mr-2 rb:inline-block rb:size-2 rb:rounded-full" style={{ backgroundColor: latestLeading?.color }} />
                          <b>{emotionName(latestLeading?.type || '')}</b>
                          <span className="rb:ml-1 rb:text-gray-500">
                            {last && formatDateTime(last.date.valueOf(), 'MM-DD')}
                          </span>
                        </>
                      }
                    </div>
                    <div className="rb:mt-2 rb:text-[11px] rb:leading-5 rb:text-gray-600">{conclusion?.message}</div>
                  </div>
                  <div className="rb:mt-2 rb:rounded-xl rb-border rb:p-3">
                    <div className="rb:text-[11px] rb:text-gray-600">
                      {t(`userMemory.emotionTime.${displayGroups.length === 1 ? 'structure' : 'structureComparison'}`)}
                    </div>
                    <Flex className="rb:mt-2!">
                      {[first, ...(displayGroups.length === 1 ? [] : [last])].filter(Boolean).map((group, index) => group && (
                        <div key={`${group.key}-${index}`} className={clsx('rb:flex-1 rb:min-w-0', {
                          'rb-border-l rb:pl-3': index > 0,
                          'rb:pr-3': index === 0
                        })}>
                          <Flex justify="space-between" className="rb:text-[11px]">
                            <span className="rb:text-900 rb:font-medium">{formatDateTime(group.date.valueOf(), 'MM-DD')}</span>
                            {group.dataQuality === 'too_few'
                              ? <Tag color="warning" variant="borderless" circle={true} className="rb:text-[10px]!">
                                {t('userMemory.emotionTime.tooFewCountSummary', { count: group.dialogueCount })}
                              </Tag>
                              : (
                                <span className="rb:text-gray-600">
                                  {t('userMemory.emotionTime.countSummary', { count: group.dialogueCount, types: group.stats.length })}
                                </span>
                              )
                            }
                          </Flex>
                          <DonutChart
                            group={group}
                            emotionName={emotionName}
                            tooltip={(name, count, ratio) => t('userMemory.emotionTime.tooltip', { name, count, ratio })}
                            topThree={t('userMemory.emotionTime.topThree')}
                            lowSample={t('userMemory.emotionTime.lowSample')}
                            countLabel={count => t('userMemory.emotionTime.emotionCount', { count })}
                          />
                        </div>
                      ))}
                    </Flex>
                  </div>
                  <div className="rb:mt-2 rb:border-l-2 rb:border-blue-50 rb:bg-gray-50 rb:px-3 rb:py-2 rb:text-[10px] rb:text-gray-600">
                    {t('userMemory.emotionTime.analysisDisclaimer')}
                  </div>
                </>
              )
            }
          </>
        )
        : (
          <>
            {!displayGroups.length
              ? <Empty
                size={88}
                title={t('userMemory.emotionTime.noTimeline')}
                subTitle={t('userMemory.emotionTime.noTimelineDescription')}
                className="rb:text-[12px]!"
                subClassName="rb:text-[10px]!"
              />
              : (<>
                <InfiniteScroll
                  dataLength={orderedGroups.length}
                  next={loadNextTimelinePage}
                  hasMore={Boolean(timeline?.page?.hasnext)}
                  loader={
                    <div className="rb:py-2 rb:text-center rb:text-[10px] rb:text-gray-500">
                      {t('common.loading')}
                    </div>
                  }
                  scrollThreshold={0.9}
                  height={354}
                  className="rb:relative rb:pl-1! rb:py-3!"
                >
                  {orderedGroups.map((group, index) => {
                    const top = group.stats[0]
                    const isExpanded = expandedDayKeys.includes(group.key)
                    const nextGroup = orderedGroups[index + 1]
                    const adjacentGapDays = nextGroup
                      ? Math.max(Math.abs(nextGroup.date.diff(group.date, 'day')) - 1, 0)
                      : 0

                    return (
                      <div key={group.key}
                        className={clsx("rb:relative rb:pl-5 rb:cursor-pointer rb:before:absolute rb:before:content-[''] rb:before:w-px rb:before:top-0 rb:before:bottom-0 rb:before:bg-gray-200 rb:before:left-1", {
                          'rb:pt-2.5': index > 0
                        })}
                        onClick={() => toggleDay(group.key)}
                      >
                        <span className="rb:absolute rb:-left-px rb:top-4 rb:size-3 rb:rounded-full rb:border-2 rb:border-white rb:ring-1 rb:ring-gray-100" style={{ backgroundColor: top.color }} />
                        <div className={clsx(
                          'rb:rounded-xl rb:border rb:p-3 rb:transition-[border-color,box-shadow] rb:duration-200',
                          {
                            'rb:border-[#8FB0FF] rb:shadow-[0_0_0_2px_rgba(38,103,255,0.08)]': isExpanded,
                            'rb:border-gray-200': !isExpanded,
                          }
                        )}>
                          <Flex justify="space-between" align="center">
                            <span className="rb:text-[13px] rb:font-medium rb:text-gray-800">
                              {formatDateTime(group.date.valueOf(), 'YYYY-MM-DD')}
                            </span>
                            <Tag color={group.dataQuality === 'too_few' ? 'warning' : 'default'} variant="borderless" circle={true} className="rb:text-[10px]!">
                              {t('userMemory.emotionTime.dialogueCount', { count: group.dialogueCount })}
                            </Tag>
                          </Flex>
                          <div className="rb:mt-1 rb:text-[10px] rb:text-gray-600">
                            {group.summary}
                          </div>
                          <div className="rb:flex rb:h-1.5 rb:w-full rb:overflow-hidden rb:rounded-full rb:my-2">
                            {group.stats.map(stat => (
                              <div
                                key={stat.type}
                                style={{ flex: stat.ratio.toFixed(2), background: stat.color }}
                              />
                            ))}
                          </div>
                          <Flex align="center" gap={12} className="rb:w-full rb:overflow-hidden">
                            <div className="rb:min-w-0 rb:flex-1">
                              <OverflowTags
                                gap={0}
                                items={group.stats.map(stat => (
                                  <StatusTag
                                    key={stat.type}
                                    status="default"
                                    text={`${emotionName(stat.type)} ${stat.count}`}
                                    strokeColor={emotionColorClass(stat.type)}
                                    circle
                                    size="small"
                                    className="rb:text-[10px]!"
                                  />
                                ))}
                                numTag={count => (
                                  <Tag
                                    size="small"
                                    circle
                                    color="default"
                                    className="rb:whitespace-nowrap"
                                  >
                                    + {t('userMemory.emotionTime.moreTypes', { count })}
                                  </Tag>
                                )}
                              />
                            </div>
                            <div className={clsx("rb:size-3.5 rb:shrink-0 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:transition-transform rb:duration-200", {
                              'rb:rotate-180': !isExpanded,
                            })}></div>
                          </Flex>

                          {isExpanded && (
                            <div className="rb:mt-2 rb:rounded-lg rb:bg-gray-100 rb:px-3 rb:py-3">
                              <Flex justify="space-between" align="center" className="rb:mb-2! rb:text-[10px] rb:text-gray-600">
                                <span className="rb:font-medium rrb:text-gray-600">{t('userMemory.emotionTime.structure')}</span>
                                <span>
                                  {t('userMemory.emotionTime.moreTypes', { count: group.stats.length })}
                                </span>
                              </Flex>
                              <Flex vertical gap={8}>
                                {group.stats.map(stat => (
                                  <div key={stat.type}>
                                    <Flex justify="space-between" align="center" className="rb:mb-1 rb:text-[10px]">
                                      <span className="rb:text-gray-600">
                                        {emotionName(stat.type)}
                                        <span className="rb:ml-1 rb:text-gray-500">{stat.type}</span>
                                      </span>
                                      <span className="rb:text-gray-600">
                                        {stat.count} {t('userMemory.emotionTime.entries')}
                                        {group.dataQuality !== 'too_few' && <>· {stat.ratio.toFixed(1)}%</>}
                                      </span>
                                    </Flex>
                                    <Progress
                                      percent={stat.ratio}
                                      strokeColor={stat.color}
                                      trailColor="#E4E7EB"
                                      strokeLinecap="round"
                                      showInfo={false}
                                      size="small"
                                      className="rb:block! rb:leading-none!"
                                    />
                                  </div>
                                ))}
                              </Flex>
                              <div className="rb:mt-3 rb-border-t rb:pt-2 rb:text-[9px] rb:text-gray-500">
                                {t('userMemory.emotionTime.analysisDisclaimer')}
                              </div>
                            </div>
                          )}
                        </div>
                        {adjacentGapDays > 0 &&
                          <div className="rb:mx-auto rb:mt-2.5 rb:w-fit rb:rounded-full rb:border rb:border-dashed rb:border-gray-200 rb:bg-white rb:px-2 rb:py-1 rb:text-[9px] rb:text-gray-600">
                            {t('userMemory.emotionTime.collapsedGap', { days: adjacentGapDays })}
                          </div>
                        }
                      </div>
                    )
                  })}
                </InfiniteScroll>

                <Flex justify="space-between" className="rb-border-t rb:pt-2! rb:text-[12px] rb:text-gray-600">
                  <span>{t('userMemory.emotionTime.colorBarHint')}</span>
                  <Tag size="small" circle={true}>Dialogue.emotion</Tag>
                </Flex>
              </>)
            }
          </>
        )
      }
    </RbCard>
  )
}

export default EmotionTimeAnalysis
