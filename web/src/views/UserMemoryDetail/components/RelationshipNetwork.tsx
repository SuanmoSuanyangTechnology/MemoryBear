import React, { type FC, useEffect, useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { Col, Row, Space, Button } from 'antd'
import dayjs from 'dayjs'

import RbCard from '@/components/RbCard/Card'
import ReactEcharts from 'echarts-for-react'
import detailEmpty from '@/assets/images/userMemory/detail_empty.png'
import type { Node, Edge, GraphData, StatementNodeProperties, ExtractedEntityNodeProperties } from '../types'
import {
  getMemorySearchEdges,
} from '@/api/memory'
import Empty from '@/components/Empty'
import Tag from '@/components/Tag'

const colors = ['#155EEF', '#369F21', '#4DA8FF', '#FF5D34', '#9C6FFF', '#FF8A4C', '#8BAEF7', '#FFB048']
const RelationshipNetwork:FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const chartRef = useRef<ReactEcharts>(null)
  const resizeScheduledRef = useRef(false)
  const [nodes, setNodes] = useState<Node[]>([])
  const [links, setLinks] = useState<Edge[]>([])
  const [categories, setCategories] = useState<{ name: string }[]>([])
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  // const [fullScreen, setFullScreen] = useState<boolean>(false)
  const navigate = useNavigate()

  console.log('categories', categories)
  // 关系网络
  const getEdgeData = useCallback(() => {
    if (!id) return
    setSelectedNode(null)
    getMemorySearchEdges(id).then((res) => {
      const { nodes, edges, statistics } = res as GraphData
      const curNodes: Node[] = []
      const curEdges: Edge[] = []
      const curNodeTypes = Object.keys(statistics.node_types).filter(vo => vo !== 'Dialogue')
      
      // 计算每个节点的连接数
      const connectionCount: Record<string, number> = {}
      edges.forEach(edge => {
        connectionCount[edge.source] = (connectionCount[edge.source] || 0) + 1
        connectionCount[edge.target] = (connectionCount[edge.target] || 0) + 1
      })
      
      // 处理节点数据
      nodes.filter(vo => vo.label !== 'Dialogue').forEach(node => {
        const connections = connectionCount[node.id] || 0
        const categoryIndex = curNodeTypes.indexOf(node.label)
        
        // 根据节点类型获取显示名称
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
          symbolSize: symbolSize, // 根据连接数调整节点大小
        })
      })
      
      // 创建节点ID到标签的映射
      const nodeIdToLabel: Record<string, string> = {}
      nodes.forEach(node => {
        nodeIdToLabel[node.id] = node.label
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
    <Row gutter={16}>
      {/* 关系网络 */}
      <Col span={16}>
        <RbCard 
          title={t('userMemory.relationshipNetwork')}
          headerType="borderless"
          headerClassName="rb:min-h-[46px]!"
          // extra={
          //   <div
          //     onClick={handleFullScreen}
          //     className="rb:group rb:cursor-pointer rb:hover:text-[#212332] rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:flex rb:items-center rb:gap-1"
          //   >
          //     <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/fullScreen.svg')] rb:hover:bg-[url('@/assets/images/fullScreen_hover.svg')]"></div>
          //     {t('userMemory.fullScreen')}
          //   </div>
          // }
        >
          <div className="rb:h-129.5 rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-sm">
            {nodes.length === 0 ? (
              <Empty className="rb:h-full" />
            ) : (
              <ReactEcharts
                option={{
                  colors: colors,
                  tooltip: {
                    show: false
                  },
                  legend: {
                    show: true,
                    bottom: 12,
                  },
                  series: [
                    {
                      type: 'graph',
                      layout: 'force',
                      data: nodes || [],
                      links: links || [],
                      categories: categories.map(vo => ({
                        name: t(`userMemory.${vo.name}`)
                      })) || [],
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
                style={{ height: '518px', width: '100%' }}
                notMerge={false}
                lazyUpdate={true}
                onEvents={{
                  // 节点点击事件处理
                  click: (params: { dataType: string; data: Node; name: string }) => {
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
        </RbCard>
      </Col>
      {/* 记忆详情 */}
      <Col span={8}>
        <RbCard 
          title={t('userMemory.memoryDetails')}
          headerType="borderless"
          headerClassName="rb:min-h-[46px]!"
          bodyClassName='rb:p-0!'
          extra={selectedNode && <Button type="text" onClick={handleViewAll}>
            <div
              className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/userMemory/view.svg')] rb:hover:bg-[url('@/assets/images/userMemory/view_hover.svg')]"
            ></div>
            {t('userMemory.completeMemory')}
          </Button>}
        >
          <div className="rb:h-133.5 rb:overflow-y-auto">
            {!selectedNode
              ? <Empty 
                url={detailEmpty}
                subTitle={t('userMemory.memoryDetailEmptyDesc')}
                className="rb:h-full rb:mx-10 rb:text-center"
                size={[197.81, 150]}
              />
              : <>
                <div className="rb:bg-[#F6F8FC] rb:border-t rb:border-b rb:border-[#DFE4ED] rb:font-medium rb:py-2 rb:px-4 rb:h-10">{selectedNode.name}</div>
                <div className="rb:p-4">
                  <>
                    <div className="rb:font-medium rb:leading-5">{t('userMemory.memoryContent')}</div>
                    <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-1 rb:pb-4 rb:border-b rb:border-[#DFE4ED]">
                      {['Chunk', 'Dialogue', 'MemorySummary'].includes(selectedNode.label) && 'content' in selectedNode.properties
                        ? selectedNode.properties.content
                        : selectedNode.label === 'ExtractedEntity' && 'description' in selectedNode.properties
                        ? selectedNode.properties.description
                        : selectedNode.label === 'Statement' && 'statement' in selectedNode.properties
                        ? selectedNode.properties.statement
                        : ''
                      }
                    </div>
                  </>
                  <div className="rb:font-medium rb:mb-2 rb:mt-4">
                    <div className="rb:font-medium rb:leading-5">{t('userMemory.created_at')}</div>
                    <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-1 rb:pb-4 rb:border-b rb:border-[#DFE4ED]">
                      {dayjs(selectedNode?.properties.created_at).format('YYYY-MM-DD HH:mm:ss')}
                    </div>

                    {selectedNode?.properties.associative_memory > 0 && <div className="rb:mt-4">
                      <div className="rb:font-medium rb:leading-5">{t('userMemory.associative_memory')}</div>
                      <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-1 rb:pb-4 rb:border-b rb:border-[#DFE4ED]">
                        <span className="rb:text-[#155EEF] rb:font-medium">{selectedNode?.properties.associative_memory}</span> {t('userMemory.unix')}{t('userMemory.associative_memory')}
                      </div>
                    </div>}

                    {selectedNode.label === 'Statement' && <>
                      {(['emotion_keywords', 'emotion_type', 'emotion_subject', 'importance_score'] as const).map(key => {
                        const statementProps = selectedNode.properties as StatementNodeProperties;
                        if ((key === 'emotion_keywords' && statementProps[key]?.length > 0) || statementProps[key]) {
                          return (
                            <div className="rb:mt-4" key={key}>
                              {t(`userMemory.Statement_${key}`)}
                              <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-1 rb:pb-4 rb:border-b rb:border-[#DFE4ED]">
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
                            <div className="rb:mt-4" key={key}>
                              {t(`userMemory.ExtractedEntity_${key}`)}
                              <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-1 rb:pb-4 rb:border-b rb:border-[#DFE4ED]">
                                {entityProps[key]}
                              </div>
                            </div>
                          )
                        }
                        return null
                      })}
                    </>}
                  </div>
                </div>
              </>
            }
          </div>
        </RbCard>
      </Col>
    </Row>
  )
}
// 使用React.memo包装组件，避免不必要的渲染
export default React.memo(RelationshipNetwork)