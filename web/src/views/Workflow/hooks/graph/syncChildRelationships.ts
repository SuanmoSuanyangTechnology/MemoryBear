import type { Graph, Node } from '@antv/x6';
import type { MutableRefObject } from 'react';

import { conditionNodeHeight, conditionNodeItemHeight, conditionNodePortItemArgsY, nodeWidth, portItemArgsY } from '../../constant';
import { calcConditionNodeTotalHeight, getConditionNodeCasePortY } from '../../utils';

export const resizeGroupNodes = (graph: Graph) => {
  graph.getNodes().forEach(parentNode => {
    const parentType = parentNode.getData()?.type
    if (parentType !== 'loop' && parentType !== 'iteration') return
    const children = graph.getNodes().filter(
      n => n.getData()?.cycle === parentNode.getData()?.id && n.getData()?.type !== 'add-node'
    )
    if (!children.length) return
    const padding = 24
    const headerHeight = 50
    const childBounds = children.map(c => c.getBBox())
    const minX = Math.min(...childBounds.map(b => b.x))
    const minY = Math.min(...childBounds.map(b => b.y))
    const maxX = Math.max(...childBounds.map(b => b.x + b.width))
    const maxY = Math.max(...childBounds.map(b => b.y + b.height))
    const parentBBox = parentNode.getBBox()
    const newWidth = Math.max(parentBBox.width, maxX - minX + padding * 2)
    const newHeight = Math.max(parentBBox.height, maxY - minY + padding * 2 + headerHeight)
    parentNode.prop('size', { width: newWidth, height: newHeight })
    parentNode.getPorts().forEach(port => {
      if (port.group === 'right' && port.args) {
        parentNode.portProp(port.id!, 'args/x', newWidth)
      }
    })
  })
}

export const syncChildRelationships = (graphRef: MutableRefObject<Graph | undefined>) => {
  if (!graphRef.current) return
  const graph = graphRef.current
  graph.disableHistory()
  graph.getNodes().forEach(node => {
    const nodeData = node.getData()
    const children = node.getChildren()

    const cycleId = nodeData?.cycle

    if (cycleId) {
      const parentNode = graph.getCellById(cycleId) as Node | null
      if (!parentNode) return
      if (!parentNode.getChildren()?.some(c => c.id === node.id)) {
        parentNode.addChild(node, { silent: true })
      }
    }

    if (nodeData.type === 'if-else') {
      const rightPorts = node.getPorts().filter(p => p.group === 'right')
      const caseCount = rightPorts.length - 1 // last port is ELSE
      const currentCases: any[] = nodeData.config?.cases?.defaultValue ?? []
      const newCases = caseCount !== currentCases.length
        ? Array.from({ length: caseCount }, (_, i) => currentCases[i] ?? { logical_operator: 'and', expressions: [] })
        : currentCases
      if (caseCount !== currentCases.length) {
        node.setData({
          ...nodeData,
          config: { ...nodeData.config, cases: { ...nodeData.config.cases, defaultValue: newCases } }
        }, { deep: false, silent: true })
      }
      // Sync node height and port Y positions
      node.prop('size', { width: nodeWidth, height: calcConditionNodeTotalHeight(newCases) })
      newCases.forEach((_c: any, i: number) => {
        node.portProp(`CASE${i + 1}`, 'args/y', getConditionNodeCasePortY(newCases, i))
      })
      node.portProp(`CASE${newCases.length + 1}`, 'args/y', getConditionNodeCasePortY(newCases, newCases.length))
      node.toFront()
      graph.getEdges().filter(e => e.getSourceCellId() === node.id).forEach(e => {
        const tgt = graph.getCellById(e.getTargetCellId())
        tgt?.toFront()
      })
    } else if (nodeData.type === 'question-classifier') {
      const rightPorts = node.getPorts().filter(p => p.group === 'right')
      const currentCategories: any[] = nodeData.config?.categories?.defaultValue ?? []
      const categoryCount = rightPorts.length
      const newCategories = categoryCount !== currentCategories.length
        ? rightPorts.map((port, i) => {
          if (currentCategories[i]) return currentCategories[i]
          const edge = graph.getEdges().find(e => e.getSourceCellId() === node.id && e.getSourcePortId() === port.id)
          return edge ? { name: '' } : {}
        })
        : currentCategories
      if (categoryCount !== currentCategories.length) {
        node.setData({
          ...nodeData,
          config: { ...nodeData.config, categories: { ...nodeData.config.categories, defaultValue: [...newCategories] } }
        }, { deep: false, silent: true })
      }
      // Sync node height and port Y positions
      const newHeight = conditionNodeHeight + (categoryCount - 2) * conditionNodeItemHeight
      node.prop('size', { width: nodeWidth, height: Math.max(newHeight, conditionNodeHeight) })
      rightPorts.forEach((_p, i) => {
        node.portProp(`CASE${i + 1}`, 'args/y', portItemArgsY * i + conditionNodePortItemArgsY)
      })
      node.toFront()
      graph.getEdges().filter(e => e.getSourceCellId() === node.id).forEach(e => {
        const tgt = graph.getCellById(e.getTargetCellId())
        tgt?.toFront()
      })
    } else if (nodeData.type === 'human-intervention') {
      const rightPorts = node.getPorts().filter(p => p.group === 'right')
      const caseCount = rightPorts.length - 1 // last port is ELSE
      const currentActions: any[] = nodeData.config?.actions?.defaultValue ?? []
      const newActions = caseCount !== currentActions.length
        ? Array.from({ length: caseCount }, (_, i) => currentActions[i] ?? { logical_operator: 'and', expressions: [] })
        : currentActions
      if (caseCount !== currentActions.length) {
        node.setData({
          ...nodeData,
          config: { ...nodeData.config, actions: { ...nodeData.config.actions, defaultValue: newActions } }
        }, { deep: false, silent: true })
      }
      // Sync node height and port Y positions
      node.prop('size', { width: nodeWidth, height: calcConditionNodeTotalHeight(newActions) })
      newActions.forEach((_c: any, i: number) => {
        node.portProp(`CASE${i + 1}`, 'args/y', getConditionNodeCasePortY(newActions, i))
      })
      node.portProp(`CASE${newActions.length + 1}`, 'args/y', getConditionNodeCasePortY(newActions, newActions.length))
      node.toFront()
      graph.getEdges().filter(e => e.getSourceCellId() === node.id).forEach(e => {
        const tgt = graph.getCellById(e.getTargetCellId())
        tgt?.toFront()
      })
    }

    if (children?.length) {
      children.forEach(child => {
        if (!child.isNode()) return
        const childCycleId = (child as Node).getData?.()?.cycle
        if (childCycleId !== node.id && childCycleId !== node.getData?.()?.id) {
          node.removeChild(child, { silent: true })
        }
      })
    }
  })
  resizeGroupNodes(graph)
  graph.getEdges().forEach(edge => {
    const src = graph.getCellById(edge.getSourceCellId())
    const tgt = graph.getCellById(edge.getTargetCellId())
    if (src?.getData()?.cycle || tgt?.getData()?.cycle) {
      edge.toFront()
    }
  })
  graph.getNodes().forEach(node => {
    if (node.getData()?.cycle) node.toFront()
  })
  graph.enableHistory()
}
