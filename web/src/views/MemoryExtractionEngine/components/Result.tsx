import { type FC, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Space, Button, Progress } from 'antd'
import { ExclamationCircleFilled, CheckCircleFilled, ClockCircleOutlined, LoadingOutlined } from '@ant-design/icons'
import clsx from 'clsx'
import Card from './Card'
import RbCard from '@/components/RbCard/Card'
import RbAlert from '@/components/RbAlert'
import type { TestResult } from '../types'
import { pilotRunMemoryExtractionConfig } from '@/api/memory'
import { type SSEMessage } from '@/utils/stream'
import Tag, { type TagProps } from '@/components/Tag'
import Markdown from '@/components/Markdown'
import { groupDataByType } from '../constant'
import type { AnyObject } from 'antd/es/_util/type';

const resultObj = {
  extractTheNumberOfEntities: 'entities.extracted_count',
  numberOfEntityDisambiguation: 'disambiguation.block_count',
  memoryFragments: 'memory.chunks',
  numberOfRelationalTriples: 'triplets.count'
}
interface ResultProps {
  loading: boolean;
  handleSave: () => void;
}
interface ModuleItem {
  status: 'pending' | 'processing' | 'completed' | 'failed';
  data: any[],
  result: any,
  start_at?: number;
  end_at?: number;
}
const tagColors: {
  [key: string]:  TagProps['color']
} = {
  pending: 'default',
  processing: 'processing',
  completed: 'success',
  failed: 'error'
}
const initObj = {
  data: [],
  status: 'pending',
  result: null
}

