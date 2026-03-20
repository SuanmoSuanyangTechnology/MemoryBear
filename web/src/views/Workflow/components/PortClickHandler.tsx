/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-09 18:30:28 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 11:24:26
 */
import { useEffect, useState } from 'react';
import { Popover } from 'antd';
import { useTranslation } from 'react-i18next';
import { nodeLibrary, graphNodeLibrary, edgeAttrs, nodeWidth } from '../constant';

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

  // Handle node selection from popover menu and create new node with edge connection
  const handleNodeSelect = (selectedNodeType: any) => {
    if (!sourceNode || !graph) return;

    const sourceNodeData = sourceNode.getData();
    const sourceNodeType = sourceNodeData?.type;
    
    // If it's a cycle-start node, handle the add-node placeholder
    let addNodePosition = null;
    const isCycleSubNode = sourceNodeData.cycle
    if (isCycleSubNode && sourceNodeType === 'cycle-start') {
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
    
    // Calculate new node position to avoid overlapping
    const sourceBBox = sourceNode.getBBox();
    const nodeWidth = graphNodeLibrary[selectedNodeType.type]?.width || 120;
    const nodeHeight = graphNodeLibrary[selectedNodeType.type]?.height || 88;
    const horizontalSpacing = isCycleSubNode ? 48 : 80;
    const verticalSpacing = 10;
    
    // Get source port group information
    const sourcePortInfo = sourceNode.getPorts().find((p: any) => p.id === sourcePort);
    const sourcePortGroup = sourcePortInfo?.group || sourcePort;
    
    // If add-node position exists, use it; otherwise calculate new position
    let newX, newY;
    if (addNodePosition) {
      newX = addNodePosition.x;
      newY = addNodePosition.y;
    } else {
      // Determine node placement direction based on port position
      if (sourcePortGroup === 'left') {
        // Left port: add node to the left
        newX = sourceBBox.x - nodeWidth*2 - horizontalSpacing;
        newY = sourceBBox.y;
      } else {
        // Right port: add node to the right
        newX = sourceBBox.x + sourceBBox.width + horizontalSpacing;
        newY = sourceBBox.y;
      }
      
      // Check if position overlaps with existing nodes (only consider connected nodes)
      const checkOverlap = (x: number, y: number) => {
        // Get nodes connected to the source node
        const connectedNodes = new Set();
        graph.getConnectedEdges(sourceNode).forEach((edge: any) => {
          const sourceId = edge.getSourceCellId();
          const targetId = edge.getTargetCellId();
          if (sourceId !== sourceNode.id) connectedNodes.add(sourceId);
          if (targetId !== sourceNode.id) connectedNodes.add(targetId);
        });
        
        return graph.getNodes().some((node: any) => {
          if (node.id === sourceNode.id) return false;
          if (!connectedNodes.has(node.id)) return false; // Only consider connected nodes
          const bbox = node.getBBox();
          return !(x + nodeWidth < bbox.x || x > bbox.x + bbox.width || 
                  y + nodeHeight < bbox.y || y > bbox.y + bbox.height);
        });
      };
      
      // If position is occupied, search downward for empty space
      while (checkOverlap(newX, newY)) {
        newY += nodeHeight + verticalSpacing;
      }
    }
    
    // Create new node
    const id = `${selectedNodeType.type.replace(/-/g, '_')}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
    const newNode = graph.addNode({
      ...(graphNodeLibrary[selectedNodeType.type] || graphNodeLibrary.default),
      x: newX,
      y: newY - (isCycleSubNode && sourceNodeType === 'cycle-start' ? 12 : 0),
      id,
      data: {
        id,
        type: selectedNodeType.type,
        icon: selectedNodeType.icon,
        name: t(`workflow.${selectedNodeType.type}`),
        cycle: sourceNodeData.cycle, // Inherit cycle from source node
        config: selectedNodeType.config || {}
      },
    });

    // Add new node as child of parent node
    if (sourceNodeData.cycle) {
      const parentNode = graph.getNodes().find((n: any) => n.getData()?.id === sourceNodeData.cycle);
      if (parentNode) {
        parentNode.addChild(newNode);
      }
    }

    // Create edge connection
    setTimeout(() => {
      const targetPorts = newNode.getPorts();
      let targetPort;
      
      if (sourcePortGroup === 'left') {
        // Connect from left port to new node's right side
        targetPort = targetPorts.find((port: any) => port.group === 'right')?.id || 'right';
        graph.addEdge({
          source: { cell: newNode.id, port: targetPort },
          target: { cell: sourceNode.id, port: sourcePort },
          ...edgeAttrs
          // zIndex: sourceNodeData.cycle && sourceNodeType == 'cycle-start' ? 1 : sourceNodeData.cycle ? 2 : 0
        });
      } else {
        // Connect from right port to new node's left side
        targetPort = targetPorts.find((port: any) => port.group === 'left')?.id || 'left';
        graph.addEdge({
          source: { cell: sourceNode.id, port: sourcePort },
          target: { cell: newNode.id, port: targetPort },
          ...edgeAttrs
          // zIndex: sourceNodeData.cycle && sourceNodeType == 'cycle-start' ? 1 : sourceNodeData.cycle ? 2 : 0
        });
      }
      
      // Adjust loop node size when child node is added via port within loop node
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

              const padding = 50;
              const newWidth = Math.max(nodeWidth, bounds.maxX - bounds.minX + padding * 2);
              const newHeight = Math.max(120, bounds.maxY - bounds.minY + padding * 2);

              parentNode.prop('size', { width: newWidth, height: newHeight });
              
              // Update right port x position
              const ports = parentNode.getPorts();
              ports.forEach((port: any) => {
                if (port.group === 'right' && port.args) {
                  parentNode.portProp(port.id!, 'args/x', newWidth);
                }
              });
            }
          }; 
          
          adjustLoopSize();
          
          // Listen to child node movement events
          const childNodes = graph.getNodes().filter((n: any) => n.getData()?.cycle === cycleId);
          childNodes.forEach((childNode: any) => {
            childNode.on('change:position', adjustLoopSize);
          });
        }
      }
    }, 50);

    // Clean up temporary element
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
    <div style={{ maxHeight: '300px', overflowY: 'auto', minWidth: `${nodeWidth}px` }}>
      {nodeLibrary.map((category, categoryIndex) => {
        const sourceNodeData = sourceNode?.getData();
        const isChildOfLoop = sourceNodeData?.cycle && graph?.getNodes().find((n: any) => n.getData()?.id === sourceNodeData.cycle && n.getData()?.type === 'loop');
        const isChildOfIteration = sourceNodeData?.cycle && graph?.getNodes().find((n: any) => n.getData()?.id === sourceNodeData.cycle && n.getData()?.type === 'iteration');
        
        const sourcePortInfo = sourceNode?.getPorts().find((p: any) => p.id === sourcePort);
        const sourcePortGroup = sourcePortInfo?.group || sourcePort;
        const isLeftPort = sourcePortGroup === 'left';

        let filteredNodes;
        if (isChildOfLoop) {
        // Use same filtering as AddNode for child nodes of loop, but allow break
          filteredNodes = category.nodes.filter(nodeType => !['start', 'end', 'loop', 'cycle-start', 'iteration'].includes(nodeType.type));
        } else if (isChildOfIteration) {
          // Filter out loop and iteration nodes for children of iteration nodes, but allow break
          filteredNodes = category.nodes.filter(nodeType => !['start', 'end', 'loop', 'cycle-start', 'iteration'].includes(nodeType.type));
        } else {
          // Original filtering for non-loop child nodes
          filteredNodes = category.nodes.filter(nodeType =>
            nodeType.type !== 'start' && nodeType.type !== 'cycle-start' && nodeType.type !== 'break'
          );
        }

        if (isLeftPort) {
          filteredNodes = filteredNodes.filter(nodeType => nodeType.type !== 'end');
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