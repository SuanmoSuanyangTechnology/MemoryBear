import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { useParams } from 'react-router-dom'
import { Space, Skeleton, Row, Col, Flex, Tooltip, Typography } from 'antd'
import { ClockCircleOutlined } from '@ant-design/icons'
import clsx from 'clsx'

import {
  getShortTerm,
  getShortMemosUrl,
} from '@/api/memory'
import PageScrollList from '@/components/PageScrollList'
import PageTabs from '@/components/PageTabs'
import RadioGroupButton from '@/components/RadioGroupButton'
import Tag from '@/components/Tag'
import Empty from '@/components/Empty'
import Markdown from '@/components/Markdown'
import RbCard from '@/components/RbCard/Card'
import { formatDateTime } from '@/utils/format'

/** A single deep-retrieval entry: the original query and its retrieved passages. */
interface ShortTermItem {
  retrieval: Array<{ query: string; retrieval: string[]; }>;
  /** The user's original message that triggered the retrieval. */
  message: string;
  /** The generated answer based on retrieved passages. */
  answer: string;
}

/** A candidate entry waiting to be promoted into long-term memory. */
interface LongTermItem {
  query: string;
  /** Aggregated retrieval text (may contain newlines). */
  retrieval: string;
}

/** Combined API response for the short-term memory page. */
interface ShortData {
  short_term: ShortTermItem[];
  long_term: LongTermItem[];
  /** Total number of extracted entities. */
  entity: number;
  /** Total retrieval count. */
  retrieval_number: number;
  /** Number of long-term memory candidates. */
  long_term_number: number;
}
interface ShortMemo {
  id: string;
  statement: string;
  status: 'pending' | 'ongoing' | 'ended';
  start_date: string | number | null;
  end_date: string | number | null;
  is_time_unspecified: boolean;
  retain_until: string | number | null;
  relative_days: number;
}

interface MemoCountGroup {
  total: number;
  dated: number;
  undated: number;
}

interface MemoCounts {
  total: number;
  current: MemoCountGroup;
  ended: MemoCountGroup;
}

type MemoScope = 'current' | 'ended'
type MemoDateType = 'all' | 'dated' | 'undated'

const getRelativeStartText = (days: number, t: TFunction) => days === 0
  ? t('shortTermDetail.startsToday')
  : days === 1
    ? t('shortTermDetail.startsTomorrow')
    : t('shortTermDetail.startsInDays', { count: days })

const getRelativeEndText = (days: number, ended: boolean, t: TFunction) => {
  if (days === 0) return t(ended ? 'shortTermDetail.endDatePassedToday' : 'shortTermDetail.endsToday')
  return t(ended ? 'shortTermDetail.daysSinceEnd' : 'shortTermDetail.daysUntilEnd', { count: days })
}

const MemoCard: FC<{ memo: ShortMemo }> = ({ memo }) => {
  const { t } = useTranslation()
  const dateFormat = t('shortTermDetail.memoDateFormat')
  const timing = memo.is_time_unspecified
    ? t('shortTermDetail.timeUnspecified')
    : memo.status === 'pending'
    ? getRelativeStartText(memo.relative_days, t)
    : memo.end_date || memo.status === 'ongoing'
      ? getRelativeEndText(memo.relative_days, memo.status === 'ended', t)
      : t('shortTermDetail.ongoing')
  const dateParts = [
    memo.start_date && formatDateTime(memo.start_date, dateFormat),
    memo.end_date && formatDateTime(memo.end_date, dateFormat),
  ].filter(Boolean)
  const dateText = memo.is_time_unspecified
    ? memo.start_date ? t('shortTermDetail.mentionedOn', { date: formatDateTime(memo.start_date, dateFormat) }) : t('shortTermDetail.timeUnspecified')
    : dateParts.length === 2
      ? t('shortTermDetail.dateRange', { start: dateParts[0], end: dateParts[1] })
      : memo.start_date
        ? t('shortTermDetail.startsOn', { date: dateParts[0] })
        : memo.end_date
          ? t('shortTermDetail.endsOn', { date: dateParts[0] })
          : ''

  return (
    <article className={clsx('rb:rounded-lg rb:border rb:border-gray-200 rb:px-3 rb:py-3', memo.status === 'ended' ? 'rb:bg-gray-50' : 'rb:bg-white')}>
      <Typography.Paragraph
        className="rb:mb-2! rb:whitespace-pre-wrap rb:wrap-break-word rb:text-sm! rb:leading-6!"
        ellipsis={{ rows: 3, expandable: 'collapsible', symbol: expanded => expanded ? t('shortTermDetail.collapseMemo') : t('shortTermDetail.expandMemo') }}
      >
        {memo.statement}
      </Typography.Paragraph>
      <Flex align="center" gap={8} wrap>
        <span className="rb:flex rb:items-center rb:gap-1 rb:text-xs rb:text-gray-500">
          <ClockCircleOutlined />
          {dateText}
        </span>
        <Tag color={(memo.is_time_unspecified || memo.status === 'ended') ? 'default' : 'processing'} variant="borderless" className="rb:mr-0! rb:text-xs!">
          {timing}
        </Tag>
      </Flex>
      {!memo.end_date && memo.retain_until && (
        <div className="rb:mt-2 rb:text-xs rb:text-gray-500">{t('shortTermDetail.retainedUntil', { date: formatDateTime(memo.retain_until, dateFormat), time: '00:00' })}</div>
      )}
    </article>
  )
}

