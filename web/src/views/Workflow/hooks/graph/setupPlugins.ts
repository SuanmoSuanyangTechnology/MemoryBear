import { Clipboard, Keyboard, MiniMap, Snapline, History, Selection, Scroller } from '@antv/x6';
import type { Graph } from '@antv/x6';
import type { RefObject, Dispatch, SetStateAction, MutableRefObject } from 'react';

import type { HistoryRecord } from '../../types';

/**
 * Context required to configure the X6 graph plugins.
 */
export interface SetupPluginsCtx {
  graphRef: MutableRefObject<Graph | undefined>;
  miniMapRef: RefObject<HTMLDivElement>;
  setCanUndo: Dispatch<SetStateAction<boolean>>;
  setCanRedo: Dispatch<SetStateAction<boolean>>;
  setHistoryRecords: Dispatch<SetStateAction<HistoryRecord[]>>;
  lastHistoryRef: MutableRefObject<{ cellIds: string[]; timestamp: number; type: string } | null>;
  isSyncingRef: MutableRefObject<boolean>;
  syncChildRelationshipsRef: MutableRefObject<() => void>;
}

/**
 * Setup X6 graph plugins (MiniMap, Snapline, Clipboard, Keyboard, History...)
 */
export const setupPlugins = ({
  graphRef,
  miniMapRef,
  setCanUndo,
  setCanRedo,
  setHistoryRecords,
  lastHistoryRef,
  isSyncingRef,
  syncChildRelationshipsRef,
}: SetupPluginsCtx) => {
  if (!graphRef.current || !miniMapRef.current) return;
  // 添加小地图
  graphRef.current.use(
    new MiniMap({
      container: miniMapRef.current,
      width: 170,
      height: 80,
      padding: 5,
    }),
  );
  graphRef.current.use(
    new Scroller({
      enabled: true,
      pannable: false,
      autoResize: true,
    }),
  );
  graphRef.current.use(
    new Snapline({
      enabled: true,
    }),
  );
  graphRef.current.use(
    new Clipboard({
      enabled: true,
      useLocalStorage: true,
    }),
  );
  graphRef.current.use(
    new Keyboard({
      enabled: true,
      global: true,
    }),
  );
  graphRef.current.use(
    new Selection({
      enabled: false,
      multiple: true,
      rubberband: true,
      movable: true,
      showNodeSelectionBox: true,
      showEdgeSelectionBox: true,
    })
  )
  graphRef.current.use(
    new History({
      enabled: false,
      beforeAddCommand(_event, args: any) {
        const key = args?.key
        if (key === 'attrs' || key === 'tools') return false
      },
    }),
  );
  const MERGE_INTERVAL = 1000
  graphRef.current.on('history:change', ({ cmds, options }: { cmds: any[]; options: any }) => {
    setCanUndo(graphRef.current?.canUndo() ?? false)
    setCanRedo(graphRef.current?.canRedo() ?? false)
    console.log('history:change', cmds, options)
    const batchName: string | undefined = options?.name
    const actionType = batchName === 'undo' ? 'undo' : batchName === 'redo' ? 'redo' : batchName ? 'batch' : 'change'
    const cellIds = [...new Set(cmds?.map((cmd: any) => cmd.data?.id).filter(Boolean))]
    const now = Date.now()
    const last = lastHistoryRef.current
    const canMerge =
      actionType === 'change' &&
      last?.type === 'change' &&
      now - last.timestamp < MERGE_INTERVAL &&
      cellIds.length > 0 &&
      cellIds.length === last.cellIds.length &&
      cellIds.every((id, i) => id === last.cellIds[i])
    if (canMerge) {
      lastHistoryRef.current!.timestamp = now
      setHistoryRecords(prev => {
        const next = [...prev]
        next[next.length - 1] = { ...next[next.length - 1], timestamp: now }
        return next
      })
    } else {
      const record: HistoryRecord = { type: actionType, timestamp: now, batchName, cellIds }
      lastHistoryRef.current = { cellIds, timestamp: now, type: actionType }
      setHistoryRecords(prev => [...prev, record])
    }
  })

  graphRef.current.on('history:undo', () => { if (!isSyncingRef.current) syncChildRelationshipsRef.current() })
  graphRef.current.on('history:redo', () => { if (!isSyncingRef.current) syncChildRelationshipsRef.current() })
};
