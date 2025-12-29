import { useRef, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { App } from 'antd'
import { Graph, Node, MiniMap, Snapline, Clipboard, Keyboard, type Edge } from '@antv/x6';
import { register } from '@antv/x6-react-shape';

import { nodeRegisterLibrary, graphNodeLibrary, nodeLibrary } from '../constant';
import type { WorkflowConfig, NodeProperties } from '../types';
import { getWorkflowConfig, saveWorkflowConfig } from '@/api/application'

export interface UseWorkflowGraphProps {
  containerRef: React.RefObject<HTMLDivElement>;
  miniMapRef: React.RefObject<HTMLDivElement>;
}

export interface UseWorkflowGraphReturn {
  config: WorkflowConfig | null;
  graphRef: React.MutableRefObject<Graph | undefined>;
  selectedNode: Node | null;
  setSelectedNode: React.Dispatch<React.SetStateAction<Node | null>>;
  zoomLevel: number;
  setZoomLevel: React.Dispatch<React.SetStateAction<number>>;
  canUndo: boolean;
  canRedo: boolean;
  isHandMode: boolean;
  setIsHandMode: React.Dispatch<React.SetStateAction<boolean>>;
  onUndo: () => void;
  onRedo: () => void;
  onDrop: (event: React.DragEvent) => void;
  blankClick: () => void;
  deleteEvent: () => boolean | void;
  copyEvent: () => boolean | void;
  parseEvent: () => boolean | void;
  handleSave: (flag?: boolean) => Promise<unknown>;
}

const edge_color = '#155EEF';
const edge_selected_color = '#4DA8FF'

export const useWorkflowGraph = ({
  containerRef,
  miniMapRef,
}: UseWorkflowGraphProps): UseWorkflowGraphReturn => {
  const { id } = useParams();
  const { message } = App.useApp();
  const { t } = useTranslation()
  const graphRef = useRef<Graph>();
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [zoomLevel, setZoomLevel] = useState(1);
  const historyRef = useRef<{ undoStack: string[], redoStack: string[] }>({ undoStack: [], redoStack: [] });
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const [isHandMode, setIsHandMode] = useState(false);
  const [config, setConfig] = useState<WorkflowConfig | null>(null);

  useEffect(() => {
    getConfig()
  }, [id])
  const getConfig = () => {
    if (!id) return
    getWorkflowConfig(id)
      .then(res => {
        setConfig(res as WorkflowConfig)
      })
  }

  useEffect(() => {
    initWorkflow()
  }, [config, graphRef.current])
  
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
            if (key === 'knowledge_retrieval' && nodeLibraryConfig.config && nodeLibraryConfig.config[key]) {
              const { query, ...rest } = config
              nodeLibraryConfig.config[key].defaultValue = {
                ...rest
              }
              console.log(type, config, nodeLibraryConfig)
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
        return nodeConfig
      })
      graphRef.current?.addNodes(nodeList)
    }
    if (edges.length) {
      const edgeList = edges.map(edge => {
        const { source, target } = edge
        const sourceCell = graphRef.current?.getCellById(source)
        const targetCell = graphRef.current?.getCellById(target)
        
        if (sourceCell && targetCell) {
          const sourcePorts = (sourceCell as Node).getPorts()
          const targetPorts = (targetCell as Node).getPorts()
          
          const edgeConfig = {
            source: {
              cell: sourceCell.id,
              port: sourcePorts.find((port: any) => port.group === 'right')?.id || 'right'
            },
            target: {
              cell: targetCell.id,
              port: targetPorts.find((port: any) => port.group === 'left')?.id || 'left'
            },
            // label,
            attrs: {
              line: {
                stroke: edge_color,
                strokeWidth: 1,
                targetMarker: {
                  name: 'block',
                  size: 8,
                },
              },
            },
          }

          return edgeConfig
        }
        return null
      })
      graphRef.current.addEdges(edgeList.filter(vo => vo !== null))
    }
    
    // 初始化完成后，将节点展示在可视区域内
    if (nodes.length > 0 || edges.length > 0) {
      setTimeout(() => {
        if (graphRef.current) {
          graphRef.current.centerContent()
        }
      }, 200)
    }
  }

  const saveState = () => {
    if (!graphRef.current) return;
    const state = JSON.stringify(graphRef.current.toJSON());
    historyRef.current.undoStack.push(state);
    historyRef.current.redoStack = [];
    if (historyRef.current.undoStack.length > 50) {
      historyRef.current.undoStack.shift();
    }
    updateHistoryState();
  };

  const updateHistoryState = () => {
    setCanUndo(historyRef.current.undoStack.length > 1);
    setCanRedo(historyRef.current.redoStack.length > 0);
  };

  // 撤销
  const onUndo = () => {
    if (!graphRef.current || historyRef.current.undoStack.length === 0) return;
    const { undoStack = [], redoStack = [] } = historyRef.current

    const currentState = JSON.stringify(graphRef.current.toJSON());
    const prevState = undoStack[undoStack.length - 2];

    historyRef.current.redoStack = [...redoStack, currentState]
    historyRef.current.undoStack = undoStack.slice(0, undoStack.length - 1)
    graphRef.current.fromJSON(JSON.parse(prevState));
    updateHistoryState();
  };
  // 重做
  const onRedo = () => {
    if (!graphRef.current || historyRef.current.redoStack.length === 0) return;
    const { undoStack = [], redoStack = [] } = historyRef.current

    const nextState = redoStack[redoStack.length - 1];

    historyRef.current.undoStack = [...undoStack, nextState]
    historyRef.current.redoStack = redoStack.slice(0, redoStack.length - 1)
    graphRef.current.fromJSON(JSON.parse(nextState));
    updateHistoryState();
  };
  // 使用插件
  const setupPlugins = () => {
    if (!graphRef.current || !miniMapRef.current) return;
    // 添加小地图
    graphRef.current.use(
      new MiniMap({
        container: miniMapRef.current,
        width: 100,
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
  const showPorts = (show: boolean) => {
    const container = containerRef.current!;
    const ports = container.querySelectorAll('.x6-port-body') as NodeListOf<SVGElement>;
    for (let i = 0, len = ports.length; i < len; i += 1) {
      ports[i].style.visibility = show ? 'visible' : 'hidden';
    }
  };
  // 节点选择事件
  const nodeClick = ({ node }: { node: Node }) => {
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
  // 连线选择事件
  const edgeClick = ({ edge }: { edge: Edge }) => {
    edge.setAttrByPath('line/stroke', edge_selected_color);
    clearNodeSelect();
  };
  // 清空选中节点
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
  // 清空选中连线
  const clearEdgeSelect = () => {
    graphRef.current?.getEdges().forEach(e => {
      e.setAttrByPath('line/stroke', edge_color);
      e.setAttrByPath('line/strokeWidth', 1);
    });
  };
  // 画布点击事件，取消选择
  const blankClick = () => {
    clearNodeSelect();
    clearEdgeSelect();
    graphRef.current?.cleanSelection();
  };
  // 画布缩放事件
  const scaleEvent = ({ sx }: { sx: number }) => {
    setZoomLevel(sx);
  };
  // 节点移动事件
  const nodeMoved = ({ node }: { node: Node }) => {
    const parentId = node.getData()?.parentId;
    if (parentId) {
      const parentNode = graphRef.current!.getNodes().find(n => n.id === parentId);
      if (parentNode?.getData()?.isGroup) {
        // 获取父节点和子节点的边界框
        const parentBBox = parentNode.getBBox();
        const childBBox = node.getBBox();
        
        // 计算父节点的内边距
        const padding = 24;
        const headerHeight = 50;
        
        // 计算子节点允许的最小和最大位置
        const minX = parentBBox.x + padding;
        const minY = parentBBox.y + padding + headerHeight;
        const maxX = parentBBox.x + parentBBox.width - padding - childBBox.width;
        const maxY = parentBBox.y + parentBBox.height - padding - childBBox.height;
        
        // 限制子节点在父节点内移动
        let newX = childBBox.x;
        let newY = childBBox.y;
        
        if (newX < minX) newX = minX;
        if (newY < minY) newY = minY;
        if (newX > maxX) newX = maxX;
        if (newY > maxY) newY = maxY;
        
        // 如果子节点位置被限制，更新其位置
        if (newX !== childBBox.x || newY !== childBBox.y) {
          node.setPosition(newX, newY);
        }
      }
    }
  };
  // 复制快捷键事件
  const copyEvent = () => {
    if (!graphRef.current) return false;
    const selectedNodes = graphRef.current.getNodes().filter(node => node.getData()?.isSelected);
    if (selectedNodes.length) {
      graphRef.current.copy(selectedNodes);
    }
    return false;
  };
  // 粘贴快捷键事件
  const parseEvent = () => {
    if (!graphRef.current?.isClipboardEmpty()) {
      graphRef.current?.paste({ offset: 32 });
      blankClick();
    }
    return false;
  };
  // 撤销快捷键事件
  const undoEvent = () => {
    if (canUndo) {
      onUndo();
    }
    return false;
  };
  // 重做快捷键事件
  const redoEvent = () => {
    if (canRedo) {
      onRedo();
    }
    return false;
  };
  // 删除选中的节点和连线事件
  const deleteEvent = () => {
    if (!graphRef.current) return;
    const nodes = graphRef.current?.getNodes();
    const edges = graphRef.current?.getEdges();
    const cells: (Node | Edge)[] = [];
    const nodesToDelete: Node[] = [];
    const parentNodesToUpdate: Node[] = [];

    // 首先收集所有选中的节点，但排除默认子节点
    nodes?.forEach(node => {
      const data = node.getData();
      // 如果节点是默认子节点，不允许单独删除
      if (data.isSelected && !data.isDefault) {
        nodesToDelete.push(node);
      }
    });

    // 收集与选中节点相关的连线
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

    // 对于每个选中的节点
    if (nodesToDelete.length > 0) {
      nodesToDelete.forEach(nodeToDelete => {
        // 检查是否为子节点
        const nodeData = nodeToDelete.getData();
        if (nodeData.parentId) {
          // 找到对应的父节点
          const parentNode = nodes?.find(n => n.id === nodeData.parentId);
          if (parentNode) {
            // 使用removeChild方法删除子节点
            parentNode.removeChild(nodeToDelete);
            parentNodesToUpdate.push(parentNode);
          }
        } 
        // 检查是否为 LoopNode、IterationNode 或 SubGraphNode
        else if (nodeToDelete.shape === 'loop-node' || nodeToDelete.shape === 'iteration-node' || nodeToDelete.shape === 'subgraph-node') {
          // 查找所有 parentId 为当前节点 id 的子节点
          nodes?.forEach(node => {
            const data = node.getData();
            if (data.parentId === nodeToDelete.id) {
              cells.push(node);
            }
          });
          // 添加父节点到删除列表
          cells.push(nodeToDelete);
        } 
        // 普通节点
        else {
          cells.push(nodeToDelete);
        }
      });
      blankClick();
    }
      
    // 删除所有收集的节点和连线
    if (cells.length > 0) {
      graphRef.current?.removeCells(cells);
    }
    return false;
  };

  // 调整画布大小
  const handleResize = () => {
    if (containerRef.current && graphRef.current) {
      graphRef.current.resize(containerRef.current.offsetWidth, containerRef.current.offsetHeight);
    }
  };

  // 初始化
  const init = () => {
    if (!containerRef.current || !miniMapRef.current) return;

    // 注册React形状
    nodeRegisterLibrary.forEach((item) => {
      register(item);
    });

    const container = containerRef.current;
    graphRef.current = new Graph({
      container,
      background: {
        color: '#F0F3F8',
      },
      // width: container.clientWidth || 800,
      // height: container.clientHeight || 600,
      autoResize: true,
      grid: {
        visible: true,
        type: 'dot',
        size: 10,
        args: {
          color: '#939AB1', // 网点颜色
          thickness: 1, // 网点大小
        }
      },
      panning: false,
      mousewheel: {
        enabled: true,
        modifiers: ['ctrl', 'meta'],
      },
      connecting: {
        // router: 'orth',
        // router: 'manhattan',
        connector: {
          name: 'rounded',
          args: {
            radius: 8,
          },
        },
        anchor: 'center',
        connectionPoint: 'anchor',
        allowBlank: false,
        allowNode: false,
        allowEdge: false,
        highlight: true,
        snap: {
          radius: 20,
        },
        createEdge() {
          return graphRef.current?.createEdge({
            attrs: {
              line: {
                stroke: edge_color,
                strokeWidth: 1,
              },
            },
            zIndex: 0,
          });
        },
        validateConnection({ sourceCell, targetCell, targetMagnet }) {
          if (!targetMagnet) return false;
          
          const sourceType = sourceCell?.getData()?.type;
          const targetType = targetCell?.getData()?.type;
          
          // 开始节点不能作为连线的终点
          if (targetType === 'start') return false;
          
          // 结束节点不能作为连线的起点
          if (sourceType === 'end') return false;
          
          // 获取源节点和目标节点的父节点ID
          const sourceParentId = sourceCell?.getData()?.parentId;
          const targetParentId = targetCell?.getData()?.parentId;
          
          // 验证父子节点关系：
          // 1. 如果两个节点都有父节点ID，必须相同才能连线
          // 2. 如果一个有父节点ID，另一个没有，不能连线
          // 3. 如果两个都没有父节点ID，可以正常连线
          if (sourceParentId && targetParentId) {
            // 同一父节点下的子节点可以互相连线
            return sourceParentId === targetParentId;
          } else if (sourceParentId || targetParentId) {
            // 一个有父节点，一个没有，不能连线
            return false;
          }
          
          return true;
        },
      },
      embedding: {
        enabled: true,
        validate (this, { parent }) {
        const parentData = parent.getData()
          return parentData.type === 'iteration' || parentData.type === 'loop'
        }
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
    });
    // 使用插件
    setupPlugins();
    // 监听连线mouseleave事件
    graphRef.current.on('edge:mouseleave', ({ edge }: { edge: Edge }) => {
      if (edge.getAttrByPath('line/stroke') !== edge_selected_color) {
        edge.setAttrByPath('line/stroke', edge_color);
        edge.setAttrByPath('line/strokeWidth', 1);
      }
    });
    // 监听节点选择事件
    graphRef.current.on('node:click', nodeClick);
    // 监听连线选择事件
    graphRef.current.on('edge:click', edgeClick);
    // 监听画布点击事件，取消选择
    graphRef.current.on('blank:click', blankClick);
    // 监听缩放事件
    graphRef.current.on('scale', scaleEvent);
    // 监听节点移动事件
    graphRef.current.on('node:moved', nodeMoved);

    // 监听画布变化事件
    const events = [
      'node:added', 
      'node:removed', 
      'edge:added', 
      'edge:removed',
    ];
    events.forEach(event => {
      graphRef.current!.on(event, () => {
        console.log('event', event);
        setTimeout(() => saveState(), 50);
      });
    });

    // 监听撤销键盘事件
    graphRef.current.bindKey(['ctrl+z', 'cmd+z'], undoEvent);
    // 监听重做键盘事件
    graphRef.current.bindKey(['ctrl+shift+z', 'cmd+shift+z', 'ctrl+y', 'cmd+y'], redoEvent);
    // 监听复制键盘事件
    graphRef.current.bindKey(['ctrl+c', 'cmd+c'], copyEvent);
    // 监听粘贴键盘事件
    graphRef.current.bindKey(['ctrl+v', 'cmd+v'], parseEvent);
    // 删除选中的节点和连线
    graphRef.current.bindKey(['ctrl+d', 'cmd+d', 'delete', 'backspace'], deleteEvent);

    // 保存初始状态
    setTimeout(() => saveState(), 100);
    // init window hook
    (window as Window & { __x6_instances__?: Graph[] }).__x6_instances__ = [];
    (window as Window & { __x6_instances__?: Graph[] }).__x6_instances__?.push(graphRef.current);
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

  const onDrop = (event: React.DragEvent) => {
    if (!graphRef.current) return;
    event.preventDefault();
    const dragData = JSON.parse(event.dataTransfer.getData('application/json'));
    const graph = graphRef.current;
    if (!graph) return;

    const point = graphRef.current.clientToLocal(event.clientX, event.clientY);
    
    // 获取节点库中的原始配置，避免config数据串联
    let nodeLibraryConfig = [...nodeLibrary]
      .flatMap(category => category.nodes)
      .find(n => n.type === dragData.type);
    nodeLibraryConfig = JSON.parse(JSON.stringify({ config: {}, ...nodeLibraryConfig })) as NodeProperties
    
    // 创建干净的节点数据，只保留必要的字段
    const cleanNodeData = {
      id: `${dragData.type}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      name: t(`workflow.${dragData.type}`),
      ...nodeLibraryConfig
    };
    
    if (dragData.type === 'loop' || dragData.type === 'iteration') {
      graphRef.current.addNode({
        ...graphNodeLibrary[dragData.type],
        x: point.x - 150,
        y: point.y - 100,
        data: { ...cleanNodeData, isGroup: true },
      });
    } else if (dragData.type === 'condition') {
      // 创建条件节点
      graphRef.current.addNode({
        ...graphNodeLibrary[dragData.type],
        x: point.x - 100,
        y: point.y - 60,
        data: { ...cleanNodeData, elifCount: 0 },
      });
    } else {
      // 检查是否放置在群组内
      const groups = graphRef.current.getNodes().filter(node => {
        const shape = node.shape;
        return shape === 'loop-node' || shape === 'iteration-node' || shape === 'subgraph-node';
      });
      let parentGroup = null;
      
      for (const group of groups) {
        const bbox = group.getBBox();
        if (point.x >= bbox.x && point.x <= bbox.x + bbox.width &&
            point.y >= bbox.y && point.y <= bbox.y + bbox.height) {
          parentGroup = group;
          break;
        }
      }
      
      const childNode = graphRef.current.addNode({
        ...(graphNodeLibrary[dragData.type] || graphNodeLibrary.default),
        x: point.x - 60,
        y: point.y - 20,
        data: { ...cleanNodeData, parentId: parentGroup?.id },
      });
      parentGroup?.addChild(childNode);
    }
  };
  // 保存workflow配置
  const handleSave = (flag = true) => {
    if (!graphRef.current || !config) return Promise.resolve()
    return new Promise((resolve, reject) => {
      const nodes = graphRef.current?.getNodes() || [];
      const edges = graphRef.current?.getEdges() || []

      const params = {
        ...config,
        nodes: nodes.map((node: Node) => {
          const data = node.getData();
          const position = node.getPosition();
          let itemConfig: Record<string, any> = {}

          if (data.config) {
            Object.keys(data.config).forEach(key => {
              if (data.config[key] && 'defaultValue' in data.config[key] && key !== 'knowledge_retrieval') {
                itemConfig[key] = data.config[key].defaultValue
              } else if (key === 'knowledge_retrieval' && data.config[key] && 'defaultValue' in data.config[key]) {
                itemConfig = {
                  ...itemConfig,
                  ...data.config[key].defaultValue
                }
              }
            })
          }

          return {
            id: data.id || node.id,
            type: data.type,
            name: data.name,
            position: {
              x: position.x,
              y: position.y,
            },
            config: itemConfig
          };
        }),
        edges: edges.map((edge: Edge) => {
          return {
            source: edge.getSourceCellId(),
            target: edge.getTargetCellId(),
            // label: edge.getAttrs()?.label?.text,
          };
        }),
      }
      saveWorkflowConfig(config.app_id, params as WorkflowConfig)
      .then(() => {
        if (flag) {
          message.success(t('common.saveSuccess'))
        }
        resolve(true)
      }).catch(error => {
        reject(error)
      })
    })
  }

  return {
    config,
    graphRef,
    selectedNode,
    setSelectedNode,
    zoomLevel,
    setZoomLevel,
    canUndo,
    canRedo,
    isHandMode,
    setIsHandMode,
    onUndo,
    onRedo,
    onDrop,
    blankClick,
    deleteEvent,
    copyEvent,
    parseEvent,
    handleSave
  };
};