const Result: FC<ResultProps> = ({ loading, handleSave }) => {
  const { t } = useTranslation();
  const { id } = useParams()
  const [runLoading, setRunLoading] = useState(false)
  const [testResult, setTestResult] = useState<TestResult>({} as TestResult)

  const [textPreprocessing, setTextPreprocessing] = useState<ModuleItem>(initObj as ModuleItem)
  const [knowledgeExtraction, setKnowledgeExtraction] = useState<ModuleItem>(initObj as ModuleItem)
  const [creatingNodesEdges, setCreatingNodesEdges] = useState<ModuleItem>(initObj as ModuleItem)
  const [deduplication, setDeduplication] = useState<ModuleItem>(initObj as ModuleItem)

  const handleRun = () => {
    if(!id) return
    setTextPreprocessing({...initObj} as ModuleItem)
    setKnowledgeExtraction({...initObj} as ModuleItem)
    setCreatingNodesEdges({...initObj} as ModuleItem)
    setDeduplication({...initObj} as ModuleItem)
    setTestResult({} as TestResult)
    const handleStreamMessage = (list: SSEMessage[]) => {
    
      list.forEach((data: AnyObject) => {
        switch(data.event) {
          case 'text_preprocessing': // 开始预处理文本
            setTextPreprocessing(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            break
          case 'text_preprocessing_result': // 预处理文本分块中
            setTextPreprocessing(prev => ({
              ...prev,
              data: [...prev.data, data.data?.data]
            }))
            break
          case 'text_preprocessing_complete': // 预处理文本完成
            setTextPreprocessing(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'knowledge_extraction': // 开始知识抽取
            setKnowledgeExtraction(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            break
          case 'knowledge_extraction_result': // 知识抽取中
            setKnowledgeExtraction(prev => ({
              ...prev,
              data: [...prev.data, data.data?.data]
            }))
            break
          case 'knowledge_extraction_complete': // 知识抽取完成
            setKnowledgeExtraction(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'creating_nodes_edges': // 开始创建节点和边
            setCreatingNodesEdges(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            break
          case 'creating_nodes_edges_result': // 创建节点和边中
            setCreatingNodesEdges(prev => ({
              ...prev,
              data: [...prev.data, data.data?.data]
            }))
            break
          case 'creating_nodes_edges_complete': // 创建节点和边完成
            setCreatingNodesEdges(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'deduplication': // 开始去重消歧
            setDeduplication(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            break
          case 'dedup_disambiguation_result': // 去重消歧中
            setDeduplication(prev => ({
              ...prev,
              data: [...prev.data, data.data.data]
            }))
            break
          case 'dedup_disambiguation_complete': // 去重消歧完成
            setDeduplication(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'generating_results': // 开始生成结果
            break
          case 'result': // 结果
            setTestResult(data.data?.extracted_result)
            break
        }
      })
    }
    setRunLoading(true)
    pilotRunMemoryExtractionConfig({
      config_id: id,
      dialogue_text: t('memoryExtractionEngine.exampleText'),
    }, handleStreamMessage)
    .finally(() => {
      setRunLoading(false)
    })
  }
  const completedNum = [textPreprocessing, knowledgeExtraction, creatingNodesEdges, deduplication].filter(item => item.status === 'completed').length
  const deduplicationData = groupDataByType(deduplication.data, 'result_type')

  const formatTag = (status: string) => {
    return (
      <Tag color={tagColors[status]}>
        {status === 'pending' && <ClockCircleOutlined className="rb:mr-1" />}
        {status === 'processing' && <LoadingOutlined spin className="rb:mr-1" />}
        {t(`memoryExtractionEngine.status.${status}`)}
      </Tag>
    )
  }
  const formatTime = (data: ModuleItem, color?: string) => {
    if (typeof data.end_at === 'number' && typeof data.start_at === 'number') {
      return <div className={`rb:mt-3 rb:text-[${color ?? '#155EEF'}]`}>{t('memoryExtractionEngine.time')}{data.end_at - data.start_at}ms</div>
    }
    return null
  }
  const lowercaseFirst = (str: string) => str.charAt(0).toLowerCase() + str.slice(1)
  return (
    <Card
      title={t('memoryExtractionEngine.exampleMemoryExtractionResults')}
      subTitle={t('memoryExtractionEngine.exampleMemoryExtractionResultsSubTitle')}
      className="rb:min-h-[calc(100vh-330px)]!"
      headerClassName="rb:pb-0! rb:pt-4!"
      bodyClassName="rb:min-h-[calc(100vh-388px)] rb:p-[16px_20px]!"
    >
      <div className="rb:min-h-[calc(100vh-480px)] rb:overflow-y-auto">
        {runLoading
          ? <>
            <RbAlert color="blue" icon={<ExclamationCircleFilled />} className="rb:mb-3.5">
              {t('memoryExtractionEngine.processing')}
            </RbAlert>
            {/* 整体进度 */}
            <div className="rb:mb-2">
              <div className="rb:flex rb:items-center rb:justify-between rb:text-[12px] rb:leading-4 rb:font-regular">
                {t('memoryExtractionEngine.overallProgress')}
                <span className="rb:text-[#155eef]">{`${completedNum}/4`}</span>
              </div>
              <Progress percent={completedNum * 100/4} showInfo={false} />
            </div>
          </>
          : !testResult || Object.keys(testResult).length === 0
          ? <RbAlert color="orange" icon={<ExclamationCircleFilled />} className="rb:mb-3.5">
            {t('memoryExtractionEngine.warning')}
          </RbAlert>
          : <RbAlert color="green" icon={<ExclamationCircleFilled />} className="rb:mb-3.5">
              {t('memoryExtractionEngine.success')}
            </RbAlert>
        }
        <Space size={16} direction="vertical" style={{ width: '100%' }}>
          {/* 文本预处理 */}
          <RbCard
            title={t(`memoryExtractionEngine.text_preprocessing`)}
            extra={formatTag(textPreprocessing.status)}
            headerType="borderL"
            headerClassName="rb:before:bg-[#155EEF]!"
          >
            {textPreprocessing.data.map((vo, index) => (
              <div key={index} className="rb:mb-3 rb:text-[12px] rb:text-[#5B6167] rb:leading-4 rb:font-regular">
                <Markdown content={'-' + t('memoryExtractionEngine.fragment') + vo.chunk_index + ': ' + (vo.content.startsWith('\n') ? vo.content : '\n' + vo.content)} />
              </div>
            ))}
            {formatTime(textPreprocessing)}
            {textPreprocessing.result &&
              <RbAlert color="blue" icon={<CheckCircleFilled />} className="rb:mt-3">
                {t('memoryExtractionEngine.text_preprocessing_desc', { count: textPreprocessing.result.total_chunks })}, 
                {t('memoryExtractionEngine.chunkerStrategy')}: {t(`memoryExtractionEngine.${lowercaseFirst(textPreprocessing.result.chunker_strategy)}`)}
              </RbAlert>
            }
          </RbCard>
          {/* 知识抽取 */}
          <RbCard
            title={t(`memoryExtractionEngine.knowledge_extraction`)}
            extra={formatTag(knowledgeExtraction.status)}
            headerType="borderL"
            headerClassName="rb:before:bg-[#155EEF]!"
          >
            {knowledgeExtraction.data.map(vo => 
              <div key={vo.statement_index} className="rb:mb-3 rb:text-[12px] rb:text-[#5B6167] rb:leading-4 rb:font-regular">{vo.statement}</div>
            )}
            {formatTime(knowledgeExtraction)}
            {knowledgeExtraction.result && <RbAlert color="blue" icon={<CheckCircleFilled />} className="rb:mt-3">
              {t('memoryExtractionEngine.knowledge_extraction_desc', {
                entities: knowledgeExtraction.result.entities_count,
                statements: knowledgeExtraction.result.statements_count,
                temporal_ranges_count: knowledgeExtraction.result.temporal_ranges_count,
                triplets: knowledgeExtraction.result.triplets_count
              })}
            </RbAlert>}
          </RbCard>
          {/* 创建实体关系 */}
          <RbCard
            title={t(`memoryExtractionEngine.creating_nodes_edges`)}
            extra={formatTag(creatingNodesEdges.status)}
            headerType="borderL"
            headerClassName="rb:before:bg-[#9C6FFF]!"
          >
            {creatingNodesEdges.data?.map((vo, index) => (
              <div key={index} className="rb:mb-3 rb:text-[12px] rb:text-[#5B6167] rb:leading-4 rb:font-regular">
                {vo?.result_type === 'entity_nodes_creation'
                  ? <>{vo.type_display_name}: {vo.entity_names.join(', ')}</>
                  : <>{vo?.relationship_text}</>
                }
              </div>
            ))}
            {formatTime(creatingNodesEdges, '#9C6FFF')}
            {creatingNodesEdges.result && <RbAlert color="blue" icon={<CheckCircleFilled />} className="rb:mt-3">
              {t('memoryExtractionEngine.creating_nodes_edges_desc', {num: creatingNodesEdges.result.entity_entity_edges_count})}
            </RbAlert>}
          </RbCard>
          {/* 去重消歧 */}
          <RbCard
            title={t(`memoryExtractionEngine.deduplication`)}
            extra={formatTag(deduplication.status)}
            headerType="borderL"
            headerClassName="rb:before:bg-[#9C6FFF]!"
          >
            {Object.keys(deduplicationData).length > 0 && Object.keys(deduplicationData).map(key => {
              return deduplicationData[key].map((vo, index) => (
                <div key={index} className="rb:mb-3 rb:text-[12px] rb:text-[#5B6167] rb:leading-4 rb:font-regular">
                  {vo.message}
                </div>
              ))
            })}
            {formatTime(deduplication, '#9C6FFF')}
            {deduplication.result && <RbAlert color="blue" icon={<CheckCircleFilled />} className="rb:mt-3">
              {t('memoryExtractionEngine.deduplication_desc', { count: deduplication.result.summary.total_merges })}<br />
            </RbAlert>}
          </RbCard>

          {testResult && Object.keys(testResult).length > 0 && resultObj && Object.keys(resultObj).length > 0 &&
            <RbCard>
              <div className="rb:grid rb:grid-cols-2 rb:gap-[40px_57px]">
                {Object.keys(resultObj).map((key, index) => {
                  const keys = (resultObj as Record<string, string>)[key].split('.')
                  return (
                  <div key={index}>
                    <div className="rb:text-[24px] rb:leading-[30px] rb:font-extrabold">{(testResult?.[keys[0] as keyof TestResult] as any)?.[keys[1]]}</div>
                    <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4 rb:font-regular">{t(`memoryExtractionEngine.${key}`)}</div>
                    <div className="rb:mt-1 rb:text-[12px] rb:text-[#369F21] rb:leading-3.5 rb:font-regular">
                      {}
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
                        :t(`memoryExtractionEngine.${key}Desc`)
                      }
                    </div>
                  </div>
                )})}
              </div>
            </RbCard>
          }
          
          {testResult?.dedup?.impact && testResult.dedup.impact?.length > 0 &&
            <RbCard
              title={t('memoryExtractionEngine.entityDeduplicationImpact')}
              headerType="borderL"
              headerClassName="rb:before:bg-[#155EEF]!"
            >
              <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-medium rb:leading-4">{t('memoryExtractionEngine.identifyDuplicates')}</div>
              {testResult.dedup.impact.map((item, index) => (
                <div key={index} className="rb:pl-2 rb:mt-2 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">
                  -{t('memoryExtractionEngine.identifyDuplicatesDesc', { ...item })}
                </div>
              ))}

              <RbAlert color="blue" icon={<CheckCircleFilled />} className="rb:mt-3">
                {t('memoryExtractionEngine.entityDeduplicationImpactDesc', { count: testResult.dedup.impact.length })}
              </RbAlert>
            </RbCard>
          }
          
          {testResult?.disambiguation && testResult.disambiguation?.effects?.length > 0 &&
            <RbCard
              title={t('memoryExtractionEngine.theEffectOfEntityDisambiguationLLMDriven')}
              headerType="borderL"
              headerClassName="rb:before:bg-[#155EEF]!"
            >
              {testResult.disambiguation.effects.map((item, index) => (
                <div key={index} className={clsx("rb:text-[12px] rb:text-[#5B6167] rb:leading-4", {
                  'rb:mt-4': index > 0,
                })}>
                  <div className="rb:font-medium rb:mb-2">Disagreement Case {index +1}:</div>
                  -{item.left.name}({item.left.type}) vs {item.right.name}({item.right.type}) → <span className="rb:text-[#369F21]">{item.result}</span>
                </div>
              ))}

              <RbAlert color="blue" icon={<CheckCircleFilled />} className="rb:mt-3">
                {t('memoryExtractionEngine.entityDeduplicationImpactDesc', { count: testResult.dedup.impact.length })}
              </RbAlert>
            </RbCard>
          }
          
          {testResult?.core_entities && testResult?.core_entities.length > 0 &&
            <RbCard
              title={t('memoryExtractionEngine.coreEntitiesAfterDedup')}
              headerType="borderL"
              headerClassName="rb:before:bg-[#369F21]!"
            >
              <div className="rb:grid rb:grid-cols-2 rb:gap-6">
                {testResult.core_entities.map((item, idx) => (
                  <div key={idx} className="rb:text-[12px]">
                    <div className="rb:text-[#369F21] rb:font-medium">{item.type}({item.count})</div>

                    <div>
                      {item.entities.map((entity, index) => (
                        <div key={index} className="rb:text-[#5B6167] rb:font-regular rb:leading-4">
                          -{entity}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </RbCard>
          }
          
          {testResult?.triplet_samples && testResult?.triplet_samples.length > 0 &&
            <RbCard
              title={t('memoryExtractionEngine.extractRelationalTriples')}
              headerType="borderL"
              headerClassName="rb:before:bg-[#9C6FFF]!"
            >
              <Space size={8} direction="vertical" className="rb:w-full">
                {testResult.triplet_samples.map((item, index) => (
                  <div key={index} className="rb:text-[12px]">
                    -({item.subject}, <span className="rb:text-[#9C6FFF] rb:font-medium">{item.predicate}</span>, {item.object})
                  </div>
                ))}
              </Space>
              <RbAlert color="purple" icon={<CheckCircleFilled />} className="rb:mt-3">
                {t('memoryExtractionEngine.extractRelationalTriplesDesc', { count: testResult.triplet_samples.length })}
              </RbAlert>
            </RbCard>
          }
        </Space>
      </div>

      <div className="rb:grid rb:grid-cols-2 rb:gap-4 rb:mt-5">
        <Button block loading={loading} onClick={handleSave}>{t('common.save')}</Button>
        <Button block type="primary" loading={runLoading} onClick={handleRun}>{t('memoryExtractionEngine.debug')}</Button>
      </div>
    </Card>
  )
}
export default Result