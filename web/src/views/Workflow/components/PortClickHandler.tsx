import { useEffect, useState } from 'react';
import { Popover } from 'antd';
import { useTranslation } from 'react-i18next';
import { nodeLibrary, graphNodeLibrary } from '../constant';

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
    
    // 计算新节点位置（在源节点右侧）
    const sourceBBox = sourceNode.getBBox();
    const newX = sourceBBox.x + sourceBBox.width + 50;
    const newY = sourceBBox.y;
    
    // 创建新节点
    const newNode = graph.addNode({
      ...(graphNodeLibrary[selectedNodeType.type] || graphNodeLibrary.default),
      x: newX,
      y: newY,
      data: {
        id: `${selectedNodeType.type}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
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
      const targetPort = targetPorts.find((port: any) => port.group === 'left')?.id || 'left';
      
      graph.addEdge({
        source: { cell: sourceNode.id, port: sourcePort },
        target: { cell: newNode.id, port: targetPort },
        attrs: {
          line: {
            stroke: '#155EEF',
            strokeWidth: 1,
            targetMarker: {
              name: 'block',
              size: 8,
            },
          },
        },
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
              const newWidth = Math.max(240, bounds.maxX - bounds.minX + padding * 2);
              const newHeight = Math.max(120, bounds.maxY - bounds.minY + padding * 2);

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
        
        let filteredNodes;
        if (isChildOfLoop) {
          // Use same filtering as AddNode for child nodes of loop
          filteredNodes = category.nodes.filter(nodeType =>
            nodeType.type !== 'start' && nodeType.type !== 'end' && nodeType.type !== 'loop' && nodeType.type !== 'cycle-start'
          );

        } else {
          // Original filtering for non-loop child nodes
          filteredNodes = category.nodes.filter(nodeType =>
            nodeType.type !== 'start' && nodeType.type !== 'end' && nodeType.type !== 'cycle-start' && nodeType.type !== 'break'
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