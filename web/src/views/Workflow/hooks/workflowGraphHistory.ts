import { History } from '@antv/x6';
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';
import { graphNodeLibrary, nodeWidth, notesConfig } from '../constant';
import type { NodeProperties } from '../types';
import type { WorkflowGraphContext } from './types';

export const createWorkflowGraphHistory = ({ graphRef, featuresRef, setHistoryRecords, lastHistoryRef, isSyncingRef, syncChildRelationships, user, t }: Pick<WorkflowGraphContext, 'graphRef' | 'featuresRef' | 'setHistoryRecords' | 'lastHistoryRef' | 'isSyncingRef' | 'syncChildRelationships' | 'user' | 't'>) => {
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

  const clearHistoryRecords = () => {
    setHistoryRecords([])
    lastHistoryRef.current = null
  }

  const getStackCellIds = (cmds: any): string[] => {
    const arr = Array.isArray(cmds) ? cmds : [cmds]
    return [...new Set(arr.map((c: any) => c.data?.id).filter(Boolean))]
  }

  const isSkippableFrame = (frame: any): boolean => {
    const arr = Array.isArray(frame) ? frame : [frame]
    return arr.every((c: any) => ['zIndex', 'attrs', 'tools'].includes(c.data?.key))
  }

  const undo = () => {
    const history = graphRef.current?.getPlugin('history') as History | undefined
    if (!history || history.getUndoSize() === 0) return
    const undoStack = (history as any).undoStack as any[]
    isSyncingRef.current = true
    while (undoStack.length > 0 && isSkippableFrame(undoStack[undoStack.length - 1])) {
      graphRef.current!.undo()
    }
    if (undoStack.length === 0) {
      isSyncingRef.current = false
      return
    }
    const topIds = getStackCellIds(undoStack[undoStack.length - 1])
    graphRef.current!.undo()
    while (undoStack.length > 0) {
      if (isSkippableFrame(undoStack[undoStack.length - 1])) {
        graphRef.current!.undo()
        continue
      }
      const nextIds = getStackCellIds(undoStack[undoStack.length - 1])
      if (nextIds.length === topIds.length && nextIds.every((id, i) => id === topIds[i])) {
        graphRef.current!.undo()
      } else {
        break
      }
    }
    isSyncingRef.current = false
    syncChildRelationships()
  }

  const redo = () => {
    const history = graphRef.current?.getPlugin('history') as History | undefined
    if (!history || history.getRedoSize() === 0) return
    const redoStack = (history as any).redoStack as any[]
    isSyncingRef.current = true
    while (redoStack.length > 0 && isSkippableFrame(redoStack[redoStack.length - 1])) {
      graphRef.current!.redo()
    }
    if (redoStack.length === 0) {
      isSyncingRef.current = false
      return
    }
    const topIds = getStackCellIds(redoStack[redoStack.length - 1])
    graphRef.current!.redo()
    while (redoStack.length > 0) {
      if (isSkippableFrame(redoStack[redoStack.length - 1])) {
        graphRef.current!.redo()
        continue
      }
      const nextIds = getStackCellIds(redoStack[redoStack.length - 1])
      if (nextIds.length === topIds.length && nextIds.every((id, i) => id === topIds[i])) {
        graphRef.current!.redo()
      } else {
        break
      }
    }
    isSyncingRef.current = false
    syncChildRelationships()
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
  return { handleAddNotes, getStartNodeVariables, clearHistoryRecords, getStackCellIds, isSkippableFrame, undo, redo, handleSaveFeaturesConfig };
};
