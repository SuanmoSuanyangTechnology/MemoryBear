import type { FC } from 'react'
import { Col, Row, Flex } from 'antd'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

interface ScoreBreakdownProps {
  data: Record<string, unknown>
}

interface ScoreFieldProps {
  label: string
  value: unknown
  badge?: string
  highlighted?: boolean
}

const formatValue = (value: unknown): string => {
  if (value === undefined || value === null || value === '') return '—'
  if (typeof value === 'boolean') return String(value)
  if (typeof value === 'number') return value.toFixed(3)
  return String(value)
}

const ScoreField: FC<ScoreFieldProps> = ({ label, value, badge, highlighted }) => (
  <Col span={8}>
    <Flex vertical justify="space-between" className={clsx('rb:h-full rb:rounded-lg rb:px-2! rb:py-1.5!', {
      'rb:bg-[rgba(21,94,239,0.08)] ': highlighted,
      'rb:bg-gray-100': !highlighted
    })}
    >
      <div>
        <p className={clsx("rb:font-semibold rb:leading-5 rb:break-all rb:font-[MiSans-Demibold]", {
          'rb:text-blue-500': highlighted,
          'rb:text-gray-900': !highlighted
        })}>
          {formatValue(value)}
        </p>
        {badge && (
          <span className="rb:rounded-xs rb:bg-blue-500 rb:px-0.5 rb:text-[11px] rb:text-white">
            {badge}
          </span>
        )}
      </div>
      <div
        className={clsx("rb:mt-1 rb:text-[11px] rb:leading-3.5", {
          'rb:text-[rgba(21,94,239,0.65)]': highlighted,
          'rb:text-[#8C9095]': !highlighted
        })}
      >{label}</div>
    </Flex>
  </Col>
)

const ScoreBreakdown: FC<ScoreBreakdownProps> = ({ data }) => {
  const { t } = useTranslation()
  const isMetadata = data.is_metadata === true

  return (
    <Row gutter={[10, 10]} className="rb:my-3">
      <ScoreField
        label={t('memoryConversation.scoreMerge.normalizedKeyword')}
        value={isMetadata ? 1 : data.normalized_keyword_score ?? data.keyword_score ?? data.kw_score}
      />
      <ScoreField
        label={t('memoryConversation.scoreMerge.cosineSemantic')}
        value={isMetadata ? 1 : data.cosine_semantic_score ?? data.semantic_score ?? data.data_emb_score ?? data.emb_score}
      />
      <ScoreField
        label={t('memoryConversation.scoreMerge.fusionRelevance')}
        badge={isMetadata ? undefined : t('memoryConversation.scoreMerge.newDiagnostic')}
        value={isMetadata ? 1 : data.fusion_relevance ?? data.fusion_score}
      />
      <ScoreField
        label={t('memoryConversation.scoreMerge.outputRelevance')}
        value={data.raw_result_score ?? data.final_score ?? data.score}
        highlighted={!isMetadata}
      />
      <ScoreField
        label={t('memoryConversation.scoreMerge.nodeType')}
        value={data.node_type ?? data.source ?? data.memory_type}
      />
      <ScoreField
        label={isMetadata
          ? 'is_metadata'
          : t('memoryConversation.scoreMerge.explicitRank')}
        value={isMetadata ? true : data.rank}
        badge={isMetadata
          ? t('memoryConversation.scoreMerge.new')
          : t('memoryConversation.scoreMerge.newDiagnostic')}
      />
    </Row>
  )
}

export default ScoreBreakdown
