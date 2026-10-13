import type { FC, ReactNode } from 'react'
import { Flex } from 'antd'
import { useTranslation } from 'react-i18next'

import Tag from '@/components/Tag'
import ScoreBreakdown from './ScoreBreakdown'
import type { LogItem } from '../types'

type RecordValue = Record<string, unknown>

const asRecord = (value: unknown): RecordValue => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as RecordValue
    : {}
)

const firstValue = (source: RecordValue, keys: string[]) => {
  for (const key of keys) {
    const value = source[key]
    if (value !== undefined && value !== null && value !== '') return value
  }
  return undefined
}

const textValue = (value: unknown, fallback = '—'): string => {
  if (value === undefined || value === null || value === '') return fallback
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(item => textValue(item)).join('、')
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

const SourceLine: FC<{ children: ReactNode }> = ({ children }) => (
  <p className="rb:border-t rb:border-dashed rb:border-[#EBEBEB] rb:pt-2 rb:text-[10px] rb:leading-4 rb:text-[#697481]">
    {children}
  </p>
)

const ResultItem: FC<{ item: RecordValue; index: number }> = ({ item, index }) => {
  const { t } = useTranslation()
  const hitSource = asRecord(item.hit_source)
  const id = item.memory_id ?? item.node_id ?? item.id ?? hitSource.node_id ?? '—'
  const source = item.source ?? hitSource.node_type ?? item.memory_type ?? '—'
  const query = item.query ?? hitSource.matched_queries ?? ''
  const rank = item.rank ?? index + 1
  const retrievalType = t(`memoryConversation.scoreMerge.${item.retrieval_type}Badge`)

  return (
    <div className="rb:w-full">
      <Flex align="center" justify="space-between" gap={8} className="rb:mb-2!">
        <Flex align="center" gap={8} className="rb:min-w-0">
          <Flex
            align="center"
            justify="center"
            className="rb:size-5 rb:shrink-0 rb:rounded-md rb:bg-[#171719] rb:font-medium rb:text-white"
          >
            {rank as string}
          </Flex>
          <p className="rb:overflow-hidden rb:text-xs rb:font-medium rb:text-ellipsis rb:whitespace-nowrap">
            {String(id)}
          </p>
        </Flex>
        <Tag size="small" variant="borderless" className="rb:shrink-0!">
          {retrievalType}
        </Tag>
      </Flex>
      <p className="rb:text-[11px] rb:leading-5 rb:text-[#5B6167]">
        {textValue(item.content ?? item.text)}
      </p>
      <ScoreBreakdown data={{ ...item, rank }} />
      <SourceLine>
        {t('memoryConversation.scoreMerge.source', {
          id: String(id),
          source: String(source),
          query: textValue(query, ''),
        })}
      </SourceLine>
    </div>
  )
}

const ScoreMergeContent: FC<{ log: LogItem }> = ({ log }) => {
  const data = {
    ...asRecord(log.data),
    ...asRecord(log.result),
    ...log,
  }
  const raw = firstValue(data, ['items', 'raw_result', 'results', 'memories', 'retrieval_results'])
  const items = Array.isArray(raw) ? raw.map(asRecord) : []

  return (
    <Flex vertical gap={8}>
      {items.map((item, index) => (
        <div
          className="rb:rounded-lg rb:border rb:border-[#EBEBEB] rb:bg-white rb:p-2.5"
          key={String(item.memory_id ?? item.id ?? item.node_id ?? index)}
        >
          <ResultItem item={item} index={index} />
        </div>
      ))}
    </Flex>
  )
}

export default ScoreMergeContent
