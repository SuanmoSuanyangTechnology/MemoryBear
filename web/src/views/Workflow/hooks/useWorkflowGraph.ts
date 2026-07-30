/*
 * @Author: ZhaoYing
 * @Date: 2026-02-03 15:17:48
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-06 16:01:59
 */
import { Graph, type Node } from '@antv/x6';
import { App } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';

import { getWorkflowConfig, saveWorkflowConfig } from '@/api/application';
import { useUser } from '@/store/user';
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';
import type { ChatVariable, EnvVariable, HistoryRecord, WorkflowConfig } from '../types';
import { useWorkflowStore } from '@/store/workflow';
import type { Memory } from '@/views/MemoryManagement/types'
import { getMemoryConfigList } from '@/api/memory'

import type { UseWorkflowGraphProps, UseWorkflowGraphReturn } from './graph/types';
import { initWorkflow as initWorkflowFn } from './graph/initWorkflow';
import { buildWorkflowSaveParams } from './graph/serializeWorkflow';
import { setupPlugins as setupPluginsFn } from './graph/setupPlugins';
import { syncChildRelationships as syncChildRelationshipsFn } from './graph/syncChildRelationships';
import { performUndo, performRedo } from './graph/history';
import { createGraphHandlers } from './graph/createGraphHandlers';
import { createGraphInit } from './graph/createGraphInit';
import { createNodeOperations } from './graph/createNodeOperations';

export type { UseWorkflowGraphProps, UseWorkflowGraphReturn } from './graph/types';

/**
 * Custom hook for managing workflow graph
 * Handles graph initialization, node/edge operations, and workflow configuration
 * @param props - Hook props containing container references
 * @returns Object containing graph state and handlers
 */
