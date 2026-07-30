import type { Graph, History } from '@antv/x6';
import type { MutableRefObject, DragEvent } from 'react';
import type { TFunction } from 'i18next';
import dayjs from 'dayjs';

import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';
import { edgeAttrs, graphNodeLibrary, nodeLibrary, nodeWidth, notesConfig } from '../../constant';
import type { NodeProperties } from '../../types';

/**
 * Context required to build node-level operations (drag drop, notes, features).
 */
export interface NodeOperationsCtx {
  graphRef: MutableRefObject<Graph | undefined>;
  t: TFunction;
  user?: { username?: string } | null;
  featuresRef: MutableRefObject<FeaturesConfigForm | undefined>;
}

/**
 * Build node-level operations that are independent from the core graph event
 * handlers: drag-drop node creation, note creation, start-node variable reads
 * and opening-statement feature syncing.
 */
export const createNodeOperations = ({ graphRef, t, user, featuresRef }: NodeOperationsCtx) => {
  /**
   * Handle node drop event from drag-and-drop
   * Creates new node at drop position
   * @param event - React drag event
   */
  const onDrop = (event: DragEvent) => {
    if (!graphRef.current) return;
    event.preventDefault();
    const dragData = JSON.parse(event.dataTransfer.getData('application/json'));
    const graph = graphRef.current;
    if (!graph) return;

    const point = graphRef.current.clientToLocal(event.clientX, event.clientY);

    // Get original config from node library to avoid config data chaining
    let nodeLibraryConfig = [...nodeLibrary]
      .flatMap(category => category.nodes)
      .find(n => n.type === dragData.type);
    nodeLibraryConfig = JSON.parse(JSON.stringify({ config: {}, ...nodeLibraryConfig })) as NodeProperties

    if (nodeLibraryConfig.type === 'trigger' && nodeLibraryConfig.config?.time) {
      nodeLibraryConfig.config.time.defaultValue = dayjs(nodeLibraryConfig.config.time.defaultValue, 'h:mm A')
    }

    // Create clean node data, only keep necessary fields
    const cleanNodeData = {
      id: `${dragData.type.replace(/-/g, '_')}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      name: t(`workflow.${dragData.type}`),
      ...nodeLibraryConfig
    };

    if (dragData.type === 'loop' || dragData.type === 'iteration') {
      graph.disableHistory()
      const parentNode = graphRef.current.addNode({
        ...graphNodeLibrary[dragData.type],
        x: point.x - 150,
        y: point.y - 100,
        id: cleanNodeData.id,
        data: { ...cleanNodeData, isGroup: true },
      })
      const parentBBox = parentNode.getBBox()
      const cycleStartId = `cycle_start_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
      const cycleStartNode = graphRef.current.addNode({
        ...graphNodeLibrary.cycleStart,
        x: parentBBox.x + 24,
        y: parentBBox.y + 70,
        id: cycleStartId,
        data: { id: cycleStartId, type: 'cycle-start', parentId: cleanNodeData.id, isDefault: true, cycle: cleanNodeData.id },
      })
      const addNode = graphRef.current.addNode({
        ...graphNodeLibrary.addStart,
        x: parentBBox.x + 24 + 84,
        y: parentBBox.y + 70 + 4,
        data: { type: 'add-node', label: t('workflow.addNode'), icon: '+', parentId: cleanNodeData.id, cycle: cleanNodeData.id },
      })
      parentNode.addChild(cycleStartNode, { silent: true })
      parentNode.addChild(addNode, { silent: true })
      const newEdge = graphRef.current.addEdge({
        source: { cell: cycleStartNode.id, port: cycleStartNode.getPorts().find(p => p.group === 'right')?.id || 'right' },
        target: { cell: addNode.id, port: addNode.getPorts().find(p => p.group === 'left')?.id || 'left' },
        ...edgeAttrs,
      })
      cycleStartNode.toFront()
      addNode.toFront()
      graph.enableHistory()
      // Manually push a single batch frame covering all 4 cells into undoStack
      const history = graph.getPlugin('history') as History
      const makeBatchCmd = (cell: any) => ({
        batch: true,
        event: 'cell:added',
        data: { id: cell.id, node: cell.isNode(), edge: cell.isEdge(), props: cell.toJSON() },
        options: {},
      })
      const batchFrame = [parentNode, cycleStartNode, addNode, newEdge].map(makeBatchCmd)
      ;(history as any).undoStack.push(batchFrame)
      ;(history as any).redoStack = []
      graph.trigger('history:change', { cmds: batchFrame, options: { name: 'add-group' } })
    } else if (dragData.type === 'if-else') {
      // Create condition node
      graphRef.current.addNode({
        ...graphNodeLibrary[dragData.type],
        x: point.x - 100,
        y: point.y - 60,
        id: cleanNodeData.id,
        data: { ...cleanNodeData },
      });
    } else {
      // Normal node creation, does not support dragging into loop node
      graphRef.current.addNode({
        ...(graphNodeLibrary[dragData.type] || graphNodeLibrary.default),
        x: point.x - 60,
        y: point.y - 20,
        id: cleanNodeData.id,
        data: { ...cleanNodeData },
      });
    }
  };

  const handleAddNotes = () => {
    if (!graphRef.current) return;
    const nodeConfig: NodeProperties = JSON.parse(JSON.stringify(notesConfig));
    nodeConfig.config = {
      ...nodeConfig.config,
      author: { type: 'define', defaultValue: user?.username || '' },
    };
    const cleanNodeData = {
      id: `notes_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      name: t('workflow.notes'),
      ...nodeConfig,
    };
    const container = graphRef.current.container;
    const nodeW = graphNodeLibrary.notes?.width || nodeWidth;
    const nodeH = graphNodeLibrary.notes?.height || 100;
    const rect = container.getBoundingClientRect();
    const center = graphRef.current.clientToLocal(rect.left + rect.width / 2, rect.top + rect.height / 2);
    graphRef.current.addNode({
      ...(graphNodeLibrary.notes || graphNodeLibrary.default),
      x: center.x - nodeW / 2,
      y: center.y - nodeH / 2,
      id: cleanNodeData.id,
      data: { ...cleanNodeData },
    });
  }

  const getStartNodeVariables = (): Array<{ name: string; type: string; readonly?: boolean }> => {
    const startNode = graphRef.current?.getNodes().find(n => n.getData()?.type === 'start')
    if (!startNode) return []
    const data = startNode.getData()
    const userVars: Array<{ name: string; type: string; readonly?: boolean }> =
      (data?.config?.variables?.defaultValue ?? []).map((v: any) => ({ name: v.name, type: v.type }))
    return userVars
  }

  const handleSaveFeaturesConfig = (value?: FeaturesConfigForm) => {
    const { statement = '' } = value?.opening_statement || {}
    featuresRef.current = value

    const usedVars = [...new Set([...(statement?.matchAll(/\{\{(\w+)\}\}/g) ?? [])].map(m => m[1]))]
    const startVars = getStartNodeVariables()
    const validNames = new Set(startVars.map(v => v.name))
    const invalid = usedVars.filter(v => !validNames.has(v))
    if (invalid.length > 0) {
      const newVars = invalid.map(name => ({
        name,
        description: name,
        type: 'string',
        required: true,
        defaultValue: '',
      }))

      const startNode = graphRef.current?.getNodes().find(n => n.getData()?.type === 'start')
      if (startNode) {
        const data = startNode.getData()
        console.log('startNode', [...startVars, ...newVars])
        startNode.setData({
          ...data,
          config: {
            ...data.config,
            variables: {
              ...data.config.variables,
              defaultValue: [...startVars, ...newVars],
            },
          },
        })
      }
    }
  }

  return {
    onDrop,
    handleAddNotes,
    getStartNodeVariables,
    handleSaveFeaturesConfig,
  }
}
