import { type FC, useEffect, useMemo, useState } from 'react'
import { Flex, Skeleton, Tooltip, Select, Form, Button, Progress } from 'antd'
import ReactEcharts from 'echarts-for-react'
import dayjs from 'dayjs'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { getEmotionOverview, getEmotionTimeline } from '@/api/memory'
import BtnTabs from '@/components/BtnTabs'
import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty'
import Tag from '@/components/Tag'
import OverflowTags from '@/components/OverflowTags'
import StatusTag from '@/components/StatusTag'
import { formatDateTime } from '@/utils/format'

export interface EmotionTrendItem {
  emotion_intensity: number
  emotion_type: string
  created_at: string | number
}

interface EmotionTimeAnalysisProps {
  id?: string
  className?: string
}

type ViewType = 'overview' | 'timeline'

interface EmotionStat {
  type: string
  count: number
  ratio: number
  color: string
}

interface DayGroup {
  key: string
  date: dayjs.Dayjs
  items: EmotionTrendItem[]
  stats: EmotionStat[]
}

const EMOTION_COLORS: Readonly<Record<string, string>> = {
  anxiety: '#E98C4C',
  relief: '#4DA6A3',
  hope: '#4FA274',
  joy: '#EEB342',
  confusion: '#8978BD',
  neutral: '#98A2B1',
  loneliness: '#6575A2',
  frustration: '#A56C5B',
  anger: '#E26060',
  sadness: '#5989BD',
}

const EMOTION_COLOR_CLASSES: Readonly<Record<string, string>> = Object.fromEntries(
  Object.entries(EMOTION_COLORS).map(([type, color]) => [type, `rb:bg-[${color}]`])
)

const UNKNOWN_EMOTION_COLOR = '#B8BEC7'
const UNKNOWN_EMOTION_COLOR_CLASS = 'rb:bg-[#B8BEC7]'
const emotionColor = (type: string) => EMOTION_COLORS[type.trim().toLowerCase()] ?? UNKNOWN_EMOTION_COLOR
const emotionColorClass = (type: string) => EMOTION_COLOR_CLASSES[type.trim().toLowerCase()] ?? UNKNOWN_EMOTION_COLOR_CLASS

const RESPONSE_LIST_KEYS = ['data', 'items', 'records', 'timeline', 'emotion_timeline', 'emotions', 'overview'] as const

const normalizeEmotionItems = (value: unknown): EmotionTrendItem[] => {
  if (Array.isArray(value)) return value.flatMap(normalizeEmotionItems)
  if (!value || typeof value !== 'object') return []

  const record = value as Record<string, unknown>
  const emotionType = record.emotion_type
  const createdAt = record.created_at
  if (typeof emotionType === 'string' && (typeof createdAt === 'string' || typeof createdAt === 'number')) {
    const intensity = Number(record.emotion_intensity)
    return [{
      emotion_type: emotionType,
      emotion_intensity: Number.isFinite(intensity) ? intensity : 0,
      created_at: createdAt,
    }]
  }

  for (const key of RESPONSE_LIST_KEYS) {
    if (key in record) {
      const items = normalizeEmotionItems(record[key])
      if (items.length) return items
    }
  }
  return []
}

const DonutChart: FC<{ group: DayGroup; emotionName: (type: string) => string; tooltip: (name: string, count: number, ratio: string) => string; topThree: string }> = ({ group, emotionName, tooltip, topThree }) => {
  const topStats = group.stats.slice(0, 3)
  const topRatio = topStats.reduce((sum, item) => sum + item.ratio, 0)
  return (
    <Flex align="center" justify="center" gap={4} className="rb:min-w-0">
      <ReactEcharts
        option={{
          animationDuration: 500,
          tooltip: { trigger: 'item', formatter: (params: { name: string; value: number; percent: number }) => tooltip(params.name, params.value, params.percent.toFixed(1)) },
          series: [{
            type: 'pie', radius: ['51%', '72%'], center: ['50%', '50%'], silent: false,
            label: { show: true, position: 'center', formatter: `{strong|${topRatio.toFixed(1)}%}\n{small|${topThree}}`, rich: { strong: { fontSize: 15, fontWeight: 600, color: '#30343B', lineHeight: 20 }, small: { fontSize: 9, color: '#858C96' } } },
            labelLine: { show: false }, itemStyle: { borderWidth: 0 },
            data: group.stats.map(item => ({ value: item.count, name: emotionName(item.type), itemStyle: { color: item.color } })),
          }],
        }}
        style={{ width: 116, height: 116 }}
      />
      <Flex vertical gap={5} className="rb:w-22.5">
        {topStats.map(item => (
          <Flex key={item.type} align="center" gap={5} className="rb:text-[10px] rb:text-gray-600 rb:whitespace-nowrap">
            <span className="rb:size-1.5 rb:rounded-full" style={{ backgroundColor: item.color }} />
            <span>{emotionName(item.type)} {item.ratio.toFixed(1)}%</span>
          </Flex>
        ))}
      </Flex>
    </Flex>
  )
}

