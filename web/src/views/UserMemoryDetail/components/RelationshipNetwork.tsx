/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 12:14:43
 */
/**
 * Relationship Network Component
 * Displays memory relationship graph with node details
 * Interactive force-directed graph visualization
 */

import React, { type FC, useEffect, useState, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { Space, Flex, Divider, type SegmentedProps } from 'antd'
import dayjs from 'dayjs'
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import type { GraphData, StatementNodeProperties, ExtractedEntityNodeProperties } from '../types'
import type { RawCommunityNode } from '@/components/D3Graph/types'
import {
  getMemorySearchEdges,
} from '@/api/memory'
import Tag from '@/components/Tag'
import GraphNetworkChart, { type Node, type Edge } from '@/components/Charts/GraphNetworkChart'
import CommunityNetwork from './CommunityNetwork'
import PageTabs from '@/components/PageTabs'

const RelationshipNetwork: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [nodes, setNodes] = useState<Node[]>([])
  const [links, setLinks] = useState<Edge[]>([])
  const [categories, setCategories] = useState<{ name: string }[]>([])
  const [selectedNode, setSelectedNode] = useState<Node | RawCommunityNode | null>(null)
  // const [fullScreen, setFullScreen] = useState<boolean>(false)
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<SegmentedProps['value']>('relationshipNetwork')

  console.log('categories', categories)
  const edgeAbortRef = useRef<AbortController | null>(null)

  /** Fetch relationship network data */
  const getEdgeData = useCallback(() => {
    if (!id) return
    edgeAbortRef.current?.abort()
    edgeAbortRef.current = new AbortController()
    setSelectedNode(null)
    getMemorySearchEdges(id, { signal: edgeAbortRef.current.signal }).then((res) => {
      const { nodes, edges, statistics } = res as GraphData
      const curNodes: Node[] = []
      const curEdges: Edge[] = []
      const curNodeTypes = Object.keys(statistics.node_types).filter(vo => vo !== 'Dialogue')

      // Calculate connection count for each node
      const connectionCount: Record<string, number> = {}
      edges.forEach(edge => {
        connectionCount[edge.source] = (connectionCount[edge.source] || 0) + 1
        connectionCount[edge.target] = (connectionCount[edge.target] || 0) + 1
      })

      // Process node data
      nodes.filter(vo => vo.label !== 'Dialogue').forEach(node => {
        const connections = connectionCount[node.id] || 0
        const categoryIndex = curNodeTypes.indexOf(node.label)

        // Get display name based on node type
        let displayName = ''
        switch (node.label) {
          // case 'Statement':
          //   displayName = 'statement' in node.properties ? node.properties.statement?.slice(0, 5) || '' : ''
          //   break
          case 'ExtractedEntity':
            displayName = 'name' in node.properties ? node.properties.name || '' : ''
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
      edges.forEach(edge => {
        curEdges.push({
          ...edge,
          source: edge.source,
          target: edge.target,
          value: edge.weight || 1
        })
      })

      // Set categories
      const curCategories = curNodeTypes.map(type => ({ name: type }))

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
        : <GraphNetworkChart
          nodes={nodes}
          links={links}
          categories={categories.map(vo => ({
            name: t(`userMemory.${vo.name}`)
          })) || []}
          onNodeClick={(node) => setSelectedNode(node as Node)}
        />
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
          <div className={clsx("rb:max-h-[calc(100vh-269px)] rb:overflow-auto", {
            'rb:max-h-[calc(100vh-269px)]': activeTab !== 'communityNetwork',
            'rb:max-h-[calc(100vh-205px)]': activeTab == 'communityNetwork',
          })}>
            {(selectedNode as RawCommunityNode).properties.community_id
              ? <div>
                <div className="rb:font-medium rb:text-[#212332] rb:text-[16px] rb:leading-5.5 rb:pl-1">
                  {(selectedNode as RawCommunityNode).properties.name}
                </div>
                <div className="rb:mt-3 rb:font-medium rb:leading-5 rb:pl-1">{t('userMemory.summary')}</div>
                <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:px-3 rb:py-2.5 rb:mt-2">
                  {(selectedNode as RawCommunityNode).properties.summary}
                </div>
                <Flex align="center" justify="space-between" className="rb:mt-5!">
                  <span className="rb:text-[#5B6167] rb:font-regular rb:pl-1">{t('userMemory.member_count')}</span>
                  <span className="rb:font-medium">{(selectedNode as RawCommunityNode).properties.member_count}{t('userMemory.member_count_desc')}</span>
                </Flex>

                <Divider className='rb:my-2.5!' />
                <div className="rb:font-medium rb:leading-5 rb:pl-1">{t('userMemory.core_entities')}</div>
                <ul className="rb:list-disc rb:pl-4 rb:text-[#5B6167] rb:mt-2">
                  {(selectedNode as RawCommunityNode).properties.core_entities.map((entity, index) => <li key={index}>{entity}</li>)}
                </ul>
              </div>
              : <>
                {(selectedNode as Node).name &&
                  <div className="rb:font-medium rb:text-[16px] rb:text-[#212332] rb:leading-5.5 rb:mb-3">
                    {(selectedNode as Node).name}
                  </div>
                }
                <Flex vertical gap={24}>
                  <div>
                    <div className="rb:font-medium rb:leading-5">{t('userMemory.memoryContent')}</div>
                    <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                      {['Chunk', 'Dialogue', 'MemorySummary'].includes(selectedNode.label) && 'content' in selectedNode.properties
                        ? selectedNode.properties.content
                        : selectedNode.label === 'ExtractedEntity' && 'description' in selectedNode.properties
                          ? selectedNode.properties.description
                          : selectedNode.label === 'Statement' && 'statement' in selectedNode.properties
                            ? selectedNode.properties.statement
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

                  {selectedNode.label === 'Statement' && (<>
                    {(['emotion_keywords', 'emotion_type', 'emotion_subject', 'importance_score'] as const).map(key => {
                      const p = selectedNode.properties as StatementNodeProperties
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

                  {selectedNode.label === 'ExtractedEntity' && <>
                    {(['name', 'entity_type', 'aliases', 'connect_strngth', 'importance_score'] as const).map(key => {
                      const p = selectedNode.properties as ExtractedEntityNodeProperties
                      if (p[key]) {
                        return (
                          <div key={key}>
                            <div className="rb:font-medium rb:leading-5">{t(`userMemory.ExtractedEntity_${key}`)}</div>
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
        </RbCard>
      }
    </div>
  )
}
/** Use React.memo to avoid unnecessary renders */
export default React.memo(RelationshipNetwork)