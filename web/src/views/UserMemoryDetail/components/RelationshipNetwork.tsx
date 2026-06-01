/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-01 12:26:24
 */
/**
 * Relationship Network Component
 * Displays memory relationship graph with node details
 * Interactive force-directed graph visualization
 */

import React, { type FC, useEffect, useState, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { Space, Flex, Divider, type SegmentedProps, Image, Button } from 'antd'
import dayjs from 'dayjs'
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import type { GraphData, Node as GraphNode, Edge as GraphEdge, PerceptualNodeProperties, StatementNodeProperties, ExtractedEntityNodeProperties, AssistantPrunedNodeProperties } from '../types'
import type { RawCommunityNode } from '@/components/D3Graph/types'
import {
  getMemorySearchEdges,
} from '@/api/memory'
import Tag from '@/components/Tag'
import GraphNetworkChart, { type Node, type Edge, type EdgeClickData, Colors } from '@/components/Charts/GraphNetworkChart'
import CommunityNetwork from './CommunityNetwork'
import PageTabs from '@/components/PageTabs'
import AudioPlayer from './AudioPlayer'
import VideoPlayer from './VideoPlayer'

export const KEYS: Record<string, string[]> = {
  image: ['summary', 'keywords', 'topic', 'domain', 'scene'],
  video: ['summary', 'keywords', 'topic', 'domain', 'scene'],
  audio: ['summary', 'keywords', 'topic', 'domain', 'speaker_count'],
  last_text: ['summary', 'keywords', 'topic', 'domain', 'section_count'],
}

const getFileType = (fileType: string) => {
  return fileType.includes('image')
    ? 'image'
    : fileType.includes('video')
    ? 'video'
    : fileType.includes('audio')
    ? 'audio'
    : 'last_text'
}
interface RelationshipNetworkProps {
  regionId: string | null;
  selectedKey: string | null;
}
const RelationshipNetwork: FC<RelationshipNetworkProps> = ({ regionId, selectedKey }) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [nodes, setNodes] = useState<Node[]>([])
  const [links, setLinks] = useState<Edge[]>([])
  const [categories, setCategories] = useState<{ name: string; value: number; }[]>([])
  const [selectedNode, setSelectedNode] = useState<GraphNode | RawCommunityNode | EdgeClickData | null>(null)
  // const [fullScreen, setFullScreen] = useState<boolean>(false)
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<SegmentedProps['value']>('relationshipNetwork')

  console.log('categories', categories)
  const edgeAbortRef = useRef<AbortController | null>(null)

  /** Fetch relationship network data */
  const getEdgeData = useCallback(() => {
    console.log('getEdgeData')
    if (!id) return
    edgeAbortRef.current?.abort()
    edgeAbortRef.current = new AbortController()
    setSelectedNode(null)
    getMemorySearchEdges(id, { signal: edgeAbortRef.current.signal }).then((res) => {
      console.log('API Response:', res)
      const { nodes, edges, statistics } = res as GraphData
      console.log('GraphData - nodes:', nodes, 'edges:', edges, 'statistics:', statistics)
      const curNodes: Node[] = []
      const curEdges: Edge[] = []
      const curNodeTypes = Object.keys(statistics.node_types)

      // Calculate connection count for each node
      const connectionCount: Record<string, number> = {}
      edges.forEach((edge: GraphEdge) => {
        connectionCount[edge.source] = (connectionCount[edge.source] || 0) + 1
        connectionCount[edge.target] = (connectionCount[edge.target] || 0) + 1
      })

      // Process node data
      nodes.forEach(node => {
        const connections = connectionCount[node.id] || 0
        const categoryIndex = curNodeTypes.indexOf(node.label)

        // Get display name based on node type
        let displayName = ''
        switch (node.label) {
          // case 'Statement':
          //   displayName = 'statement' in node.properties ? node.properties.statement?.slice(0, 5) || '' : ''
          //   break
          case 'ExtractedEntity':
            displayName = 'name' in node.properties && typeof node.properties.name === 'string' ? node.properties.name || '' : ''
            break
          // default:
          //   displayName = 'content' in node.properties ? node.properties.content?.slice(0, 5) || '' : ''
          //   break
        }
        let symbolSize = 0
        if (connections <= 1) {
          symbolSize = 5
        } else if (connections <= 10) {
          symbolSize = 10
        } else if (connections <= 15) {
          symbolSize = 15
        } else if (connections <= 20) {
          symbolSize = 25
        } else {
          symbolSize = 35
        }

        curNodes.push({
          ...node,
          name: displayName,
          category: categoryIndex >= 0 ? categoryIndex : 0,
          symbolSize: symbolSize, // Adjust node size based on connection count
        })
      })

      // Create mapping from node ID to label
      const nodeIdToLabel: Record<string, string> = {}
      nodes.forEach(node => {
        nodeIdToLabel[node.id] = node.label
      })
      // Process edge data
      edges.forEach((edge: GraphEdge) => {
        curEdges.push({
          ...edge,
          source: edge.source,
          target: edge.target,
          value: edge.weight || 1,
          caption: edge.caption || '',
        })
      })

      // Set categories
      const curCategories = Object.keys(statistics.node_types).map(type => ({ name: type, value: (statistics as GraphData['statistics']).node_types[type] || 0 }))

      setNodes(curNodes)
      setLinks(curEdges)
      setCategories(curCategories)
    })
  }, [id])
  useEffect(() => {
    if (!id) return
    getEdgeData()
    return () => { edgeAbortRef.current?.abort() }
  }, [id])

  /** Navigate to full graph view */
  const handleViewAll = () => {
    if (!selectedNode) return
    const params = new URLSearchParams({
      nodeId: selectedNode.id,
      nodeLabel: selectedNode.label,
      nodeName: (selectedNode as Node).name || ''
    })
    navigate(`/user-memory/detail/${id}/GRAPH?${params.toString()}`)
  }
  const handleChangeTab = (tab: SegmentedProps['value']) => {
    if (tab === 'communityNetwork') {
      edgeAbortRef.current?.abort()
    } else {
      getEdgeData()
    }
    setActiveTab(tab)
    setSelectedNode(null)
  }

  const [fileSize, setFileSize] = useState<string>('')
  useEffect(() => {
    setFileSize('')
    if (selectedNode) {
      setSelectedCategory(null)
    }
    const properties: PerceptualNodeProperties = (selectedNode as GraphNode)?.properties as PerceptualNodeProperties || {}
    if (selectedNode && (selectedNode as Node).type !== 'edge' && 'file_path' in properties && properties.file_path) {
      fetch(properties.file_path, { method: 'HEAD' })
        .then(r => {
          const bytes = Number(r.headers.get('content-length'))
          if (!bytes) return
          setFileSize(bytes < 1024 * 1024
            ? `${(bytes / 1024).toFixed(1)} KB`
            : `${(bytes / 1024 / 1024).toFixed(1)} MB`)
        })
        .catch(() => {})
    }
  }, [selectedNode])
  const handleDownload = () => {
    if (!((selectedNode as GraphNode)?.properties as PerceptualNodeProperties)?.file_path) return
    window.open(((selectedNode as GraphNode)?.properties as PerceptualNodeProperties)?.file_path, '_blank')
  }

  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)
  const handleClickCategory = (category: string | null) => {
    setSelectedNode(null)
    setTimeout(() => {
      setSelectedCategory(prev => category === prev ? null : category)
    }, 0)
  }
  const handleClickNode = (node: GraphNode) => {
    setSelectedCategory(null)
    setTimeout(() => {
      setSelectedNode(node)
    }, 0)
  }
  console.log('selectedCategory', selectedCategory, selectedNode)
  return (
    <div className="rb:flex-1 rb:relative">
      <div className="rb:absolute rb:z-111 rb:bottom-10 rb:left-[calc(50%-96px)] rb:transition-transform-[translateX(-50%]">
        <PageTabs
          value={activeTab}
          options={['relationshipNetwork', 'communityNetwork'].map(value => ({
            value,
            label: t(`userMemory.${value}`)
          }))}
          onChange={handleChangeTab}
          className=""
        />
      </div>
      {activeTab === 'communityNetwork'
        ? <CommunityNetwork onSelectCommunity={community => setSelectedNode(community)} />
        : <div className="rb:h-full rb:w-full">
          {categories.length > 0 &&
            <Flex gap={24} align="center" justify="space-between" wrap={false}
              className={clsx('rb:absolute! rb:top-4 rb:bg-[#FFFFFF] rb:rounded-xl rb:py-2! rb:px-3!', {
                'rb:w-full': !selectedNode && !selectedKey,
                'rb:w-[calc(100%-412px)]': selectedNode,
                'rb:left-103 rb:w-[calc(100%-412px)]': selectedKey,
                'rb:left-0': !selectedKey
              })}
            >
              <Flex wrap gap={8}>
                {categories.map((item, index) => (
                  <Flex
                    key={item.name}
                    gap={4}
                    align="center"
                    className={clsx("rb:cursor-pointer rb:px-2! rb:py-1! rb:rounded-full rb:text-[12px] rb:leading-4 rb:text-[#000000]", {
                      'rb:border rb:border-[#171719]': selectedCategory === item.name,
                      'rb-border': selectedCategory !== item.name
                    })}
                    onClick={() => handleClickCategory(item.name)}
                  >
                    <div className={clsx(`rb:size-1.25 rb:rounded-full rb:mr-2`)}
                      style={{ backgroundColor: Colors[index] }}
                    ></div>
                    {item.name}
                    <div className="rb:px-1 rb:rounded-full rb:bg-[#F6F6F6] rb:text-[10px] rb:h-3.5">{item.value}</div>
                  </Flex>
                ))}
              </Flex>
              <Button onClick={() => setSelectedCategory(null)}>{t('userMemory.resetView')}</Button>
            </Flex>
          }
          <GraphNetworkChart
            nodes={nodes}
            links={links}
            categories={categories.map(vo => ({
              name: t(`userMemory.${vo.name}`)
            })) || []}
            onNodeClick={(node) => handleClickNode(node as GraphNode)}
            selectedNodeId={selectedNode?.id}
            selectedCategory={selectedCategory}
            regionId={regionId}
          />
        </div>
      }
      {selectedNode &&
        <RbCard
          title={t('userMemory.memoryDetails')}
          className="rb:absolute! rb:top-4 rb:right-0 rb:w-100! rb:bg-white!"
          headerType="borderless"
          headerClassName="rb:min-h-[60px]!"
          bodyClassName={clsx('rb:px-5! rb:pt-0! rb:h-auto!', {
            'rb:pb-[76px]!': activeTab !== 'communityNetwork',
            'rb:pb-3!': activeTab === 'communityNetwork',
          })}
          extra={<div className="rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/close.svg')]" onClick={() => setSelectedNode(null)}></div>}
        >
          {selectedNode && 'type' in selectedNode && (selectedNode as EdgeClickData).type === 'edge'
            ? (() => {
              const sourceNode = nodes.find(n => n.id === (selectedNode as EdgeClickData).source)
              const targetNode = nodes.find(n => n.id === (selectedNode as EdgeClickData).target)
              return (
                <Flex vertical gap={12}>
                {/* <div> */}
                  {selectedNode.label && <Tag>{selectedNode.label}</Tag>}
                {/* </div> */}
                <div className="rb:font-medium rb:text-[#212332] rb:text-[16px] rb:leading-5.5">
                  {(selectedNode as EdgeClickData).label}
                </div>
                <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.relationshipType')}</div>
                <div className="rb:font-medium">
                  {(selectedNode as Edge).caption}
                </div>

                <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.sourceNode')}</div>
                <div className="rb:font-medium">
                  {sourceNode?.name || (selectedNode as EdgeClickData).source}
                  {sourceNode?.caption && ` (${sourceNode!.caption})`}
                </div>

                <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.targetNode')}</div>
                <div className="rb:font-medium">
                  {targetNode?.name || (selectedNode as EdgeClickData).target}
                  {targetNode?.caption && ` (${targetNode!.caption})`}
                </div>
              </Flex>
              )
            })()
            : <>
              <div className={clsx("rb:max-h-[calc(100vh-269px)] rb:overflow-auto", {
                'rb:max-h-[calc(100vh-269px)]': activeTab !== 'communityNetwork',
                'rb:max-h-[calc(100vh-205px)]': activeTab == 'communityNetwork',
              })}>
                {(selectedNode as RawCommunityNode).properties.community_id
                  ? <div>
                      <div className="rb:font-medium rb:text-[#212332] rb:text-[16px] rb:leading-5.5 rb:pl-1">
                        {(selectedNode as RawCommunityNode).properties.name || selectedNode.id}
                      </div>
                      {(selectedNode as RawCommunityNode).properties.summary && <>
                        <div className="rb:mt-3 rb:font-medium rb:leading-5 rb:pl-1">{t('userMemory.summary')}</div>
                        <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:px-3 rb:py-2.5 rb:mt-2">
                          {(selectedNode as RawCommunityNode).properties.summary}
                        </div>
                      </>}
                      <Flex align="center" justify="space-between" className="rb:mt-5!">
                        <span className="rb:text-[#5B6167] rb:font-regular rb:pl-1">{t('userMemory.member_count')}</span>
                        <span className="rb:font-medium">{(selectedNode as RawCommunityNode).properties.member_count}{t('userMemory.member_count_desc')}</span>
                      </Flex>

                      {(selectedNode as RawCommunityNode).properties.core_entities && <>
                        <Divider className='rb:my-2.5!' />
                        <div className="rb:font-medium rb:leading-5 rb:pl-1">{t('userMemory.core_entities')}</div>
                        <ul className="rb:list-disc rb:pl-4 rb:text-[#5B6167] rb:mt-2">
                          {(selectedNode as RawCommunityNode).properties.core_entities?.map((entity, index) => <li key={index}>{entity}</li>)}
                        </ul>
                      </>}
                    </div>
                  : <>
                    {((selectedNode as Node).name || selectedNode.label === 'Conversation') &&
                      <div className="rb:font-medium rb:text-[16px] rb:text-[#212332] rb:leading-5.5 rb:mb-3">
                        {(selectedNode as Node).name || selectedNode.label}
                      </div>
                    }
                    <Flex vertical gap={24}>
                      <div>
                        <div className="rb:font-medium rb:leading-5">{t('userMemory.memoryContent')}</div>
                        <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                          {['Chunk', 'Dialogue', 'MemorySummary', 'AssistantOriginal'].includes(selectedNode.label) && 'content' in ((selectedNode as GraphNode).properties as { content: string })
                            ? ((selectedNode as GraphNode).properties as { content: string }).content
                            : selectedNode.label === 'ExtractedEntity' && 'description' in ((selectedNode as GraphNode).properties as { description: string })
                              ? ((selectedNode as GraphNode).properties as { description: string }).description
                              : selectedNode.label === 'Statement' && 'statement' in ((selectedNode as GraphNode).properties as { statement: string })
                                ? ((selectedNode as GraphNode).properties as { statement: string }).statement
                                : selectedNode.label === 'Perceptual' && 'summary' in ((selectedNode as GraphNode).properties as { summary: string })
                                  ? ((selectedNode as GraphNode).properties as { summary: string }).summary
                                  : ['AssistantOriginal', 'AssistantPruned'].includes(selectedNode.label ) && 'text' in ((selectedNode as GraphNode).properties as { text: string })
                                    ? ((selectedNode as GraphNode).properties as { text: string }).text
                                    : ''
                          }
                        </div>
                      </div>
                      <div>
                        <div className="rb:font-medium rb:leading-5">{t('userMemory.created_at')}</div>
                        <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                          {dayjs((selectedNode as Node).properties.created_at).format('YYYY-MM-DD HH:mm:ss')}
                        </div>
                      </div>

                      {(selectedNode as Node).properties.associative_memory > 0 && <div>
                        <div className="rb:font-medium rb:leading-5">{t('userMemory.associative_memory')}</div>
                        <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-1 rb:pb-4 rb:border-b rb:border-[#DFE4ED]">
                          <span className="rb:text-[#155EEF] rb:font-medium">{(selectedNode as Node).properties.associative_memory}</span> {t('userMemory.unix')}{t('userMemory.associative_memory')}
                        </div>
                      </div>}


                      {selectedNode.label === 'ExtractedEntity' && <>
                        {(['entity_type', 'aliases'] as const).map(key => {
                          const p = (selectedNode as Node).properties as ExtractedEntityNodeProperties
                          if (p[key]) {
                            return (
                              <div key={key}>
                                <div className="rb:font-medium rb:leading-5">{t(`userMemory.ExtractedEntity_${key}`)}</div>
                                <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                                  {Array.isArray(p[key]) && p[key].length > 0
                                    ? p[key].map((v, i) => <div key={i}>- {v}</div>)
                                    : p[key]
                                  }
                                </div>
                              </div>
                            )
                          }
                          return null
                        })}
                      </>}
                      {selectedNode.label === 'Perceptual' && <>
                        <Flex vertical gap={16} className="rb:w-full!">
                          {((selectedNode as GraphNode).properties as { file_path: string }).file_path
                            ? <>
                              {((selectedNode as GraphNode).properties as { file_type: string }).file_type.includes('image')
                                ? <Image src={((selectedNode as GraphNode).properties as { file_path: string }).file_path} alt={((selectedNode as GraphNode).properties as { file_name: string }).file_name} width="100%" className="rb:rounded-xl rb:h-45!" />
                                : ((selectedNode as GraphNode).properties as { file_type: string }).file_type.includes('video')
                                ? <VideoPlayer src={((selectedNode as GraphNode).properties as { file_path: string }).file_path} />
                                : ((selectedNode as GraphNode).properties as { file_type: string }).file_type.includes('audio')
                                ? <AudioPlayer
                                  src={((selectedNode as GraphNode).properties as { file_path: string }).file_path}
                                  fileName={((selectedNode as GraphNode).properties as { file_name: string }).file_name}
                                  fileSize={fileSize}
                                />
                                : <Flex gap={11} align="center" justify="space-between" className="rb:bg-[#F6F6F6] rb:min-h-15.5! rb:rounded-xl rb:p-3!">
                                  <Flex gap={12} align="center">
                                    <div className="rb:w-7.5 rb:h-9 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/file.svg')]"></div>
                                    <div>
                                      <div className="rb:leading-5 rb:font-medium rb:mb-1 rb:wrap-break-word rb:line-clamp-1">
                                        {((selectedNode as GraphNode).properties as { file_name: string }).file_name}
                                      </div>
                                      <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">
                                        {fileSize || '-'}
                                      </div>
                                    </div>
                                  </Flex>
                                  <div
                                    className="rb:size-6 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/userMemory/download.svg')] rb:hover:bg-[url('@/assets/images/userMemory/download_hover.svg')]"
                                    onClick={handleDownload}
                                  ></div>
                                </Flex>
                              }
                            </>
                            : null
                          }
                          {KEYS[getFileType(((selectedNode as GraphNode).properties as PerceptualNodeProperties).file_type)]?.map(key => {
                            const value = ((selectedNode as GraphNode).properties as any)[key]
                            return (
                              <div key={key} className="rb:leading-5">
                                <div className="rb:mb-1">{t(`perceptualDetail.${key}`)}</div>

                                {typeof value === 'string'
                                  ? <div className="rb:text-[#5B6167]">{value}</div>
                                  : Array.isArray(value)
                                  ? <Flex wrap gap={11}>
                                      {value.map((vo, index) => <div key={index} className="rb:bg-[#F6F6F6] rb:rounded-[13px] rb:py-1 rb:px-2 rb:text-[12px] rb:font-medium rb:leading-4.5">{vo}</div>)}
                                    </Flex>
                                  : '-'
                                }
                              </div>
                            )
                          })}
                        </Flex>
                      </>}
                      {selectedNode.label === 'Statement' && (<>
                        {(['emotion_keywords'] as const).map(key => {
                          const p = (selectedNode as GraphNode).properties as StatementNodeProperties
                          if ((key === 'emotion_keywords' && p[key]?.length > 0) || typeof p[key] === 'string') {
                            return (
                              <div key={key}>
                                <div className="rb:font-medium rb:leading-5">{t(`userMemory.Statement_${key}`)}</div>
                                <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                                  {key === 'emotion_keywords'
                                    ? <Space>{p.emotion_keywords.map((v, i) => <Tag key={i}>{v}</Tag>)}</Space>
                                    : p[key]}
                                </div>
                              </div>
                            )
                          }
                          return null
                        })}
                      </>)}

                      {selectedNode.label === 'AssistantPruned' && <>
                        {(['memory_type'] as const).map(key => {
                          const p = (selectedNode as Node).properties as AssistantPrunedNodeProperties
                          if (p[key]) {
                            return (
                              <div key={key}>
                                <div className="rb:font-medium rb:leading-5">{t(`userMemory.AssistantPruned_${key}`)}</div>
                                <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                                  {Array.isArray(p[key]) && p[key].length > 0
                                    ? p[key].map((v, i) => <div key={i}>- {v}</div>)
                                    : p[key]}
                                </div>
                              </div>
                            )
                          }
                          return null
                        })}
                      </>}
                    </Flex>
                  </>}
              </div>

              {activeTab !== 'communityNetwork' && <Flex align="center" justify="center" className="rb:absolute rb:bottom-3 rb:left-6 rb:right-6 rb:border rb:border-[#171719] rb:rounded-xl rb:h-11 rb:font-medium rb:leading-5 rb:cursor-pointer" onClick={handleViewAll}>
                {t('userMemory.completeMemory')}
              </Flex>}
            </>
          }
        </RbCard>
      }
    </div>
  )
}
/** Use React.memo to avoid unnecessary renders */
export default React.memo(RelationshipNetwork)