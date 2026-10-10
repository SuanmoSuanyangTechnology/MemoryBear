import type { FC } from 'react'
import { Flex } from 'antd'
import { useTranslation } from 'react-i18next'

import type { LogItem } from '../types'

type RecordValue = Record<string, unknown>

interface RequestSummaryCardProps {
  log?: LogItem
  query: string
  searchSwitch: string
}

const MODE_BY_SEARCH_SWITCH: Record<string, string> = {
  '0': 'deep',
  '1': 'normal',
  '2': 'quick',
  '5': 'express',
}

const asRecord = (value: unknown): RecordValue => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as RecordValue
    : {}
)

const formatBackend = (value: unknown) => {
  const backend = String(value || 'neo4j')
  return backend.toLowerCase() === 'neo4j' ? 'Neo4j' : backend
}

const RequestSummaryCard: FC<RequestSummaryCardProps> = ({ log, query, searchSwitch }) => {
  const { t } = useTranslation()
  const data = {
    ...asRecord(log?.data),
    ...asRecord(log?.input),
    ...asRecord(log),
  }
  const currentSearchSwitch = String(data.search_switch ?? data.searchSwitch ?? searchSwitch)
  const mode = String(data.mode ?? MODE_BY_SEARCH_SWITCH[currentSearchSwitch] ?? currentSearchSwitch)
  const backend = formatBackend(data.backend)
  const limit = String(data.limit ?? 10)
  const tags = [
    `mode=${mode}`,
    t('memoryConversation.requestSummary.syncResponse'),
    backend,
    t('memoryConversation.requestSummary.defaultLimit', { limit }),
  ]

  return (
    <div className="rb:rounded-xl rb:bg-gray-100 rb:p-3">
      <p className="rb:font-medium rb:leading-5 rb:text-gray-950">
        {query}
      </p>
      <Flex wrap gap={6} className="rb:mt-2!">
        {tags.map(tag => (
          <span
            className="rb:rounded-md rb-border rb:bg-white rb:px-2 rb:py-1 rb:text-[12px] rb:leading-4 rb:text-gray-600"
            key={tag}
          >
            {tag}
          </span>
        ))}
      </Flex>
    </div>
  )
}

export default RequestSummaryCard
