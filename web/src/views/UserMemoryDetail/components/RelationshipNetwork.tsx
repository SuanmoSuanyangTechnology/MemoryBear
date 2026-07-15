/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-17 17:40:02
 */
/**
 * Relationship Network Component
 * Displays memory relationship graph with node details
 * Interactive force-directed graph visualization
 */

import React, { type FC, useEffect, useState, useCallback, useRef, type Dispatch, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { Flex, type SegmentedProps, Button } from 'antd'
import clsx from 'clsx'

import type { GraphData, Node as GraphNode, PerceptualNodeProperties } from '../types'
import type { RawCommunityNode } from '@/components/D3Graph/types'
import {
  getMemorySearchEdges,
} from '@/api/memory'
import GraphNetworkChart from '@/components/Charts/GraphNetworkChart'
import { type Node, type Edge, type EdgeClickData, Colors } from '@/components/Charts/graphNetworkUtils'
import CommunityNetwork from './CommunityNetwork'
import PageTabs from '@/components/PageTabs'
import NodeDetailPanel from './NodeDetailPanel'
import ForgetMemoryConfirmModal, { type ForgetMemoryConfirmModalRef } from './ForgetMemoryConfirmModal'

interface RelationshipNetworkProps {
  regionId: string | null;
  selectedKey: string | null;
  setRegionId: Dispatch<SetStateAction<string | null>>;
  setSelectedKey: Dispatch<SetStateAction<string | null>>;
  setBrainMemories: Dispatch<SetStateAction<string[]>>;
  refresh: () => void
}
const RelationshipNetwork: FC<RelationshipNetworkProps> = ({ regionId, selectedKey, setRegionId, setBrainMemories, setSelectedKey, refresh }) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [nodes, setNodes] = useState<Node[]>([])
  const [links, setLinks] = useState<Edge[]>()
  const [categories, setCategories] = useState<{ name: string; value: number; }[]>([])
  const [selectedNode, setSelectedNode] = useState<GraphNode | RawCommunityNode | EdgeClickData | null>(null)
  // 双向边当前选中的半边方向
  const [activeEdgeDirection, setActiveEdgeDirection] = useState<'a_to_b' | 'b_to_a' | null>(null)
  // 边详情面板中当前激活的关系下标
  const [activeRelationIndex, setActiveRelationIndex] = useState(0)
  // const [fullScreen, setFullScreen] = useState<boolean>(false)
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<SegmentedProps['value']>('relationshipNetwork')

  console.log('categories', categories)
  const edgeAbortRef = useRef<AbortController | null>(null)
  const forgetModalRef = useRef<ForgetMemoryConfirmModalRef>(null)

  /** Open the confirmation dialog to manually forget the currently selected node. */
  const handleForget = () => {
    if (!id || !selectedNode) return
    const nodeId = selectedNode.id
    forgetModalRef.current?.handleOpen({
      endUserId: id,
      nodeId,
      nodeLabel: (selectedNode as GraphNode).label,
      name: (selectedNode as GraphNode).name || `${t(`userMemory.${(selectedNode as GraphNode).caption}`)}_${nodeId.slice(-5)}`,
      relations: (selectedNode as Node).properties?.associative_memory || 0,
      associated: (selectedNode as Node).properties?.associative_memory || 0,
    })
  }

  /** Fetch relationship network data */
  const getEdgeData = useCallback(() => {
    if (!id) return
    edgeAbortRef.current?.abort()
    edgeAbortRef.current = new AbortController()
    setSelectedNode(null)
    getMemorySearchEdges(id, { signal: edgeAbortRef.current.signal }).then((res) => {
      const { nodes, edges, statistics } = res as GraphData
      const curNodes: Node[] = []
      const curNodeTypes = Object.keys(statistics.node_types)

      // Calculate connection count for each node
      const connectionCount: Record<string, number> = {}
      edges.forEach((edge: Edge) => {
        connectionCount[`${edge.node_a}_${edge.node_b}`] = (connectionCount[`${edge.node_a}_${edge.node_b}`] || 0) + 1
        connectionCount[`${edge.node_b}_${edge.node_a}`] = (connectionCount[`${edge.node_b}_${edge.node_a}`] || 0) + 1
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
          symbolSize = 30
        } else if (connections <= 10) {
          symbolSize = 40
        } else if (connections <= 15) {
          symbolSize = 50
        } else if (connections <= 20) {
          symbolSize = 60
        } else {
          symbolSize = 70
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

      // Set categories
      const curCategories = Object.keys(statistics.node_types).map(type => ({ name: type, value: (statistics as GraphData['statistics']).node_types[type] || 0 }))

      setNodes(curNodes)
      setLinks(edges)
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
      nodeLabel: (selectedNode as GraphNode).label,
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
    handleReset()
  }

  const [fileSize, setFileSize] = useState<string>('')
  useEffect(() => {
    setActiveRelationIndex(0)
    setActiveEdgeDirection(null)
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
  const handleClickNode = useCallback((node: GraphNode) => {
    setSelectedCategory(null)
    setTimeout(() => {
      setSelectedNode(node)
    }, 0)
  }, [])
  const handleReset = () => {
    console.log('handleReset')
    setActiveRelationIndex(0)
    setActiveEdgeDirection(null)
    setSelectedNode(null)
    setSelectedCategory(null)
    setRegionId(null)
    setBrainMemories([])
    setSelectedKey(null)
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
        : <div className="rb:absolute rb:inset-0">
          {categories.length > 0 &&
            <Flex gap={24} align="center" justify="space-between" wrap={false}
              className={clsx('rb:absolute! rb:z-999 rb:top-4 rb:bg-[#FFFFFF] rb:rounded-xl rb:py-2! rb:px-3!', {
                'rb:w-full': !selectedNode && !selectedKey,
                'rb:w-[calc(100%-412px)]': selectedNode,
                'rb:left-103 rb:w-[calc(100%-412px)]': selectedKey,
                'rb:left-0': !selectedKey,
                'rb:left-103 rb:w-[calc(100%-824px)]': selectedKey && selectedNode,
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
                    {t(`userMemory.${item.name}`)}
                    <div className="rb:px-1 rb:rounded-full rb:bg-[#F6F6F6] rb:text-[10px] rb:h-3.5">{item.value}</div>
                  </Flex>
                ))}
              </Flex>
              <Button onClick={() => handleReset()}>{t('userMemory.resetView')}</Button>
            </Flex>
          }
          <GraphNetworkChart
            nodes={nodes}
            links={links || []}
            onNodeClick={handleClickNode as any}
            selectedNodeId={selectedNode?.id}
            selectedCategory={selectedCategory}
            activeEdgeDirection={activeEdgeDirection}
            activeRelationIndex={activeRelationIndex}
            regionId={regionId}
          />
        </div>
      }
      {selectedNode &&
        <NodeDetailPanel
          selectedNode={selectedNode}
          nodes={nodes}
          activeTab={activeTab}
          activeRelationIndex={activeRelationIndex}
          fileSize={fileSize}
          onActiveIndexChange={setActiveRelationIndex}
          onRelationChange={(_, direction) => setActiveEdgeDirection(direction)}
          onClose={() => setSelectedNode(null)}
          onDownload={handleDownload}
          onForget={handleForget}
          onViewAll={handleViewAll}
        />
      }
      <ForgetMemoryConfirmModal
        ref={forgetModalRef}
        onSuccess={() => {
          setSelectedNode(null)
          getEdgeData()
          refresh()
        }}
      />
    </div>
  )
}
/** Use React.memo to avoid unnecessary renders */
export default React.memo(RelationshipNetwork)
