/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:17:48 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-29 18:50:18
 */
import { Clipboard, Graph, Keyboard, MiniMap, Node, Snapline, History, Selection,
  // Scroller,
type Edge } from '@antv/x6';
import { register as registerReactShape } from '@antv/x6-react-shape';
import type { PortMetadata } from '@antv/x6/lib/model/port';
import { App } from 'antd';
import { useEffect, useRef, useState, createElement } from 'react';
import type { RefObject, Dispatch, SetStateAction, MutableRefObject, DragEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import dayjs from 'dayjs';

import { getWorkflowConfig, saveWorkflowConfig } from '@/api/application';
import { useUser } from '@/store/user';
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';
import { conditionNodeHeight, conditionNodeItemHeight, conditionNodePortItemArgsY, defaultAbsolutePortGroups, defaultPortItems, edgeAttrs, edgeHoverTool, edge_color, edge_selected_color, edge_width, graphNodeLibrary, nodeLibrary, nodeRegisterLibrary, nodeWidth, notesConfig, portAttrs, portItemArgsY, portMarkup, portTextAttrs, unknownNode } from '../constant';
import type { ChatVariable, HistoryRecord, NodeProperties, WorkflowConfig } from '../types';
import { calcConditionNodeTotalHeight, getConditionNodeCasePortY } from '../utils';
import { useWorkflowStore } from '@/store/workflow';
import type { Application } from '@/views/ApplicationManagement/types'

const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);

/**
 * Props for useWorkflowGraph hook
 */
export interface UseWorkflowGraphProps {
  /** Reference to the main graph container element */
  containerRef: RefObject<HTMLDivElement>;
  /** Reference to the minimap container element */
  miniMapRef: RefObject<HTMLDivElement>;
  /** Callback when features config is loaded */
  onFeaturesLoad?: (features: FeaturesConfigForm | undefined) => void;
  /** Application type */
  appType?: Application['type'];
}

/**
 * Return type for useWorkflowGraph hook
 */
export interface UseWorkflowGraphReturn {
  /** Current workflow configuration */
  config: WorkflowConfig | null;
  /** Function to update workflow configuration */
  setConfig: Dispatch<SetStateAction<WorkflowConfig | null>>;
  /** Reference to the X6 graph instance */
  graphRef: MutableRefObject<Graph | undefined>;
  /** Currently selected node */
  selectedNode: Node | null;
  /** Function to update selected node */
  setSelectedNode: Dispatch<SetStateAction<Node | null>>;
  /** Current zoom level of the graph */
  zoomLevel: number;
  /** Function to update zoom level */
  setZoomLevel: Dispatch<SetStateAction<number>>;
  /** Whether hand/pan mode is enabled */
  isHandMode: boolean;
  /** Function to toggle hand mode */
  setIsHandMode: Dispatch<SetStateAction<boolean>>;
  /** Handler for dropping nodes onto canvas */
  onDrop: (event: DragEvent) => void;
  /** Handler for clicking blank canvas area */
  blankClick: () => void;
  /** Handler for delete keyboard event */
  deleteEvent: () => boolean | void;
  /** Handler for copy keyboard event */
  copyEvent: () => boolean | void;
  /** Handler for paste keyboard event */
  parseEvent: () => boolean | void;
  /** Whether undo is available */
  canUndo: boolean;
  /** Whether redo is available */
  canRedo: boolean;
  /** Undo last action */
  undo: () => void;
  /** Redo last undone action */
  redo: () => void;
  /** Function to save workflow configuration */
  handleSave: (flag?: boolean) => Promise<unknown>;
  /** Chat variables for workflow */
  chatVariables: ChatVariable[];
  /** Function to update chat variables */
  setChatVariables: Dispatch<SetStateAction<ChatVariable[]>>;

  handleAddNotes: () => void;
  handleSaveFeaturesConfig: (value: FeaturesConfigForm) => void;
  features?: FeaturesConfigForm;
  /** Get start node output variable list (user-defined + system variables) */
  getStartNodeVariables: () => Array<{ name: string; type: string; readonly?: boolean }>;
  nodeClick: ({ node }: { node: Node }) => void;
  /** All recorded history operations */
  historyRecords: HistoryRecord[];
  /** Clear history records */
  clearHistoryRecords: () => void;
  lastExecuteId: string;
}

/**
 * Custom hook for managing workflow graph
 * Handles graph initialization, node/edge operations, and workflow configuration
 * @param props - Hook props containing container references
 * @returns Object containing graph state and handlers
 */
