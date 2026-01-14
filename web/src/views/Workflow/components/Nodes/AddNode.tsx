import { useState } from 'react';
import { Popover } from 'antd';
import clsx from 'clsx';
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { nodeLibrary, graphNodeLibrary } from '../../constant';
import { useTranslation } from 'react-i18next';

const AddNode: ReactShapeConfig['component'] = ({ node, graph }) => {
  const data = node?.getData() || {};
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const handleNodeSelect = (selectedNodeType: any) => {
    const parentBBox = node.getBBox();
    const cycleId = data.cycle;

    const id = `${selectedNodeType.type.replace(/-/g, '_') }_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
    const newNode = graph.addNode({
      ...(graphNodeLibrary[selectedNodeType.type] || graphNodeLibrary.default),
      x: parentBBox.x,
      y: parentBBox.y,
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

    // 将新节点添加为父节点的子节点
    if (cycleId) {
      const parentNode = graph.getNodes().find((n: any) => n.getData()?.id === cycleId);
      if (parentNode) {
        parentNode.addChild(newNode);
      }
    }

    const incomingEdges = graph.getIncomingEdges(node);
    const outgoingEdges = graph.getOutgoingEdges(node);
    
    incomingEdges?.forEach(edge => {
      graph.addEdge({
        source: { cell: edge.getSourceCellId(), port: edge.getSourcePortId() },
        target: { cell: newNode.id, port: newNode.getPorts().find((port: any) => port.group === 'left')?.id || 'left' },
        attrs: edge.getAttrs(),
        zIndex: 3
      });
    });

    outgoingEdges?.forEach(edge => {
      const targetCell = graph.getCellById(edge.getTargetCellId()) as any;
      const targetPortId = targetCell?.getPorts?.()?.find((port: any) => port.group === 'left')?.id || edge.getTargetPortId();
      graph.addEdge({
        source: { cell: newNode.id, port: newNode.getPorts().find((port: any) => port.group === 'right')?.id || 'right' },
        target: { cell: edge.getTargetCellId(), port: targetPortId },
        attrs: edge.getAttrs(),
        zIndex: 3
      });
    });

    // 删除所有add-node类型的节点
    graph.getNodes().forEach((n: any) => {
      if (n.getData()?.type === 'add-node' && n.getData()?.cycle === cycleId) {
        n.remove();
      }
    });

    // 自动调整循环节点大小
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
          
          const padding = 20;
          const newWidth = Math.max(240, bounds.maxX - bounds.minX + padding * 2);
          const newHeight = Math.max(120, bounds.maxY - bounds.minY + padding * 2);
          
          loopNode.prop('size', { width: newWidth, height: newHeight });
        }
      };
      
      adjustLoopSize();
      
      // 监听子节点移动事件
      const childNodes = graph.getNodes().filter((n: any) => n.getData()?.cycle === cycleId);
      childNodes.forEach((childNode: any) => {
        childNode.on('change:position', adjustLoopSize);
      });
    }
    setOpen(false);
  };

  const content = (
    <div style={{ maxHeight: '300px', overflowY: 'auto', minWidth: '240px' }}>
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
                <img src={nodeType.icon} className="rb:w-4 rb:h-4" />
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
      <div 
        className={clsx('rb:group rb:relative rb:h-11 rb:w-22 rb:border rb:rounded-xl rb:flex rb:items-center rb:justify-center rb:text-[12px] rb:p-1 rb:box-border rb:cursor-pointer', {
          'rb:border-orange-500 rb:border-[3px] rb:bg-white rb:text-gray-700': data.isSelected,
          'rb:border-[#d1d5db] rb:bg-white rb:text-[#374151]': !data.isSelected
        })}
      >
        <span className="rb:overflow-hidden rb:whitespace-nowrap rb:text-ellipsis">
          {data.icon} {data.label}
        </span>
      </div>
    </Popover>
  );
};

export default AddNode;