/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-11 15:06:05
 */
/**
 * Relationship Network Component
 * Displays memory relationship graph with node details
 * Interactive force-directed graph visualization
 */

import React, { type FC, useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { Space, Flex } from 'antd'
import dayjs from 'dayjs'

import RbCard from '@/components/RbCard/Card'
import type { GraphData, StatementNodeProperties, ExtractedEntityNodeProperties } from '../types'
import {
  getMemorySearchEdges,
} from '@/api/memory'
import Tag from '@/components/Tag'
import GraphNetworkChart, { type Node, type Edge } from '@/components/Charts/GraphNetworkChart'

const RelationshipNetwork:FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [nodes, setNodes] = useState<Node[]>([])
  const [links, setLinks] = useState<Edge[]>([])
  const [categories, setCategories] = useState<{ name: string }[]>([])
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const navigate = useNavigate()

  /** Fetch relationship network data */
  const getEdgeData = useCallback(() => {
    if (!id) return
    setSelectedNode(null)
    getMemorySearchEdges(id).then((res) => {
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
  }, [id])

  /** Navigate to full graph view */
  const handleViewAll = () => {
    if (!selectedNode) return
    const params = new URLSearchParams({
      nodeId: selectedNode.id,
      nodeLabel: selectedNode.label,
      nodeName: selectedNode.name || ''
    })
    navigate(`/user-memory/detail/${id}/GRAPH?${params.toString()}`)
  }
  return (
    <div className="rb:flex-1 rb:relative">
      <GraphNetworkChart
        nodes={nodes}
        links={links}
        categories={categories.map(vo => ({
          name: t(`userMemory.${vo.name}`)
        })) || []}
        onNodeClick={setSelectedNode}
      />

      {selectedNode &&
        <RbCard
          title={t('userMemory.memoryDetails')}
          className="rb:absolute! rb:top-4 rb:right-0 rb:w-100! rb:bg-white!"
          headerType="borderless"
          headerClassName="rb:min-h-[60px]!"
          bodyClassName='rb:px-5! rb:pb-[76px]! rb:pt-0! rb:h-auto!'
          extra={<div className="rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/close.svg')]" onClick={() => setSelectedNode(null)}></div>}
        >
          <div className="rb:max-h-[calc(100vh-269px)] rb:overflow-auto">
            {selectedNode.name &&
              <div className="rb:font-medium rb:text-[16px] rb:text-[#212332] rb:leading-5.5 rb:mb-3">
                {selectedNode.name}
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
                  {dayjs(selectedNode?.properties.created_at).format('YYYY-MM-DD HH:mm:ss')}
                </div>
              </div>

              {selectedNode?.properties.associative_memory > 0 && <div>
                <div className="rb:font-medium rb:leading-5">{t('userMemory.associative_memory')}</div>
                <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                  <span className="rb:text-[#155EEF] rb:font-medium">{selectedNode?.properties.associative_memory}</span> {t('userMemory.unix')}{t('userMemory.associative_memory')}
                </div>
              </div>}


              {selectedNode.label === 'Statement' && <>
                {(['emotion_keywords', 'emotion_type', 'emotion_subject', 'importance_score'] as const).map(key => {
                  const statementProps = selectedNode.properties as StatementNodeProperties;
                  if ((key === 'emotion_keywords' && statementProps[key]?.length > 0) || typeof statementProps[key] === 'string') {
                    return (
                      <div key={key}>
                        <div className="rb:font-medium rb:leading-5">{t(`userMemory.Statement_${key}`)}</div>
                        <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                          {key === 'emotion_keywords'
                            ? <Space>{statementProps.emotion_keywords.map((vo, index) => <Tag key={index}>{vo}</Tag>)}</Space>
                            : statementProps[key]
                          }
                        </div>
                      </div>
                    )
                  }
                  return null
                })}
              </>}


              {selectedNode.label === 'ExtractedEntity' && <>
                {(['name', 'entity_type', 'aliases', 'connect_strngth', 'importance_score'] as const).map(key => {
                  const entityProps = selectedNode.properties as ExtractedEntityNodeProperties;
                  if (entityProps[key]) {
                    return (
                      <div key={key}>
                        <div className="rb:font-medium rb:leading-5">{t(`userMemory.ExtractedEntity_${key}`)}</div>
                        <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                          {Array.isArray(entityProps[key]) && entityProps[key].length > 0
                            ? entityProps[key].map((vo, index) => <div key={index}>- {vo}</div>)
                            : entityProps[key]
                          }
                        </div>
                      </div>
                    )
                  }
                  return null
                })}
              </>}
            </Flex>
          </div>

          <Flex align="center" justify="center" className="rb:absolute rb:bottom-3 rb:left-6 rb:right-6 rb:border rb:border-[#171719] rb:rounded-xl rb:h-11 rb:font-medium rb:leading-5 rb:cursor-pointer" onClick={handleViewAll}>
            {t('userMemory.completeMemory')}
          </Flex>
        </RbCard>
      }
    </div>
  )
}
/** Use React.memo to avoid unnecessary renders */
export default React.memo(RelationshipNetwork)