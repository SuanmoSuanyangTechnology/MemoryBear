import React, { type FC, useEffect, useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Col } from 'antd'
import RbCard from '@/components/RbCard/Card'
import ReactEcharts from 'echarts-for-react'
import zoom from '@/assets/images/userMemory/zoom.svg'
import drag from '@/assets/images/userMemory/drag.svg'
import pointer from '@/assets/images/userMemory/pointer.svg'
import empty from '@/assets/images/userMemory/empty.svg'
import Empty from '@/components/Empty'

// Knowledge graph data type definitions
export interface KnowledgeNode {
  id: string
  entity_name: string
  entity_type: string
  description: string
  pagerank: number
  source_id: string[]
  // Properties required by ECharts
  name: string
  category: number
  symbolSize: number
  itemStyle: {
    color: string
  }
}

export interface KnowledgeEdge {
  src_id: string
  tgt_id: string
  description: string
  keywords: string[]
  weight: number
  source_id: string[]
  source: string
  target: string
  // Properties required by ECharts
  value: number
}

export interface KnowledgeGraphData {
  directed: boolean
  multigraph: boolean
  graph: {
    source_id: string[]
  }
  nodes: KnowledgeNode[]
  edges: KnowledgeEdge[]
}

export interface KnowledgeGraphResponse {
  graph: KnowledgeGraphData
  mind_map: Record<string, unknown>
}

interface KnowledgeGraphProps {
  data?: KnowledgeGraphResponse
  loading?: boolean
}

const operations = [
  { name: 'click', icon: pointer },
  { name: 'drag', icon: drag },
  { name: 'zoom', icon: zoom },
]

// Predefined color palette
const colorPalette = [
  '#155EEF', '#4DA8FF', '#9C6FFF', '#8BAEF7', '#369F21', 
  '#FF5D34', '#FF8A4C', '#FFB048', '#E74C3C', '#9B59B6',
  '#3498DB', '#1ABC9C', '#F39C12', '#D35400', '#C0392B',
  '#8E44AD', '#2980B9', '#16A085', '#F1C40F', '#E67E22'
]

// Dynamically generate entity type color mapping
const generateEntityTypeColors = (entityTypes: string[]): Record<string, string> => {
  const colorMap: Record<string, string> = {}
  entityTypes.forEach((type, index) => {
    colorMap[type] = colorPalette[index % colorPalette.length]
  })
  return colorMap
}

