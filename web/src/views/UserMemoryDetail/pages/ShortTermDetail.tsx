/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-08 19:46:02 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-16 15:09:49 
 */
import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Space, Skeleton, Row, Col, Flex, Tooltip } from 'antd'
import clsx from 'clsx'

import {
  getShortTerm,
} from '@/api/memory'
import Empty from '@/components/Empty'
import Markdown from '@/components/Markdown'
import RbCard from '@/components/RbCard/Card'

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
/**
 * ShortTermDetail – Displays the AI system's short-term "workbench" memory.
 *
 * Layout:
 * - Top-left: three KPI cards (retrieval count, extracted entities, long-term candidates).
 * - Left column: deep-retrieval entries – each shows the user message, expandable
 *   sub-queries with retrieved passages, and the generated answer.
 * - Right column: long-term candidate pool – aggregated entries waiting to be
 *   promoted into persistent memory, with expand/collapse for long content.
 *
 * Route param `id` is the end-user ID.
 */
const ShortTermDetail: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<ShortData>({} as ShortData)
  /** Tracks expand/collapse state for each long-term candidate card by index. */
  const [longTermExpandedMap, setLongTermExpandedMap] = useState<Record<number, boolean>>({})
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

  return (
    <Row gutter={12}>
      <Col span={12}>
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
          bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-54px)] rb:overflow-y-auto!"
          className="rb:h-[calc(100vh-183px)]!"
        >
          <Flex gap={12} vertical>
            {loading
              ? <Skeleton active />
              : !data.short_term || data.short_term.length === 0
                ? <Empty />
                : data.short_term?.map((vo, voIdx) => (
                  <Flex key={voIdx} gap={12} vertical className="rb:leading-5 rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3!">
                    <div className="rb:font-medium rb:text-[#212332] rb:leading-5">{vo.message}</div>
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
                              <div className="rb:text-[#5B6167] rb:mb-4">
                                <Markdown content={item.query} />
                              </div>
                              <div className="rb:font-medium rb:mb-2">{t('shortTermDetail.answer')}</div>
                              {item.retrieval.length > 0
                                ? <ul className="rb:list-disc rb:ml-4 rb:text-[#5B6167]">
                                  {item.retrieval.map((retrieval, retrievalIdx) => (
                                    <li key={retrievalIdx} className="rb:text-[#5B6167]">{retrieval}</li>
                                  ))}
                                </ul>
                                : <div className="rb:text-[#5B6167]">{t('shortTermDetail.noAnswer')}</div>
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
                        <div className="rb:text-[#5B6167]">
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
      <Col span={12}>
        <RbCard
          title={() => (<Space size={4}>
            {t('shortTermDetail.longTermTitle')}
            <Tooltip title={t('shortTermDetail.longTermTitleSubTitle')}>
              <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/question.svg')]"></div>
            </Tooltip>
          </Space>)}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-54px)] rb:overflow-y-auto!"
          className="rb:h-[calc(100vh-88px)]!"
        >
          <Flex vertical gap={12}>
            {loading
              ? <Skeleton active />
              : !data.long_term || data.long_term.length === 0
              ? <Empty />
              : data.long_term?.map((vo, voIdx) => {
                const lines = vo.retrieval.split('\n')
                const expanded = longTermExpandedMap[voIdx]
                return (
                  <div key={voIdx} className="rb:leading-5 rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3">
                    <div className="rb:mb-3 rb:text-[#212332] rb:font-medium rb:px-1">{vo.query}</div>
                    <div className="rb:bg-white rb:rounded-xl rb:px-3 rb:py-2.5">
                      <div className={expanded ? undefined : "rb:wrap-break-word rb:line-clamp-3"}>
                        <Markdown content={vo.retrieval} />
                      </div>

                      {lines.length > 4 && (
                        <div
                          className="rb:text-[#155EEF] rb:cursor-pointer rb:mt-1"
                          onClick={() => setLongTermExpandedMap(prev => ({ ...prev, [voIdx]: !prev[voIdx] }))}
                        >
                          {expanded ? t('common.foldUp') : t('common.expanded')}
                        </div>
                      )}
                    </div>
                  </div>
                )
              })
            }
          </Flex>
        </RbCard>
      </Col>
    </Row>
  )
}
export default ShortTermDetail