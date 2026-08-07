import { type FC, type ReactNode, useState } from 'react'
import { DownOutlined, LoadingOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import clsx from 'clsx'

import type {
  MemoryRecallItem,
  MemoryRetrieval,
  MemoryStage,
  MemoryToolCall,
} from './types'

interface MemoryRecallProps {
  retrieval: MemoryRetrieval
  assistantContent?: string | null
  isStreaming?: boolean
}

interface StageRowProps {
  title: string
  description?: string
  badge?: string
  children?: ReactNode
}

const StageRow: FC<StageRowProps> = ({ title, description, badge, children }) => (
  <div className="rb:mb-4 rb:last:mb-0">
    <div className="rb:flex rb:items-center rb:gap-2 rb:text-[12px] rb:leading-5 rb:font-medium rb:text-[#34353F]">
      <span>{title}</span>
      {badge && <span className="rb:text-[10px] rb:font-normal rb:text-[#155EEF]">{badge}</span>}
    </div>
    {description && (
      <div className="rb:mt-0.5 rb:text-[11px] rb:leading-4 rb:text-[#8A8D93]">{description}</div>
    )}
    {children}
  </div>
)

const getMode = (call: MemoryToolCall) => {
  const mode = call.input.search_mode
  return typeof mode === 'string' && mode ? mode : 'unknown'
}

const getQuestion = (call: MemoryToolCall) => {
  const question = call.input.question
  return typeof question === 'string' ? question : ''
}

const getResultStage = (call: MemoryToolCall) => (
  call.stages.find(stage => stage.stage === 'result_ready')
)

const formatScore = (score?: number) => {
  if (typeof score !== 'number' || Number.isNaN(score)) return '--'
  const percent = Math.max(0, score * 100)
  return `${Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(1)}%`
}

const MemoryItems: FC<{ items: MemoryRecallItem[] }> = ({ items }) => {
  const { t } = useTranslation()

  return (
    <div className="rb:mt-2 rb:space-y-2.5">
      {items.map((item, index) => {
        const memoryType = item.memory_type || 'unknown'
        const content = item.content || [item.source, item.relation, item.target].filter(Boolean).join(' ')
        return (
          <div key={`${item.rank ?? index}-${item.source ?? ''}-${content}`} className="rb:flex rb:items-start rb:gap-3">
            <div className="rb:min-w-0 rb:flex-1 rb:flex rb:items-start rb:gap-2 rb:text-[11px] rb:leading-4 rb:text-[#5B6167]">
              <span className="rb:shrink-0 rb:text-[#8A8D93]">{item.rank ?? index + 1}.</span>
              <span className="rb:shrink-0 rb:font-medium rb:text-[#34353F]">
                {t(`memoryConversation.memoryRecall.memoryTypes.${memoryType}`, {
                  defaultValue: t('memoryConversation.memoryRecall.memoryTypes.unknown'),
                })}
              </span>
              <span className="rb:min-w-0 rb:wrap-break-word">{content}</span>
            </div>
            {typeof item.score === 'number' && (
              <span className="rb:shrink-0 rb:text-[10px] rb:leading-4 rb:text-[#8A8D93]">
                {t('memoryConversation.memoryRecall.relevance', { score: formatScore(item.score) })}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

const StageContent: FC<{ stage: MemoryStage }> = ({ stage }) => {
  const { t } = useTranslation()
  const { data } = stage

  switch (stage.stage) {
    case 'profile_loaded':
      return (
        <StageRow
          title={t('memoryConversation.memoryRecall.profileLoaded')}
          description={t(data.has_profile
            ? 'memoryConversation.memoryRecall.profileFound'
            : 'memoryConversation.memoryRecall.profileEmpty')}
        />
      )
    case 'query_split': {
      const questions = data.questions || []
      return (
        <StageRow
          title={t('memoryConversation.memoryRecall.querySplit')}
          description={t('memoryConversation.memoryRecall.querySplitDesc', { count: data.count ?? questions.length })}
        >
          {questions.length > 0 && (
            <ol className="rb:mt-1 rb:pl-4 rb:list-decimal rb:text-[11px] rb:leading-5 rb:text-[#5B6167]">
              {questions.map((question, index) => <li key={`${index}-${question}`}>{question}</li>)}
            </ol>
          )}
        </StageRow>
      )
    }
    case 'hybrid_searched':
      return (
        <StageRow
          title={t('memoryConversation.memoryRecall.hybridSearched')}
          badge={`${data.hit_count ?? 0} ${t('memoryConversation.memoryRecall.unitItems')}`}
          description={t('memoryConversation.memoryRecall.hybridSearchedDesc', { count: data.hit_count ?? 0 })}
        />
      )
    case 'relation_searched':
      return (
        <StageRow
          title={t('memoryConversation.memoryRecall.relationSearched')}
          badge={`${data.relation_count ?? 0} ${t('memoryConversation.memoryRecall.unitItems')}`}
          description={t('memoryConversation.memoryRecall.relationSearchedDesc', { count: data.relation_count ?? 0 })}
        />
      )
    case 'results_merged':
      return (
        <StageRow
          title={t('memoryConversation.memoryRecall.resultsMerged')}
          description={t('memoryConversation.memoryRecall.resultsMergedDesc', {
            memoryCount: data.memory_count ?? 0,
            relationCount: data.relation_count ?? 0,
          })}
        />
      )
    case 'perceptual_processed':
      return (
        <StageRow
          title={t('memoryConversation.memoryRecall.perceptualProcessed')}
          description={t('memoryConversation.memoryRecall.perceptualProcessedDesc')}
        />
      )
    case 'results_ranked':
      return (
        <StageRow
          title={t('memoryConversation.memoryRecall.resultsRanked')}
          description={t('memoryConversation.memoryRecall.resultsRankedDesc', { count: data.count ?? 0 })}
        />
      )
    case 'context_prepared':
      return (
        <StageRow
          title={t('memoryConversation.memoryRecall.contextPrepared')}
          description={t('memoryConversation.memoryRecall.contextPreparedDesc', { count: data.memory_count ?? 0 })}
        />
      )
    case 'result_ready': {
      const items = data.items || []
      return (
        <StageRow
          title={t('memoryConversation.memoryRecall.resultReady')}
          badge={`${data.shown_count ?? items.length} ${t('memoryConversation.memoryRecall.unitItems')}`}
          description={items.length > 0
            ? t('memoryConversation.memoryRecall.resultReadyDesc', { shownCount: data.shown_count ?? items.length })
            : t('memoryConversation.memoryRecall.resultEmpty')}
        >
          {items.length > 0 && <MemoryItems items={items} />}
        </StageRow>
      )
    }
    default:
      return null
  }
}

interface RecallStepSummary {
  title: string
  description: string
}

const getStageSummary = (stage: MemoryStage, t: TFunction): RecallStepSummary | undefined => {
  const { data } = stage
  switch (stage.stage) {
    case 'profile_loaded':
      return {
        title: t('memoryConversation.memoryRecall.profileLoaded'),
        description: t(data.has_profile
          ? 'memoryConversation.memoryRecall.profileFound'
          : 'memoryConversation.memoryRecall.profileEmpty'),
      }
    case 'query_split':
      return {
        title: t('memoryConversation.memoryRecall.querySplit'),
        description: t('memoryConversation.memoryRecall.querySplitDesc', {
          count: data.count ?? data.questions?.length ?? 0,
        }),
      }
    case 'hybrid_searched':
      return {
        title: t('memoryConversation.memoryRecall.hybridSearched'),
        description: t('memoryConversation.memoryRecall.hybridSearchedDesc', { count: data.hit_count ?? 0 }),
      }
    case 'relation_searched':
      return {
        title: t('memoryConversation.memoryRecall.relationSearched'),
        description: t('memoryConversation.memoryRecall.relationSearchedDesc', { count: data.relation_count ?? 0 }),
      }
    case 'results_merged':
      return {
        title: t('memoryConversation.memoryRecall.resultsMerged'),
        description: t('memoryConversation.memoryRecall.resultsMergedDesc', {
          memoryCount: data.memory_count ?? 0,
          relationCount: data.relation_count ?? 0,
        }),
      }
    case 'perceptual_processed':
      return {
        title: t('memoryConversation.memoryRecall.perceptualProcessed'),
        description: t('memoryConversation.memoryRecall.perceptualProcessedDesc'),
      }
    case 'results_ranked':
      return {
        title: t('memoryConversation.memoryRecall.resultsRanked'),
        description: t('memoryConversation.memoryRecall.resultsRankedDesc', { count: data.count ?? 0 }),
      }
    case 'context_prepared':
      return {
        title: t('memoryConversation.memoryRecall.contextPrepared'),
        description: t('memoryConversation.memoryRecall.contextPreparedDesc', { count: data.memory_count ?? 0 }),
      }
    case 'result_ready':
      return {
        title: t('memoryConversation.memoryRecall.resultReady'),
        description: (data.items?.length || 0) > 0
          ? t('memoryConversation.memoryRecall.resultReadyDesc', {
            shownCount: data.shown_count ?? data.items?.length ?? 0,
          })
          : t('memoryConversation.memoryRecall.resultEmpty'),
      }
    default:
      return undefined
  }
}

const ToolCallRecall: FC<{
  call: MemoryToolCall
  assistantContent?: string | null
  isStreaming: boolean
}> = ({ call, assistantContent, isStreaming }) => {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const mode = getMode(call)
  const question = getQuestion(call)
  const resultStage = getResultStage(call)
  const duration = resultStage?.data.duration_ms
  const items = resultStage?.data.items || []
  const hasFailed = call.status === 'failed'
  const hasCompletedWriting = call.status === 'completed' && !isStreaming
  const isThinking = Boolean(resultStage) && isStreaming && !hasFailed
  const isProcessing = isStreaming && !hasFailed
  const title = hasFailed
    ? t('memoryConversation.memoryRecall.failed')
    : hasCompletedWriting
      ? t('memoryConversation.memoryRecall.completedWithWriting')
      : isThinking
        ? t('memoryConversation.memoryRecall.thinking')
        : t('memoryConversation.memoryRecall.running')
  const modeDescriptionKey = `memoryConversation.memoryRecall.mode${mode.charAt(0).toUpperCase()}${mode.slice(1)}`
  const latestStageSummary = [...call.stages]
    .reverse()
    .map(stage => getStageSummary(stage, t))
    .find((summary): summary is RecallStepSummary => Boolean(summary))
  const collapsedSummary: RecallStepSummary = hasCompletedWriting
    ? {
      title: t('memoryConversation.memoryRecall.writeSubmitted'),
      description: t('memoryConversation.memoryRecall.writeSubmittedDesc'),
    }
    : call.status === 'failed'
      ? {
        title: t('memoryConversation.memoryRecall.failed'),
        description: call.error || t('memoryConversation.memoryRecall.errorFallback'),
      }
      : call.status === 'completed' && !resultStage
        ? {
          title: t('memoryConversation.memoryRecall.resultReady'),
          description: t('memoryConversation.memoryRecall.resultUnavailable'),
        }
        : resultStage
          ? {
            title: t('memoryConversation.memoryRecall.contextInjected'),
            description: t('memoryConversation.memoryRecall.contextInjectedDesc', {
              count: resultStage.data.shown_count ?? items.length,
            }),
          }
          : latestStageSummary || {
            title: t('memoryConversation.memoryRecall.executeRecall', {
              mode: mode === 'unknown'
                ? t('memoryConversation.memoryRecall.unknownMode')
                : `${mode.charAt(0).toUpperCase()}${mode.slice(1)}`,
            }),
            description: t(modeDescriptionKey, {
              defaultValue: t('memoryConversation.memoryRecall.unknownMode'),
            }),
          }

  return (
    <div className="rb:mb-5 rb:w-full rb:text-left">
      <button
        type="button"
        className={clsx(
          'rb:flex rb:items-center rb:gap-2 rb:border-0 rb:bg-transparent rb:p-0 rb:text-[12px] rb:leading-5 rb:text-[#5B6167] rb:cursor-pointer',
          expanded ? 'rb:mb-4' : 'rb:mb-1',
        )}
        onClick={() => setExpanded(value => !value)}
      >
        {isProcessing && (
          <LoadingOutlined spin className="rb:text-[12px] rb:text-[#B8C7E3]" />
        )}
        <span className={clsx({ 'rb:text-[#E5484D]': hasFailed })}>{title}</span>
        <span className={clsx("rb:text-[10px] rb:font-medium rb:text-[#a0a6b0]", {
          'rb:text-[#6D5BD0]': mode === 'deep'
        })}>{mode.toUpperCase()}</span>
        <DownOutlined className={clsx('rb:text-[9px] rb:transition-transform', { 'rb:-rotate-90': !expanded })} />
      </button>

      {!expanded && (
        <div className="rb:flex rb:min-w-0 rb:items-center rb:gap-1 rb:text-[11px] rb:leading-4 rb:text-[#8A8D93]">
          <span className="rb:shrink-0 rb:font-medium rb:text-[#5B6167]">{collapsedSummary.title}</span>
          <span className="rb:shrink-0">·</span>
          <span className="rb:truncate">{collapsedSummary.description}</span>
        </div>
      )}

      {expanded && (
        <div>
          {question && (
            <StageRow
              title={t('memoryConversation.memoryRecall.understandQuery')}
              description={t('memoryConversation.memoryRecall.queryPrepared', { question })}
            />
          )}
          <StageRow
            title={t('memoryConversation.memoryRecall.executeRecall', {
              mode: mode === 'unknown' ? t('memoryConversation.memoryRecall.unknownMode') : `${mode.charAt(0).toUpperCase()}${mode.slice(1)}`,
            })}
            badge={typeof duration === 'number' ? `${duration}ms` : undefined}
            description={t(modeDescriptionKey, { defaultValue: t('memoryConversation.memoryRecall.unknownMode') })}
          >
          </StageRow>

          {call.stages.map((stage, index) => (
            <StageContent key={`${stage.stage}-${index}`} stage={stage} />
          ))}

          {call.status === 'completed' && !resultStage && (
            <StageRow
              title={t('memoryConversation.memoryRecall.resultReady')}
              description={t('memoryConversation.memoryRecall.resultUnavailable')}
            />
          )}
          {call.status === 'failed' && (
            <StageRow
              title={t('memoryConversation.memoryRecall.failed')}
              description={call.error || t('memoryConversation.memoryRecall.errorFallback')}
            />
          )}
          {resultStage && (
            <StageRow
              title={t('memoryConversation.memoryRecall.contextInjected')}
              description={t('memoryConversation.memoryRecall.contextInjectedDesc', {
                count: resultStage.data.shown_count ?? items.length,
              })}
            />
          )}
          {hasCompletedWriting && (
            <>
              <StageRow
                title={t('memoryConversation.memoryRecall.extractPrepared')}
                badge={t('memoryConversation.memoryRecall.async')}
                description={t('memoryConversation.memoryRecall.extractPreparedDesc')}
              >
                <ol className="rb:mt-1 rb:pl-4 rb:list-decimal rb:text-[11px] rb:leading-5 rb:text-[#5B6167]">
                  {question && (
                    <li>
                      <span className="rb:mr-1 rb:text-[#8A8D93]">{t('memoryConversation.memoryRecall.userMessage')}</span>
                      {question}
                    </li>
                  )}
                  <li>
                    <span className="rb:mr-1 rb:text-[#8A8D93]">{t('memoryConversation.memoryRecall.assistantReply')}</span>
                    {assistantContent}
                  </li>
                </ol>
              </StageRow>
              <StageRow
                title={t('memoryConversation.memoryRecall.writeSubmitted')}
                badge={t('memoryConversation.memoryRecall.async')}
                description={t('memoryConversation.memoryRecall.writeSubmittedDesc')}
              />
            </>
          )}
        </div>
      )}
    </div>
  )
}

const MemoryRecall: FC<MemoryRecallProps> = ({ retrieval, assistantContent, isStreaming = false }) => {
  const calls = retrieval.tool_calls.filter(call => call.name === 'long_term_memory')
  if (calls.length === 0) return null

  return (
    <div className="rb:mb-4 rb:w-full">
      {calls.map((call, index) => (
        <ToolCallRecall
          key={call.step_id || `${call.name}-${index}`}
          call={call}
          assistantContent={assistantContent}
          isStreaming={isStreaming}
        />
      ))}
    </div>
  )
}

export default MemoryRecall
