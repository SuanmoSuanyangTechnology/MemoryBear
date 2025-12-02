import React, { type FC, useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Col } from 'antd'
import RbCard from '@/components/RbCard/Card'
import ReactEcharts from 'echarts-for-react'
import zoom from '@/assets/images/userMemory/zoom.svg'
import drag from '@/assets/images/userMemory/drag.svg'
import pointer from '@/assets/images/userMemory/pointer.svg'
import empty from '@/assets/images/userMemory/empty.svg'
import type { EdgeData, Node, Edge } from '../types'
import {
  getMemorySearchEdges,
} from '@/api/memory'
import Empty from '@/components/Empty'

const operations = [
  { name: 'click', icon: pointer },
  { name: 'drag', icon: drag },
  { name: 'zoom', icon: zoom },
]
const RelationshipNetwork:FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const chartRef = useRef<ReactEcharts>(null)
  const [nodes, setNodes] = useState<Node[]>([])
  const [links, setLinks] = useState<Edge[]>([])
  const [categories, setCategories] = useState<{ name: string }[]>([])
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)

  useEffect(() => {
    if (!id) return
    getEdgeData()
  }, [id])

  // 关系网络
  const getEdgeData = () => {
    if (!id) return
    setSelectedNode(null)
    getMemorySearchEdges(id).then((res) => {
      const list = (res as { detials?: EdgeData[] }).detials || []
      const nodes: Node[] = [];
      const links: Edge[] = [];
      const categories: { name: string }[] = []
      
      list.forEach(item => {
        if (item.edge) {
          links.push({
            ...item.edge,
            target: item.edge?.target_id,
            source: item.edge?.source_id,
          })
        }
        if (item.sourceNode) {
          nodes.push(item.sourceNode)
          categories.push({name: item.sourceNode.entity_type})
        }
        if (item.targetNode) {
          nodes.push(item.targetNode)
          categories.push({name: item.targetNode.entity_type})
        }
      })
      
      // 根据ID字段去重节点
      const uniqueNodes = nodes.filter((node, index, self) =>
        index === self.findIndex((n) => n.id === node.id && n.name === node.name)
      )
      const uniqueLinks = links.filter((node, index, self) =>
        index === self.findIndex((n) => n.target === node.target && n.source === node.source)
      )
      const uniqueCategories = categories.filter((node, index, self) =>
        index === self.findIndex((n) => n.name === node.name)
      )
    
      setLinks(uniqueLinks)
      setCategories(uniqueCategories)

      uniqueNodes.map(item => {
        const index = uniqueCategories.findIndex((n) => n.name === item.entity_type)
        item.category = index
        item.symbolSize = index < 10 ? 5 : index <100 ? 8 : 10
      })
      setNodes(uniqueNodes)
    })
  }
  return (
    <>
      {/* 关系网络 */}
      <Col span={24}>
        <RbCard 
          title={t('userMemory.relationshipNetwork')}
          headerType="borderless"
          headerClassName="rb:text-[18px]! rb:leading-[24px]"
        >
          <div className="rb:h-[496px]">
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
                      lineStyle: {
                        color: 'source',
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
                  // 图表渲染完成后再次调整大小，确保宽度正确
                  // 使用 setTimeout 避免在主渲染过程中调用 resize
                  rendered: () => {
                    if (chartRef.current) {
                      setTimeout(() => {
                        chartRef.current?.getEchartsInstance().resize();
                      }, 0);
                    }
                  },
                  // 节点点击事件处理
                  click: (params: { dataType: string; data: Node }) => {
                    if (params.dataType === 'node') {
                      // 处理节点点击事件
                      console.log('Node clicked:', params.data);
                      setSelectedNode(params.data)
                      if (selectedNode?.id === params.data.id) {
                        setSelectedNode(null)
                      } else {
                        setSelectedNode(params.data)
                      }
                    }
                  }
                }}
              />
            )}
          </div>
          <div className="rb:bg-[#F0F3F8] rb:flex rb:items-center rb:gap-[24px] rb:rounded-[0px_0px_12px_12px] rb:p-[14px_40px] rb:m-[0_-20px_-16px_-16px]">
            {operations.map((item) => (
              <div key={item.name} className="rb:flex rb:items-center rb:text-[#5B6167] rb:leading-[20px]">
                <img src={item.icon} className="rb:w-[20px] rb:h-[20px] rb:mr-[4px]" />
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
          {(!selectedNode || (!selectedNode?.description && !selectedNode?.entity_type))
            ? <Empty 
              url={empty}
              title={t('userMemory.memoryDetailEmpty')}
              subTitle={t('userMemory.memoryDetailEmptyDesc')}
              className="rb:mb-[12px]"
              size={88}
            />
            : <>
              {selectedNode?.description &&
                <div className="rb:font-medium rb:mb-[8px]">
                  {t('userMemory.description')}
                  <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-[8px]"> {selectedNode.description}</div>
                </div>
              }
              {selectedNode?.entity_type &&
                <div className="rb:font-medium rb:mb-[8px]">
                  {t('userMemory.entityType')}
                  <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-[8px]"> {selectedNode.entity_type}</div>
                </div>
              }
            </>
          }
        </RbCard>
      </Col>
    </>
  )
}
// 使用React.memo包装组件，避免不必要的渲染
export default React.memo(RelationshipNetwork)