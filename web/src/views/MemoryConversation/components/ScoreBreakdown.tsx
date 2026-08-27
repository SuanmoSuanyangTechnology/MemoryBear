import type { FC } from 'react'
import { Col, Row } from 'antd'
import { useTranslation } from 'react-i18next'

interface ScoreBreakdownProps {
  data: Record<string, unknown>
}

interface ScoreFieldProps {
  label: string
  value: unknown
  fieldKey?: string
  badge?: string
  highlighted?: boolean
}

const formatValue = (value: unknown): string => {
  if (value === undefined || value === null || value === '') return '—'
  if (typeof value === 'boolean') return String(value)
  if (typeof value === 'number') return value.toFixed(3)
  return String(value)
}

const ScoreField: FC<ScoreFieldProps> = ({ label, value, fieldKey, badge, highlighted }) => (
  <Col span={8}>
    <div className={highlighted
      ? 'rb:h-full rb:rounded-lg rb:bg-[#EEF4FF] rb:px-2 rb:py-1.5'
      : 'rb:h-full rb:rounded-lg rb:bg-[#F6F6F6] rb:px-2 rb:py-1.5'}
    >
      <p className="rb:flex rb:flex-wrap rb:items-center rb:gap-1 rb:text-[9px] rb:leading-4 rb:text-[#A0A4AA]">
        <span>{label}</span>
        {fieldKey && (
          <code className="rb:rounded rb:bg-[#E8EEF5] rb:px-1 rb:text-[8px] rb:text-[#697481]">
            {fieldKey}
          </code>
        )}
        {badge && (
          <span className="rb:rounded rb:bg-[#FFF1D6] rb:px-1 rb:py-0.5 rb:text-[8px] rb:text-[#B7791F]">
            {badge}
          </span>
        )}
      </p>
      <p className="rb:text-[11px] rb:font-semibold rb:leading-5 rb:text-[#171719] rb:break-all">
        {formatValue(value)}
      </p>
    </div>
  </Col>
)

const ScoreBreakdown: FC<ScoreBreakdownProps> = ({ data }) => {
  const { t } = useTranslation()
  const isMetadata = data.is_metadata === true

  return (
    <Row gutter={[8, 8]} className="rb:my-2">
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
