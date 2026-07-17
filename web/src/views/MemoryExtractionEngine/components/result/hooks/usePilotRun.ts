/*
 * Pilot run logic (multi-stage streaming: text preprocessing / knowledge extraction / edge building / dedup & disambiguation / result)
 */
import { useState, type MutableRefObject } from 'react'
import type { AnyObject } from 'antd/es/_util/type'
import { pilotRunMemoryExtractionConfig } from '@/api/memory'
import { type SSEMessage } from '@/utils/stream'
import { useI18n } from '@/store/locale'
import type { TestResult, OntologyCoverage } from '../../../types'
import { initObj, initialExpanded } from '../constants'
import { getDebugChatMock } from '../debugChatMock'
import type { ModuleItem } from '../types'
import type { ChatItem } from '@/components/Chat/types'

/**
 * Pilot run hook
 * @param id config id
 * @param abortRef streaming abort reference
 * @param chatList chat list
 */
export const usePilotRun = (
  id: string | undefined,
  abortRef: MutableRefObject<(() => void) | null>,
  chatList: ChatItem[],
) => {
  const [runLoading, setRunLoading] = useState(false)
  const { language } = useI18n()
  const [activeTab, setActiveTab] = useState('processData')
  const [testResult, setTestResult] = useState<TestResult>({} as TestResult)
  const [coreEntitiesTab, setCoreEntitiesTab] = useState<string | null>(null)
  const [textPreprocessing, setTextPreprocessing] = useState<ModuleItem>(initObj as ModuleItem)
  const [chunking, setChunking] = useState<ModuleItem>(initObj as ModuleItem)
  const [knowledgeExtraction, setKnowledgeExtraction] = useState<ModuleItem>(initObj as ModuleItem)
  const [creatingNodesEdges, setCreatingNodesEdges] = useState<ModuleItem>(initObj as ModuleItem)
  const [deduplication, setDeduplication] = useState<ModuleItem>(initObj as ModuleItem)
  const [perceptual, setPerceptual] = useState<ModuleItem>(initObj as ModuleItem)
  const [ontologyCoverage, setOntologyCoverage] = useState<OntologyCoverage>({} as OntologyCoverage)
  const [expandedCards, setExpandedCards] = useState<Record<string, boolean>>(initialExpanded)

  const toggleCard = (key: string) => {
    setExpandedCards(prev => ({ ...prev, [key]: !prev[key] }))
  }

  /** Run pilot test */
  const handleRun = () => {
    if (!id) return
    setActiveTab('processData')
    setCoreEntitiesTab(null)
    setTextPreprocessing({ ...initObj } as ModuleItem)
    setChunking({ ...initObj } as ModuleItem)
    setKnowledgeExtraction({ ...initObj } as ModuleItem)
    setCreatingNodesEdges({ ...initObj } as ModuleItem)
    setDeduplication({ ...initObj } as ModuleItem)
    setPerceptual({ ...initObj } as ModuleItem)
    setTestResult({} as TestResult)
    setExpandedCards(initialExpanded)
    const handleStreamMessage = (list: SSEMessage[]) => {
      list.forEach((data: AnyObject) => {
        switch (data.event) {
          case 'perceptual_extract': // start extracting perceptual memory
            setPerceptual(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            toggleCard('perceptual')
            break
          case 'perceptual_result': // perceptual memory result
            setPerceptual(prev => ({
              ...prev,
              data: data.data?.data?.perceptual_nodes || []
            }))
            setExpandedCards(prev => ({ ...prev, perceptual: true }))
            break
          case 'perceptual_complete': // perceptual memory extraction complete
            setPerceptual(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'pruning_extract': // start semantic pruning
            setTextPreprocessing(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            toggleCard('text_preprocessing')
            break
          case 'pruning_result': // semantic pruning result
            setTextPreprocessing(prev => ({
              ...prev,
              data: [...prev.data, ...(data.data?.data?.user_message_changes || [])],
            }))
            break
          case 'pruning_complete': // semantic pruning complete
            setTextPreprocessing(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'chunking_extract': // start chunking
            setChunking(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            toggleCard('chunking')
            break
          case 'chunking_result': // chunking in progress
            setChunking(prev => ({
              ...prev,
              data: [...prev.data, data.data?.data]
            }))
            break
          case 'chunking_complete': // chunking complete
            setChunking(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'extract_statement': // start extracting statements
            setKnowledgeExtraction(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            toggleCard('knowledge_extraction')
            break
          case 'extract_statement_result': // statement extraction in progress
            setKnowledgeExtraction(prev => ({
              ...prev,
              data: [...prev.data, data.data?.data]
            }))
            break
          case 'extract_statement_complete': // statement extraction complete
            setKnowledgeExtraction(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'extract_triplet': // start extracting triplets
            setCreatingNodesEdges(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            toggleCard('creating_nodes_edges')
            break
          case 'extract_triplet_result': // triplet extraction in progress (each carries entities/relationships)
            setCreatingNodesEdges(prev => ({
              ...prev,
              data: [...prev.data, data.data?.data]
            }))
            break
          case 'extract_triplet_complete': // triplet extraction complete
            setCreatingNodesEdges(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'deduplication': // start dedup & merge
            setDeduplication(prev => ({
              ...prev,
              status: 'processing',
              start_at: data.data.time
            }))
            toggleCard('deduplication')
            break
          case 'dedup_result': // dedup & merge in progress
            setDeduplication(prev => ({
              ...prev,
              data: [...prev.data, data.data.data]
            }))
            break
          case 'dedup_complete': // dedup complete
            setDeduplication(prev => ({
              ...prev,
              result: data.data?.data,
              status: 'completed',
              end_at: data.data.time
            }))
            break
          case 'generating_results': // generating results
            break
          case 'result': // result
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
    abortRef.current?.()
    abortRef.current = null
    const list = chatList?.length > 0 ? chatList : getDebugChatMock(language)
    pilotRunMemoryExtractionConfig({
      config_id: id,
      messages: list.map(item => ({
        role: item.role,
        content: item.content,
        files: item.meta_data?.files?.map(file => {
          if (file.transfer_method === 'remote_url') {
            return file
          }
          return {
            type: file.type,
            transfer_method: "local_file",
            upload_file_id: file.response?.data?.file_id
          }
        }) || undefined,
      })),
    }, handleStreamMessage, (abort) => { abortRef.current = abort })
      .finally(() => {
        setRunLoading(false)
      })
  }

  return {
    runLoading,
    activeTab,
    setActiveTab,
    testResult,
    coreEntitiesTab,
    setCoreEntitiesTab,
    textPreprocessing,
    chunking,
    knowledgeExtraction,
    creatingNodesEdges,
    deduplication,
    perceptual,
    ontologyCoverage,
    expandedCards,
    toggleCard,
    handleRun,
  }
}