export const useWorkflowGraph = ({
  containerRef,
  miniMapRef,
  appType,
  setRunOpen,
}: UseWorkflowGraphProps): UseWorkflowGraphReturn => {
  // Hooks
  const { id } = useParams();
  const { message } = App.useApp();
  const { t } = useTranslation()
  const { user } = useUser();
  const { chatHistoryMap } = useWorkflowStore()
  const lastExecuteId = Object.keys(chatHistoryMap).at(-1) ?? ''
  const chatHistory = chatHistoryMap[lastExecuteId] ?? []

  // Refs
  const graphRef = useRef<Graph>();

  // State
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [isHandMode, setIsHandMode] = useState(true);
  const isHandModeRef = useRef(true)
  const [config, setConfig] = useState<WorkflowConfig | null>(null);
  const [chatVariables, setChatVariables] = useState<ChatVariable[]>([])
  const [envVariables, setEnvVariables] = useState<EnvVariable[]>([])
  const featuresRef = useRef<FeaturesConfigForm | undefined>(undefined)
  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)
  const [historyRecords, setHistoryRecords] = useState<HistoryRecord[]>([])
  const lastHistoryRef = useRef<{ cellIds: string[]; timestamp: number; type: string } | null>(null)
  const syncChildRelationshipsRef = useRef<() => void>(() => { })
  const isSyncingRef = useRef(false)
  /**
   * Tracks whether `initWorkflow` has been invoked for the initial
   * config load. Subsequent `setConfig` calls (e.g. after saving
   * the workflow) must NOT trigger a re-initialization, otherwise
   * the graph would be torn down and rebuilt, losing any local
   * edits and duplicating edges.
   */
  const workflowInitializedRef = useRef(false)
  const [activeMemoryConfig, setActiveMemoryConfig] = useState<Memory | null>(null)
  const getActiveMemoryConfig = () => {
    getMemoryConfigList()
      .then((res) => {
        setActiveMemoryConfig((res as Memory[]).find(item => item.is_active) || null)
      })
      .catch(() => {
        setActiveMemoryConfig(null)
      })
  }
  useEffect(() => {
    if (!graphRef.current) return
    graphRef.current.getNodes().forEach(node => {
      const data = node.getData()
      if (['if-else', 'question-classifier', 'human-intervention'].includes(data?.type)) {
        node.setData({ ...data, chatVariables, envVariables })
      }
      if (['memory-read', 'memory-write'].includes(data?.type)) {
        node.setData({ ...data, activeMemoryConfig })
      }
    })
  }, [chatVariables, envVariables, activeMemoryConfig, graphRef.current?.getNodes()])

  useEffect(() => {
    if (!appType || !graphRef.current) return
    graphRef.current.getNodes().forEach(node => {
      const data = node.getData()
      node.setData({ ...data, appType })
    })
  }, [appType, graphRef.current])

  useEffect(() => {
    getConfig()
    getActiveMemoryConfig()
  }, [id])
  /**
   * Fetch workflow configuration from API
   */
  const getConfig = () => {
    if (!id) return
    getWorkflowConfig(id)
      .then(res => {
        const { variables, environment_variables, ...rest } = res as WorkflowConfig
        const initChatVariables = variables.map(v => {
          const { default: _, ...cleanV } = v
          return {
            ...cleanV,
            defaultValue: v.default ?? ''
          }
        })
        setChatVariables(initChatVariables)
        setEnvVariables(environment_variables ?? [])
        setConfig({ ...rest, variables: initChatVariables, environment_variables: environment_variables ?? [] })
        featuresRef.current = rest.features
      })
  }

  // Interactive event handlers (mutually referenced, kept in one factory)
  const {
    nodeClick,
    edgeClick,
    blankClick,
    scaleEvent,
    nodeMoved,
    copyEvent,
    parseEvent,
    deleteEvent,
    nodePortClickEvent,
    handleResize,
  } = createGraphHandlers({
    graphRef,
    containerRef,
    setSelectedNode,
    setZoomLevel,
    setRunOpen,
    isHandModeRef,
    t,
  })

  // Node-level operations
  const { onDrop, handleAddNotes, getStartNodeVariables, handleSaveFeaturesConfig } = createNodeOperations({
    graphRef,
    t,
    user,
    featuresRef,
  })

  // Layer reordering / parent-child sync
  const syncChildRelationships = () => syncChildRelationshipsFn(graphRef)
  syncChildRelationshipsRef.current = syncChildRelationships

  const undo = () => performUndo({ graphRef, isSyncingRef, syncChildRelationships })
  const redo = () => performRedo({ graphRef, isSyncingRef, syncChildRelationships })

  const setupPlugins = () => setupPluginsFn({
    graphRef,
    miniMapRef,
    setCanUndo,
    setCanRedo,
    setHistoryRecords,
    lastHistoryRef,
    isSyncingRef,
    syncChildRelationshipsRef,
  })

  const initWorkflow = () => initWorkflowFn({ graphRef, config, chatVariables, envVariables, t })

  console.log('workflowInitializedRef', workflowInitializedRef.current)
  useEffect(() => {
    if (!config || !graphRef.current) return
    if (workflowInitializedRef.current) return
    workflowInitializedRef.current = true
    initWorkflow()
  }, [config, graphRef.current])

  useEffect(() => {
    isHandModeRef.current = isHandMode
    if (!graphRef.current) return;
    if (isHandMode) {
      graphRef.current?.enablePanning();
      graphRef.current?.disableSelection();
      graphRef.current?.cleanSelection()
    } else {
      graphRef.current?.disablePanning();
      graphRef.current?.enableSelection();
    }
  }, [isHandMode, graphRef.current]);

  const init = createGraphInit({
    containerRef,
    miniMapRef,
    graphRef,
    isHandMode,
    setupPlugins,
    nodeClick,
    edgeClick,
    nodePortClickEvent,
    blankClick,
    scaleEvent,
    nodeMoved,
    copyEvent,
    parseEvent,
    deleteEvent,
    undo,
    redo,
  })

  useEffect(() => {
    if (!containerRef.current || !miniMapRef.current) return;
    init();

    window.addEventListener('resize', handleResize);

    const handleNoteKeydown = (e: KeyboardEvent) => {
      if (!graphRef.current) return;
      const selectedNote = graphRef.current.getNodes().find(n => n.getData()?.isSelected && n.getData()?.type === 'notes');
      if (!selectedNote) return;
      const isMeta = e.ctrlKey || e.metaKey;
      if (e.key === 'Delete' || e.key === 'Backspace') {
        // Only delete node when editor is not focused on text
        const active = document.activeElement;
        if (active && (active as HTMLElement).isContentEditable) return;
        deleteEvent();
      } else if (isMeta && e.key === 'c') {
        copyEvent();
      } else if (isMeta && e.key === 'v') {
        parseEvent();
      } else if (isMeta && e.key === 'd') {
        e.preventDefault();
        deleteEvent();
      }
    };
    window.addEventListener('keydown', handleNoteKeydown);

    return () => {
      window.removeEventListener('resize', handleResize);
      window.removeEventListener('keydown', handleNoteKeydown);
      graphRef.current?.dispose();
    };
  }, []);

  /**
   * Save workflow configuration to backend
   * Serializes graph state (nodes, edges, variables) and sends to API
   * @param flag - Whether to show success message (default: true)
   * @returns Promise that resolves when save is complete
   */
  const handleSave = (flag = true) => {
    if (!graphRef.current || !config) return Promise.resolve()
    return new Promise((resolve, reject) => {
      const params = buildWorkflowSaveParams({ graphRef, config, chatVariables, envVariables, featuresRef })
      saveWorkflowConfig(config.app_id, params as WorkflowConfig)
        .then((res) => {
          if (flag) {
            message.success({ content: t('common.saveSuccess'), duration: 1 })
          }
          const { variables, environment_variables, ...rest } = res as WorkflowConfig
          const initChatVariables = variables.map(v => {
            const { default: _, ...cleanV } = v
            return {
              ...cleanV,
              defaultValue: v.default ?? ''
            }
          })
          setChatVariables(initChatVariables)
          setEnvVariables(environment_variables ?? [])
          setConfig({ ...rest, variables: initChatVariables, environment_variables: environment_variables ?? [] })
          resolve(res)
        }).catch(error => {
          reject(error)
        })
    })
  }

  const clearHistoryRecords = () => {
    setHistoryRecords([])
    lastHistoryRef.current = null
  }

  useEffect(() => {
    if (!graphRef.current) return;
    const nodes = graphRef.current.getNodes();

    // Reset all node execution status on every chatHistory change
    nodes.forEach(node => {
      const data = node.getData();
      node.setData({ ...data, executionStatus: '' });
    });

    const lastAssistant = [...chatHistory].reverse().find(item => item.role === 'assistant');
    if (!lastAssistant?.subContent?.length) return;
    lastAssistant.subContent.forEach(sub => {
      if (typeof sub.status === 'string') {
        const node = nodes.find(n => n.getData()?.id === sub.node_id);
        if (node) {
          node.setData({ ...node.getData(), executionStatus: sub.status });
        }
      }
    });
  }, [chatHistory, graphRef.current]);

  return {
    config,
    setConfig,
    graphRef,
    selectedNode,
    setSelectedNode,
    zoomLevel,
    setZoomLevel,
    isHandMode,
    setIsHandMode,
    onDrop,
    blankClick,
    nodeClick,
    deleteEvent,
    copyEvent,
    parseEvent,
    handleSave,
    chatVariables,
    setChatVariables,
    envVariables,
    setEnvVariables,
    handleAddNotes,
    handleSaveFeaturesConfig,
    features: featuresRef.current,
    getStartNodeVariables,
    canUndo,
    canRedo,
    undo,
    redo,
    historyRecords,
    clearHistoryRecords,
    activeMemoryConfig,
  };
};
