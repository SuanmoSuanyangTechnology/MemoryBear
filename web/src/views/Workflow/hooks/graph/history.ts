import type { Graph, History } from '@antv/x6';
import type { MutableRefObject } from 'react';

/**
 * Context shared by the undo/redo helpers.
 */
export interface HistoryCtx {
  graphRef: MutableRefObject<Graph | undefined>;
  isSyncingRef: MutableRefObject<boolean>;
  syncChildRelationships: () => void;
}

export const getStackCellIds = (cmds: any): string[] => {
  const arr = Array.isArray(cmds) ? cmds : [cmds]
  return [...new Set(arr.map((c: any) => c.data?.id).filter(Boolean))]
}

export const isSkippableFrame = (frame: any): boolean => {
  const arr = Array.isArray(frame) ? frame : [frame]
  return arr.every((c: any) => ['zIndex', 'attrs', 'tools'].includes(c.data?.key))
}

export const performUndo = ({ graphRef, isSyncingRef, syncChildRelationships }: HistoryCtx) => {
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

export const performRedo = ({ graphRef, isSyncingRef, syncChildRelationships }: HistoryCtx) => {
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
