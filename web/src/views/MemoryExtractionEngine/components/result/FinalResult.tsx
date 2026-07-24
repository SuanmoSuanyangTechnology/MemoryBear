/*
 * Final result panel
 * Shows data statistics, dedup impact, disambiguation effects, core entities, triplet samples and ontology coverage
 */
import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Flex } from 'antd'
import clsx from 'clsx'
import RbAlert from '@/components/RbAlert'
import Empty from '@/components/Empty'
import NoDataIcon from '@/assets/images/empty/noData.png'
import ResultCard from '@/components/RbCard/ResultCard'
import type { TestResult, OntologyCoverage } from '../../types'
import type { ModuleItem } from './types'
import PerceptualNodes from './PerceptualNodes'
import { resultObj } from './constants'

interface FinalResultProps {
  testResult: TestResult;
  ontologyCoverage: OntologyCoverage;
  perceptual: ModuleItem;
  coreEntitiesTab: string | null;
  setCoreEntitiesTab: (tab: string | null) => void;
  expandedCards: Record<string, boolean>;
  toggleCard: (key: string) => void;
}

const FinalResult: FC<FinalResultProps> = ({
  testResult,
  ontologyCoverage,
  perceptual,
  coreEntitiesTab,
  setCoreEntitiesTab,
  expandedCards,
  toggleCard,
}) => {
  const { t } = useTranslation()

  // Perceptual memory nodes (same as "Process data")
  const perceptualNodes = perceptual.data || []

  return (
    <Flex vertical gap={12} className="rb:pb-3!">
      {!testResult || Object.keys(testResult).length === 0
        ? <Empty url={NoDataIcon} />
        : null
      }

      {testResult && Object.keys(testResult).length > 0 && resultObj && Object.keys(resultObj).length > 0 &&
        <ResultCard
          title={t(`memoryExtractionEngine.dataStatistics`)}
          expanded={expandedCards['dataStatistics']}
          handleExpand={() => toggleCard('dataStatistics')}
        >
          <div className="rb:grid rb:grid-cols-2 rb:gap-2.5 rb:mb-3">
            {Object.keys(resultObj).map((key, index) => {
              const keys = (resultObj as Record<string, string>)[key].split('.')
              return (
                <div key={index} className="rb:bg-white rb:rounded-lg rb:py-2 rb:px-3">
                  <div className="rb:text-[24px] rb:leading-8 rb:font-bold rb:font-[MiSans-Bold] rb:mb-1">{(testResult?.[keys[0] as keyof TestResult] as any)?.[keys[1]]}</div>
                  <div className="rb:text-[12px] rb:leading-4 rb:mb-0.5">{t(`memoryExtractionEngine.${key}`)}</div>
                  <div className="rb:text-[12px] rb:text-[#369F21] rb:leading-4">
                    {key === 'extractTheNumberOfEntities' && testResult.dedup
                      ? t(`memoryExtractionEngine.${key}Desc`, {
                        num: testResult.dedup.total_merged_count,
                        exact: testResult.dedup.breakdown.exact,
                        fuzzy: testResult.dedup.breakdown.fuzzy,
                        llm: testResult.dedup.breakdown.llm,
                      })
                      : key === 'perceptualMemory' && testResult.perceptual
                        ? t(`memoryExtractionEngine.${key}Desc`, { num: testResult.perceptual.count })
                        : key === 'numberOfRelationalTriples' && testResult.triplets
                          ? t(`memoryExtractionEngine.${key}Desc`, { num: testResult.triplets.count })
                          : t(`memoryExtractionEngine.${key}Desc`)
                    }
                  </div>
                </div>
              )
            })}
          </div>
        </ResultCard>
      }

      {testResult?.dedup?.impact && testResult.dedup.impact?.length > 0 &&
        <ResultCard
          title={t('memoryExtractionEngine.entityDeduplicationImpact')}
          expanded={expandedCards['entityDeduplicationImpact']}
          handleExpand={() => toggleCard('entityDeduplicationImpact')}
        >
          <div className="rb:bg-white rb:rounded-xl rb:p-3 rb:mb-3">
            <RbAlert color="blue" className="rb:mb-2!">
              {t('memoryExtractionEngine.entityDeduplicationImpactDesc', { count: testResult.dedup.impact.length })}
            </RbAlert>
            <div className="rb:font-medium rb:leading-5 rb:mb-2">{t('memoryExtractionEngine.identifyDuplicates')}:</div>

            <ul className="rb:list-disc rb:ml-4">
              {testResult.dedup.impact.map((item, index) => (
                <li key={index} className="rb:leading-6">
                  {t('memoryExtractionEngine.identifyDuplicatesDesc', { ...item })}
                </li>
              ))}
            </ul>
          </div>
        </ResultCard>
      }

      {testResult?.disambiguation && testResult.disambiguation?.effects?.length > 0 &&
        <ResultCard
          title={t('memoryExtractionEngine.theEffectOfEntityDisambiguationLLMDriven')}
          expanded={expandedCards['disambiguation']}
          handleExpand={() => toggleCard('disambiguation')}
        >
          <div className="rb:bg-white rb:rounded-xl rb:p-3 rb:mb-3">
            <RbAlert color="blue" className="rb:mb-2!">
              {t('memoryExtractionEngine.entityDeduplicationImpactDesc', { count: testResult.dedup.impact.length })}
            </RbAlert>
            {testResult.disambiguation.effects.map((item, index) => (
              <div key={index} className={clsx("rb:text-[12px] rb:text-[#5B6167] rb:leading-4", {
                'rb:mt-5': index > 0,
              })}>
                <div className="rb:font-medium rb:leading-5 rb:mb-1">{t('memoryExtractionEngine.disagreementCase')} {index + 1}:</div>

                <ul className="rb:list-disc rb:ml-4">
                  <li key={index} className="rb:leading-6">
                    {item.left.name}({item.left.type}) vs {item.right.name}({item.right.type}) → {item.result}
                  </li>
                </ul>
              </div>
            ))}
          </div>
        </ResultCard>
      }

      {testResult?.core_entities && testResult?.core_entities.length > 0 &&
        <ResultCard
          title={t('memoryExtractionEngine.coreEntitiesAfterDedup')}
          expanded={expandedCards['coreEntities']}
          handleExpand={() => toggleCard('coreEntities')}
        >
          <Flex gap={10} wrap className="rb:px-1! rb:mb-3! rb:gap-y-2!">
            {testResult.core_entities.map((item, index) => (
              <div
                key={item.type}
                className={clsx("rb:rounded-[13px] rb:py-0.5 rb:px-3 rb:leading-5 rb:cursor-pointer", {
                  'rb:bg-white': !((coreEntitiesTab && item.type === coreEntitiesTab) || (!coreEntitiesTab && index === 0)),
                  'rb:bg-[#171719] rb:text-white': (coreEntitiesTab && item.type === coreEntitiesTab) || (!coreEntitiesTab && index === 0)
                })}
                onClick={() => setCoreEntitiesTab(item.type)}
              >
                {item.type}({item.count})
              </div>
            ))}
          </Flex>
          <div className="rb:bg-white rb:rounded-lg rb:py-2.5 rb:px-3 rb:mb-3">
            {testResult.core_entities.filter((item, index) => (coreEntitiesTab && item.type === coreEntitiesTab) || (!coreEntitiesTab && index === 0)).map((item, idx) => (
              <div key={idx} className="rb:leading-5">
                <div className="rb:text-[#155EEF] rb:font-medium rb:mb-2">{item.type}({item.count})</div>

                <ul className="rb:list-disc rb:ml-4">
                  {item.entities.map((entity, index) => (
                    <li key={index} className="rb:leading-6">
                      {entity}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </ResultCard>
      }

      {testResult?.triplet_samples && testResult?.triplet_samples.length > 0 &&
        <ResultCard
          title={t('memoryExtractionEngine.extractRelationalTriples')}
          expanded={expandedCards['triplet_samples']}
          handleExpand={() => toggleCard('triplet_samples')}
        >
          <div className="rb:bg-white rb:rounded-xl rb:p-3 rb:mb-3">
            <RbAlert color="blue" className="rb:mb-2!">
              {t('memoryExtractionEngine.extractRelationalTriplesDesc', { count: testResult.triplet_samples.length })}
            </RbAlert>
            <ul className="rb:list-disc rb:ml-4">
              {testResult.triplet_samples.map((item, index) => (
                <li key={index} className="rb:leading-6">
                  ({item.subject}, <span className="rb:text-[#155EEF] rb:font-medium">{item.predicate}</span>, {item.object})
                </li>
              ))}
            </ul>
          </div>
        </ResultCard>
      }
      {perceptualNodes.length > 0 &&
        <ResultCard
          title={t('memoryExtractionEngine.perceptualMemory')}
          expanded={expandedCards['perceptual']}
          handleExpand={() => toggleCard('perceptual')}
        >
          <PerceptualNodes nodes={perceptualNodes} />
        </ResultCard>
      }
      {ontologyCoverage && Object.keys(ontologyCoverage).length > 0 &&
        <ResultCard
          title={<>{t('memoryExtractionEngine.ontologyCoverage')}({ontologyCoverage.total_entities})</>}
          expanded={expandedCards['ontologyCoverage']}
          handleExpand={() => toggleCard('ontologyCoverage')}
        >
          <div className="rb:bg-white rb:rounded-xl rb:p-3 rb:mb-3 rb:leading-5">
            <div className="rb:grid rb:grid-cols-1 rb:gap-3">
              {(['scene_type_distribution', 'general_type_distribution', 'unmatched'] as const).map((key, idx) => {
                if (!ontologyCoverage[key]) return null
                return (
                  <div key={idx}>
                    <div className="rb:text-[#155EEF] rb:font-medium rb:mb-1">{t(`memoryExtractionEngine.${key}`)}({ontologyCoverage[key].type_count})</div>
                    <div className="rb:text-[#212332] rb:mb-1">{t('memoryExtractionEngine.entity_total', { num: ontologyCoverage[key].entity_total })}</div>

                    <ul className="rb:list-disc rb:ml-4">
                      {ontologyCoverage[key].types.map((type, index) => {
                        if (!type.type || type.type === '') return null
                        return (
                          <li key={index} className="rb:leading-6 rb:text-[#5B6167]">
                            {type.type}({type.count})
                          </li>
                        )
                      })}
                    </ul>
                  </div>
                )
              })}
            </div>
          </div>
        </ResultCard>
      }
    </Flex>
  )
}
export default FinalResult
