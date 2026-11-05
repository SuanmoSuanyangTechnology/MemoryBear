import { Flex } from 'antd'
import { useTranslation } from 'react-i18next'
import type { LogItem } from '../types'
import type { MemoryStageKey } from '../constants'

interface CommunityStageContentProps {
  stage: MemoryStageKey
  log: LogItem
}

const asRecord = (value: unknown): Record<string, unknown> => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
)
const text = (value: unknown) => typeof value === 'string' || typeof value === 'number' ? String(value) : '—'
const decimal = (value: unknown) => typeof value === 'number' ? value.toFixed(3) : text(value)

/** Display identifiers, content, and retrieval metrics for communities, scenes, and packed evidence. */
const CommunityStageContent = ({ stage, log }: CommunityStageContentProps) => {
  const { t } = useTranslation()
  const data = asRecord(log.data)
  const items = Array.isArray(data.items) ? data.items.map(asRecord) : []
  const emptyReason = typeof data.empty_reason === 'string' ? data.empty_reason : ''
  const reasons = ['no_community_hit', 'no_scene_hit_in_communities', 'no_valid_evidence']

  return (
    <Flex vertical gap={8}>
      {emptyReason && (
        <p className="rb:text-gray-600">
          {reasons.includes(emptyReason) ? t(`memoryConversation.communityAnalysis.${emptyReason}`) : emptyReason}
        </p>
      )}
      {!items.length && !emptyReason && (
        <p className="rb:text-gray-500">
          {t(`memoryConversation.${log.status === 'skipped' ? 'skipped' : log.status === 'failed' ? 'failed' : log.status === 'running' ? 'running' : 'communityAnalysis.empty'}`)}
        </p>
      )}
      {items.map((item, index) => (
        <Flex
          key={`${text(item.id)}-${index}`}
          vertical
          gap={12}
          className="rb:pb-3! rb:border-b rb:border-gray-200 rb:last:border-b-0 rb:last:pb-0!"
        >
          <Flex align="center" gap={8}>
            <span className="rb:inline-flex rb:size-5 rb:shrink-0 rb:items-center rb:justify-center rb:rounded-md rb:bg-primary-600 rb:text-white">
              {text(item.evidence_no ?? index + 1)}
            </span>
            <span className="rb:min-w-0 rb:break-all rb:text-sm rb:font-medium">{text(item.id)}</span>
          </Flex>
          {Boolean(item.title || item.topic_scope || item.content) && (
            <p className="rb:whitespace-pre-wrap rb:wrap-break-word rb:text-xs rb:leading-6 rb:text-gray-600">
              {[item.title ?? item.topic_scope, item.content].filter(value => value !== undefined && value !== null && value !== '').map(text).join(': ')}
            </p>
          )}
          <div className="rb:grid rb:grid-cols-[1fr_136px_1fr] rb:gap-2">
            {[
              { label: 'score', value: decimal(item.score) },
              { label: 'nodeType', value: text(item.node_type) },
              { label: 'rank', value: decimal(item.rank) },
            ].map(metric => (
              <Flex vertical justify="space-between" gap={16} key={metric.label} className="rb:min-w-0 rb:rounded-lg rb:bg-gray-100 rb:p-2!">
                <span className="rb:wrap-break-word rb:text-sm rb:font-medium">{metric.value}</span>
                <span className="rb:text-gray-600">{t(`memoryConversation.communityAnalysis.${metric.label}`)}</span>
              </Flex>
            ))}
          </div>
          {item.community_id !== undefined && (
            <p className="rb:wrap-break-word rb:text-gray-600">
              {t('memoryConversation.communityAnalysis.sourceCommunity')}: {text(item.community_name ?? item.community_id)}
            </p>
          )}
          {stage === 'communityEvidencePacked' && (
            <p className="rb:text-gray-600">{t('memoryConversation.communityAnalysis.packed')}</p>
          )}
        </Flex>
      ))}
    </Flex>
  )
}

export default CommunityStageContent
