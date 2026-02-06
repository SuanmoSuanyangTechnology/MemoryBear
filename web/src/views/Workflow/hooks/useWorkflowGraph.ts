/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:17:48 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 15:17:48 
 */
import { useRef, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { App } from 'antd'
import { Graph, Node, MiniMap, Snapline, Clipboard, Keyboard, type Edge } from '@antv/x6';
import { register } from '@antv/x6-react-shape';
import type { PortMetadata } from '@antv/x6/lib/model/port';

import { nodeRegisterLibrary, graphNodeLibrary, nodeLibrary, portMarkup, portAttrs, edgeAttrs, edge_color, edge_selected_color, portArgs } from '../constant';
import type { WorkflowConfig, NodeProperties, ChatVariable } from '../types';
import { getWorkflowConfig, saveWorkflowConfig } from '@/api/application'

/**
 * Props for useWorkflowGraph hook
 */
export interface UseWorkflowGraphProps {
  /** Reference to the main graph container element */
  containerRef: React.RefObject<HTMLDivElement>;
  /** Reference to the minimap container element */
  miniMapRef: React.RefObject<HTMLDivElement>;
}

/**
 * Return type for useWorkflowGraph hook
 */
export interface UseWorkflowGraphReturn {
  /** Current workflow configuration */
  config: WorkflowConfig | null;
  /** Function to update workflow configuration */
  setConfig: React.Dispatch<React.SetStateAction<WorkflowConfig | null>>;
  /** Reference to the X6 graph instance */
  graphRef: React.MutableRefObject<Graph | undefined>;
  /** Currently selected node */
  selectedNode: Node | null;
  /** Function to update selected node */
  setSelectedNode: React.Dispatch<React.SetStateAction<Node | null>>;
  /** Current zoom level of the graph */
  zoomLevel: number;
  /** Function to update zoom level */
  setZoomLevel: React.Dispatch<React.SetStateAction<number>>;
  /** Whether hand/pan mode is enabled */
  isHandMode: boolean;
  /** Function to toggle hand mode */
  setIsHandMode: React.Dispatch<React.SetStateAction<boolean>>;
  /** Handler for dropping nodes onto canvas */
  onDrop: (event: React.DragEvent) => void;
  /** Handler for clicking blank canvas area */
  blankClick: () => void;
  /** Handler for delete keyboard event */
  deleteEvent: () => boolean | void;
  /** Handler for copy keyboard event */
  copyEvent: () => boolean | void;
  /** Handler for paste keyboard event */
  parseEvent: () => boolean | void;
  /** Function to save workflow configuration */
  handleSave: (flag?: boolean) => Promise<unknown>;
  /** Chat variables for workflow */
  chatVariables: ChatVariable[];
  /** Function to update chat variables */
  setChatVariables: React.Dispatch<React.SetStateAction<ChatVariable[]>>;
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
}: UseWorkflowGraphProps): UseWorkflowGraphReturn => {
  // Hooks
  const { id } = useParams();
  const { message } = App.useApp();
  const { t } = useTranslation()
  
  // Refs
  const graphRef = useRef<Graph>();
  
  // State
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [isHandMode, setIsHandMode] = useState(true);
  const [config, setConfig] = useState<WorkflowConfig | null>(null);
  const [chatVariables, setChatVariables] = useState<ChatVariable[]>([])

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
      })
  }

  useEffect(() => {
    initWorkflow()
  }, [config, graphRef.current])
  
  /**
   * Initialize workflow graph with nodes and edges from configuration
   */
  const initWorkflow = () => {
    if (!config || !graphRef.current) return
    const { nodes, edges } = config

    if (nodes.length) {
      const nodeList = nodes.map(node => {
        const { id, type, name, position, config = {} } = node
        let nodeLibraryConfig = [...nodeLibrary]
          .flatMap(category => category.nodes)
          .find(n => n.type === type)
        nodeLibraryConfig = JSON.parse(JSON.stringify({ config: {}, ...nodeLibraryConfig })) as NodeProperties

        if (nodeLibraryConfig?.config) {
          Object.keys(nodeLibraryConfig.config).forEach(key => {
            if (key === 'memory' && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
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
              nodeLibraryConfig.config[key].defaultValue = Object.entries(config[key]).map(([name, value]) => ({ name, value }))
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
          data: { ...node, ...nodeLibraryConfig},
          ...position,
        }
        
        // Generate ports dynamically for if-else node based on cases
        if (type === 'if-else' && config.cases && Array.isArray(config.cases)) {
          const caseCount = config.cases.length;
          const totalPorts = caseCount + 1; // IF/ELIF + ELSE
          const baseHeight = 88;
          const newHeight = baseHeight + (totalPorts - 2) * 30;
          
          const portItems: PortMetadata[] = [
            { group: 'left' },
            { group: 'right', id: 'CASE1', args: portArgs, attrs: { text: { text: 'IF', fontSize: 12, fill: '#5B6167' }} }
          ];
          
          // Add ELIF ports
          for (let i = 1; i < caseCount; i++) {
            portItems.push({
              group: 'right',
              id: `CASE${i + 1}`,
              args: portArgs,
              attrs: { text: { text: 'ELIF', fontSize: 12, fill: '#5B6167' }}
            });
          }
          
          // Add ELSE port
          portItems.push({
            group: 'right',
            id: `CASE${caseCount + 1}`,
            args: portArgs,
            attrs: { text: { text: 'ELSE', fontSize: 12, fill: '#5B6167' }}
          });
          
          nodeConfig.ports = {
            groups: {
              right: { position: 'right', markup: portMarkup, attrs: portAttrs },
              left: { position: 'left', markup: portMarkup, attrs: portAttrs },
            },
            items: portItems
          };
          
          nodeConfig.height = newHeight;
        }
        
        // Generate ports dynamically for question-classifier node based on categories
        if (type === 'question-classifier' && config.categories && Array.isArray(config.categories)) {
          const categoryCount = config.categories.length;
          const baseHeight = 88;
          const newHeight = baseHeight + (categoryCount - 1) * 30;
          
          const portItems: PortMetadata[] = [
            { group: 'left' }
          ];
          
          // Add category ports
          config.categories.forEach((_category: any, index: number) => {
            portItems.push({
              group: 'right',
              id: `CASE${index + 1}`,
              args: portArgs,
              attrs: { text: { text: `分类${index + 1}`, fontSize: 12, fill: '#5B6167' }}
            });
          });
          
          nodeConfig.ports = {
            groups: {
              right: { position: 'right', markup: portMarkup, attrs: portAttrs },
              left: { position: 'left', markup: portMarkup, attrs: portAttrs },
            },
            items: portItems
          };
          
          nodeConfig.height = newHeight;
        }
        
        // Check error_handle.method config for http-request node
        if (type === 'http-request' && (config as any).error_handle?.method === 'branch') {
          nodeConfig.ports = {
            groups: {
              right: { position: 'right', markup: portMarkup, attrs: portAttrs },
              left: { position: 'left', markup: portMarkup, attrs: portAttrs },
            },
            items: [
              { group: 'left' },
              { group: 'right', id: 'right' },
              { group: 'right', id: 'ERROR', attrs: { text: { text: t('workflow.config.http-request.errorBranch'), fontSize: 12, fill: '#5B6167' }}}
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
              parentNode.addChild(addedChild)
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

              console.log('newWidth', newHeight, newWidth)
              
              parentNode.prop('size', { width: newWidth, height: newHeight })
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
          if (sourceCell.getData()?.type === 'http-request' && label) {
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
    
    // Initialize after completion, display nodes in visible area
    if (nodes.length > 0 || edges.length > 0) {
      setTimeout(() => {
        if (graphRef.current) {
          graphRef.current.centerContent()
        }
      }, 200)
    }
  }
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
  };
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
    // Ignore add-node type node clicks
    if (node.getData()?.type === 'add-node' || node.getData().type === 'break' || node.getData().type === 'cycle-start') {
      setSelectedNode(null)
      return;
    }
    
    const nodes = graphRef.current?.getNodes();

    nodes?.forEach(vo => {
      const data = vo.getData();
      if (data.isSelected) {
        vo.setData({
          ...data,
          isSelected: false,
        });
      }
    });
    node.setData({
      ...node.getData(),
      isSelected: true,
    });
    setSelectedNode(node);
  };
  /**
   * Handle edge click event
   * @param edge - Clicked edge
   */
  const edgeClick = ({ edge }: { edge: Edge }) => {
    edge.setAttrByPath('line/stroke', edge_selected_color);
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
      e.setAttrByPath('line/stroke', edge_color);
      e.setAttrByPath('line/strokeWidth', 1);
    });
  };
  /**
   * Handle blank canvas click - deselect all
   */
  const blankClick = () => {
    clearNodeSelect();
    clearEdgeSelect();
    graphRef.current?.cleanSelection();
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
      if (parentNode?.getData()?.isGroup) {
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
    const selectedNodes = graphRef.current.getNodes().filter(node => node.getData()?.isSelected);
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
      graphRef.current?.paste({ offset: 32 });
      blankClick();
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
            // Use removeChild method to delete child node
            parentNode.removeChild(nodeToDelete);
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
      graphRef.current?.removeCells(cells);
    }
    return false;
  };

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
  const init = () => {
    if (!containerRef.current || !miniMapRef.current) return;

    // Register React shapes
    nodeRegisterLibrary.forEach((item) => {
      register(item);
    });

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
        validateConnection({ sourceCell, targetCell, targetMagnet }) {
          if (!targetMagnet) return false;
          
          // Node cannot connect to itself
          if (sourceCell?.id === targetCell?.id) return false;
          
          const sourceType = sourceCell?.getData()?.type;
          const targetType = targetCell?.getData()?.type;
          
          // Start node cannot be connection target
          if (targetType === 'start') return false;
          
          // End node cannot be connection source
          if (sourceType === 'end') return false;
          
          // Get source node and target node parent IDs
          const sourceParentId = sourceCell?.getData()?.cycle;
          const targetParentId = targetCell?.getData()?.cycle;
          
          // Validate parent-child relationship:
          // 1. If both nodes have parent IDs, they must be same to connect
          // 2. If both have no parent ID, can connect normally
          // 3. If one has parent, one doesn't, cannot connect
          console.log('sourceParentId', sourceParentId, targetParentId)
          if (sourceParentId && targetParentId) {
            // Child nodes under same parent can connect to each other
            return sourceParentId === targetParentId;
          } else if (sourceParentId || targetParentId) {
            // One has parent, one doesn't, cannot connect
            return false;
          }
          
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
    // Listen to edge mouseleave event
    graphRef.current.on('edge:mouseleave', ({ edge }: { edge: Edge }) => {
      if (edge.getAttrByPath('line/stroke') !== edge_selected_color) {
        edge.setAttrByPath('line/stroke', edge_color);
        edge.setAttrByPath('line/strokeWidth', 1);
      }
    });
    // Listen to node selection event
    graphRef.current.on('node:click', nodeClick);
    // Listen to edge selection event
    graphRef.current.on('edge:click', edgeClick);
    // Listen to port click event
    graphRef.current.on('node:port:click', ({ e, node, port }: { e: MouseEvent, node: Node, port: string }) => {
      e.stopPropagation();
      const portElement = e.target as HTMLElement;
      const rect = portElement.getBoundingClientRect();
      
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
    });
    // Listen to canvas click event, cancel selection
    graphRef.current.on('blank:click', blankClick);
    // Listen to zoom event
    graphRef.current.on('scale', scaleEvent);
    // Listen to node move event
    graphRef.current.on('node:moved', nodeMoved);
    // Listen to copy keyboard event
    graphRef.current.bindKey(['ctrl+c', 'cmd+c'], copyEvent);
    // Listen to paste keyboard event
    graphRef.current.bindKey(['ctrl+v', 'cmd+v'], parseEvent);
    // Delete selected nodes and edges
    graphRef.current.bindKey(['ctrl+d', 'cmd+d', 'delete', 'backspace'], deleteEvent);

  };

  useEffect(() => {
    if (!containerRef.current || !miniMapRef.current) return;
    init();

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      graphRef.current?.dispose();
    };
  }, []);

  /**
   * Handle node drop event from drag-and-drop
   * Creates new node at drop position
   * @param event - React drag event
   */
  const onDrop = (event: React.DragEvent) => {
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
    
    // Create clean node data, only keep necessary fields
    const cleanNodeData = {
      id: `${dragData.type.replace(/-/g, '_')}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      name: t(`workflow.${dragData.type}`),
      ...nodeLibraryConfig
    };
    
    if (dragData.type === 'loop' || dragData.type === 'iteration') {
      graphRef.current.addNode({
        ...graphNodeLibrary[dragData.type],
        x: point.x - 150,
        y: point.y - 100,
        id: cleanNodeData.id,
        data: { ...cleanNodeData, isGroup: true },
      });
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
              if (data.type === 'code' && key === 'code' && data.config[key] && 'defaultValue' in data.config[key]) {
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
                    itemConfig[key][vo.name] = vo.value
                  })
                }
              } else if (data.config[key] && 'defaultValue' in data.config[key] && key !== 'knowledge_retrieval') {
                itemConfig[key] = data.config[key].defaultValue
              } else if (key === 'knowledge_retrieval' && data.config[key] && 'defaultValue' in data.config[key]) {
                const { knowledge_bases } = data.config[key].defaultValue || {}
                itemConfig = {
                  ...itemConfig,
                  ...(data.config[key].defaultValue || {}),
                  knowledge_bases: knowledge_bases?.map((vo: any) => {
                    const kb_config = vo.config || { similarity_threshold: vo.similarity_threshold, retrieve_type: vo.retrieve_type, top_k: vo.top_k, weight: vo.weight }
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
          if (sourceCell?.getData()?.type === 'http-request') {
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
          message.success(t('common.saveSuccess'))
        }
        resolve(res)
      }).catch(error => {
        reject(error)
      })
    })
  }

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
    deleteEvent,
    copyEvent,
    parseEvent,
    handleSave,
    chatVariables,
    setChatVariables
  };
};
