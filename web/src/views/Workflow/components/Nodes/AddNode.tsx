/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-09 18:31:30 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-30 11:55:10
 */
import { useState } from 'react';
import { Popover, Flex } from 'antd';
import clsx from 'clsx';
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { nodeLibrary, graphNodeLibrary, edgeAttrs, nodeWidth } from '../../constant';
import { useTranslation } from 'react-i18next';

const AddNode: ReactShapeConfig['component'] = ({ node, graph }) => {
  const data = node?.getData() || {};
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  // Handle node selection from popover and create new node replacing the add-node placeholder
  const handleNodeSelect = (selectedNodeType: any) => {
    const parentBBox = node.getBBox();
    const cycleId = data.cycle;
    const horizontalSpacing = 0;

    const id = `${selectedNodeType.type.replace(/-/g, '_') }_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
    const newNode = graph.addNode({
      ...(graphNodeLibrary[selectedNodeType.type] || graphNodeLibrary.default),
      x: parentBBox.x + horizontalSpacing,
      y: parentBBox.y - 12,
      id,
      data: {
        id,
        type: selectedNodeType.type,
        icon: selectedNodeType.icon,
        name: t(`workflow.${selectedNodeType.type}`),
        cycle: cycleId,
        parentId: data.parentId,
        config: selectedNodeType.config || {}
      },
    });

    // Add new node as child of parent node
    if (cycleId) {
      const parentNode = graph.getNodes().find((n: any) => n.getData()?.id === cycleId);
      if (parentNode) {
        parentNode.addChild(newNode);
      }
    }

    const incomingEdges = graph.getIncomingEdges(node);
    const outgoingEdges = graph.getOutgoingEdges(node);
    const addedEdges: any[] = [];

    incomingEdges?.forEach((edge: any) => {
      addedEdges.push(graph.addEdge({
        source: { cell: edge.getSourceCellId(), port: edge.getSourcePortId() },
        target: { cell: newNode.id, port: newNode.getPorts().find((port: any) => port.group === 'left')?.id || 'left' },
        ...edgeAttrs
      }));
    });

    outgoingEdges?.forEach((edge: any) => {
      const targetCell = graph.getCellById(edge.getTargetCellId()) as any;
      const targetPortId = targetCell?.getPorts?.()?.find((port: any) => port.group === 'left')?.id || edge.getTargetPortId();
      addedEdges.push(graph.addEdge({
        source: { cell: newNode.id, port: newNode.getPorts().find((port: any) => port.group === 'right')?.id || 'right' },
        target: { cell: edge.getTargetCellId(), port: targetPortId },
        ...edgeAttrs
      }));
    });

    // Remove all add-node type nodes
    graph.getNodes().forEach((n: any) => {
      if (n.getData()?.type === 'add-node' && n.getData()?.cycle === cycleId) {
        n.remove();
      }
    });

    setTimeout(() => {
      addedEdges.forEach(e => {
        const src = graph.getCellById(e.getSourceCellId());
        const tgt = graph.getCellById(e.getTargetCellId());
        if (src?.isNode()) src.toFront();
        if (tgt?.isNode()) tgt.toFront();
      });
    }, 50);

    // Automatically adjust loop node size
    const loopNode = graph.getNodes().find((n: any) => n.getData()?.id === cycleId);
    if (loopNode) {
      const adjustLoopSize = () => {
        const childNodes = graph.getNodes().filter((n: any) => n.getData()?.cycle === cycleId);
        if (childNodes.length > 0) {
          const bounds = childNodes.reduce((acc, child) => {
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
          
          loopNode.prop('size', { width: newWidth, height: newHeight });
          
          // Update right port x position
          const ports = loopNode.getPorts();
          ports.forEach(port => {
            if (port.group === 'right' && port.args) {
              loopNode.portProp(port.id!, 'args/x', newWidth);
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
    setOpen(false);
  };

  const content = (
    <div style={{ maxHeight: '300px', overflowY: 'auto', minWidth: `${nodeWidth}px'` }}>
      {nodeLibrary.map((category, categoryIndex) => {
        const filteredNodes = category.nodes.filter(nodeType => 
          nodeType.type !== 'start' && nodeType.type !== 'end' && nodeType.type !== 'iteration' && nodeType.type !== 'loop' && nodeType.type !== 'cycle-start'
        );
        
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
                <div className={`rb:size-4 rb:bg-cover ${nodeType.icon}`} />
                <span style={{ fontSize: '14px' }}>{t(`workflow.${nodeType.type}`)}</span>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );

  return (
    <Popover
      content={content}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="bottomLeft"
    >
      <Flex
        align="center"
        justify="center"
        gap={4}
        className={clsx('rb:text-[#212332] rb:font-medium rb:text-[12px] rb:cursor-pointer rb:group rb:relative rb:h-full rb:w-full rb:border rb:rounded-lg rb:bg-[#FCFCFD] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:border-[#FCFCFD] rb:flex rb:items-center rb:justify-center', {
          'rb:border-orange-500 rb:border-[3px] rb:bg-[#FCFCFD] rb:text-[#475467]': data.isSelected,
          'rb:border-[#d1d5db] rb:bg-[#FCFCFD] rb:text-[#374151]': !data.isSelected
        })}
      >
        <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/workflow/node_plus.png')]"></div>
        {data.label}
      </Flex>
    </Popover>
  );
};

export default AddNode;