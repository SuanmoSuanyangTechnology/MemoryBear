import { useEffect, useState } from 'react';
import { Popover } from 'antd';
import { useTranslation } from 'react-i18next';
import { nodeLibrary, graphNodeLibrary, edgeAttrs } from '../constant';

interface PortClickHandlerProps {
  graph: any;
}

const PortClickHandler: React.FC<PortClickHandlerProps> = ({ graph }) => {
  const { t } = useTranslation();
  const [popoverVisible, setPopoverVisible] = useState(false);
  const [popoverPosition, setPopoverPosition] = useState({ x: 0, y: 0 });
  const [sourceNode, setSourceNode] = useState<any>(null);
  const [sourcePort, setSourcePort] = useState<string>('');
  const [tempElement, setTempElement] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const handlePortClick = (event: CustomEvent) => {
      const { node, port, element, rect } = event.detail;
      setSourceNode(node);
      setSourcePort(port);
      setTempElement(element);
      setPopoverPosition({ x: rect.left, y: rect.top });
      setPopoverVisible(true);
    };

    window.addEventListener('port:click', handlePortClick as EventListener);
    
    return () => {
      window.removeEventListener('port:click', handlePortClick as EventListener);
    };
  }, []);

  const handleNodeSelect = (selectedNodeType: any) => {
    if (!sourceNode || !graph) return;

    const sourceNodeData = sourceNode.getData();
    const sourceNodeType = sourceNodeData?.type;
    
    // 如果是cycle-start节点，需要处理add-node节点
    let addNodePosition = null;
    if (sourceNodeType === 'cycle-start' && sourceNodeData.cycle) {
      const cycleId = sourceNodeData.cycle;
      const addNodes = graph.getNodes().filter((n: any) => 
        n.getData()?.type === 'add-node' && n.getData()?.cycle === cycleId
      );
      
      if (addNodes.length > 0) {
        const addNode = addNodes[0];
        addNodePosition = addNode.getBBox();
        addNode.remove();
      }
    }
    
    // 计算新节点位置，避免重叠
    const sourceBBox = sourceNode.getBBox();
    const nodeWidth = graphNodeLibrary[selectedNodeType.type]?.width || 120;
    const nodeHeight = graphNodeLibrary[selectedNodeType.type]?.height || 88;
    const horizontalSpacing = sourceNodeType === 'cycle-start' ? 40 : 80;
    const verticalSpacing = 10;
    
    // 获取源连接桩的group信息
    const sourcePortInfo = sourceNode.getPorts().find((p: any) => p.id === sourcePort);
    const sourcePortGroup = sourcePortInfo?.group || sourcePort;
    console.log('sourcePortGroup', sourcePortGroup, sourcePortInfo)
    
    // 如果有add-node位置，使用该位置，否则计算新位置
    let newX, newY;
    if (addNodePosition) {
      newX = addNodePosition.x;
      newY = addNodePosition.y;
    } else {
      // 根据连接桩位置决定节点放置方向
      if (sourcePortGroup === 'left') {
        // 左侧连接桩，在左侧添加节点
        newX = sourceBBox.x - nodeWidth*2 - horizontalSpacing;
        newY = sourceBBox.y;
      } else {
        // 右侧连接桩，在右侧添加节点
        newX = sourceBBox.x + sourceBBox.width + horizontalSpacing;
        newY = sourceBBox.y;
      }
      
      // 检查位置是否与现有节点重叠（只考虑与当前节点相连的节点）
      const checkOverlap = (x: number, y: number) => {
        // 获取与源节点相连的节点
        const connectedNodes = new Set();
        graph.getConnectedEdges(sourceNode).forEach((edge: any) => {
          const sourceId = edge.getSourceCellId();
          const targetId = edge.getTargetCellId();
          if (sourceId !== sourceNode.id) connectedNodes.add(sourceId);
          if (targetId !== sourceNode.id) connectedNodes.add(targetId);
        });
        
        return graph.getNodes().some((node: any) => {
          if (node.id === sourceNode.id) return false;
          if (!connectedNodes.has(node.id)) return false; // 只考虑相连的节点
          const bbox = node.getBBox();
          return !(x + nodeWidth < bbox.x || x > bbox.x + bbox.width || 
                  y + nodeHeight < bbox.y || y > bbox.y + bbox.height);
        });
      };
      
      // 如果位置被占用，向下寻找空位
      while (checkOverlap(newX, newY)) {
        newY += nodeHeight + verticalSpacing;
      }
    }
    
    // 创建新节点
    const id = `${selectedNodeType.type.replace(/-/g, '_')}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
    const newNode = graph.addNode({
      ...(graphNodeLibrary[selectedNodeType.type] || graphNodeLibrary.default),
      x: newX,
      y: newY,
      id,
      data: {
        id,
        type: selectedNodeType.type,
        icon: selectedNodeType.icon,
        name: t(`workflow.${selectedNodeType.type}`),
        cycle: sourceNodeData.cycle, // 继承源节点的cycle
        config: selectedNodeType.config || {}
      },
    });

    // 将新节点添加为父节点的子节点
    if (sourceNodeData.cycle) {
      const parentNode = graph.getNodes().find((n: any) => n.getData()?.id === sourceNodeData.cycle);
      if (parentNode) {
        parentNode.addChild(newNode);
      }
    }

    // 创建连线
    setTimeout(() => {
      const targetPorts = newNode.getPorts();
      let targetPort;
      
      if (sourcePortGroup === 'left') {
        // 从左侧连接桩连出，连接到新节点的右侧
        targetPort = targetPorts.find((port: any) => port.group === 'right')?.id || 'right';
      } else {
        // 从右侧连接桩连出，连接到新节点的左侧
        targetPort = targetPorts.find((port: any) => port.group === 'left')?.id || 'left';
      }
      
      graph.addEdge({
        source: { cell: sourceNode.id, port: sourcePort },
        target: { cell: newNode.id, port: targetPort },
        ...edgeAttrs
        // zIndex: sourceNodeData.cycle && sourceNodeType == 'cycle-start' ? 1 : sourceNodeData.cycle ? 2 : 0
      });
      
      // 循环节点内子节点通过连接桩添加时，调整循环节点大小
      const cycleId = sourceNodeData.cycle;
      if (cycleId) {
        const parentNode = graph.getNodes().find((n: any) => n.getData()?.id === cycleId);

        if (parentNode) {
          const adjustLoopSize = () => {
            const childNodes = graph.getNodes().filter((n: any) => n.getData()?.cycle === cycleId);
            if (childNodes.length > 0) {
              const bounds = childNodes.reduce((acc: any, child: any) => {
                const bbox = child.getBBox();
                return {
                  minX: Math.min(acc.minX, bbox.x),
                  minY: Math.min(acc.minY, bbox.y),
                  maxX: Math.max(acc.maxX, bbox.x + bbox.width),
                  maxY: Math.max(acc.maxY, bbox.y + bbox.height)
                };
              }, { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity });
              
              const padding = 20;
              const bottomPadding = 50;
              const newWidth = Math.max(240, bounds.maxX - bounds.minX + padding * 2);
              const newHeight = Math.max(120, bounds.maxY - bounds.minY + padding + bottomPadding);

              parentNode.prop('size', { width: newWidth, height: newHeight });
            }
          };
          
          adjustLoopSize();
          
          // 监听子节点移动事件
          const childNodes = graph.getNodes().filter((n: any) => n.getData()?.cycle === cycleId);
          childNodes.forEach((childNode: any) => {
            childNode.on('change:position', adjustLoopSize);
          });
        }
      }
    }, 50);

    // 清理临时元素
    if (tempElement) {
      document.body.removeChild(tempElement);
      setTempElement(null);
    }
    
    setPopoverVisible(false);
  };

  const handlePopoverClose = () => {
    setPopoverVisible(false);
    if (tempElement) {
      document.body.removeChild(tempElement);
      setTempElement(null);
    }
  };

  const content = (
    <div style={{ maxHeight: '300px', overflowY: 'auto', minWidth: '240px' }}>
      {nodeLibrary.map((category, categoryIndex) => {
        const sourceNodeData = sourceNode?.getData();
        const isChildOfLoop = sourceNodeData?.cycle && graph?.getNodes().find((n: any) => n.getData()?.id === sourceNodeData.cycle && n.getData()?.type === 'loop');
        const isChildOfIteration = sourceNodeData?.cycle && graph?.getNodes().find((n: any) => n.getData()?.id === sourceNodeData.cycle && n.getData()?.type === 'iteration');
        
        let filteredNodes;
        if (isChildOfLoop) {
          // Use same filtering as AddNode for child nodes of loop, but allow break
          filteredNodes = category.nodes.filter(nodeType => !['start', 'end', 'loop', 'cycle-start', 'iteration'].includes(nodeType.type));
        } else if (isChildOfIteration) {
          // Filter out loop and iteration nodes for children of iteration nodes, but allow break
          filteredNodes = category.nodes.filter(nodeType => !['start', 'end', 'loop', 'cycle-start', 'iteration'].includes(nodeType.type));
        } else {
          // Original filtering for non-loop child nodes
          filteredNodes = category.nodes.filter(nodeType => !['start', 'break', 'cycle-start'].includes(nodeType.type));
          filteredNodes = category.nodes.filter(nodeType =>
            nodeType.type !== 'start' && nodeType.type !== 'cycle-start' && nodeType.type !== 'break'
          );
        }
        
        if (filteredNodes.length === 0) return null;
        
        return (
          <div key={category.category}>
            {categoryIndex > 0 && <div style={{ height: '1px', background: '#f0f0f0', margin: '4px 0' }} />}
            <div style={{ padding: '4px 12px', fontSize: '12px', color: '#999', fontWeight: 'bold' }}>
              {t(`workflow.${category.category}`)}
            </div>
            {filteredNodes.map((nodeType) => (
              <div
                key={nodeType.type}
                style={{
                  padding: '8px 12px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                }}
                onClick={() => handleNodeSelect(nodeType)}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = '#f0f8ff';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'white';
                }}
              >
                <img src={nodeType.icon} className="rb:w-4 rb:h-4" />
                <span style={{ fontSize: '14px' }}>{t(`workflow.${nodeType.type}`)}</span>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );

  if (!tempElement) return null;

  return (
    <Popover
      content={content}
      open={popoverVisible}
      onOpenChange={(visible) => {
        if (!visible) handlePopoverClose();
      }}
      placement="right"
      overlayStyle={{
        position: 'fixed',
        left: popoverPosition.x + 10,
        top: popoverPosition.y - 10,
      }}
    >
      <div />
    </Popover>
  );
};

export default PortClickHandler;