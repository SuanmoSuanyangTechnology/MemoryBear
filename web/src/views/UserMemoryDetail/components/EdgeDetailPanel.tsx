import React, { useState } from 'react'
import { Flex } from 'antd'

import type { Edge, Node } from '@/components/Charts/GraphNetworkChart/types'
import { formatDateTime } from '@/utils/format'

interface EdgeDetailPanelProps {
  selectedNode: Edge
  nodes: Node[]
  t: (key: string) => string
  onRelationChange?: (index: number, direction: 'a_to_b' | 'b_to_a') => void
  activeRelationIndex?: number
  onActiveIndexChange?: (index: number) => void
}

const EdgeDetailPanel: React.FC<EdgeDetailPanelProps> = ({
  selectedNode,
  nodes,
  t,
  onRelationChange,
  activeRelationIndex: activeRelationIndexProp,
  onActiveIndexChange,
 }) => {
  const sourceNode = nodes.find(n => n.id === selectedNode.source)
  const targetNode = nodes.find(n => n.id === selectedNode.target)

  const aToBData = selectedNode?.a_to_b || []
  const bToAData = selectedNode?.b_to_a || []
  const allRelations = [
    ...aToBData.map((d: any, i: number) => ({ ...d, direction: 'a_to_b', edge_type: selectedNode.edge_type, index: i })),
    ...bToAData.map((d: any, i: number) => ({ ...d, direction: 'b_to_a', edge_type: selectedNode.edge_type, index: i }))
  ]
  // 是否为双向边：两端都有数据
  const isBidirectional = aToBData.length > 0 && bToAData.length > 0
  // 在双向边情况下，合并后数组的每一项对应"同一对 (a,b) 上的不同关系"
  // 第 0 项为 a_to_b[0]，第 1 项为 b_to_a[0]，第 2 项为 a_to_b[1]（如果存在），第 3 项为 b_to_a[1]（如果存在）...
  // 按此规则把 a_to_b 和 b_to_a 交错排列展示
  const interleavedRelations: any[] = []
  const maxLen = Math.max(aToBData.length, bToAData.length)
  for (let i = 0; i < maxLen; i++) {
    if (i < aToBData.length) {
      interleavedRelations.push({ ...aToBData[i], direction: 'a_to_b', edge_type: selectedNode.edge_type, index: interleavedRelations.length })
    }
    if (i < bToAData.length) {
      interleavedRelations.push({ ...bToAData[i], direction: 'b_to_a', edge_type: selectedNode.edge_type, index: interleavedRelations.length })
    }
  }

  // 优先使用受控的 activeRelationIndex，否则使用组件内部 state
  const [internalActiveIndex, setInternalActiveIndex] = useState(0)
  const activeRelationIndex = activeRelationIndexProp ?? internalActiveIndex

  // 切换关系时通知父组件
  const handleRelationChange = (index: number) => {
    if (onActiveIndexChange) {
      onActiveIndexChange(index)
    } else {
      setInternalActiveIndex(index)
    }
    const list = isBidirectional ? interleavedRelations : allRelations
    const relation = list[index]
    if (relation && onRelationChange) {
      onRelationChange(index, relation.direction as 'a_to_b' | 'b_to_a')
    }
  }

  const getRelationTypeName = (type: string) => {
    const typeMap: Record<string, string> = {
      'EXTRACTED_RELATIONSHIP': t('userMemory.relation_extracted'),
      'PRUNED_TO': t('userMemory.relation_pruned'),
      'BELONGS_TO_CONVERSATION': t('userMemory.relation_belongs'),
      'REFERENCES_ENTITY': t('userMemory.relation_references'),
      'NEW_RELATION': t('userMemory.relation_new'),
      'RELATED': t('userMemory.relation_related'),
      'DERIVED_FROM_STATEMENT': t('userMemory.relation_derived'),
      'CONTAINS': t('userMemory.relation_contains'),
    }
    return typeMap[type] || type
  }

  // 当前展示的关系列表（双向边时按交错顺序，单向时按合并顺序）
  const displayList = isBidirectional ? interleavedRelations : allRelations
  const currentRelation = displayList[activeRelationIndex] || displayList[0] || null

  // 根据当前关系方向调整 sourceNode / targetNode：
  // a_to_b：source=a, target=b；b_to_a：source=b, target=a
  const currentDirection = currentRelation?.direction as 'a_to_b' | 'b_to_a' | undefined
  const isExtracted = currentRelation?.edge_type === "BIDIRECTIONAL" || currentRelation?.edge_type === "MULTI_BIDIRECTIONAL"
  const currentSourceNode = isExtracted && currentDirection === 'b_to_a' ? targetNode : sourceNode
  const currentTargetNode = isExtracted && currentDirection === 'b_to_a' ? sourceNode : targetNode
  const currentSourceId = currentSourceNode?.id
  const currentTargetId = currentTargetNode?.id

  return (
    <div>
      {displayList.length > 1 && (
        <Flex gap={8} wrap className="rb:mb-4!">
          {displayList.map((_relation, index) => (
            <div
              key={index}
              onClick={() => handleRelationChange(index)}
              className={`rb:rounded-[13px] rb:py-0.5 rb:px-3 rb:leading-5 rb-border rb:cursor-pointer ${
                activeRelationIndex === index
                  ? 'rb:bg-[#171719] rb:border-[#171719]! rb:text-white'
                  : 'rb:bg-white'
              }`}
            >
              {t('userMemory.relation')}{index + 1}
            </div>
          ))}
        </Flex>
      )}

      {/* 仅展示 activeRelationIndex 对应的关系信息（不铺开 a_to_b / b_to_a） */}
      <Flex vertical gap={12}>
        <div className="rb:text-[#212332] rb:text-lg rb:font-medium">
          {currentRelation?.predicate_surface || currentRelation?.predicate}
        </div>

        <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.relationType')}</div>
        <div className="rb:font-medium">
          {getRelationTypeName(currentRelation?.type || '')}
        </div>

        <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.relationSubtype')}</div>
        <div className="rb:font-medium">
          {currentRelation?.predicate || '-'}
        </div>

        {currentRelation?.predicate_description && (
          <>
            <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.relationDescription')}</div>
            <div className="rb:font-medium">
              {currentRelation?.predicate_description}
            </div>
          </>
        )}

        <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.relationDirection')}</div>
        <div className="rb:font-medium">
          {currentRelation?.edge_type ? t(`userMemory.${currentRelation?.edge_type}`) : '-'}
        </div>

        <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.sourceNode')}</div>
        <div className="rb:font-medium">
          {currentSourceNode?.name && currentSourceNode?.name?.length > 0 ? currentSourceNode!.name : currentSourceId}
          {currentSourceNode?.label && ` (${currentSourceNode!.label})`}
        </div>

        <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.targetNode')}</div>
        <div className="rb:font-medium">
          {currentTargetNode?.name && currentTargetNode?.name?.length > 0 ? currentTargetNode!.name : currentTargetId}
          {currentTargetNode?.label && ` (${currentTargetNode!.label})`}
        </div>
        {currentRelation?.created_at && (
          <>
            <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.created_at')}</div>
            <div className="rb:font-medium">
              {currentRelation?.created_at
                ? formatDateTime(currentRelation.created_at)
                : '-'}
            </div>
          </>
        )}

        {currentRelation?.effective_time && (
          <>
            <div className="rb:text-[#5B6167] rb:font-regular">{t('userMemory.effectiveTime')}</div>
            <div className="rb:font-medium">
              {currentRelation?.effective_time
                ? formatDateTime(currentRelation.effective_time, 'YYYY-MM-DD HH:mm:ss')
                : '-'}
            </div>
          </>
        )}
      </Flex>
    </div>
  )
}

export default EdgeDetailPanel
