/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:30:11 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 11:40:38
 */
/**
 * Result Component
 * Displays real-time extraction results with progress tracking
 * Shows text preprocessing, knowledge extraction, node/edge creation, and deduplication
 */

import { type FC, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Space, Button, Progress, Form, Input, Flex } from 'antd'
import { ExclamationCircleFilled, LoadingOutlined } from '@ant-design/icons'
import clsx from 'clsx'
import type { AnyObject } from 'antd/es/_util/type';

import Card from './Card'
import RbAlert from '@/components/RbAlert'
import type { TestResult, OntologyCoverage } from '../types'
import { pilotRunMemoryExtractionConfig } from '@/api/memory'
import { type SSEMessage } from '@/utils/stream'
import Tag, { type TagProps } from '@/components/Tag'
import Markdown from '@/components/Markdown'
import { groupDataByType } from '../constant'
import Empty from '@/components/Empty'
import NoDataIcon from '@/assets/images/empty/noData.png'
import ResultCard from '@/components/RbCard/ResultCard'

/** Result metric mapping */
const resultObj = {
  extractTheNumberOfEntities: 'entities.extracted_count',
  numberOfEntityDisambiguation: 'disambiguation.block_count',
  memoryFragments: 'memory.chunks',
  numberOfRelationalTriples: 'triplets.count'
}
/**
 * Component props
 */
interface ResultProps {
  loading: boolean;
  handleSave: () => void;
}
/**
 * Module processing item
 */
interface ModuleItem {
  status: 'pending' | 'processing' | 'completed' | 'failed';
  data: any[],
  result: any,
  start_at?: number;
  end_at?: number;
}
/** Tag color mapping by status */
const tagColors: {
  [key: string]:  TagProps['color']
} = {
  pending: 'warning',
  processing: 'processing',
  completed: 'success',
  failed: 'error'
}
/** Initial module state */
const initObj = {
  data: [],
  status: 'pending',
  result: null
}
const initialExpanded = {
  text_preprocessing: false,
  knowledge_extraction: false,
  creating_nodes_edges: false,
  deduplication: false,
  dataStatistics: false,
  entityDeduplicationImpact: false,
  disambiguation: false,
  coreEntities: false,
  triplet_samples: false,
  ontologyCoverage: false,
}

