import React, { type FC, useEffect, useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Col } from 'antd'
import RbCard from '@/components/RbCard/Card'
import ReactEcharts from 'echarts-for-react'
import zoom from '@/assets/images/userMemory/zoom.svg'
import drag from '@/assets/images/userMemory/drag.svg'
import pointer from '@/assets/images/userMemory/pointer.svg'
import empty from '@/assets/images/userMemory/empty.svg'
import type { Node, Edge, GraphData } from '../types'
import {
  getMemorySearchEdges,
} from '@/api/memory'
import Empty from '@/components/Empty'
import dayjs from 'dayjs'

const operations = [
  { name: 'click', icon: pointer },
  { name: 'drag', icon: drag },
  { name: 'zoom', icon: zoom },
]
const RelationshipNetwork:FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const chartRef = useRef<ReactEcharts>(null)
  const resizeScheduledRef = useRef(false)
  const [nodes, setNodes] = useState<Node[]>([])
  const [links, setLinks] = useState<Edge[]>([])
  const [categories, setCategories] = useState<{ name: string }[]>([])
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)


  // 关系网络
  const getEdgeData = useCallback(() => {
    if (!id) return
    setSelectedNode(null)
    getMemorySearchEdges(id).then((res) => {
      const { nodes, edges, statistics } = res as GraphData
      const curNodes: Node[] = []
      const curEdges: Edge[] = []
      const curNodeTypes = Object.keys(statistics.node_types)
      
      // 计算每个节点的连接数
      const connectionCount: Record<string, number> = {}
      edges.forEach(edge => {
        connectionCount[edge.source] = (connectionCount[edge.source] || 0) + 1
        connectionCount[edge.target] = (connectionCount[edge.target] || 0) + 1
      })
      
      // 处理节点数据
      nodes.forEach(node => {
        const connections = connectionCount[node.id] || 0
        const categoryIndex = curNodeTypes.indexOf(node.label)
        
        // 根据节点类型获取显示名称
        let displayName = ''
        switch (node.label) {
          case 'Statement':
            displayName = 'statement' in node.properties ? node.properties.statement?.slice(0, 5) || '' : ''
            break
          case 'ExtractedEntity':
            displayName = 'name' in node.properties ? node.properties.name || '' : ''
            break
          default:
            displayName = 'content' in node.properties ? node.properties.content?.slice(0, 5) || '' : ''
            break
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
          symbolSize: symbolSize, // 根据连接数调整节点大小
          itemStyle: {
            color: ['#155EEF', '#4DA8FF', '#9C6FFF', '#8BAEF7', '#369F21', '#FF5D34', '#FF8A4C', '#FFB048'][categoryIndex % 8]
          }
        })
      })
      
      // 处理边数据
      edges.forEach(edge => {
        curEdges.push({
          ...edge,
          source: edge.source,
          target: edge.target,
          value: edge.weight || 1
        })
      })
      
      // 设置分类
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
  
  useEffect(() => {
    const handleResize = () => {
      if (chartRef.current && !resizeScheduledRef.current) {
        resizeScheduledRef.current = true
        requestAnimationFrame(() => {
          chartRef.current?.getEchartsInstance().resize();
          resizeScheduledRef.current = false
        });
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
    <>
      {/* 关系网络 */}
      <Col span={24}>
        <RbCard 
          title={t('userMemory.relationshipNetwork')}
          headerType="borderless"
          headerClassName="rb:text-[18px]! rb:leading-[24px]"
        >
          <div className="rb:h-124">
            {nodes.length === 0 ? (
              <Empty className="rb:h-full" />
            ) : (
              <ReactEcharts
                option={{
                  colors: ['#155EEF', '4DA8FF', '#9C6FFF', '#8BAEF7', '#369F21', '#FF5D34', '#FF8A4C', '#FFB048'],
                  tooltip: {
                    show: false
                  },
                  series: [
                    {
                      type: 'graph',
                      layout: 'force',
                      data: nodes || [],
                      links: links || [],
                      categories: categories || [],
                      roam: true,
                      label: {
                        show: true,
                        position: 'right',
                        formatter: '{b}',
                      },
                      lineStyle: {
                        color: '#5B6167',
                        curveness: 0.3
                      },
                      force: {
                        repulsion: 100,
                        // 启用类别聚合
                        edgeLength: 80,
                        gravity: 0.3,
                        // 同类别的节点相互吸引
                        layoutAnimation: true,
                        // 防止点击时重新计算布局
                        preventOverlap: true,
                        // 点击节点后保持布局稳定
                        edgeSymbol: ['none', 'arrow'],
                        edgeSymbolSize: [4, 10],
                        // 初始布局完成后关闭力导向
                        initLayout: 'force'
                      },
                      selectedMode: 'single',
                      draggable: true,
                      // 防止数据更新时重新计算布局
                      animationDurationUpdate: 0,
                      select: {
                        itemStyle: {
                          borderWidth: 2,
                          borderColor: '#ffffff',
                          shadowBlur: 10,
                        }
                      }
                    }
                  ]
                }}
                style={{ height: '496px', width: '100%' }}
                notMerge={false}
                lazyUpdate={true}
                onEvents={{
                  // 节点点击事件处理
                  click: (params: { dataType: string; data: Node }) => {
                    if (params.dataType === 'node') {
                      // 处理节点点击事件
                      console.log('Node clicked:', params.data);
                      // 使用函数式更新避免状态依赖问题
                      setSelectedNode(params.data)
                    }
                  }
                }}
              />
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
      {/* 记忆详情 */}
      <Col span={24}>
        <RbCard 
          title={t('userMemory.memoryDetails')}
          headerType="borderless"
          headerClassName="rb:text-[18px]! rb:leading-[24px]"
        >
          {!selectedNode
            ? <Empty 
              url={empty}
              title={t('userMemory.memoryDetailEmpty')}
              subTitle={t('userMemory.memoryDetailEmptyDesc')}
              className="rb:mb-3"
              size={88}
            />
            : <>

              <div className="rb:font-medium rb:mb-2">
                {t('userMemory.memoryContent')}
                <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-2">
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
              <div className="rb:font-medium rb:mb-2">
                {t('userMemory.created_at')}
                <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-2">
                  {dayjs(selectedNode?.properties.created_at).format('YYYY/MM/DD HH:mm:ss')}
                </div>
              </div>
            </>
          }
        </RbCard>
      </Col>
    </>
  )
}
// 使用React.memo包装组件，避免不必要的渲染
export default React.memo(RelationshipNetwork)