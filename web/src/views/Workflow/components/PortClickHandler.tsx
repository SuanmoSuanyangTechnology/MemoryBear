/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-09 18:30:28 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-30 15:14:02
 */
import { useEffect, useState } from 'react';
import { Flex, Popover } from 'antd';
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
  const [edgeInsertion, setEdgeInsertion] = useState<any>(null);

  useEffect(() => {
    const handlePortClick = (event: CustomEvent) => {
      const { node, port, element, rect, edgeInsertion } = event.detail;
      setSourceNode(node);
      setSourcePort(port);
      setTempElement(element);
      setEdgeInsertion(edgeInsertion || null);
      setPopoverPosition({ x: rect.left, y: rect.top });
      setPopoverVisible(true);
    };

    window.addEventListener('port:click', handlePortClick as EventListener);
    const handleBlankClick = () => handlePopoverClose();
    window.addEventListener('blank:click', handleBlankClick);
    
    return () => {
      window.removeEventListener('port:click', handlePortClick as EventListener);
      window.removeEventListener('blank:click', handleBlankClick);
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
    
    // Calculate new node position
    let newX, newY;
    if (edgeInsertion) {
      // Edge insertion: place new node on the same row as target, between source and target
      const targetBBox = edgeInsertion.targetCell.getBBox();
      const gap = targetBBox.x - (sourceBBox.x + sourceBBox.width);
      const requiredSpace = nodeWidth + horizontalSpacing * 4;

      // New node x: right after source + spacing
      newX = sourceBBox.x + sourceBBox.width + horizontalSpacing;
      // Same row as target node
      newY = targetBBox.y + (targetBBox.height - nodeHeight) / 2;

      // If not enough space, shift target and all downstream nodes to the right
      if (gap < requiredSpace) {
        const shiftX = requiredSpace - gap;
        const visited = new Set<string>();
        const shiftDownstream = (cell: any) => {
          const cellId = cell.id;
          if (visited.has(cellId)) return;
          visited.add(cellId);
          const pos = cell.getPosition();
          cell.setPosition(pos.x + shiftX, pos.y);
          // Recursively shift nodes connected from right ports
          graph.getConnectedEdges(cell, { outgoing: true }).forEach((e: any) => {
            const tId = e.getTargetCellId();
            if (tId && !visited.has(tId)) {
              const tCell = graph.getCellById(tId);
              if (tCell?.isNode()) shiftDownstream(tCell);
            }
          });
        };
        shiftDownstream(edgeInsertion.targetCell);
      }
    } else if (addNodePosition) {
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

    // Edge insertion: remove old edge immediately before creating new edges
    if (edgeInsertion) {
      const { edge: oldEdge } = edgeInsertion;
      if (oldEdge.id && graph.getCellById(oldEdge.id)) {
        graph.removeCell(oldEdge.id);
      } else {
        graph.removeEdge(oldEdge);
      }
    }

    // Create edge connection
    setTimeout(() => {
      const newPorts = newNode.getPorts();

      const addedEdges: any[] = [];
      if (edgeInsertion) {
        // Edge insertion: create source→new and new→target edges
        const { targetCell, targetPort: origTargetPort } = edgeInsertion;
        const newLeftPort = newPorts.find((p: any) => p.group === 'left')?.id || 'left';
        const newRightPort = newPorts.find((p: any) => p.group === 'right')?.id || 'right';
        addedEdges.push(graph.addEdge({
          source: { cell: sourceNode.id, port: sourcePort },
          target: { cell: newNode.id, port: newLeftPort },
          ...edgeAttrs
        }));
        addedEdges.push(graph.addEdge({
          source: { cell: newNode.id, port: newRightPort },
          target: { cell: targetCell.id, port: origTargetPort },
          ...edgeAttrs
        }));
        setEdgeInsertion(null);
      } else if (sourcePortGroup === 'left') {
        // Connect from left port to new node's right side
        const targetPort = newPorts.find((port: any) => port.group === 'right')?.id || 'right';
        addedEdges.push(graph.addEdge({
          source: { cell: newNode.id, port: targetPort },
          target: { cell: sourceNode.id, port: sourcePort },
          ...edgeAttrs
        }));
      } else {
        // Connect from right port to new node's left side
        const targetPort = newPorts.find((port: any) => port.group === 'left')?.id || 'left';
        addedEdges.push(graph.addEdge({
          source: { cell: sourceNode.id, port: sourcePort },
          target: { cell: newNode.id, port: targetPort },
          ...edgeAttrs
        }));
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

      const isCycleContainer = (type: string) => type === 'loop' || type === 'iteration';
      const newNodeType = selectedNodeType.type;

      // Helper: bring all child nodes and their edges of a cycle container to front
      const bringCycleChildrenToFront = (cycleContainerId: string) => {
        
        graph.getEdges().forEach((e: any) => {
          const src = graph.getCellById(e.getSourceCellId());
          const tgt = graph.getCellById(e.getTargetCellId());
          if (src?.getData()?.cycle === cycleContainerId || tgt?.getData()?.cycle === cycleContainerId) e.toFront();
        });
        graph.getNodes().forEach((n: any) => {
          if (n.getData()?.cycle === cycleContainerId) n.toFront();
        });
      };

      if (isCycleContainer(sourceNodeType)) {
        console.log('isCycleContainer(sourceNodeType)')
        // Case 4: source is a loop/iteration node — bring new node to front, then its children
        newNode.toFront();
        sourceNode.toFront();
        bringCycleChildrenToFront(sourceNodeData.id);
      } else if (isCycleContainer(newNodeType)) {
        console.log('isCycleContainer(newNodeType)')
        // Case 3: adding a loop/iteration node from a normal node — bring new node to front, then its children
        newNode.toFront();
        sourceNode.toFront()
        bringCycleChildrenToFront(id);
      } else {
        // Case 2: normal node → normal node
        addedEdges.forEach(e => {
          const src = graph.getCellById(e.getSourceCellId());
          const tgt = graph.getCellById(e.getTargetCellId());
          if (src?.isNode()) src.toFront();
          if (tgt?.isNode()) tgt.toFront();
        });
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
    <Flex vertical gap={16} className="rb:max-h-[300px] rb:overflow-y-auto rb:p-3" style={{ minWidth: `${nodeWidth}px` }}>
      {nodeLibrary.map((category) => {
        const sourceNodeData = sourceNode?.getData();
        const isChildOfLoop = sourceNodeData?.cycle && graph?.getNodes().find((n: any) => n.getData()?.id === sourceNodeData.cycle && n.getData()?.type === 'loop');
        const isChildOfIteration = sourceNodeData?.cycle && graph?.getNodes().find((n: any) => n.getData()?.id === sourceNodeData.cycle && n.getData()?.type === 'iteration');

        let filteredNodes;
        if (isChildOfLoop || isChildOfIteration) {
          filteredNodes = category.nodes.filter(nodeType => !['start', 'end', 'loop', 'cycle-start', 'iteration'].includes(nodeType.type));
        } else {
          filteredNodes = category.nodes.filter(nodeType =>
            nodeType.type !== 'start' && nodeType.type !== 'cycle-start' && nodeType.type !== 'break'
          );
        }
        
        if (filteredNodes.length === 0) return null;
        
        return (
          <div key={category.category}>
            <div className="rb:font-semibold rb:mb-2 rb:text-[12px] rb:leading-4.5 rb:pl-1">
              {t(`workflow.${category.category}`)}
            </div>
            <Flex gap={6} vertical>
              {filteredNodes.map((nodeType) => (
                <Flex
                  key={nodeType.type}
                  align="center"
                  gap={8}
                  className="rb:rounded-xl rb:p-2! rb:border rb:border-[#EBEBEB] rb:cursor-pointer rb:hover:border rb:hover:border-[#171719]!"
                  onClick={() => handleNodeSelect(nodeType)}
                >
                  <div className={`rb:size-6 rb:bg-cover ${nodeType.icon}`} />
                  <span className="rb:font-medium rb:text-[12px] rb:leading-4">{t(`workflow.${nodeType.type}`)}</span>
                </Flex>
              ))}
            </Flex>
          </div>
        );
      })}
    </Flex>
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