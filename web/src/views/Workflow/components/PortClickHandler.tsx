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

  const handleNodeSelect = (selectedNodeType: any) => {
    if (!sourceNode || !graph) return;

    const sourceNodeData = sourceNode.getData();
    const sourceNodeType = sourceNodeData?.type;
    const isCycleSubNode = !!sourceNodeData.cycle;
    const isCycleContainer = (type: string) => type === 'loop' || type === 'iteration';
    const newNodeType = selectedNodeType.type;

    // Save add-node placeholder position before disabling history
    let addNodePosition = null;
    if (isCycleSubNode && sourceNodeType === 'cycle-start') {
      const cycleId = sourceNodeData.cycle;
      const addNodes = graph.getNodes().filter((n: any) =>
        n.getData()?.type === 'add-node' && n.getData()?.cycle === cycleId
      );
      if (addNodes.length > 0) addNodePosition = addNodes[0].getBBox();
    }

    // Calculate position
    const sourceBBox = sourceNode.getBBox();
    const nw = graphNodeLibrary[newNodeType]?.width || 120;
    const nh = graphNodeLibrary[newNodeType]?.height || 88;
    const hSpacing = isCycleSubNode ? 48 : 80;
    const vSpacing = 10;
    const sourcePortInfo = sourceNode.getPorts().find((p: any) => p.id === sourcePort);
    const sourcePortGroup = sourcePortInfo?.group || sourcePort;

    let newX: number, newY: number;
    if (edgeInsertion) {
      const targetBBox = edgeInsertion.targetCell.getBBox();
      const gap = targetBBox.x - (sourceBBox.x + sourceBBox.width);
      const requiredSpace = nw + hSpacing * 4;
      newX = sourceBBox.x + sourceBBox.width + hSpacing;
      newY = targetBBox.y + (targetBBox.height - nh) / 2;
      if (gap < requiredSpace) {
        const shiftX = requiredSpace - gap;
        const visited = new Set<string>();
        const shiftDownstream = (cell: any) => {
          if (visited.has(cell.id)) return;
          visited.add(cell.id);
          const pos = cell.getPosition();
          cell.setPosition(pos.x + shiftX, pos.y);
          graph.getConnectedEdges(cell, { outgoing: true }).forEach((e: any) => {
            const tCell = graph.getCellById(e.getTargetCellId());
            if (tCell?.isNode()) shiftDownstream(tCell);
          });
        };
        shiftDownstream(edgeInsertion.targetCell);
      }
    } else if (addNodePosition) {
      newX = addNodePosition.x;
      newY = addNodePosition.y;
    } else if (sourcePortGroup === 'left') {
      newX = sourceBBox.x - nw * 2 - hSpacing;
      newY = sourceBBox.y;
    } else {
      newX = sourceBBox.x + sourceBBox.width + hSpacing;
      newY = sourceBBox.y;
      const connectedNodes = new Set<string>();
      graph.getConnectedEdges(sourceNode).forEach((e: any) => {
        [e.getSourceCellId(), e.getTargetCellId()].forEach((cid: string) => {
          if (cid !== sourceNode.id) connectedNodes.add(cid);
        });
      });
      const checkOverlap = (x: number, y: number) =>
        graph.getNodes().some((n: any) => {
          if (n.id === sourceNode.id || !connectedNodes.has(n.id)) return false;
          const b = n.getBBox();
          return !(x + nw < b.x || x > b.x + b.width || y + nh < b.y || y > b.y + b.height);
        });
      while (checkOverlap(newX, newY)) newY += nh + vSpacing;
    }

    // Disable history for all graph mutations
    graph.disableHistory();

    // Remove add-node placeholder
    if (isCycleSubNode && sourceNodeType === 'cycle-start') {
      const cycleId = sourceNodeData.cycle;
      graph.getNodes()
        .filter((n: any) => n.getData()?.type === 'add-node' && n.getData()?.cycle === cycleId)
        .forEach((n: any) => n.remove());
    }

    const id = `${newNodeType.replace(/-/g, '_')}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    const newNode = graph.addNode({
      ...(graphNodeLibrary[newNodeType] || graphNodeLibrary.default),
      x: newX,
      y: newY - (isCycleSubNode && sourceNodeType === 'cycle-start' ? 12 : 0),
      id,
      data: {
        id,
        type: newNodeType,
        icon: selectedNodeType.icon,
        name: t(`workflow.${newNodeType}`),
        cycle: sourceNodeData.cycle,
        config: selectedNodeType.config || {}
      },
    });

    if (sourceNodeData.cycle) {
      const parentNode = graph.getNodes().find((n: any) => n.getData()?.id === sourceNodeData.cycle);
      if (parentNode) parentNode.addChild(newNode, { silent: true });
    }

    if (edgeInsertion) {
      const { edge: oldEdge } = edgeInsertion;
      if (oldEdge.id && graph.getCellById(oldEdge.id)) graph.removeCell(oldEdge.id);
      else graph.removeEdge(oldEdge);
    }

    const newPorts = newNode.getPorts();
    const addedCells: any[] = [newNode];

    if (edgeInsertion) {
      const { targetCell, targetPort: origTargetPort } = edgeInsertion;
      const newLeftPort = newPorts.find((p: any) => p.group === 'left')?.id || 'left';
      const newRightPort = newPorts.find((p: any) => p.group === 'right')?.id || 'right';
      addedCells.push(graph.addEdge({ source: { cell: sourceNode.id, port: sourcePort }, target: { cell: newNode.id, port: newLeftPort }, ...edgeAttrs }));
      addedCells.push(graph.addEdge({ source: { cell: newNode.id, port: newRightPort }, target: { cell: targetCell.id, port: origTargetPort }, ...edgeAttrs }));
      setEdgeInsertion(null);
    } else if (sourcePortGroup === 'left') {
      const tp = newPorts.find((p: any) => p.group === 'right')?.id || 'right';
      addedCells.push(graph.addEdge({ source: { cell: newNode.id, port: tp }, target: { cell: sourceNode.id, port: sourcePort }, ...edgeAttrs }));
    } else {
      const tp = newPorts.find((p: any) => p.group === 'left')?.id || 'left';
      addedCells.push(graph.addEdge({ source: { cell: sourceNode.id, port: sourcePort }, target: { cell: newNode.id, port: tp }, ...edgeAttrs }));
    }

    // If adding a loop/iteration node, create cycle-start, add-node and inner edge regardless of source type
    if (isCycleContainer(newNodeType)) {
      const parentBBox = newNode.getBBox();
      const cycleStartId = `cycle_start_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      const cycleStartNode = graph.addNode({
        ...graphNodeLibrary.cycleStart,
        x: parentBBox.x + 24,
        y: parentBBox.y + 70,
        id: cycleStartId,
        data: { id: cycleStartId, type: 'cycle-start', parentId: id, isDefault: true, cycle: id },
      });
      const addNodePlaceholder = graph.addNode({
        ...graphNodeLibrary.addStart,
        x: parentBBox.x + 24 + 84,
        y: parentBBox.y + 70 + 4,
        data: { type: 'add-node', label: t('workflow.addNode'), icon: '+', parentId: id, cycle: id },
      });
      newNode.addChild(cycleStartNode, { silent: true });
      newNode.addChild(addNodePlaceholder, { silent: true });
      const innerEdge = graph.addEdge({
        source: { cell: cycleStartNode.id, port: cycleStartNode.getPorts().find((p: any) => p.group === 'right')?.id || 'right' },
        target: { cell: addNodePlaceholder.id, port: addNodePlaceholder.getPorts().find((p: any) => p.group === 'left')?.id || 'left' },
        ...edgeAttrs,
      });
      addedCells.push(cycleStartNode, addNodePlaceholder, innerEdge);
    }

    // Adjust parent size if adding inside a cycle container
    const cycleId = sourceNodeData.cycle;
    if (cycleId) {
      const parentNode = graph.getNodes().find((n: any) => n.getData()?.id === cycleId);
      if (parentNode) {
        const childNodes = graph.getNodes().filter((n: any) => n.getData()?.cycle === cycleId);
        if (childNodes.length > 0) {
          const bounds = childNodes.reduce((acc: any, child: any) => {
            const b = child.getBBox();
            return { minX: Math.min(acc.minX, b.x), minY: Math.min(acc.minY, b.y), maxX: Math.max(acc.maxX, b.x + b.width), maxY: Math.max(acc.maxY, b.y + b.height) };
          }, { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity });
          const padding = 50;
          const newWidth = Math.max(nodeWidth, bounds.maxX - bounds.minX + padding * 2);
          const newHeight = Math.max(120, bounds.maxY - bounds.minY + padding * 2);
          parentNode.prop('size', { width: newWidth, height: newHeight });
          parentNode.getPorts().forEach((port: any) => {
            if (port.group === 'right' && port.args) parentNode.portProp(port.id!, 'args/x', newWidth);
          });
        }
      }
    }

    // toFront
    const bringCycleChildrenToFront = (cycleContainerId: string) => {
      graph.getEdges().forEach((e: any) => {
        const src = graph.getCellById(e.getSourceCellId());
        const tgt = graph.getCellById(e.getTargetCellId());
        if (src?.getData()?.cycle === cycleContainerId || tgt?.getData()?.cycle === cycleContainerId) e.toFront();
      });
      graph.getNodes().forEach((n: any) => { if (n.getData()?.cycle === cycleContainerId) n.toFront(); });
    };

    if (isCycleContainer(sourceNodeType)) {
      newNode.toFront(); sourceNode.toFront(); bringCycleChildrenToFront(sourceNodeData.id);
      if (isCycleContainer(newNodeType)) bringCycleChildrenToFront(id);
    } else if (isCycleContainer(newNodeType)) {
      newNode.toFront(); sourceNode.toFront(); bringCycleChildrenToFront(id);
    } else {
      addedCells.forEach(c => { if (c.isNode?.()) c.toFront(); });
    }

    // Re-enable history and manually push one batch frame for all added cells
    graph.enableHistory();
    const history = graph.getPlugin('history') as any;
    if (history) {
      const batchFrame = addedCells.map((cell: any) => ({
        batch: true,
        event: 'cell:added',
        data: { id: cell.id, node: cell.isNode(), edge: cell.isEdge(), props: cell.toJSON() },
        options: {},
      }));
      history.undoStack.push(batchFrame);
      history.redoStack = [];
      graph.trigger('history:change', { cmds: batchFrame, options: { name: 'add-node' } });
    }

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
    <Flex vertical gap={16} className="rb:max-h-75 rb:overflow-y-auto rb:p-3" style={{ minWidth: `${nodeWidth}px` }}>
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