export const useWorkflowGraph = ({
  containerRef,
  miniMapRef,
  onFeaturesLoad,
  appType,
}: UseWorkflowGraphProps): UseWorkflowGraphReturn => {
  // Hooks
  const { id } = useParams();
  const { message } = App.useApp();
  const { t } = useTranslation()
  const { user } = useUser();
  const { chatHistoryMap } = useWorkflowStore()
  const lastExecuteId = Object.keys(chatHistoryMap).at(-1) ?? ''
  const chatHistory = chatHistoryMap[lastExecuteId] ?? []
  console.log('chatHistoryMap', chatHistoryMap, 'lastExecuteId', lastExecuteId)

  // Refs
  const graphRef = useRef<Graph>();

  // State
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [isHandMode, setIsHandMode] = useState(true);
  const isHandModeRef = useRef(true)
  const [config, setConfig] = useState<WorkflowConfig | null>(null);
  const [chatVariables, setChatVariables] = useState<ChatVariable[]>([])
  const featuresRef = useRef<FeaturesConfigForm | undefined>(undefined)
  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)
  const [historyRecords, setHistoryRecords] = useState<HistoryRecord[]>([])
  const lastHistoryRef = useRef<{ cellIds: string[]; timestamp: number; type: string } | null>(null)
  const syncChildRelationshipsRef = useRef<() => void>(() => { })
  const isSyncingRef = useRef(false)
  useEffect(() => {
    if (!graphRef.current) return
    graphRef.current.getNodes().forEach(node => {
      const data = node.getData()
      if (data?.type === 'if-else' || data?.type === 'question-classifier') {
        node.setData({ ...data, chatVariables })
      }
    })
  }, [chatVariables, graphRef.current])

  useEffect(() => {
    if (!appType || !graphRef.current) return
    graphRef.current.getNodes().forEach(node => {
      const data = node.getData()
      node.setData({ ...data, appType })
    })
  }, [appType, graphRef.current])

  useEffect(() => {
    getConfig()
  }, [id])
  /**
   * Fetch workflow configuration from API
   */
  const getConfig = () => {
    if (!id) return
    getWorkflowConfig(id)
      .then(res => {
        const { variables, ...rest } = res as WorkflowConfig
        const initChatVariables = variables.map(v => {
          const { default: _, ...cleanV } = v
          return {
            ...cleanV,
            defaultValue: v.default ?? ''
          }
        })
        setChatVariables(initChatVariables)
        setConfig({ ...rest, variables: initChatVariables })
        featuresRef.current = rest.features
        onFeaturesLoad?.(rest.features)
      })
  }

  useEffect(() => {
    initWorkflow()
  }, [config, graphRef.current])

  /**
   * Assign explicit zIndex values to enforce layer order:
   *   parent nodes (loop/iteration) → child edges → child nodes
   * Ports live inside each node's SVG container and are always above
   * edges once the node zIndex is higher than the edge zIndex.
   */
  const reorderCells = (graph: Graph) => {
    // Safari uses x6-html-shape (dual HTML layer architecture).
    // zIndex controls order within each HTML layer and SVG layer.
    graph.getEdges().forEach(edge => edge.setZIndex(0));
    graph.getNodes().forEach(node => {
      node.setZIndex(node.getData()?.cycle ? 2 : 1);
    });
  };

  /**
   * Initialize workflow graph with nodes and edges from configuration
   */
  const initWorkflow = () => {
    if (!config || !graphRef.current) return
    const { nodes, edges } = config

    if (nodes.length) {
      const nodeList = nodes.map(node => {
        const { id, type, name, position, config = {} } = node
        let nodeLibraryConfig: NodeProperties | undefined = [...nodeLibrary, { nodes: [unknownNode, notesConfig] }]
          .flatMap(category => category.nodes)
          .find(n => n.type === type) as NodeProperties || unknownNode
        nodeLibraryConfig = JSON.parse(JSON.stringify({ ...nodeLibraryConfig, config: nodeLibraryConfig.config || {} }))

        if (nodeLibraryConfig?.config) {
          Object.keys(nodeLibraryConfig.config).forEach(key => {
            if (type === 'trigger' && key === 'time' && nodeLibraryConfig.config) {
              nodeLibraryConfig.config[key].defaultValue = dayjs('12:00 AM', 'h:mm A')
            } else if (type === 'loop' && key === 'condition' && nodeLibraryConfig.config) {
              const { condition } = config;
              console.log('condition', condition)
              nodeLibraryConfig.config[key].defaultValue = condition ? {
                ...condition,
                expressions: (condition as any).expressions.map((expr: any) => {
                  return expr.input_type ? { ...expr, input_type: expr.input_type.toLocaleLowerCase() } : expr
                })
              } : {}
            } else if (type === 'if-else' && key === 'cases' && nodeLibraryConfig.config) {
              const { cases } = config;
              nodeLibraryConfig.config[key].defaultValue = cases && Array.isArray(cases) ? cases.map(item => ({
                ...item,
                expressions: item.expressions.map((expr: any) => {
                  return expr.input_type ? { ...expr, input_type: expr.input_type.toLocaleLowerCase() } : expr
                }),
              })) : []
            } else if (type === 'memory-write' && key === 'message' && nodeLibraryConfig.config) {
              nodeLibraryConfig.config['messages'].defaultValue = [{ role: 'USER', content: config[key] }]
              delete nodeLibraryConfig.config[key]
            } else if (key === 'memory' && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
              const { memory, messages } = config as any;
              if (memory?.enable && messages && messages.length > 0) {
                const lastMessage = messages[messages.length - 1]
                nodeLibraryConfig.config[key].defaultValue = {
                  ...memory,
                  messages: lastMessage.content
                }
                nodeLibraryConfig.config.messages.defaultValue.splice(-1, 1)
              }
            } else if (key === 'knowledge_retrieval' && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
              const { query, ...rest } = config
              nodeLibraryConfig.config[key].defaultValue = {
                ...rest
              }
            } else if (key === 'group_variables' && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
              const { group_variables, group } = config
              nodeLibraryConfig.config[key].defaultValue = group
                ? Object.entries(group_variables as Record<string, any>).map(([key, value]) => ({ key, value }))
                : group_variables
            } else if (type === 'http-request' && (key === 'headers' || key === 'params') && config[key] && typeof config[key] === 'object' && !Array.isArray(config[key]) && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
              nodeLibraryConfig.config[key].defaultValue = Object.entries(config[key]).map(([key, value]) => ({ key, value }))
            } else if (type === 'code' && key === 'code' && config[key] && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
              try {
                nodeLibraryConfig.config[key].defaultValue = decodeURIComponent(atob(config[key] as string))
              } catch {
                nodeLibraryConfig.config[key].defaultValue = config[key]
              }
            } else if (nodeLibraryConfig.config && nodeLibraryConfig.config[key] && config[key]) {
              nodeLibraryConfig.config[key].defaultValue = config[key]
            }
          })
        }

        const nodeConfig = {
          ...(graphNodeLibrary[type] ?? graphNodeLibrary.default),
          id,
          type,
          name,
          data: { ...node, ...nodeLibraryConfig, ...((type === 'if-else' || type === 'question-classifier') ? { chatVariables } : {}) },
          ...position,
        }

        if (type === 'start' && config?.variables && Array.isArray(config.variables)) {
          config?.variables?.forEach(item => {
            item.ui_type = item.ui_type || (item.type === 'string' ? 'text-input' : item.type === 'number' ? 'number' : 'boolean')
          })
        }

        if (type === 'notes') {
          const w = config.width;
          const h = config.height;
          if (w) nodeConfig.width = w as number;
          if (h) nodeConfig.height = h as number;
        }

        // Generate ports dynamically for if-else node based on cases
        if (type === 'if-else' && config.cases && Array.isArray(config.cases)) {
          const totalPorts = config.cases.length + 1; // IF/ELIF + ELSE

          const portItems: PortMetadata[] = [
            defaultPortItems[0],
          ];
          // Add IF/ELIF/ELSE ports
          for (let i = 0; i < totalPorts; i++) {
            portItems.push({
              group: 'right',
              id: `CASE${i + 1}`,
              args: {
                x: nodeWidth,
                y: getConditionNodeCasePortY(config.cases, i),
              },
            });
          }

          nodeConfig.ports = {
            groups: defaultAbsolutePortGroups,
            items: portItems
          };

          nodeConfig.height = calcConditionNodeTotalHeight(config.cases);
        }

        // Generate ports dynamically for question-classifier node based on categories
        if (type === 'question-classifier' && config.categories && Array.isArray(config.categories)) {
          const categoryCount = config.categories.length;
          const newHeight = conditionNodeHeight + (categoryCount - 2) * conditionNodeItemHeight;

          const portItems: PortMetadata[] = [
            defaultPortItems[0]
          ];

          // Add category ports
          config.categories.forEach((_category: any, index: number) => {
            portItems.push({
              group: 'right',
              id: `CASE${index + 1}`,
              args: {
                x: nodeWidth,
                y: portItemArgsY * index + conditionNodePortItemArgsY,
              },
            });
          });

          nodeConfig.ports = {
            groups: defaultAbsolutePortGroups,
            items: portItems
          };

          nodeConfig.height = newHeight;
        }
        // Check error_handle.method config for http-request node
        if (['code', 'http-request', 'llm'].includes(type) && (config as any)?.error_handle?.method === 'branch') {
          nodeConfig.ports = {
            groups: {
              right: { position: 'right', markup: portMarkup, attrs: portAttrs },
              left: { position: 'left', markup: portMarkup, attrs: portAttrs },
            },
            items: [
              defaultPortItems[0],
              { ...defaultPortItems[1], id: 'right' },
              {
                ...defaultPortItems[1],
                args: {
                  x: nodeWidth,
                  y: portItemArgsY + portItemArgsY,
                },
                id: 'ERROR', attrs: { text: { text: t('workflow.config.http-request.errorBranch'), ...portTextAttrs } }
              }
            ]
          };
        }

        return nodeConfig
      })

      // Separate parent nodes and child nodes
      const parentNodes = nodeList.filter(node => !node.data.cycle)
      const childNodes = nodeList.filter(node => node.data.cycle)

      // Add parent nodes first
      graphRef.current?.addNodes(parentNodes)

      // Then process child nodes, use addChild to add to corresponding parent node
      childNodes.forEach(childNode => {
        const cycleId = childNode.data.cycle
        if (cycleId) {
          const parentNode = graphRef.current?.getCellById(cycleId)
          if (parentNode) {
            const addedChild = graphRef.current?.addNode(childNode)
            if (addedChild) {
              parentNode.addChild(addedChild, { silent: true })
            }
          }
        }
      })

      // Adjust parent node size to fit child nodes
      setTimeout(() => {
        const parentNodesWithChildren = parentNodes.filter(parentNode => {
          const parentId = parentNode.data.id
          return childNodes.some(child => child.data.cycle === parentId)
        })

        parentNodesWithChildren.forEach(parentNodeConfig => {
          const parentNode = graphRef.current?.getCellById(parentNodeConfig.data.id)
          if (parentNode) {
            const children = parentNode.getChildren()
            if (children && children.length > 0) {
              const childBounds = children.map(child => child.getBBox())
              const minX = Math.min(...childBounds.map(b => b.x))
              const minY = Math.min(...childBounds.map(b => b.y))
              const maxX = Math.max(...childBounds.map(b => b.x + b.width))
              const maxY = Math.max(...childBounds.map(b => b.y + b.height))

              const padding = 24
              const headerHeight = 50
              const parentBBox = parentNode.getBBox()

              const newWidth = Math.max(parentBBox.width, maxX - minX + padding * 2)
              const newHeight = Math.max(parentBBox.height, maxY - minY + padding * 2 + headerHeight)

              parentNode.prop('size', { width: newWidth, height: newHeight })

              // Update x position of right group ports
              const ports = (parentNode as Node).getPorts()
              ports.forEach(port => {
                if (port.group === 'right' && port.args) {
                  (parentNode as Node).portProp(port.id!, 'args/x', newWidth)
                }
              })
            }
          }
        })
      }, 100)
    }
    if (edges.length) {
      // Deduplication: For if-else and question-classifier nodes, different ports can connect to same node
      const uniqueEdges = edges.filter((edge, index, arr) => {
        return arr.findIndex(e => {
          const sourceCell = graphRef.current?.getCellById(e.source);
          const sourceType = sourceCell?.getData()?.type;
          const isMultiPortNode = sourceType === 'question-classifier' || sourceType === 'if-else';

          if (isMultiPortNode) {
            // Multi-port nodes need to compare source, target and label
            return e.source === edge.source && e.target === edge.target && e.label === edge.label;
          } else {
            // Other nodes only compare source and target
            return e.source === edge.source && e.target === edge.target;
          }
        }) === index;
      });

      const edgeList = uniqueEdges.map(edge => {
        const { source, target, label } = edge
        const sourceCell = graphRef.current?.getCellById(source)
        const targetCell = graphRef.current?.getCellById(target)

        if (sourceCell && targetCell) {
          const sourcePorts = (sourceCell as Node).getPorts()
          const targetPorts = (targetCell as Node).getPorts()

          let sourcePort = sourcePorts.find((port: any) => port.group === 'right')?.id || 'right';

          // If if-else node has label, match corresponding port by label
          if (sourceCell.getData()?.type === 'if-else' && label) {
            // Find matching port ID
            const matchingPort = sourcePorts.find((port: any) => port.id === label);
            if (matchingPort) {
              sourcePort = label;
            }
          }

          // If question-classifier node has label, match corresponding port by label
          if (sourceCell.getData()?.type === 'question-classifier' && label) {
            const matchingPort = sourcePorts.find((port: any) => port.id === label);
            if (matchingPort) {
              sourcePort = label;
            }
          }

          // If http-request node has label, match corresponding port by label
          if (['code', 'http-request', 'llm'].includes(sourceCell.getData()?.type) && label) {
            const matchingPort = sourcePorts.find((port: any) => port.id === label);
            if (matchingPort) {
              sourcePort = label;
            }
          }

          const edgeConfig = {
            source: {
              cell: sourceCell.id,
              port: sourcePort
            },
            target: {
              cell: targetCell.id,
              port: targetPorts.find((port: any) => port.group === 'left')?.id || 'left'
            },
            connector: { name: 'smooth' },
            ...edgeAttrs
            // zIndex: loopIterationCount
          }

          return edgeConfig
        }
        return null
      })
      graphRef.current.addEdges(edgeList.filter(vo => vo !== null))
    }

    // Check if loop/iteration nodes need add-node added
    const parentNodes = graphRef.current.getNodes().filter(node => {
      const type = node.getData()?.type;
      return type === 'loop' || type === 'iteration';
    });

    parentNodes.forEach(parentNode => {
      const parentData = parentNode.getData();
      const allChildren = graphRef.current!.getNodes().filter(n => n.getData()?.cycle === parentData.id);
      const cycleStartNodes = allChildren.filter(n => n.getData()?.type === 'cycle-start');

      // If only cycle-start exists, add add-node
      if (cycleStartNodes.length === 1 && allChildren.length === 1) {
        const cycleStartNode = cycleStartNodes[0];
        const bbox = cycleStartNode.getBBox();
        const addNode = graphRef.current!.addNode({
          ...graphNodeLibrary.addStart,
          x: bbox.x + 84,
          y: bbox.y + 4,
          data: { type: 'add-node', parentId: parentNode.id, cycle: parentData.id, label: t('workflow.addNode'), icon: '+' },
        });
        parentNode.addChild(addNode, { silent: true });
        graphRef.current!.addEdge({
          source: { cell: cycleStartNode.id, port: cycleStartNode.getPorts().find(p => p.group === 'right')?.id || 'right' },
          target: { cell: addNode.id, port: addNode.getPorts().find(p => p.group === 'left')?.id || 'left' },
          ...edgeAttrs,
        });
      }
    });

    graphRef.current.centerContent()
    // Initialize after completion, display nodes in visible area
    if (nodes.length > 0 || edges.length > 0) {
      setTimeout(() => {
        if (graphRef.current) {
          if (isSafari) {
            reorderCells(graphRef.current)
          } else {
            graphRef.current.getNodes().forEach(node => {
              if (!node.getData()?.cycle) node.toFront();
            });
            // Bring edges to front first, then child nodes above edges; parent nodes stay behind
            graphRef.current.getEdges().forEach(edge => {
              const sourceCell = graphRef.current?.getCellById(edge.getSourceCellId());
              const targetCell = graphRef.current?.getCellById(edge.getTargetCellId());
              if (sourceCell?.getData()?.cycle || targetCell?.getData()?.cycle) {
                edge.toFront();
              }
            });
            graphRef.current.getNodes().forEach(node => {
              if (node.getData()?.cycle) node.toFront();
            });
          }
          graphRef.current.enableHistory()
          graphRef.current.cleanHistory()
        }
      }, isSafari ? 0 : 200)
    } else {
      graphRef.current.enableHistory()
      graphRef.current.cleanHistory()
    }
  }

  const resizeGroupNodes = (graph: Graph) => {
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

  const syncChildRelationships = () => {
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
  syncChildRelationshipsRef.current = syncChildRelationships
  /**
   * Setup X6 graph plugins (MiniMap, Snapline, Clipboard, Keyboard)
   */
  const setupPlugins = () => {
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
    // graphRef.current.use(
    //   new Scroller({
    //     enabled: true,
    //     pannable: false,
    //     autoResize: true,
    //   }),
    // );
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
  const nodeClick = ({ node }: { node: Node }) => {
    // add-node type: dispatch port:click to open node selection popover
    // Must handle before blankClick() to avoid blank:click closing the popover immediately
    const nodeData = node.getData()
    if (nodeData?.type === 'add-node') {
      const b = node.getBBox();
      const screenPos = graphRef.current!.localToClient(b.x + b.width, b.y + b.height / 2);
      const tempDiv = document.createElement('div');
      tempDiv.style.cssText = `position:fixed;left:${screenPos.x}px;top:${screenPos.y}px;width:1px;height:1px;z-index:9999;`;
      document.body.appendChild(tempDiv);
      window.dispatchEvent(new CustomEvent('port:click', {
        detail: {
          node,
          port: 'right',
          element: tempDiv,
          rect: { left: screenPos.x, top: screenPos.y },
          edgeInsertion: null,
        },
      }));
      return;
    }

    blankClick()

    setTimeout(() => {
    // Ignore add-node type node clicks
      const nodeData = node.getData()
      if (nodeData.type === 'break' || nodeData.type === 'cycle-start') {
        setSelectedNode(null)
        return;
      }
      clearNodeSelect()
      node.setData({
        ...nodeData,
        isSelected: true,
      });
      clearEdgeSelect()
      if (nodeData.type !== 'notes') {
        setSelectedNode(node);
      }
    }, 0)
  };
  /**
   * Handle edge click event
   * @param edge - Clicked edge
   */
  const edgeClick = ({ edge }: { edge: Edge }) => {
    clearEdgeSelect();
    edge.setAttrByPath('line/stroke', edge_selected_color);
    edge.setData({ ...edge.getData(), isSelected: true }, { silent: true });
    clearNodeSelect();
  };
  /**
   * Clear all selected nodes
   */
  const clearNodeSelect = () => {
    const nodes = graphRef.current?.getNodes();

    nodes?.forEach(node => {
      const data = node.getData();
      if (data.isSelected) {
        node.setData({
          ...data,
          isSelected: false,
        });
      }
    });
    setSelectedNode(null);
  };
  /**
   * Clear all selected edges
   */
  const clearEdgeSelect = () => {
    graphRef.current?.getEdges().forEach(e => {
      e.setData({ ...e.getData(), isSelected: false, isNodeHover: false }, { silent: true });
      e.setAttrByPath('line/stroke', edge_color);
      e.setAttrByPath('line/strokeWidth', edge_width);
    });
  };
  /**
   * Handle blank canvas click - deselect all
   */
  const blankClick = () => {
    clearNodeSelect();
    clearEdgeSelect();
    graphRef.current?.cleanSelection();
    setSelectedNode(null);
    window.dispatchEvent(new CustomEvent('blank:click'));
  };
  /**
   * Handle canvas scale/zoom event
   * @param sx - Scale factor on x-axis
   */
  const scaleEvent = ({ sx }: { sx: number }) => {
    setZoomLevel(sx);
  };
  /**
   * Handle node moved event - restrict child nodes within parent bounds
   * @param node - Moved node
   */
  const nodeMoved = ({ node }: { node: Node }) => {
    const cycle = node.getData()?.cycle;
    if (cycle) {
      const parentNode = graphRef.current!.getNodes().find(n => n.id === cycle);
      const parentType = parentNode?.getData()?.type;
      if (parentNode?.getData()?.isGroup || (parentNode && (parentType === 'loop' || parentType === 'iteration'))) {
        // Get parent node and child node bounding boxes
        const parentBBox = parentNode.getBBox();
        const childBBox = node.getBBox();

        // Calculate parent node padding
        const padding = 24;
        const headerHeight = 50;

        // Calculate minimum and maximum positions allowed for child node
        const minX = parentBBox.x + padding;
        const minY = parentBBox.y + padding + headerHeight;
        const maxX = parentBBox.x + parentBBox.width - padding - childBBox.width;
        const maxY = parentBBox.y + parentBBox.height - padding - childBBox.height;

        // Restrict child node movement within parent node
        let newX = childBBox.x;
        let newY = childBBox.y;

        if (newX < minX) newX = minX;
        if (newY < minY) newY = minY;
        if (newX > maxX) newX = maxX;
        if (newY > maxY) newY = maxY;

        // If child node position is restricted, update its position
        if (newX !== childBBox.x || newY !== childBBox.y) {
          node.setPosition(newX, newY);
        }
      }
    }
  };
  /**
   * Handle copy keyboard shortcut (Ctrl+C / Cmd+C)
   * @returns false to prevent default behavior
   */
  const copyEvent = () => {
    if (!graphRef.current) return false;
    let selectedNodes = []
    if (isHandModeRef.current) {
      selectedNodes = graphRef.current.getNodes().filter(node => node.getData()?.isSelected);
    } else {
     selectedNodes = graphRef.current.getSelectedCells();
    }
    if (selectedNodes.length) {
      graphRef.current.copy(selectedNodes);
    }
    return false;
  };
  /**
   * Handle paste keyboard shortcut (Ctrl+V / Cmd+V)
   * @returns false to prevent default behavior
   */
  const parseEvent = () => {
    if (!graphRef.current?.isClipboardEmpty()) {
      graphRef.current?.startBatch('copy');
      const pastedNodes = graphRef.current?.paste({ offset: 32 }) ?? [];
      pastedNodes.forEach(cell => {
        if (cell.isNode()) {
          const data = cell.getData();
          const newId = `${(data.type as string).replace(/-/g, '_')}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
          cell.setData({ ...data, id: newId });
        }
      });
      blankClick();
      graphRef.current?.stopBatch('copy');
    }
    return false;
  };
  /**
   * Handle delete keyboard shortcut
   * Removes selected nodes, edges, and handles parent-child relationships
   * @returns false to prevent default behavior
   */
  const deleteEvent = () => {
    if (!graphRef.current) return;
    const nodes = graphRef.current?.getNodes();
    const edges = graphRef.current?.getEdges();
    const cells: (Node | Edge)[] = [];
    const nodesToDelete: Node[] = [];
    const parentNodesToUpdate: Node[] = [];

    // First collect all selected nodes, but exclude default child nodes
    nodes?.forEach(node => {
      const data = node.getData();
      // If node is default child node, do not allow individual deletion
      if (data.isSelected && !data.isDefault) {
        nodesToDelete.push(node);
      }
    });

    // Collect edges related to selected nodes
    edges?.forEach(edge => {
      const attrs = edge.getAttrs()
      if (attrs.line.stroke === edge_selected_color) {
        cells.push(edge)
      }
      const sourceId = edge.getSourceCellId();
      const targetId = edge.getTargetCellId();
      if (sourceId && targetId) {
        const sourceNode = nodes?.find(n => n.id === sourceId);
        const targetNode = nodes?.find(n => n.id === targetId);
        if (sourceNode?.getData()?.isSelected || targetNode?.getData()?.isSelected) {
          cells.push(edge);
        }
      }
    })

    // For each selected node
    if (nodesToDelete.length > 0) {
      nodesToDelete.forEach(nodeToDelete => {
        // Check if it's a child node
        const nodeData = nodeToDelete.getData();
        if (nodeData.cycle) {
          // Find corresponding parent node
          const parentNode = nodes?.find(n => n.id === nodeData.cycle);
          if (parentNode) {
            parentNodesToUpdate.push(parentNode);
          }
          // Add child node to deletion list
          cells.push(nodeToDelete);
        }
        // Check if it's LoopNode, IterationNode or SubGraphNode
        else if (nodeToDelete.shape === 'loop-node' || nodeToDelete.shape === 'iteration-node' || nodeToDelete.shape === 'subgraph-node') {
          // Find all child nodes with cycle equal to current node id
          nodes?.forEach(node => {
            const data = node.getData();
            if (data.cycle === nodeToDelete.id || data.cycle === nodeToDelete.getData()?.id) {
              cells.push(node);
            }
          });
          // Add parent node to deletion list
          cells.push(nodeToDelete);
        }
        // Normal node
        else {
          cells.push(nodeToDelete);
        }
      });
      blankClick();
    }

    // Delete all collected nodes and edges
    if (cells.length > 0) {
      // Pre-calculate which parents need an add-node restored (before removal changes the graph)
      const parentsNeedingAddNode = parentNodesToUpdate
        .filter(parentNode => {
          const parentShape = parentNode.shape;
          if (parentShape !== 'loop-node' && parentShape !== 'iteration-node') return false;
          const parentData = parentNode.getData();
          const allChildren = graphRef.current!.getNodes().filter(n => n.getData()?.cycle === parentData.id);
          const cycleStartNodes = allChildren.filter(n => n.getData()?.type === 'cycle-start');
          // After deletion, only cycle-start will remain
          const nonCycleStartToDelete = cells.filter(c =>
            c.isNode() &&
            (c as Node).getData()?.cycle === parentData.id &&
            (c as Node).getData()?.type !== 'cycle-start'
          );
          return cycleStartNodes.length === 1 && (allChildren.length - nonCycleStartToDelete.length) === 1;
        })
        .map(parentNode => ({
          parentNode,
          cycleStartNode: graphRef.current!.getNodes().find(
            n => n.getData()?.cycle === parentNode.getData().id && n.getData()?.type === 'cycle-start'
          )!
        }))
        .filter(({ cycleStartNode }) => !!cycleStartNode);

      graphRef.current?.startBatch('delete');
      graphRef.current?.removeCells(cells);

      parentsNeedingAddNode.forEach(({ parentNode, cycleStartNode }) => {
        const parentData = parentNode.getData();
        const bbox = cycleStartNode.getBBox();
        const addNode = graphRef.current!.addNode({
          ...graphNodeLibrary.addStart,
          x: bbox.x + 84,
          y: bbox.y + 4,
          data: { type: 'add-node', parentId: parentNode.id, cycle: parentData.id, label: t('workflow.addNode'), icon: '+' },
        });
        parentNode.addChild(addNode, { silent: true });
        graphRef.current!.addEdge({
          source: { cell: cycleStartNode.id, port: cycleStartNode.getPorts().find(p => p.group === 'right')?.id || 'right' },
          target: { cell: addNode.id, port: addNode.getPorts().find(p => p.group === 'left')?.id || 'left' },
          ...edgeAttrs,
        });
      });

      graphRef.current?.stopBatch('delete');
    }
    return false;
  };
  const nodePortClickEvent = ({ e, node, port }: { e: MouseEvent, node: Node, port: string }) => {
    e.stopPropagation();
    e.preventDefault();
    const portElement = e.target as HTMLElement;
    const rect = portElement.getBoundingClientRect();
    const clickPort = node.getPorts().find(p => p.id === port)
    const portGroup = clickPort?.group

    if (portGroup === 'left') {
      return
    }

    // Create temporary popover trigger element
    const tempDiv = document.createElement('div');
    tempDiv.style.position = 'fixed';
    tempDiv.style.left = rect.left + 'px';
    tempDiv.style.top = rect.top + 'px';
    tempDiv.style.width = '1px';
    tempDiv.style.height = '1px';
    tempDiv.style.zIndex = '9999';
    document.body.appendChild(tempDiv);

    // Trigger custom event to show node selection popover
    const customEvent = new CustomEvent('port:click', {
      detail: { node, port, element: tempDiv, rect }
    });
    window.dispatchEvent(customEvent);
    clearNodeSelect();
  }

  /**
   * Handle window resize event
   */
  const handleResize = () => {
    if (containerRef.current && graphRef.current) {
      graphRef.current.resize(containerRef.current.offsetWidth, containerRef.current.offsetHeight);
    }
  };

  /**
   * Initialize X6 graph with configuration and event listeners
   */
  const init = async () => {
    if (!containerRef.current || !miniMapRef.current) return;

    // Register React shapes
    // Safari: use x6-html-shape to avoid foreignObject rendering issues
    if (isSafari) {
      const { register: registerHtmlShape } = await import('x6-html-shape');
      nodeRegisterLibrary.forEach(({ shape, width, height, component }) => {
        registerHtmlShape({
          shape,
          width,
          height,
          render(node: Node, _graph: unknown, container: HTMLElement) {
            const root = createRoot(container);
            const doRender = () => {
              root.render(createElement(component as any, { node, graph: node.model?.graph, data: node.getData() }));
            };
            doRender();
            node.on('change:data', doRender);
            return () => {
              node.off('change:data', doRender);
              root.unmount();
            };
          },
        });
      });
    } else {
      nodeRegisterLibrary.forEach((item) => {
        registerReactShape(item);
      });
    }

    const container = containerRef.current;
    graphRef.current = new Graph({
      container,
      background: {
        color: '#F0F3F8',
      },
      autoResize: true,
      grid: {
        visible: true,
        type: 'dot',
        size: 10,
        args: {
          color: '#939AB1', // Grid dot color
          thickness: 1, // Grid dot size
        }
      },
      panning: isHandMode,
      mousewheel: {
        enabled: true,
        factor: 0.1,
        modifiers: null,
      },
      connecting: {
        connector: {
          name: 'smooth',
          args: {
            radius: 8,
          },
        },
        anchor: 'midSide',
        connectionPoint: 'anchor',
        allowBlank: false,
        allowLoop: false,
        allowNode: false,
        allowEdge: false,
        allowPort: true,
        allowMulti: true,
        highlight: true,
        snap: {
          radius: 20,
        },
        createEdge() {
          return graphRef.current?.createEdge(edgeAttrs);
        },
        validateConnection({ sourceCell, targetCell, sourceMagnet, targetMagnet }) {
          if (!targetMagnet) return false;

          // Only allow right port → left port connections
          const getPortGroup = (magnet: Element) => {
            let el: Element | null = magnet;
            while (el) {
              const group = el.getAttribute('port-group');
              if (group) return group;
              el = el.parentElement;
            }
            return null;
          };
          const sourceGroup = sourceMagnet ? getPortGroup(sourceMagnet) : null;
          const targetGroup = targetMagnet ? getPortGroup(targetMagnet) : null;

          if (sourceGroup === 'left' || targetGroup === 'right') return false;

          // Node cannot connect to itself
          if (sourceCell?.id === targetCell?.id) return false;

          const targetType = targetCell?.getData()?.type;

          // Start node cannot be connection target
          if (targetType === 'start') return false;

          // Get source node and target node parent IDs
          const sourceParentId = sourceCell?.getData()?.cycle;
          const targetParentId = targetCell?.getData()?.cycle;

          // Validate parent-child relationship:
          // 1. If both nodes have parent IDs, they must be same to connect
          // 2. If both have no parent ID, can connect normally
          // 3. If one has parent, one doesn't, cannot connect
          if (sourceParentId && targetParentId) {
            // Child nodes under same parent can connect to each other
            if (sourceParentId !== targetParentId) return false;
          } else if (sourceParentId || targetParentId) {
            // One has parent, one doesn't, cannot connect
            return false;
          }

          // Prevent duplicate connections between same ports
          const sourcePortId = sourceMagnet?.getAttribute('port') ?? sourceMagnet?.closest('[port]')?.getAttribute('port');
          const targetPortId = targetMagnet?.getAttribute('port') ?? targetMagnet?.closest('[port]')?.getAttribute('port');
          const duplicate = graphRef.current?.getEdges().some(e =>
            e.getSourceCellId() === sourceCell?.id &&
            e.getTargetCellId() === targetCell?.id &&
            e.getSourcePortId() === sourcePortId &&
            e.getTargetPortId() === targetPortId
          );
          if (duplicate) return false;

          return true;
        },
      },
      embedding: {
        enabled: false,
      },
      translating: {
        restrict(view) {
          if (!view) return null
          const cell = view.cell
          if (cell.isNode()) {
            // Parent (iteration/loop) nodes are not restricted
            if (cell.getData()?.type === 'iteration' || cell.getData()?.type === 'loop') return null
            const parent = cell.getParent()
            if (parent) {
              return parent.getBBox()
            }
          }
          return null
        },
      },
      highlighting: {
        embedding: {
          name: 'stroke',
          args: {
            padding: -1,
            attrs: {
              stroke: '#73d13d',
            },
          },
        },
      },
    });
    // Use plugins
    setupPlugins();
    // Listen to edge mouseenter event: show hover style and add button
    graphRef.current.on('edge:mouseenter', ({ edge }: { edge: Edge }) => {
      setTimeout(() => {
        edge.addTools([edgeHoverTool]);
      }, 0)
    });
    // Listen to edge mouseleave event: revert style and remove add button
    graphRef.current.on('edge:mouseleave', ({ edge }: { edge: Edge }) => {
      const data = edge.getData();
      if (!data?.isSelected) {
        if (data?.isNodeHover) {
          edge.setAttrByPath('line/stroke', edge_selected_color);
        } else {
          edge.setAttrByPath('line/stroke', edge_color);
          edge.setAttrByPath('line/strokeWidth', edge_width);
        }
      }
      edge.removeTools();
    });
    // Listen to node selection event
    graphRef.current.on('node:click', nodeClick);
    // Listen to edge selection event
    graphRef.current.on('edge:click', edgeClick);
    // Listen to port click event
    graphRef.current.on('node:port:click', nodePortClickEvent);
    // Listen to canvas click event, cancel selection
    graphRef.current.on('blank:click', blankClick);
    // Node hover: highlight connected edges
    graphRef.current.on('node:mouseenter', ({ node }) => {
      graphRef.current?.getEdges().forEach(edge => {
        const view = graphRef.current?.findViewByCell(edge);
        view?.removeTools();
        if (!edge.getData()?.isSelected && edge.getAttrByPath('line/stroke') === edge_selected_color) {
          edge.setAttrByPath('line/stroke', edge_color);
        }
      });
      graphRef.current?.getConnectedEdges(node).forEach(edge => {
        if (!edge.getData()?.isSelected) {
          edge.setAttrByPath('line/stroke', edge_selected_color);
          edge.setData({ ...edge.getData(), isNodeHover: true }, { silent: true });
        }
      });
    });
    graphRef.current.on('node:mouseleave', ({ node }) => {
      graphRef.current?.getConnectedEdges(node).forEach(edge => {
        if (!edge.getData()?.isSelected) {
          edge.setAttrByPath('line/stroke', edge_color);
          edge.setData({ ...edge.getData(), isNodeHover: false }, { silent: true });
        }
      });
    });
    // Listen to zoom event
    graphRef.current.on('scale', scaleEvent);
    // Listen to node move event
    graphRef.current.on('node:moved', nodeMoved);

    if (isSafari) {
      // When a parent (loop/iteration) node moves, keep child nodes in sync.
      // Store each child's offset relative to the parent at drag start, then
      // reapply it every frame to avoid cumulative delta errors.
      const dragOffsets = new Map<string, { dx: number; dy: number }>();

      graphRef.current.on('node:moving', ({ node }: { node: Node }) => {
        const data = node.getData();
        if (data?.type !== 'loop' && data?.type !== 'iteration') return;
        const pos = node.getPosition();
        const PORT_RADIUS = 6;

        // Update parent componentContainer directly
        const parentView = graphRef.current?.findViewByCell(node) as any;
        if (parentView?.componentContainer) {
          parentView.componentContainer.style.transform =
            `translate(${pos.x + PORT_RADIUS}px, ${pos.y}px)`;
        }

        const children = graphRef.current?.getNodes().filter(child => {
          const cycle = child.getData()?.cycle;
          return cycle === data.id || cycle === node.id;
        }) ?? [];

        // First event for this drag: record offsets
        if (!dragOffsets.has(node.id)) {
          children.forEach(child => {
            const cp = child.getPosition();
            dragOffsets.set(child.id, { dx: cp.x - pos.x, dy: cp.y - pos.y });
          });
        }

        // Apply stored offsets to keep children in place relative to parent
        children.forEach(child => {
          const off = dragOffsets.get(child.id);
          if (!off) return;
          const nx = pos.x + off.dx;
          const ny = pos.y + off.dy;
          child.setPosition(nx, ny);
          const childView = graphRef.current?.findViewByCell(child) as any;
          if (childView?.componentContainer) {
            childView.componentContainer.style.transform =
              `translate(${nx + PORT_RADIUS}px, ${ny}px)`;
          }
        });
      });

      graphRef.current.on('node:moved', ({ node }: { node: Node }) => {
        // Clear offsets for this parent and all its children
        const data = node.getData();
        graphRef.current?.getNodes().forEach(child => {
          const cycle = child.getData()?.cycle;
          if (cycle === data?.id || cycle === node.id) dragOffsets.delete(child.id);
        });
        dragOffsets.delete(node.id);
        nodeMoved({ node });
      });
    }

    graphRef.current.on('node:removed', blankClick)
    // When edge connected, reorder all cells to maintain correct layer order
    graphRef.current.on('edge:connected', ({ isNew, edge }) => {
      if (isSafari && isNew && graphRef.current) {
        reorderCells(graphRef.current);
      } else if (!isSafari && isNew) {
        const sourceCellId = edge.getSourceCellId()
        const targetCellId = edge.getTargetCellId()
        const sourceCell = graphRef.current?.getCellById(sourceCellId);
        const targetCell = graphRef.current?.getCellById(targetCellId);

        sourceCell?.toFront();
        targetCell?.toFront()
        if (['loop', 'iteration'].includes(sourceCell?.getData()?.type)) {
          graphRef.current?.getEdges().forEach(edge => {
            const edgeSourceCell = graphRef.current?.getCellById(edge.getSourceCellId());
            const edgeTargetCell = graphRef.current?.getCellById(edge.getTargetCellId());
            if (edgeSourceCell?.getData()?.cycle === sourceCellId || edgeTargetCell?.getData()?.cycle === sourceCellId) {
              edge.toFront();
            }
          });
          graphRef.current?.getNodes().forEach(node => {
            if (node.getData()?.cycle === sourceCellId) node.toFront();
          });
        }
        if (['loop', 'iteration'].includes(targetCell?.getData()?.type)) {
          graphRef.current?.getEdges().forEach(edge => {
            const edgeSourceCell = graphRef.current?.getCellById(edge.getSourceCellId());
            const edgeTargetCell = graphRef.current?.getCellById(edge.getTargetCellId());
            if (edgeSourceCell?.getData()?.cycle === targetCellId || edgeTargetCell?.getData()?.cycle === targetCellId) {
              edge.toFront();
            }
          });
          graphRef.current?.getNodes().forEach(node => {
            if (node.getData()?.cycle === targetCellId) node.toFront();
          });
        }
      }
    });

    // During edge dragging, manually detect port hover since the dragging edge blocks mouse events
    let lastHoveredPort: { node: Node; portId: string } | null = null;
    graphRef.current.on('edge:mousemove', ({ e }: { e: MouseEvent }) => {
      if (!graphRef.current) return;
      const { clientX, clientY } = e;
      let found: { node: Node; portId: string } | null = null;

      for (const node of graphRef.current.getNodes()) {
        for (const port of node.getPorts().filter(p => p.group === 'right')) {
          const portView = graphRef.current.findViewByCell(node);
          if (!portView) continue;
          const portEl = (portView as any).findPortElem(port.id!, 'body') as SVGElement | null;
          if (!portEl) continue;
          const rect = portEl.getBoundingClientRect();
          const hitRadius = 16;
          const cx = rect.left + rect.width / 2;
          const cy = rect.top + rect.height / 2;
          if (Math.abs(clientX - cx) <= hitRadius && Math.abs(clientY - cy) <= hitRadius) {
            found = { node, portId: port.id! };
            break;
          }
        }
        if (found) break;
      }

      lastHoveredPort = found;
    });
    graphRef.current.on('edge:mouseup', () => { lastHoveredPort = null; });
    // Listen to copy keyboard event
    graphRef.current.bindKey(['ctrl+c', 'cmd+c'], copyEvent);
    // Listen to paste keyboard event
    graphRef.current.bindKey(['ctrl+v', 'cmd+v'], parseEvent);
    // Delete selected nodes and edges
    graphRef.current.bindKey(['ctrl+d', 'cmd+d', 'delete', 'backspace'], deleteEvent);
    // Undo / Redo
    graphRef.current.bindKey(['ctrl+z', 'cmd+z'], () => { undo(); return false; });
    graphRef.current.bindKey(['ctrl+y', 'cmd+y', 'ctrl+shift+z', 'cmd+shift+z'], () => { redo(); return false; });

  };

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
  /**
   * Save workflow configuration to backend
   * Serializes graph state (nodes, edges, variables) and sends to API
   * @param flag - Whether to show success message (default: true)
   * @returns Promise that resolves when save is complete
   */
  const handleSave = (flag = true) => {
    if (!graphRef.current || !config) return Promise.resolve()
    return new Promise((resolve, reject) => {
      const nodes = graphRef.current?.getNodes().filter((node: Node) => {
        const nodeData = node.getData();
        return nodeData?.type !== 'add-node';
      }) || [];
      const edges = graphRef.current?.getEdges() || []

      const params = {
        ...config,
        features: featuresRef.current,
        variables: chatVariables.map(v => {
          const { defaultValue, ...cleanV } = v
          return {
            ...cleanV,
            default: defaultValue ?? ''
          }
        }),
        nodes: nodes.map((node: Node) => {
          const data = node.getData();
          const position = node.getPosition();
          let itemConfig: Record<string, any> = {}

          if (data.config) {
            Object.keys(data.config).forEach(key => {
              if (data.type === 'trigger' && key === 'time' && data.config[key] && 'defaultValue' in data.config[key]) {
                itemConfig[key] = dayjs(data.config[key].defaultValue, 'h:mm A').format('h:mm A')
              } else if (data.type === 'code' && key === 'code' && data.config[key] && 'defaultValue' in data.config[key]) {
                const code = data.config[key].defaultValue || ''
                itemConfig = {
                  ...itemConfig,
                  code: btoa(encodeURIComponent(code || ''))
                }
              } else if (key === 'memory' && data.config[key] && 'defaultValue' in data.config[key]) {
                const { messages, ...rest } = data.config[key].defaultValue
                let memoryMessage = { role: 'USER', content: data.config[key].defaultValue.messages }
                itemConfig = {
                  ...itemConfig,
                  messages: rest.enable ? [...itemConfig.messages, memoryMessage] : itemConfig.messages,
                  memory: { ...rest },
                }
              } else if (data.config[key] && 'defaultValue' in data.config[key] && key === 'group_variables') {
                let group_variables = data.config.group.defaultValue ? {} : data.config[key].defaultValue
                if (data.config.group.defaultValue) {
                  data.config[key].defaultValue.map((vo: any) => {
                    group_variables[vo.key] = vo.value
                  })
                }
                itemConfig[key] = group_variables
              } else if (data.config[key] && 'defaultValue' in data.config[key] && key === 'group_type') {
                let group = data.config.group.defaultValue
                let group_type = group ? {} : data.config[key].defaultValue
                let group_variables = data.config.group_variables.defaultValue

                if (group) {
                  group_variables.forEach((item: any, index: number) => {
                    group_type[item.key] = data.config[key].defaultValue[index] || data.config[key].defaultValue[item.key]
                  })
                }

                itemConfig[key] = group_type
              } else if (data.type === 'http-request' && (key === 'headers' || key === 'params') && data.config[key] && 'defaultValue' in data.config[key]) {
                const value = data.config[key].defaultValue
                itemConfig[key] = {}
                if (value.length > 0) {
                  value.forEach((vo: any) => {
                    itemConfig[key][vo.key] = vo.value
                  })
                }
              } else if (data.type === 'http-request' && key === 'body' && data.config[key] && 'defaultValue' in data.config[key]) {
                const value = data.config[key].defaultValue
                itemConfig[key] = value
                if (value.content_type === 'json' && value.data && value.data !== '') {
                  itemConfig[key].data = value.data.replace(/\u00a0/g, ' ')
                } else {
                  itemConfig[key].data = value.data
                }
              } else if (data.config[key] && 'defaultValue' in data.config[key] && key !== 'knowledge_retrieval') {
                itemConfig[key] = data.config[key].defaultValue
              } else if (key === 'knowledge_retrieval' && data.config[key] && 'defaultValue' in data.config[key]) {
                const { knowledge_bases } = data.config[key].defaultValue || {}
                itemConfig = {
                  ...itemConfig,
                  ...(data.config[key].defaultValue || {}),
                  knowledge_bases: knowledge_bases?.map((vo: any) => {
                    const kb_config = vo.config || vo
                    return { kb_id: vo.kb_id || vo.id, ...kb_config, }
                  })
                }
              }
            })
          }

          return {
            id: data.id || node.id,
            type: data.type,
            name: data.name,
            cycle: data.cycle, // Save cycle parameter
            position: {
              x: position.x,
              y: position.y,
            },
            config: itemConfig
          };
        }),
        edges: edges.map((edge: Edge) => {
          const sourceCell = graphRef.current?.getCellById(edge.getSourceCellId());
          const targetCell = graphRef.current?.getCellById(edge.getTargetCellId());
          const sourcePortId = edge.getSourcePortId();

          // Filter invalid edges: source or target node doesn't exist, or is add-node type
          if (!sourceCell?.getData()?.id || !targetCell?.getData()?.id ||
            sourceCell?.getData()?.type === 'add-node' || targetCell?.getData()?.type === 'add-node') {
            return null;
          }

          // If if-else node right port connection, add label
          if (sourceCell?.getData()?.type === 'if-else' && sourcePortId?.startsWith('CASE')) {
            return {
              source: sourceCell.getData().id,
              target: targetCell?.getData().id,
              label: sourcePortId,
            };
          }

          // If question-classifier node right port connection, add label
          if (sourceCell?.getData()?.type === 'question-classifier' && sourcePortId?.startsWith('CASE')) {
            return {
              source: sourceCell.getData().id,
              target: targetCell?.getData().id,
              label: sourcePortId,
            };
          }

          // If http-request node right port connection, add label
          if (['code', 'http-request', 'llm'].includes(sourceCell?.getData()?.type)) {
            if (sourcePortId === 'ERROR') {
              return {
                source: sourceCell.getData().id,
                target: targetCell?.getData().id,
                label: 'ERROR',
              };
            } else {
              return {
                source: sourceCell.getData().id,
                target: targetCell?.getData().id,
                label: 'SUCCESS',
              };
            }
          }

          return {
            source: sourceCell?.getData().id,
            target: targetCell?.getData().id,
          };
        })
          .filter(edge => edge !== null)
          .filter((edge, index, arr) => {
            // Deduplication: For if-else and question-classifier nodes, different ports can connect to same node
            return arr.findIndex(e => {
              if (!e || !edge) return false;
              const sourceCell = graphRef.current?.getCellById(e.source);
              const sourceType = sourceCell?.getData()?.type;
              const isMultiPortNode = sourceType === 'question-classifier' || sourceType === 'if-else';

              if (isMultiPortNode) {
                // Multi-port nodes need to compare source, target and label
                return e.source === edge.source && e.target === edge.target && e.label === edge.label;
              } else {
                // Other nodes only compare source and target
                return e.source === edge.source && e.target === edge.target;
              }
            }) === index;
          }),
      }
      saveWorkflowConfig(config.app_id, params as WorkflowConfig)
        .then((res) => {
          if (flag) {
            message.success({ content: t('common.saveSuccess'), duration: 1 })
          }
          resolve(res)
        }).catch(error => {
          reject(error)
        })
    })
  }

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
    onFeaturesLoad?.(value)

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
    lastExecuteId,
  };
};