const KnowledgeGraph: FC<KnowledgeGraphProps> = ({ data, loading = false }) => {
  const { t } = useTranslation()
  const chartRef = useRef<ReactEcharts>(null)
  const resizeScheduledRef = useRef(false)
  const modalRef = useRef<HTMLDivElement>(null)
  const [nodes, setNodes] = useState<KnowledgeNode[]>([])
  const [links, setLinks] = useState<KnowledgeEdge[]>([])
  const [categories, setCategories] = useState<{ name: string }[]>([])
  const [selectedNode, setSelectedNode] = useState<KnowledgeNode | null>(null)
  const [entityTypeColors, setEntityTypeColors] = useState<Record<string, string>>({})
  
  // Modal drag-related state
  const [modalPosition, setModalPosition] = useState({ x: 20, y: 20 })
  const [isDragging, setIsDragging] = useState(false)
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 })

  // Drag handling functions
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    setIsDragging(true)
    setDragStart({
      x: e.clientX - modalPosition.x,
      y: e.clientY - modalPosition.y
    })
  }, [modalPosition])

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!isDragging) return
    
    const newX = e.clientX - dragStart.x
    const newY = e.clientY - dragStart.y
    
    // Limit drag range to ensure modal doesn't exceed container bounds
    const container = chartRef.current?.getEchartsInstance().getDom().parentElement
    if (container && modalRef.current) {
      const containerRect = container.getBoundingClientRect()
      const modalRect = modalRef.current.getBoundingClientRect()
      
      const maxX = containerRect.width - modalRect.width
      const maxY = containerRect.height - modalRect.height
      
      setModalPosition({
        x: Math.max(0, Math.min(newX, maxX)),
        y: Math.max(0, Math.min(newY, maxY))
      })
    }
  }, [isDragging, dragStart])

  const handleMouseUp = useCallback(() => {
    setIsDragging(false)
  }, [])

  // Add global mouse event listeners
  useEffect(() => {
    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove)
      document.addEventListener('mouseup', handleMouseUp)
      return () => {
        document.removeEventListener('mousemove', handleMouseMove)
        document.removeEventListener('mouseup', handleMouseUp)
      }
    }
  }, [isDragging, handleMouseMove, handleMouseUp])

  // Close modal
  const handleCloseModal = useCallback(() => {
    setSelectedNode(null)
  }, [])

  // Process knowledge graph data
  const processGraphData = useCallback(() => {
    if (!data?.graph) {
      setNodes([])
      setLinks([])
      setCategories([])
      setSelectedNode(null)
      return
    }

    const { nodes: rawNodes, edges: rawEdges } = data.graph
    const processedNodes: KnowledgeNode[] = []
    const processedEdges: KnowledgeEdge[] = []

    // Get all entity types
    const entityTypes = [...new Set(rawNodes.map(node => node.entity_type))]
    const categoryMap = entityTypes.reduce((acc, type, index) => {
      acc[type] = index
      return acc
    }, {} as Record<string, number>)

    // Dynamically generate entity type color mapping
    const dynamicEntityTypeColors = generateEntityTypeColors(entityTypes)
    setEntityTypeColors(dynamicEntityTypeColors)

    // Calculate connection count for each node
    const connectionCount: Record<string, number> = {}
    rawEdges.forEach(edge => {
      // Use src_id and tgt_id to calculate connection count
      connectionCount[edge.src_id] = (connectionCount[edge.src_id] || 0) + 1
      connectionCount[edge.tgt_id] = (connectionCount[edge.tgt_id] || 0) + 1
    })

    // Process node data
    rawNodes.forEach(node => {
      const connections = connectionCount[node.id] || 0
      const categoryIndex = categoryMap[node.entity_type] || 0
      
      // Calculate node size based on pagerank and connection count
      let symbolSize = Math.max(10, Math.min(50, node.pagerank * 200 + connections * 2))
      
      processedNodes.push({
        ...node,
        name: node.entity_name,
        category: categoryIndex,
        symbolSize,
        itemStyle: {
          color: dynamicEntityTypeColors[node.entity_type] || colorPalette[0]
        }
      })
    })

    // Process edge data
    rawEdges.forEach(edge => {
      // Note: Based on data structure, source and target fields may be opposite to src_id and tgt_id
      // We use src_id and tgt_id as the correct connection relationship
      processedEdges.push({
        ...edge, // Keep all original fields
        source: edge.src_id, // Use src_id as source node
        target: edge.tgt_id, // Use tgt_id as target node
        value: edge.weight || 1
      })
    })

    // Verify node IDs and edge connections
    const nodeIds = new Set(processedNodes.map(n => n.id))
    const validEdges = processedEdges.filter(edge => {
      const sourceExists = nodeIds.has(edge.source)
      const targetExists = nodeIds.has(edge.target)
      if (!sourceExists || !targetExists) {
        console.warn('Invalid edge:', edge, 'Source exists:', sourceExists, 'Target exists:', targetExists)
      }
      return sourceExists && targetExists
    })

    // Debug information
    console.log('Total nodes:', processedNodes.length)
    console.log('Total edges:', processedEdges.length)
    console.log('Valid edges:', validEdges.length)
    console.log('Node IDs:', Array.from(nodeIds).slice(0, 5))
    console.log('Edge sample:', validEdges.slice(0, 3))

    // Set categories
    const processedCategories = entityTypes.map(type => ({ name: type }))

    setNodes(processedNodes)
    setLinks(validEdges) // Only use valid edges
    setCategories(processedCategories)
  }, [data])

  useEffect(() => {
    processGraphData()
  }, [processGraphData])

  useEffect(() => {
    const handleResize = () => {
      if (chartRef.current && !resizeScheduledRef.current) {
        resizeScheduledRef.current = true
        requestAnimationFrame(() => {
          chartRef.current?.getEchartsInstance().resize()
          resizeScheduledRef.current = false
        })
      }
    }

    const resizeObserver = new ResizeObserver(handleResize)
    const chartElement = chartRef.current?.getEchartsInstance().getDom().parentElement
    if (chartElement) {
      resizeObserver.observe(chartElement)
    }
    
    return () => {
      resizeObserver.disconnect()
    }
  }, [nodes])

  return (
    <Col span={24}>
      <RbCard 
        title={t('knowledgeBase.knowledgeGraph')}
        headerType="borderless"
        headerClassName="rb:text-[18px]! rb:leading-[24px]"
      >
        <div className="rb:h-124 rb:relative">
          {loading ? (
            <div className="rb:h-full rb:flex rb:items-center rb:justify-center">
              <div className="rb:text-[#5B6167]">加载中...</div>
            </div>
          ) : nodes.length === 0 ? (
            <Empty className="rb:h-full" />
          ) : (
            <>
              <ReactEcharts
                ref={chartRef}
                option={{
                  colors: Object.values(entityTypeColors),
                  tooltip: {
                    show: true,
                    formatter: (params: any) => {
                      if (params.dataType === 'node') {
                        const node = params.data as KnowledgeNode
                        return `
                          <div class="rb:max-w-[560px]">
                            <div><strong>${node.entity_name}</strong></div>
                            <div>类型: ${node.entity_type}</div>
                            <div>重要度: ${(node.pagerank * 100).toFixed(2)}%</div>
                          </div>
                        `
                      } else if (params.dataType === 'edge') {
                        const edge = params.data as KnowledgeEdge
                        return `
                          <div class="rb:max-w-[560px]">
                            <div><strong>关系</strong></div>
                            <div>权重: ${edge.weight}</div>
                            <div class="rb:break-words rb:whitespace-pre-wrap">${edge.description}</div>
                          </div>
                        `
                      }
                      return ''
                    }
                  },
                  legend: {
                    data: categories.map(cat => cat.name),
                    orient: 'vertical',
                    left: 'right',
                    top: 'center'
                  },
                  series: [
                    {
                      type: 'graph',
                      layout: 'force',
                      data: nodes,
                      links: links,
                      categories: categories,
                      roam: true,
                      label: {
                        show: true,
                        position: 'right',
                        formatter: '{b}',
                        fontSize: 12
                      },
                      lineStyle: {
                        color: '#5B6167',
                        curveness: 0.3,
                        width: 2, // Fixed line width to avoid function issues
                        opacity: 0.8
                      },
                      force: {
                        repulsion: 300,
                        edgeLength: 150,
                        gravity: 0.1,
                        layoutAnimation: true,
                        preventOverlap: true
                      },
                      selectedMode: 'single',
                      draggable: true,
                      animationDurationUpdate: 0,
                      select: {
                        itemStyle: {
                          borderWidth: 2,
                          borderColor: '#ffffff',
                          shadowBlur: 10,
                        }
                      },
                      emphasis: {
                        focus: 'adjacency',
                        lineStyle: {
                          width: 3
                        }
                      }
                    }
                  ]
                }}
                style={{ height: '496px', width: '100%' }}
                notMerge={false}
                lazyUpdate={true}
                onEvents={{
                  click: (params: { dataType: string; data: KnowledgeNode }) => {
                    if (params.dataType === 'node') {
                      console.log('Knowledge node clicked:', params.data)
                      setSelectedNode(params.data)
                    }
                  }
                }}
              />
              
              {/* Entity details modal */}
              {selectedNode && (
                <div
                  ref={modalRef}
                  className="rb:absolute rb:bg-white rb:border rb:border-[#EBEBEB] rb:rounded-[12px] rb:shadow-lg rb:p-4 rb:w-80 rb:z-10"
                  style={{
                    left: modalPosition.x,
                    top: modalPosition.y,
                    cursor: isDragging ? 'grabbing' : 'grab'
                  }}
                >
                  {/* Modal header - draggable area */}
                  <div
                    className="rb:flex rb:items-center rb:justify-between rb:mb-3 rb:pb-2 rb:border-b rb:border-[#EBEBEB] rb:cursor-grab"
                    onMouseDown={handleMouseDown}
                    style={{ cursor: isDragging ? 'grabbing' : 'grab' }}
                  >
                    <div className="rb:text-[16px] rb:font-medium rb:text-[#1A1A1A]">
                      {t('knowledgeBase.entityDetails')}
                    </div>
                    <button
                      onClick={handleCloseModal}
                      className="rb:w-6 rb:h-6 rb:flex rb:items-center rb:justify-center rb:text-[#5B6167] hover:rb:text-[#1A1A1A] hover:rb:bg-[#F0F3F8] rb:rounded rb:transition-colors"
                    >
                      ×
                    </button>
                  </div>
                  
                  {/* Modal content */}
                  <div>
                    <div className="rb:font-medium rb:mb-4">
                      <div className="rb:text-[16px] rb:mb-2">{selectedNode.entity_name}</div>
                      <div className="rb:text-[12px] rb:text-[#5B6167] rb:mb-2">
                        <span className="rb:inline-block rb:px-2 rb:py-1 rb:bg-[#F0F3F8] rb:rounded rb:mr-2">
                          {selectedNode.entity_type}
                        </span>
                        <span>重要度: {(selectedNode.pagerank * 100).toFixed(2)}%</span>
                      </div>
                    </div>
                    
                    <div className="rb:font-medium rb:mb-4">
                      {t('knowledgeBase.entityDescription')}
                      <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-2 rb:leading-5">
                        {selectedNode.description}
                      </div>
                    </div>
                    
                    <div className="rb:font-medium rb:mb-2">
                      {t('knowledgeBase.sourceDocuments')}
                      <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-2">
                        {selectedNode.source_id.length} 个文档
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
        <div className="rb:bg-[#F0F3F8] rb:flex rb:items-center rb:gap-6 rb:rounded-[0px_0px_12px_12px] rb:p-[14px_40px] rb:m-[0_-20px_-16px_-16px]">
          {operations.map((item) => (
            <div key={item.name} className="rb:flex rb:items-center rb:text-[#5B6167] rb:leading-5">
              <img src={item.icon} className="rb:w-5 rb:h-5 rb:mr-1" />
              {t(`userMemory.${item.name}`)}
            </div>
          ))}
        </div>
      </RbCard>
    </Col>
  )
}

export default React.memo(KnowledgeGraph)