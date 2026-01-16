import { useRef, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { App } from 'antd'
import { Graph, Node, MiniMap, Snapline, Clipboard, Keyboard, type Edge } from '@antv/x6';
import { register } from '@antv/x6-react-shape';

import { nodeRegisterLibrary, graphNodeLibrary, nodeLibrary, portMarkup, portAttrs } from '../constant';
import type { WorkflowConfig, NodeProperties, ChatVariable } from '../types';
import { getWorkflowConfig, saveWorkflowConfig } from '@/api/application'
import type { PortMetadata } from '@antv/x6/lib/model/port';

export interface UseWorkflowGraphProps {
  containerRef: React.RefObject<HTMLDivElement>;
  miniMapRef: React.RefObject<HTMLDivElement>;
}

export interface UseWorkflowGraphReturn {
  config: WorkflowConfig | null;
  setConfig: React.Dispatch<React.SetStateAction<WorkflowConfig | null>>;
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
  chatVariables: ChatVariable[];
  setChatVariables: React.Dispatch<React.SetStateAction<ChatVariable[]>>;
}

export const edge_color = '#155EEF';
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
  const [isHandMode, setIsHandMode] = useState(true);
  const [config, setConfig] = useState<WorkflowConfig | null>(null);
  const [chatVariables, setChatVariables] = useState<ChatVariable[]>([])

  useEffect(() => {
    getConfig()
  }, [id])
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
        
        // 如果是if-else节点，根据cases动态生成端口
        if (type === 'if-else' && config.cases && Array.isArray(config.cases)) {
          const caseCount = config.cases.length;
          const totalPorts = caseCount + 1; // IF/ELIF + ELSE
          const baseHeight = 88;
          const newHeight = baseHeight + (totalPorts - 2) * 30;
          
          const portItems: PortMetadata[] = [
            { group: 'left' },
            { group: 'right', id: 'CASE1', args: { dy: 24 }, attrs: { text: { text: 'IF', fontSize: 12, fill: '#5B6167' }} }
          ];
          
          // 添加 ELIF 端口
          for (let i = 1; i < caseCount; i++) {
            portItems.push({
              group: 'right',
              id: `CASE${i + 1}`,
              attrs: { text: { text: 'ELIF', fontSize: 12, fill: '#5B6167' }}
            });
          }
          
          // 添加 ELSE 端口
          portItems.push({
            group: 'right',
            id: `CASE${caseCount + 1}`,
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
        
        // 如果是question-classifier节点，根据categories动态生成端口
        if (type === 'question-classifier' && config.categories && Array.isArray(config.categories)) {
          const categoryCount = config.categories.length;
          const baseHeight = 88;
          const newHeight = baseHeight + (categoryCount - 1) * 30;
          
          const portItems: PortMetadata[] = [
            { group: 'left' }
          ];
          
          // 添加分类端口
          config.categories.forEach((category: any, index: number) => {
            portItems.push({
              group: 'right',
              id: `CASE${index + 1}`,
              args: index === 0 ? { dy: 24 } : undefined,
              attrs: { text: { text: category.class_name || `分类${index + 1}`, fontSize: 12, fill: '#5B6167' }}
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
        
        // 如果是http-request节点，检查error_handle.method配置
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
      
      // 分离父节点和子节点
      const parentNodes = nodeList.filter(node => !node.data.cycle)
      const childNodes = nodeList.filter(node => node.data.cycle)
      
      // 先添加父节点
      graphRef.current?.addNodes(parentNodes)
      
      // 然后处理子节点，使用addChild添加到对应的父节点
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
      
      // 调整父节点大小以适应子节点
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
      // 计算loop和iteration类型节点的数量
      const loopIterationCount = nodes.filter(node => 
        node.type === 'loop' || node.type === 'iteration'
      ).length;
      
      // 去重处理：对于if-else和question-classifier节点，不同连接桩允许连接到相同节点
      const uniqueEdges = edges.filter((edge, index, arr) => {
        return arr.findIndex(e => {
          const sourceCell = graphRef.current?.getCellById(e.source);
          const sourceType = sourceCell?.getData()?.type;
          const isMultiPortNode = sourceType === 'question-classifier' || sourceType === 'if-else';
          
          if (isMultiPortNode) {
            // 多端口节点需要同时比较source、target和label
            return e.source === edge.source && e.target === edge.target && e.label === edge.label;
          } else {
            // 其他节点只比较source和target
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
          
          // 如果是if-else节点且有label，根据label匹配对应的端口
          if (sourceCell.getData()?.type === 'if-else' && label) {
            // 查找匹配的端口ID
            const matchingPort = sourcePorts.find((port: any) => port.id === label);
            if (matchingPort) {
              sourcePort = label;
            }
          }
          
          // 如果是question-classifier节点且有label，根据label匹配对应的端口
          if (sourceCell.getData()?.type === 'question-classifier' && label) {
            const matchingPort = sourcePorts.find((port: any) => port.id === label);
            if (matchingPort) {
              sourcePort = label;
            }
          }
          
          // 如果是http-request节点且有label，根据label匹配对应的端口
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
            attrs: {
              line: {
                stroke: edge_color,
                strokeWidth: 1,
                targetMarker: {
                  name: 'diamond',
                  width: 4,
                  height: 4,
                },
              },
            },
            // zIndex: loopIterationCount
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
  // 节点选择事件
  const nodeClick = ({ node }: { node: Node }) => {
    // 忽略 add-node 类型的节点点击
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
    const cycle = node.getData()?.cycle;
    if (cycle) {
      const parentNode = graphRef.current!.getNodes().find(n => n.id === cycle);
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
        if (nodeData.cycle) {
          // 找到对应的父节点
          const parentNode = nodes?.find(n => n.id === nodeData.cycle);
          if (parentNode) {
            // 使用removeChild方法删除子节点
            parentNode.removeChild(nodeToDelete);
            parentNodesToUpdate.push(parentNode);
          }
          // 将子节点添加到删除列表
          cells.push(nodeToDelete);
        } 
        // 检查是否为 LoopNode、IterationNode 或 SubGraphNode
        else if (nodeToDelete.shape === 'loop-node' || nodeToDelete.shape === 'iteration-node' || nodeToDelete.shape === 'subgraph-node') {
          // 查找所有 cycle 为当前节点 id 的子节点
          nodes?.forEach(node => {
            const data = node.getData();
            if (data.cycle === nodeToDelete.id || data.cycle === nodeToDelete.getData()?.id) {
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
      panning: isHandMode,
      mousewheel: {
        enabled: true,
      },
      connecting: {
        // router: 'orth',
        // router: 'manhattan',
        connector: {
          name: 'smooth',
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
                targetMarker: {
                  name: 'diamond',
                  width: 4,
                  height: 4,
                },
              },
            },
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
          const sourceParentId = sourceCell?.getData()?.cycle;
          const targetParentId = targetCell?.getData()?.cycle;
          
          // 验证父子节点关系：
          // 1. 如果两个节点都有父节点ID，必须相同才能连线
          // 2. 如果两个都没有父节点ID，可以正常连线
          // 3. 如果一个有父节点，一个没有，不能连线
          console.log('sourceParentId', sourceParentId, targetParentId)
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
        validate (this) {
          return false
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
    // 监听连接桩点击事件
    graphRef.current.on('node:port:click', ({ e, node, port }: { e: MouseEvent, node: Node, port: string }) => {
      e.stopPropagation();
      const portElement = e.target as HTMLElement;
      const rect = portElement.getBoundingClientRect();
      
      // 创建临时的popover触发元素
      const tempDiv = document.createElement('div');
      tempDiv.style.position = 'fixed';
      tempDiv.style.left = rect.left + 'px';
      tempDiv.style.top = rect.top + 'px';
      tempDiv.style.width = '1px';
      tempDiv.style.height = '1px';
      tempDiv.style.zIndex = '9999';
      document.body.appendChild(tempDiv);
      
      // 触发自定义事件来显示节点选择popover
      const customEvent = new CustomEvent('port:click', {
        detail: { node, port, element: tempDiv, rect }
      });
      window.dispatchEvent(customEvent);
    });
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
      // 创建条件节点
      graphRef.current.addNode({
        ...graphNodeLibrary[dragData.type],
        x: point.x - 100,
        y: point.y - 60,
        id: cleanNodeData.id,
        data: { ...cleanNodeData },
      });
    } else {
      // 普通节点创建，不支持拖拽到循环节点内
      graphRef.current.addNode({
        ...(graphNodeLibrary[dragData.type] || graphNodeLibrary.default),
        x: point.x - 60,
        y: point.y - 20,
        id: cleanNodeData.id,
        data: { ...cleanNodeData },
      });
    }
  };
  // 保存workflow配置
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
              if (key === 'memory' && data.config[key] && 'defaultValue' in data.config[key]) {
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
                    const kb_config = vo.config || { similarity_threshold: vo.similarity_threshold, strategy: vo.strategy, top_k: vo.top_k, weight: vo.weight }
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
            cycle: data.cycle, // 保存cycle参数
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

          // 过滤无效连线：源节点或目标节点不存在，或者是add-node类型
          if (!sourceCell?.getData()?.id || !targetCell?.getData()?.id || 
              sourceCell?.getData()?.type === 'add-node' || targetCell?.getData()?.type === 'add-node') {
            return null;
          }
          
          // 如果是if-else节点的右侧端口连线，添加label
          if (sourceCell?.getData()?.type === 'if-else' && sourcePortId?.startsWith('CASE')) {
            return {
              source: sourceCell.getData().id,
              target: targetCell?.getData().id,
              label: sourcePortId,
            };
          }
          
          // 如果是question-classifier节点的右侧端口连线，添加label
          if (sourceCell?.getData()?.type === 'question-classifier' && sourcePortId?.startsWith('CASE')) {
            return {
              source: sourceCell.getData().id,
              target: targetCell?.getData().id,
              label: sourcePortId,
            };
          }
          
          // 如果是http-request节点的右侧端口连线，添加label
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
          // 去重：对于if-else和question-classifier节点，不同连接桩允许连接到相同节点
          return arr.findIndex(e => {
            if (!e || !edge) return false;
            const sourceCell = graphRef.current?.getCellById(e.source);
            const sourceType = sourceCell?.getData()?.type;
            const isMultiPortNode = sourceType === 'question-classifier' || sourceType === 'if-else';
            
            if (isMultiPortNode) {
              // 多端口节点需要同时比较source、target和label
              return e.source === edge.source && e.target === edge.target && e.label === edge.label;
            } else {
              // 其他节点只比较source和target
              return e.source === edge.source && e.target === edge.target;
            }
          }) === index;
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
    setConfig,
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
    handleSave,
    chatVariables,
    setChatVariables
  };
};
