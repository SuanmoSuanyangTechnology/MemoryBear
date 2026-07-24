/*
 * Process data panel
 * Six cards: pruning / chunking / statement extraction / triplet extraction / perceptual memory extraction / deduplication
 */
import { type FC, type ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Flex } from 'antd'
import clsx from 'clsx'
import RbAlert from '@/components/RbAlert'
import ResultCard from '@/components/RbCard/ResultCard'
import type { ModuleItem } from './types'
import { formatTag, formatTime } from './helpers'
import PerceptualNodes from './PerceptualNodes'

interface ProcessDataProps {
  textPreprocessing: ModuleItem;
  chunking: ModuleItem;
  knowledgeExtraction: ModuleItem;
  creatingNodesEdges: ModuleItem;
  deduplication: ModuleItem;
  perceptual: ModuleItem;
  /** Whether semantic pruning is enabled; hides the pruning card when false */
  pruningEnabled?: boolean;
  expandedCards: Record<string, boolean>;
  toggleCard: (key: string) => void;
}

/** Strip the leading [timestamp] prefix from an entity description */
const cleanDescription = (desc?: string) => (desc || '').replace(/^\[[^\]]*\]\s*/, '')

/** Card title: name + event type badge */
const CardTitle: FC<{ label: string; }> = ({ label }) => (
  <span className="rb:inline-flex rb:items-center rb:gap-2">
    {label}
  </span>
)

type PillTone = 'default' | 'green' | 'purple'
const PILL_TONE: Record<PillTone, string> = {
  default: 'rb:bg-[#F0F0F1] rb:text-[#5A5C66]',
  green: 'rb:bg-[#E7F6EC] rb:text-[#12B76A]',
  purple: 'rb:bg-[#EFE9FC] rb:text-[#7A5AF8]',
}

/** Tag pill: shows only value when label is empty */
const Pill: FC<{ label?: string; value: ReactNode; tone?: PillTone }> = ({ label, value, tone = 'default' }) => (
  <span className={clsx('rb:inline-flex rb:items-center rb:rounded rb:px-2 rb:py-0.5 rb:text-[12px] rb:leading-5 rb:mr-2 rb:mb-1', PILL_TONE[tone])}>
    {label ? `${label}: ` : ''}{value}
  </span>
)