/**
 * ShortTermDetail – Displays the AI system's short-term "workbench" memory.
 *
 * Layout:
 * - Top-left: three KPI cards (retrieval count, extracted entities, long-term candidates).
 * - Left column: deep-retrieval entries – each shows the user message, expandable
 *   sub-queries with retrieved passages, and the generated answer.
 * - Right column: short-term memos with scope/date filters and infinite scrolling.
 *
 * Route param `id` is the end-user ID.
 */
const ShortTermDetail: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<ShortData>({} as ShortData)
  const [memoScope, setMemoScope] = useState<MemoScope>('current')
  const [memoDateTypes, setMemoDateTypes] = useState<Record<MemoScope, MemoDateType>>({ current: 'all', ended: 'all' })
  const [memoCounts, setMemoCounts] = useState<MemoCounts>({
    total: 0,
    current: { total: 0, dated: 0, undated: 0 },
    ended: { total: 0, dated: 0, undated: 0 },
  })
  const memoDateType = memoDateTypes[memoScope]
  /** Tracks expand/collapse state for short-term sub-queries and answers by composite key. */
  const [shortTermExpandedMap, setShortTermExpandedMap] = useState<Record<string, boolean>>({})

  /* Fetch data whenever the route user ID changes. */
  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  /** Load short-term memory data (deep retrieval + long-term candidates) for the current user. */
  const getData = () => {
    if (!id) return
    setLoading(true)
    getShortTerm(id).then((res) => {
      const response = res as ShortData
      setData(response)
      setLoading(false)
    })
    .finally(() => {
      setLoading(false)
    })
  }

  const getMemoCount = (res: { data?: { counts?: MemoCounts }; counts?: MemoCounts }) => {
    const counts = res.data?.counts ?? res.counts
    if (counts) setMemoCounts(counts)
  }

  const currentMemoCounts = memoCounts[memoScope]
  const memoScopeOptions = [
    { label: `${t('shortTermDetail.currentMemo')} (${memoCounts.current.total})`, value: 'current' },
    { label: `${t('shortTermDetail.endedMemo')} (${memoCounts.ended.total})`, value: 'ended' },
  ]
  const memoDateOptions = [
    { label: `${t('shortTermDetail.allDates')} (${currentMemoCounts.total})`, value: 'all' },
    { label: `${t('shortTermDetail.dated')} (${currentMemoCounts.dated})`, value: 'dated' },
    { label: `${t('shortTermDetail.undated')} (${currentMemoCounts.undated})`, value: 'undated' },
  ]

  return (
    <Row gutter={12} className="rb:h-full!">
      <Col span={12} className="rb:h-full!">
        <div className="rb:grid rb:grid-cols-3 rb:gap-3 rb:mb-3">
          {(['retrieval_number', 'entity', 'long_term_number'] as const).map(key => (
            <Flex key={key} align="center" justify="space-between" className="rb:bg-white rb:rounded-xl rb:py-3! rb:pl-5! rb:pr-4!">
              <div>
                <div className="rb:text-[24px] rb:leading-8 rb:mb-1">{(data as any)[key] ?? 0}</div>
                {t(`shortTermDetail.${key}`)}
              </div>
              {key === 'retrieval_number'
                ? <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/retrieval_number.svg)]"></div>
                : key === 'entity'
                  ? <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/entity.svg')]"></div>
                  : <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/long_term_number.svg')]"></div>
              }
            </Flex>
          ))}
        </div>

        <RbCard
          title={() => (<Space size={4}>
            {t('shortTermDetail.shortTermTitle')}
            <Tooltip title={t('shortTermDetail.shortTermSubTitle')}>
              <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/question.svg')]"></div>
            </Tooltip>
          </Space>)}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-54px)]"
          className="rb:h-[calc(100%-94px)]!"
        >
          <Flex gap={12} vertical className="rb:h-full!  rb:overflow-y-auto!">
            {loading
              ? <Skeleton active />
              : !data.short_term || data.short_term.length === 0
                ? <Empty />
                : data.short_term?.map((vo, voIdx) => (
                  <Flex key={voIdx} gap={12} vertical className="rb:leading-5 rb:bg-gray-100 rb:rounded-xl rb:p-3!">
                    <div className="rb:font-medium rb:text-gray-800 rb:leading-5">{vo.message}</div>
                    {vo.retrieval.map((item, index) => {
                      const key = `${voIdx}-${index}`
                      const expanded = shortTermExpandedMap[key]
                      return (
                        <div key={index} className="rb:bg-white rb:rounded-md rb:px-3 rb:py-2.5 rb:leading-5">
                          <Flex
                            align="center"
                            justify="space-between"
                            className={clsx("rb:font-medium rb:cursor-pointer", {
                              'rb:mb-2!': expanded,
                            })}
                            onClick={() => setShortTermExpandedMap(prev => ({ ...prev, [key]: !prev[key] }))}
                          >
                            <span>{t('shortTermDetail.query')}{index + 1}</span>
                            <div className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/down.svg')]", {
                              'rb:rotate-180': expanded
                            })}></div>
                          </Flex>
                          {expanded && (
                            <>
                              <div className="rb:text-gray-600 rb:mb-4">
                                <Markdown content={item.query} />
                              </div>
                              <div className="rb:font-medium rb:mb-2">{t('shortTermDetail.answer')}</div>
                              {item.retrieval.length > 0
                                ? <ul className="rb:list-disc rb:ml-4 rb:text-gray-600">
                                  {item.retrieval.map((retrieval, retrievalIdx) => (
                                    <li key={retrievalIdx} className="rb:text-gray-600">{retrieval}</li>
                                  ))}
                                </ul>
                                : <div className="rb:text-gray-600">{t('shortTermDetail.noAnswer')}</div>
                              }
                            </>
                          )}
                        </div>
                      )
                    })}
                    <div className="rb:leading-5 rb:bg-white rb:rounded-xl rb:p-3!">
                      <Flex
                        align="center"
                        justify="space-between"
                        className={clsx("rb:font-medium rb:cursor-pointer", {
                          'rb:mb-2!': shortTermExpandedMap[`ans-${voIdx}`],
                        })}
                        onClick={() => setShortTermExpandedMap(prev => ({ ...prev, [`ans-${voIdx}`]: !prev[`ans-${voIdx}`] }))}
                      >
                        <span>{t('shortTermDetail.answer')}</span>
                        <div className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/down.svg')]", {
                          'rb:rotate-180': shortTermExpandedMap[`ans-${voIdx}`]
                        })}></div>
                      </Flex>
                      {shortTermExpandedMap[`ans-${voIdx}`] && (
                        <div className="rb:text-gray-600">
                          <Markdown content={vo.answer} />
                        </div>
                      )}
                    </div>
                  </Flex>
                ))
            }
          </Flex>
        </RbCard>
      </Col>
      <Col span={12} className="rb:h-full!">
        <RbCard
          title={() => (
            <Space size={4}>
              {t('shortTermDetail.memoTitle')}
              <span className="rb:text-sm rb:font-[-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,'Noto Sans',sans-serif,'Apple Color Emoji','Segoe UI Emoji','Segoe UI Symbol','Noto Color Emoji'] rb:font-normal rb:text-gray-600">({memoCounts.total})</span>
              <Tooltip title={t('shortTermDetail.memoTip')}>
                <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/question.svg')]"></div>
              </Tooltip>
            </Space>
          )}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-54px)] rb:flex rb:flex-col rb:gap-3 rb:overflow-hidden"
          className="rb:h-full!"
        >
          <PageTabs
            block
            aria-label={t('shortTermDetail.memoScopeLabel')}
            value={memoScope}
            onChange={value => {
              const scope = value as MemoScope
              setMemoScope(scope)
              setMemoDateTypes(prev => ({ ...prev, [scope]: 'all' }))
            }}
            options={memoScopeOptions}
          />
          <RadioGroupButton
            block
            aria-label={t('shortTermDetail.memoDateLabel')}
            value={memoDateType}
            onChange={value => setMemoDateTypes(prev => ({ ...prev, [memoScope]: value as MemoDateType }))}
            options={memoDateOptions}
          />
          {id && (
            <PageScrollList<ShortMemo>
              key={`${id}:${memoScope}:${memoDateType}`}
              url={getShortMemosUrl}
              query={{ end_user_id: id, scope: memoScope, date_type: memoDateType }}
              column={1}
              heightClass="rb:flex-1 rb:min-h-0"
              className="rb:pr-1"
              pageLoading={<Skeleton active paragraph={{ rows: 3 }} />}
              empty={<Empty subTitle={memoDateType !== 'all' ? t('shortTermDetail.filteredMemoEmpty') : memoScope === 'current' ? t('shortTermDetail.currentMemoEmpty') : t('shortTermDetail.endedMemoEmpty')} />}
              renderItem={memo => <MemoCard memo={memo} />}
              onLoad={getMemoCount}
            />
          )}
        </RbCard>
      </Col>
    </Row>
  )
}
export default ShortTermDetail