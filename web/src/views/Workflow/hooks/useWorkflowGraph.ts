import { Graph, Node } from '@antv/x6';
import { App } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import { getWorkflowConfig } from '@/api/application';
import { getMemoryConfigList } from '@/api/memory';
import { useUser } from '@/store/user';
import { useWorkflowStore } from '@/store/workflow';
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';
import type { Memory } from '@/views/MemoryManagement/types';
import type { ChatVariable, EnvVariable, HistoryRecord, WorkflowConfig, UseWorkflowGraphProps, UseWorkflowGraphReturn } from '../types';
import { createWorkflowGraphLoad } from './workflowGraphLoad';
import { createWorkflowGraphRelations } from './workflowGraphRelations';
import { createWorkflowGraphPlugins } from './workflowGraphPlugins';
import { createWorkflowGraphEvents } from './workflowGraphEvents';
import { createWorkflowGraphInit } from './workflowGraphInit';
import { createWorkflowGraphPersistence } from './workflowGraphPersistence';
import { createWorkflowGraphHistory } from './workflowGraphHistory';

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
    // Runtime context refreshes are not user edits and must not enter undo history.
    graphRef.current.getNodes().forEach(node => {
      const data = node.getData()
      if (['if-else', 'question-classifier', 'human-intervention'].includes(data?.type)) {
        node.setData({ ...data, chatVariables, envVariables }, { dryrun: true })
      }
      if (['memory-read', 'memory-write'].includes(data?.type)) {
        node.setData({ ...data, activeMemoryConfig }, { dryrun: true })
      }
    })
  }, [chatVariables, envVariables, activeMemoryConfig, graphRef.current?.getNodes()])

  useEffect(() => {
    if (!appType || !graphRef.current) return
    graphRef.current.getNodes().forEach(node => {
      const data = node.getData()
      node.setData({ ...data, appType }, { dryrun: true })
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

  console.log('workflowInitializedRef', workflowInitializedRef.current)
  useEffect(() => {
    if (!config || !graphRef.current) return
    if (workflowInitializedRef.current) return
    workflowInitializedRef.current = true
    initWorkflow()
  }, [config, graphRef.current])

  /**
   * Assign explicit zIndex values to enforce layer order:
   *   parent nodes (loop/iteration) → child edges → child nodes
   * Ports live inside each node's SVG container and are always above
   * edges once the node zIndex is higher than the edge zIndex.
   */
  const { reorderCells, initWorkflow } = createWorkflowGraphLoad({ graphRef, config, chatVariables, envVariables, t });

  const { syncChildRelationships } = createWorkflowGraphRelations({ graphRef });
  syncChildRelationshipsRef.current = syncChildRelationships
  const { handleAddNotes, getStartNodeVariables, clearHistoryRecords, undo, redo, handleSaveFeaturesConfig } = createWorkflowGraphHistory({ graphRef, featuresRef, setHistoryRecords, lastHistoryRef, isSyncingRef, syncChildRelationships, user, t });
  /**
   * Setup X6 graph plugins (MiniMap, Snapline, Clipboard, Keyboard)
   */
  const { setupPlugins } = createWorkflowGraphPlugins({ graphRef, miniMapRef, lastHistoryRef, syncChildRelationshipsRef, isSyncingRef, setCanUndo, setCanRedo, setHistoryRecords });

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
  // 显示/隐藏连接桩
  // const showPorts = (show: boolean) => {
  //   const container = containerRef.current!;
  //   const ports = container.querySelectorAll('.x6-port-body') as NodeListOf<SVGElement>;
  //   for (let i = 0, len = ports.length; i < len; i += 1) {
  //     ports[i].style.visibility = show ? 'visible' : 'hidden';
  //   }
  // };
  /**
   * Handle node click event
   * @param node - Clicked node
   */
  const { nodeClick, edgeClick, blankClick, scaleEvent, nodeMoved, copyEvent, parseEvent, deleteEvent, nodePortClickEvent, handleResize } = createWorkflowGraphEvents({ graphRef, setRunOpen, setSelectedNode, setZoomLevel, isHandModeRef, containerRef, t });

  /**
   * Initialize X6 graph with configuration and event listeners
   */
  const { init } = createWorkflowGraphInit({ containerRef, miniMapRef, graphRef, isHandMode, reorderCells, setupPlugins, nodeClick, edgeClick, blankClick, scaleEvent, nodeMoved, copyEvent, parseEvent, deleteEvent, nodePortClickEvent, undo, redo });

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
   * Handle node drop event from drag-and-drop
   * Creates new node at drop position
   * @param event - React drag event
   */
  const { onDrop, handleSave } = createWorkflowGraphPersistence({ graphRef, config, setConfig, chatVariables, setChatVariables, envVariables, setEnvVariables, featuresRef, message, t });

  useEffect(() => {
    if (!graphRef.current) return;
    const nodes = graphRef.current.getNodes();

    // Reset all node execution status on every chatHistory change
    nodes.forEach(node => {
      const data = node.getData();
      node.setData({ ...data, executionStatus: '' }, { dryrun: true });
    });

    const lastAssistant = [...chatHistory].reverse().find(item => item.role === 'assistant');
    if (!lastAssistant?.subContent?.length) return;
    lastAssistant.subContent.forEach(sub => {
      if (typeof sub.status === 'string') {
        const node = nodes.find(n => n.getData()?.id === sub.node_id);
        if (node) {
          node.setData({ ...node.getData(), executionStatus: sub.status }, { dryrun: true });
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