const Result: FC<ResultProps> = ({ loading, handleSave }) => {
  const { t } = useTranslation();
  const { id } = useParams()
  const [runLoading, setRunLoading] = useState(false)
  const [activeTab, setActiveTab] = useState('processData')
  const [testResult, setTestResult] = useState<TestResult>({} as TestResult)
  const [coreEntitiesTab, setCoreEntitiesTab] = useState<string | null>(null) 
  const [textPreprocessing, setTextPreprocessing] = useState<ModuleItem>(initObj as ModuleItem)
  const [textPreprocessingTab, setTextPreprocessingTab] = useState('chunking')
  const [knowledgeExtraction, setKnowledgeExtraction] = useState<ModuleItem>(initObj as ModuleItem)
  const [creatingNodesEdges, setCreatingNodesEdges] = useState<ModuleItem>(initObj as ModuleItem)
  const [deduplication, setDeduplication] = useState<ModuleItem>(initObj as ModuleItem)
  const [ontologyCoverage, setOntologyCoverage] = useState<OntologyCoverage>({} as OntologyCoverage)

  const [expandedCards, setExpandedCards] = useState<Record<string, boolean>>(initialExpanded)
  const toggleCard = (key: string) => {
    console.log('toggleCard', key)
    setExpandedCards(prev => ({ ...prev, [key]: !prev[key] }))
  }
  console.log('expandedCards', expandedCards)

  const [runForm] = Form.useForm()
  const customText = Form.useWatch(['custom_text'], runForm)

  /** Run pilot test */
  const handleRun = () => {
    if(!id) return
    setActiveTab('processData')
    setCoreEntitiesTab(null)
    setTextPreprocessing({...initObj} as ModuleItem)
    setTextPreprocessingTab('chunking')
    setKnowledgeExtraction({...initObj} as ModuleItem)
    setCreatingNodesEdges({...initObj} as ModuleItem)
    setDeduplication({...initObj} as ModuleItem)
    setTestResult({} as TestResult)
    setExpandedCards(initialExpanded)
    const handleStreamMessage = (list: SSEMessage[]) => {

      list.forEach((data: AnyObject) => {
        switch(data.event) {
          case 'text_preprocessing': // Start text preprocessing
            setTextPreprocessing(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            toggleCard('text_preprocessing')
            break
          case 'text_preprocessing_result': // Text preprocessing in progress
            setTextPreprocessing(prev => ({
              ...prev,
              data: [...prev.data, data.data?.deleted_messages ? { deleted_messages: data.data?.deleted_messages } : data.data?.data],
            }))
            break
          case 'text_preprocessing_complete': // Text preprocessing complete
            setTextPreprocessing(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'knowledge_extraction': // Start knowledge extraction
            setKnowledgeExtraction(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            toggleCard('knowledge_extraction')
            break
          case 'knowledge_extraction_result': // Knowledge extraction in progress
            setKnowledgeExtraction(prev => ({
              ...prev,
              data: [...prev.data, data.data?.data]
            }))
            break
          case 'knowledge_extraction_complete': // Knowledge extraction complete
            setKnowledgeExtraction(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'creating_nodes_edges': // Start creating nodes and edges
            setCreatingNodesEdges(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            toggleCard('creating_nodes_edges')
            break
          case 'creating_nodes_edges_result': // Creating nodes and edges in progress
            setCreatingNodesEdges(prev => ({
              ...prev,
              data: [...prev.data, data.data?.data]
            }))
            break
          case 'creating_nodes_edges_complete': // Creating nodes and edges complete
            setCreatingNodesEdges(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'deduplication': // Start deduplication and disambiguation
            setDeduplication(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            toggleCard('deduplication')
            break
          case 'dedup_disambiguation_result': // Deduplication and disambiguation in progress
            setDeduplication(prev => ({
              ...prev,
              data: [...prev.data, data.data.data]
            }))
            break
          case 'dedup_disambiguation_complete': // Deduplication and disambiguation complete
            setDeduplication(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'generating_results': // Generating results
            break
          case 'result': // Result
            setTestResult(data.data?.extracted_result)
            setOntologyCoverage(data.data?.ontology_coverage)
            setExpandedCards(prev => ({
              ...prev,
              dataStatistics: true,
              entityDeduplicationImpact: true,
              disambiguation: true,
              coreEntities: true,
              triplet_samples: true,
              ontologyCoverage: true,
            }))
            break
        }
      })
    }
    setRunLoading(true)
    pilotRunMemoryExtractionConfig({
      config_id: id,
      dialogue_text: t('memoryExtractionEngine.exampleText'),
      custom_text: runForm.getFieldValue('custom_text')
    }, handleStreamMessage)
      .finally(() => {
        setRunLoading(false)
      })
  }
  const completedNum = [textPreprocessing, knowledgeExtraction, creatingNodesEdges, deduplication].filter(item => item.status === 'completed').length
  const deduplicationData = groupDataByType(deduplication.data, 'result_type')

  /** Format status tag */
  const formatTag = (status: string) => {
    return (
      <Tag color={tagColors[status]} className="rb:flex! rb:items-center rb:gap-1 rb:bg-white! rb:border-white!">
        {status === 'pending' && <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/memory/clock_orange.svg')]"></div>}
        {status === 'processing' && <LoadingOutlined spin className="rb:mr-1" />}
        {status === 'completed' && <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/check_green.svg')]"></div>}
        {t(`memoryExtractionEngine.status.${status}`)}
      </Tag>
    )
  }
  /** Format processing time */
  const formatTime = (data: ModuleItem, color?: string) => {
    if (typeof data.end_at === 'number' && typeof data.start_at === 'number') {
      return <div className={`rb:text-[${color ?? '#155EEF'}] rb:mb-0.5`}>{t('memoryExtractionEngine.time')}{data.end_at - data.start_at}ms</div>
    }
    return null
  }
  /** Convert first character to lowercase */
  const lowercaseFirst = (str: string) => str.charAt(0).toLowerCase() + str.slice(1)

  return (
    <Card
      title={t('memoryExtractionEngine.exampleMemoryExtractionResults')}
      subTitle={t('memoryExtractionEngine.exampleMemoryExtractionResultsSubTitle')}
      headerClassName="rb:pb-0! rb:pt-4!"
      bodyClassName="rb:h-[calc(100vh-163px)]! rb:overflow-y-auto rb:p-[16px_20px]!"
      extra={<Space size={8}>
        <Button
          icon={<div className="rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/common/save.svg')]"></div>}
          loading={loading}
          onClick={handleSave}
        >{t('common.save')}</Button>
        <Button
          type="primary"
          icon={<div className="rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/memory/debug.svg')]"></div>}
          loading={runLoading}
          onClick={handleRun}
        >{t('memoryExtractionEngine.debug')}</Button>
      </Space>}
    >
      {/* <RbAlert color="orange" icon={<ExclamationCircleFilled />} className="rb:mb-3!">
        {t('memoryExtractionEngine.warning')}
      </RbAlert> */}
      <Form form={runForm} layout="vertical" className="rb:bg-[#F6F6F6]! rb:rounded-xl rb:py-2! rb:mb-4!">
        <Flex align="center" justify="space-between" className="rb:px-3! rb:mb-2!">
          <div className="rb:text-[#212332] rb:font-medium rb:leading-5">{t('memoryExtractionEngine.custom_text')}</div>
          <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4.5">{customText?.length || 0}</div>
        </Flex>
        <Form.Item
          name="custom_text"
          label={t('memoryExtractionEngine.custom_text')}
          noStyle
        >
          <Input.TextArea placeholder={t('common.pleaseEnter')} variant="borderless" />
        </Form.Item>
      </Form>

      {runLoading
        ? <>
          <RbAlert color="blue">
            <div className="rb:w-full">
              {t('memoryExtractionEngine.processing')}

              {/* Overall Progress */}
              <Flex gap={13} align="center">
                <Progress percent={completedNum * 100 / 4} showInfo={false} className="rb:flex-1!" />
                <div className="rb:text-[12px] rb:leading-4 rb:font-regular">
                  {t('memoryExtractionEngine.overallProgress')}{`${(completedNum*100/4).toFixed(0)}%`}
                </div>
              </Flex>
            </div>
          </RbAlert>
        </>
        : !testResult || Object.keys(testResult).length === 0
        ? <RbAlert color="orange" icon={<ExclamationCircleFilled />}>
          {t('memoryExtractionEngine.warning')}
        </RbAlert>
        : <RbAlert color="green" icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/check_green.svg')]"></div>}>
          {t('memoryExtractionEngine.success')}
        </RbAlert>
      }

      <Space size={24} className="rb:mt-4! rb:mb-3!">
        {['processData', 'finalResult'].map(tab => (
          <div
            className={clsx('rb:font-[MiSans-Bold] rb:font-bold rb:leading-5 rb:cursor-pointer', {
              'rb:text-[#212332]': activeTab === tab,
              'rb:text-[#A8A9AA]': activeTab !== tab,
            })}
            onClick={() => setActiveTab(tab)}
          >{t(`memoryExtractionEngine.${tab}`)}</div>
        ))}
      </Space>

      {activeTab === 'processData' && <Flex vertical gap={12} className="rb:pb-3!">
        {/* Text Preprocessing */}
        <ResultCard
          title={t(`memoryExtractionEngine.text_preprocessing`)}
          extra={formatTag(textPreprocessing.status)}
          expanded={expandedCards['text_preprocessing']}
          handleExpand={() => toggleCard('text_preprocessing')}
        >
          {expandedCards['text_preprocessing'] && textPreprocessing.data?.length > 0 &&
            <Space size={10} className="rb:px-1! rb:mb-3!">
              {(['chunking', ...(textPreprocessing.data.some(vo => vo.deleted_messages) ? ['pruning'] : [])] as string[]).map(type => (
                <div
                  key={type}
                  className={clsx("rb:rounded-[13px] rb:py-0.5 rb:px-3 rb:leading-5 rb:cursor-pointer", {
                    'rb:bg-white': textPreprocessingTab !== type,
                    'rb:bg-[#171719] rb:text-white': textPreprocessingTab === type
                  })}
                  onClick={() => setTextPreprocessingTab(type)}
                >
                  {t(`memoryExtractionEngine.${type}`)}
                </div>
              ))}
            </Space>
          }
          {expandedCards['text_preprocessing'] && textPreprocessing.result &&
            <RbAlert color="blue" className="rb:mb-2!">
              <div>
                <div>{formatTime(textPreprocessing)}</div>
                {t('memoryExtractionEngine.pruning_desc', { count: textPreprocessing.result.pruning.deleted_count || 0 })},
                {t('memoryExtractionEngine.text_preprocessing_desc', { count: textPreprocessing.result.total_chunks })},
                {t('memoryExtractionEngine.chunkerStrategy')}: {t(`memoryExtractionEngine.${lowercaseFirst(textPreprocessing.result.chunker_strategy)}`)}
              </div>
            </RbAlert>
          }
          {expandedCards['text_preprocessing'] && textPreprocessing.data.map((vo, index) => {
            if (vo.deleted_messages && textPreprocessingTab === 'pruning') {
              return <div key={index} className="rb:mb-3 rb:pb-1 rb:border-b rb:border-b-[#EBEBEB]">
                <div className="rb:font-medium rb:text-[12px] rb:mb-2">{t('memoryExtractionEngine.Pruned')}</div>
                {vo.deleted_messages.map((msg: any, idx: number) => (
                  <div key={idx} className="rb:leading-5">
                    <div className="rb:font-medium">-{t('memoryExtractionEngine.pruning')}{idx}:</div>
                    <Markdown content={msg.content} />
                  </div>
                ))}
              </div>
            }
            if (textPreprocessingTab === 'chunking' && vo.content) {
              return (
                <div key={index} className="rb:leading-5">
                  <div className="rb:font-medium">-{t('memoryExtractionEngine.fragment')}{vo.chunk_index}:</div>
                  <Markdown content={vo.content.startsWith('\n') ? vo.content : '\n' + vo.content} className="rb:text-[#212332]" />
                </div>
              )
            }
            return null
          })}
        </ResultCard>
        {/* Knowledge Extraction */}
        <ResultCard
          title={t(`memoryExtractionEngine.knowledge_extraction`)}
          extra={formatTag(knowledgeExtraction.status)}
          expanded={expandedCards['knowledge_extraction']}
          handleExpand={() => toggleCard('knowledge_extraction')}
        >
          {knowledgeExtraction.result &&
            <RbAlert color="blue" className="rb:mb-2!">
              <div>
                <div>{formatTime(knowledgeExtraction)}</div>
                {t('memoryExtractionEngine.knowledge_extraction_desc', {
                  entities: knowledgeExtraction.result.entities_count,
                  statements: knowledgeExtraction.result.statements_count,
                  temporal_ranges_count: knowledgeExtraction.result.temporal_ranges_count,
                  triplets: knowledgeExtraction.result.triplets_count
                })}
              </div>
            </RbAlert>
          }
          {knowledgeExtraction.data?.length > 0 &&
            <ul className="rb:list-disc rb:ml-4 rb:mb-3">
              {knowledgeExtraction.data.map((vo, index) =>
                <li key={index} className="rb:leading-6">{vo.statement}</li>
              )}
            </ul>
          }
        </ResultCard>
        {/* Creating Entity Relationships */}
        <ResultCard
          title={t(`memoryExtractionEngine.creating_nodes_edges`)}
          extra={formatTag(creatingNodesEdges.status)}
          expanded={expandedCards['creating_nodes_edges']}
          handleExpand={() => toggleCard('creating_nodes_edges')}
        >
          {creatingNodesEdges.result &&
            <RbAlert color="blue" className="rb:mb-2!">
              <div>
                <div>{formatTime(creatingNodesEdges)}</div>
                {t('memoryExtractionEngine.creating_nodes_edges_desc', { num: creatingNodesEdges.result.entity_entity_edges_count })}
              </div>
            </RbAlert>
          }
          {creatingNodesEdges.data?.length > 0 &&
            <ul className="rb:list-disc rb:ml-4 rb:mb-3">
              {creatingNodesEdges.data.map((vo, index) =>
                <li key={index} className="rb:leading-6">
                  {vo?.result_type === 'entity_nodes_creation'
                    ? <>{vo.type_display_name}: {vo.entity_names.join(', ')}</>
                    : <>{vo?.relationship_text}</>
                  }
                </li>
              )}
            </ul>
          }
        </ResultCard>
        {/* Deduplication and Disambiguation */}
        <ResultCard
          title={t(`memoryExtractionEngine.deduplication`)}
          extra={formatTag(deduplication.status)}
          expanded={expandedCards['deduplication']}
          handleExpand={() => toggleCard('deduplication')}
        >
          {deduplication.result &&
            <RbAlert color="blue" className="rb:mb-2!">
              <div>
                <div>{formatTime(deduplication)}</div>
                {t('memoryExtractionEngine.deduplication_desc', { count: deduplication.result.summary.total_merges })}
              </div>
            </RbAlert>
          }
          {Object.keys(deduplicationData).length > 0 &&
            <ul className="rb:list-disc rb:ml-4 rb:mb-3">
              {Object.keys(deduplicationData).map(key => {
                return deduplicationData[key].map((vo, index) => (
                  <li key={index} className="rb:leading-6">
                    {vo.message}
                  </li>
                ))
              })}
            </ul>
          }
        </ResultCard>
      </Flex>}

      {activeTab === 'finalResult' && <Flex vertical gap={12} className="rb:pb-3!">
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
                        : key === 'numberOfEntityDisambiguation' && testResult.disambiguation
                          ? t(`memoryExtractionEngine.${key}Desc`, { num: testResult.disambiguation.effects?.length, block_count: testResult.disambiguation.block_count })
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
              <RbAlert color="blue"className="rb:mb-2!">
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
      </Flex>}
    </Card>
  )
}
export default Result