const ProcessData: FC<ProcessDataProps> = ({
  textPreprocessing,
  chunking,
  knowledgeExtraction,
  creatingNodesEdges,
  deduplication,
  perceptual,
  pruningEnabled = true,
  expandedCards,
  toggleCard,
}) => {
  const { t } = useTranslation()

  // Triplet card "entities / triplets" toggle
  const [tripletTab, setTripletTab] = useState('entities')

  // Pruning: text preprocessing result is directly a user_message_changes list
  const pruningChanges = textPreprocessing.data || []

  // Chunking: each item is { chunk_index, content, ... }
  const chunks = chunking.data || []
  const chunkCount = chunking.result?.total_chunks ?? chunks.length

  // Statements
  const statements = knowledgeExtraction.data || []
  const statementCount = knowledgeExtraction.result?.statements_count ?? statements.length

  // Triplets: each item carries entity_creation[] / relationship_creation[]; flatten across items then dedupe
  const cne = creatingNodesEdges.data || []
  const entities = cne
    .flatMap((vo: any) => vo?.entity_creation || [])
  const relationships = cne
    .flatMap((vo: any) => vo?.relationship_creation || [])
  const entityCount = creatingNodesEdges.result?.entities_count ?? entities.length
  const tripletCount = creatingNodesEdges.result?.triplets_count ?? relationships.length

  // Deduplication
  const merges = deduplication.data || []
  const mergedPairs = deduplication.result?.merged_pairs || []
  const dedupBefore = deduplication.result?.entities?.original_count ?? '-'
  const dedupAfter = deduplication.result?.entities?.final_count ?? '-'
  const pairCount = mergedPairs.length || merges.length

  // Perceptual memory nodes
  const perceptualNodes = perceptual.data || []

  return (
    <Flex vertical gap={12} className="rb:pb-3!">
      {/* Perceptual memory extraction complete */}
      <ResultCard
        title={<CardTitle label={t('memoryExtractionEngine.perceptual_result_title')} />}
        extra={formatTag(perceptual.status, t)}
        expanded={expandedCards['perceptual']}
        handleExpand={() => toggleCard('perceptual')}
      >
        {perceptual.result &&
          <RbAlert color="blue" className="rb:mb-2!">
            <div>
              <div>{formatTime(perceptual, t)}</div>
              {t('memoryExtractionEngine.perceptual_result_desc', { count: perceptualNodes.length })}
            </div>
          </RbAlert>
        }
        {perceptualNodes.length > 0 &&
          <PerceptualNodes nodes={perceptualNodes} />
        }
      </ResultCard>

      {/* Pruning */}
      {pruningEnabled &&
      <ResultCard
        title={<CardTitle label={t('memoryExtractionEngine.pruning_result_title')} />}
        extra={formatTag(textPreprocessing.status, t)}
        expanded={expandedCards['text_preprocessing']}
        handleExpand={() => toggleCard('text_preprocessing')}
      >
        {textPreprocessing.result &&
          <RbAlert color="blue" className="rb:mb-2!">
            <div>
              <div>{formatTime(textPreprocessing, t)}</div>
              {t('memoryExtractionEngine.pruning_result_desc', { count: pruningChanges.length })}
            </div>
          </RbAlert>
        }
        {pruningChanges.length > 0 &&
          <Flex vertical gap={12} className="rb:mb-2!">
            {pruningChanges.map((change: any, index: number) => (
              <div key={index} className="rb:p-3 rb:bg-white rb:rounded-xl">
                <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-5 rb:mb-1">{t('memoryExtractionEngine.original_preview')} (original_preview)</div>
                <div className="rb:leading-5 rb:mb-2">{change.original}</div>
                <div className="rb:text-center rb:text-[#155EEF] rb:text-[12px] rb:mb-2">↓ {t('memoryExtractionEngine.compressedInto')}</div>
                <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-5 rb:mb-1">{t('memoryExtractionEngine.memory_hint')} (memory_hint)</div>
                <div className="rb:leading-5 rb:mb-2">{change.pruned}</div>
              </div>
            ))}
          </Flex>
        }
      </ResultCard>
      }

      {/* Chunking */}
      <ResultCard
        title={<CardTitle label={t('memoryExtractionEngine.chunking_result_title')} />}
        extra={formatTag(chunking.status, t)}
        expanded={expandedCards['chunking']}
        handleExpand={() => toggleCard('chunking')}
      >
        {chunking.result &&
          <RbAlert color="blue" className="rb:mb-2!">
            <div>
              <div>{formatTime(chunking, t)}</div>
              {t('memoryExtractionEngine.chunking_result_desc', { count: chunkCount, strategy: chunking.result?.chunker_strategy || '-' })}
            </div>
          </RbAlert>
        }
        {chunks.length > 0 &&
          <Flex vertical gap={12} className="rb:mb-2!">
            {chunks.map((chunk: any, index: number) => (
              <div key={index} className="rb:p-3 rb:bg-white rb:rounded-xl">
                <Flex align="center" gap={6} className="rb:mb-1">
                  <span className="rb:text-[#5B6167] rb:text-[12px]">{t('memoryExtractionEngine.fragment')}{chunk.chunk_index ?? index + 1}</span>
                </Flex>
                <div className="rb:text-[#212332] rb:leading-5">{chunk.content}</div>
              </div>
            ))}
          </Flex>
        }
      </ResultCard>

      {/* Statement extraction complete */}
      <ResultCard
        title={<CardTitle label={t('memoryExtractionEngine.statement_result_title')} />}
        extra={formatTag(knowledgeExtraction.status, t)}
        expanded={expandedCards['knowledge_extraction']}
        handleExpand={() => toggleCard('knowledge_extraction')}
      >
        {knowledgeExtraction.result &&
          <RbAlert color="blue" className="rb:mb-2!">
            <div>
              <div>{formatTime(knowledgeExtraction, t)}</div>
              {t('memoryExtractionEngine.statement_result_desc', { count: statementCount })}
            </div>
          </RbAlert>
        }
        {statements.length > 0 &&
          <Flex vertical gap={12} className="rb:mb-2!">
            {statements.map((vo: any, index: number) => (
              <div key={index} className="rb:p-3 rb:bg-white rb:rounded-xl">
                <div className="rb:leading-6 rb:text-[#212332]">
                  <span className="rb:text-[#5B6167] rb:mr-1">[s{index + 1}]</span>
                  {vo.statement || vo.content}
                </div>
              </div>
            ))}
          </Flex>
        }
      </ResultCard>

      {/* Triplet extraction complete */}
      <ResultCard
        title={<CardTitle label={t('memoryExtractionEngine.triplet_result_title')} />}
        extra={formatTag(creatingNodesEdges.status, t)}
        expanded={expandedCards['creating_nodes_edges']}
        handleExpand={() => toggleCard('creating_nodes_edges')}
      >
        {creatingNodesEdges.result &&
          <RbAlert color="blue" className="rb:mb-2!">
            <div>
              <div>{formatTime(creatingNodesEdges, t)}</div>
              {t('memoryExtractionEngine.triplet_result_desc', { entity: entityCount, triplet: tripletCount })}
            </div>
          </RbAlert>
        }
        {(entities.length > 0 || relationships.length > 0) &&
          <>
            <Flex gap={10} wrap className="rb:px-1! rb:mb-3! rb:gap-y-2!">
              {[
                { value: 'entities', label: `${t('memoryExtractionEngine.entities')} (${entities.length})` },
                { value: 'triplets', label: `${t('memoryExtractionEngine.triplets')} (${relationships.length})` },
              ].map((item) => (
                <div
                  key={item.value}
                  className={clsx("rb:rounded-[13px] rb:py-0.5 rb:px-3 rb:leading-5 rb:cursor-pointer", {
                    'rb:bg-white': tripletTab !== item.value,
                    'rb:bg-[#171719] rb:text-white': tripletTab === item.value
                  })}
                  onClick={() => setTripletTab(item.value)}
                >
                  {item.label}
                </div>
              ))}
            </Flex>
            <div className="rb:p-3 rb:mb-2 rb:bg-white rb:rounded-xl">
              {tripletTab === 'entities'
                ? <ul className="rb:list-disc rb:ml-4">
                    {entities.map((vo: any, index: number) => (
                      <li key={index} className="rb:leading-6">
                        <span>{vo.name}</span>
                        {vo.type && <span className="rb:ml-1"><Pill label="type" value={vo.type} /></span>}
                        {cleanDescription(vo.description) && <span> — {cleanDescription(vo.description)}</span>}
                      </li>
                    ))}
                  </ul>
                : <ul className="rb:list-disc rb:ml-4">
                    {relationships.map((vo: any, index: number) => (
                      <li key={index} className="rb:leading-6">
                        {vo.source_entity} —<span className="rb:text-[#155EEF] rb:font-medium">[{vo.relation_type}]</span>→ {vo.target_entity}
                      </li>
                    ))}
                  </ul>
              }
            </div>
          </>
        }
      </ResultCard>

      {/* Deduplication complete */}
      <ResultCard
        title={<CardTitle label={t('memoryExtractionEngine.dedup_result_title')} />}
        extra={formatTag(deduplication.status, t)}
        expanded={expandedCards['deduplication']}
        handleExpand={() => toggleCard('deduplication')}
      >
        {deduplication.result &&
          <RbAlert color="blue" className="rb:mb-2!">
            <div>
              <div>{formatTime(deduplication, t)}</div>
              {t('memoryExtractionEngine.dedup_result_desc', { before: dedupBefore, after: dedupAfter, pairs: pairCount })}
            </div>
          </RbAlert>
        }
        {(mergedPairs.length > 0 || merges.length > 0) &&
          <div className="rb:mb-2 rb:p-3 rb:bg-white rb:rounded-xl">
            <div className="rb:font-medium rb:mb-1">{t('memoryExtractionEngine.merged_pairs')} (merged_pairs)</div>
            <ul className="rb:list-disc rb:ml-4">
              {mergedPairs.length > 0
                ? mergedPairs.map((pair: any, index: number) => (
                  <li key={index} className="rb:leading-6">
                    [{pair.source || pair.a || pair[0]}] ⟷ [{pair.target || pair.b || pair[1]}] → {t('memoryExtractionEngine.mergedIntoOne')}
                  </li>
                ))
                : merges.map((vo: any, index: number) => (
                  <li key={index} className="rb:leading-6">
                    [{vo.merged_entity_name}] {t('memoryExtractionEngine.mergedCount', { count: vo.merged_count })} → {t('memoryExtractionEngine.mergedIntoOne')}
                  </li>
                ))
              }
            </ul>
          </div>
        }
      </ResultCard>
    </Flex>
  )
}
export default ProcessData