const EmotionTimeAnalysis: FC<EmotionTimeAnalysisProps> = ({ id }) => {
  const { t, i18n } = useTranslation()
  const [data, setData] = useState<EmotionTrendItem[]>([])
  const [loading, setLoading] = useState(false)
  const [view, setView] = useState<ViewType>('overview')
  const [expandedDayKeys, setExpandedDayKeys] = useState<string[]>([])
  const [timelineForm] = Form.useForm();
  const { ascending } = Form.useWatch([], timelineForm) || {}

  useEffect(() => {
    if (!id) {
      setData([])
      setLoading(false)
      return
    }

    let cancelled = false
    setData([])
    setLoading(true)
    Promise.allSettled([
      getEmotionTimeline({ id }),
      getEmotionOverview({ id }),
    ]).then(([timelineResult, overviewResult]) => {
      if (cancelled) return
      const timelineItems = timelineResult.status === 'fulfilled' ? normalizeEmotionItems(timelineResult.value) : []
      const overviewItems = overviewResult.status === 'fulfilled' ? normalizeEmotionItems(overviewResult.value) : []
      setData(timelineItems.length ? timelineItems : overviewItems)
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })

    return () => {
      cancelled = true
    }
  }, [id])

  const emotionName = (type: string) => {
    const key = `userMemory.emotionTime.emotions.${type.toLowerCase()}`
    return i18n.exists(key) ? t(key) : type
  }

  const groups = useMemo<DayGroup[]>(() => {
    const dateMap = new Map<string, EmotionTrendItem[]>()
    data.forEach(item => {
      const date = dayjs(item.created_at)
      if (!date.isValid()) return
      const key = date.format('YYYY-MM-DD')
      dateMap.set(key, [...(dateMap.get(key) || []), item])
    })
    return [...dateMap.entries()].map(([key, items]) => {
      const countMap = new Map<string, number>()
      items.forEach(item => countMap.set(item.emotion_type, (countMap.get(item.emotion_type) || 0) + 1))
      const stats = [...countMap.entries()]
        .map(([type, count]) => ({ type, count, ratio: count / items.length * 100, color: emotionColor(type) }))
        .sort((a, b) => b.count - a.count)
      return { key, date: dayjs(key), items, stats }
    }).sort((a, b) => a.date.valueOf() - b.date.valueOf())
  }, [data])

  useEffect(() => {
    setExpandedDayKeys(groups.length ? [groups[0].key] : [])
  }, [groups])

  const emotionCount = new Set(data.map(item => item.emotion_type)).size
  const orderedGroups = ascending ? groups : [...groups].reverse()
  const allExpanded = groups.length > 0 && groups.every(group => expandedDayKeys.includes(group.key))
  const toggleDay = (key: string) => {
    setExpandedDayKeys(keys => keys.includes(key) ? keys.filter(item => item !== key) : [...keys, key])
  }
  const toggleAllDays = () => {
    setExpandedDayKeys(allExpanded ? [] : groups.map(group => group.key))
  }
  const first = groups[0]
  const last = groups.at(-1)
  const leading = first?.stats[0]
  const latestLeading = last?.stats[0]

  const insight = useMemo(() => {
    if (!first || !last || !leading) return ''
    if (groups.length === 1) return t('userMemory.emotionTime.singleInsight')
    if (leading.type === latestLeading?.type) return t('userMemory.emotionTime.stableInsight', { emotion: emotionName(leading.type) })
    return t('userMemory.emotionTime.shiftInsight', { from: emotionName(leading.type), to: emotionName(latestLeading?.type || '') })
  }, [first, last, leading, latestLeading, groups.length])

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
        <div className="rb:text-gray-600 rb:text-[12px] rb:leading-4 rb:font-regular">{t('userMemory.emotionTime.summary', { dialogues: data.length, emotions: emotionCount, days: groups.length })}</div>
      </Flex>

      <Flex align="center" justify="space-between" className="rb:mb-3!">
        <BtnTabs
          className="rb:mx-4 rb:mt-2 rb:mb-2"
          activeKey={view}
          items={([
            ['overview', t('userMemory.emotionTime.overview')],
            ['timeline', t('userMemory.emotionTime.timeline')],
          ] as const).map(([key, label]) => ({ key, label }))}
          onChange={key => setView(key as ViewType)}
        />

        <Form
          form={timelineForm}
          initialValues={{ ascending: true }}
        >
          <Flex align="center" gap={12}>
            <Form.Item name="ascending" noStyle>
              <Select
                options={[
                  { value: true, label: t('userMemory.emotionTime.chronological') },
                  { value: false, label: t('userMemory.emotionTime.latestFirst') },
                ]}
                size="small"
                labelRender={(props) => <span className="rb:text-[12px]">{props.label}</span>}
                className="rb:w-24"
                popupMatchSelectWidth={false}
              />
            </Form.Item>
            {!!groups.length &&
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
      </Flex>

      {loading
        ? <Skeleton active paragraph={{ rows: 8 }} className="rb:p-4" />
        : view === 'overview'
        ? (
          <>
            {!groups.length
              ? <Empty
                size={88}
                title={t('userMemory.emotionTime.noTimeline')}
                subTitle={t('userMemory.emotionTime.noTimelineDescription')}
                className="rb:text-[12px]!"
                subClassName="rb:text-[10px]!"
              />
              : (
                <>
                  <div className="rb:rounded-xl rb-border rb:p-3">
                    <Flex align="center" justify="space-between">
                      <span className="rb:text-[11px] rb:text-gray-600">{t('userMemory.emotionTime.coreInsight')}</span>
                      <span className="rb:rounded-full rb:bg-gray-100 rb:px-2 rb:py-0.5 rb:text-[10px] rb:text-gray-500">
                        {t(`userMemory.emotionTime.${groups.length === 1 ? 'singleDayData' : 'dominantEmotionShift'}`)}
                      </span>
                    </Flex>
                    <div className="rb:mt-3 rb:text-[12px] rb:text-gray-600">
                      <span className="rb:mr-2 rb:inline-block rb:size-2 rb:rounded-full" style={{ backgroundColor: leading?.color }} />
                      <b>{emotionName(leading?.type || '')}</b>
                      <span className="rb:mx-1 rb:text-gray-500">
                        {first && formatDateTime(first.date.valueOf(), 'MM-DD')}
                      </span>
                      {groups.length > 1 &&
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
                    <div className="rb:mt-2 rb:text-[11px] rb:leading-5 rb:text-gray-600">{insight}</div>
                  </div>
                  <div className="rb:mt-2 rb:rounded-xl rb-border rb:p-3">
                    <div className="rb:text-[11px] rb:text-gray-600">
                      {t(`userMemory.emotionTime.${groups.length === 1 ? 'structure' : 'structureComparison'}`)}
                    </div>
                    <Flex className="rb:mt-2!">
                      {[first, ...(groups.length === 1 ? [] : [last])].filter(Boolean).map((group, index) => group && (
                        <div key={`${group.key}-${index}`} className={clsx('rb:flex-1 rb:min-w-0', {
                          'rb-border-l rb:pl-3': index > 0,
                          'rb:pr-3': index === 0
                        })}>
                          <Flex justify="space-between" className="rb:text-[11px]">
                            <span className="rb:text-900 rb:font-medium">{formatDateTime(group.date.valueOf(), 'MM-DD')}</span>
                            <span className="rb:text-gray-600">
                              {t('userMemory.emotionTime.countSummary', { count: group.items.length, types: group.stats.length })}
                            </span>
                          </Flex>
                          <DonutChart group={group} emotionName={emotionName} tooltip={(name, count, ratio) => t('userMemory.emotionTime.tooltip', { name, count, ratio })} topThree={t('userMemory.emotionTime.topThree')} />
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
            {!groups.length
              ? <Empty
                size={88}
                title={t('userMemory.emotionTime.noTimeline')}
                subTitle={t('userMemory.emotionTime.noTimelineDescription')}
                className="rb:text-[12px]!"
                subClassName="rb:text-[10px]!"
              />
              : (
                <Flex vertical className="rb:relative rb:pl-1! rb:max-h-88.5 rb:overflow-auto rb:py-3!">
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
                            <span className="rb:rounded-full rb:bg-gray-100 rb:px-2 rb:py-1 rb:text-[9px] rb:text-gray-600">
                              {t('userMemory.emotionTime.dialogueCount', { count: group.items.length })}
                            </span>
                          </Flex>
                          <div className="rb:mt-1 rb:text-[10px] rb:text-gray-600">
                            {t(`userMemory.emotionTime.${group.stats.length > 3 ? 'distributionScattered' : 'distributionConcentrated'}`, {
                              first: emotionName(top.type),
                              second: group.stats[1]
                                ? `${t('userMemory.emotionTime.emotionSeparator')}${emotionName(group.stats[1].type)}`
                                : ''
                              })
                            }
                          </div>
                          <div className="rb:flex rb:h-1.5 rb:w-full rb:overflow-hidden rb:rounded-full rb:my-2">
                            {group.stats.map(stat => (
                              <div
                                key={stat.type}
                                style={{ flex: stat.ratio.toFixed(2), background: stat.color }}
                              />
                            ))}
                          </div>
                          <Flex align="center" gap={12}>
                            <div className="rb:min-w-0 rb:flex-1">
                              <OverflowTags
                                gap={6}
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
                                  <Tag size="small" circle>
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
                                      <span className="rb:text-gray-600">{stat.count} · {stat.ratio.toFixed(1)}%</span>
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
                </Flex>
              )
            }
            <Flex justify="space-between" className="rb-border-t rb:pt-2! rb:text-[12px] rb:text-gray-600">
              <span>{t('userMemory.emotionTime.colorBarHint')}</span>
              <Tag size="small" circle={true}>Dialogue.emotion</Tag>
            </Flex>
          </>
        )
      }
    </RbCard>
  )
}

export default EmotionTimeAnalysis
