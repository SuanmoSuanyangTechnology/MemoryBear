import { useEffect } from 'react';
import { useTranslation } from 'react-i18next'
import clsx from 'clsx';
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { graphNodeLibrary } from '../../constant';

import { edge_color } from '../../hooks/useWorkflowGraph'

const LoopNode: ReactShapeConfig['component'] = ({ node, graph }) => {
  const data = node.getData() || {};
  const { t } = useTranslation()

  useEffect(() => {
    initNodes()
    // 检查是否需要添加add-node
    checkAndAddAddNode()
  }, [])

  const checkAndAddAddNode = () => {
    if (!graph) return;
    
    const childNodes = graph.getNodes().filter((n: any) => n.getData()?.cycle === data.id);
    const cycleStartNodes = childNodes.filter((n: any) => n.getData()?.type === 'cycle-start');
    
    // 如果只有一个cycle-start节点且没有其他类型的子节点，则添加add-node
    if (cycleStartNodes.length === 1 && childNodes.length === 1) {
      const cycleStartNode = cycleStartNodes[0];
      const cycleStartBBox = cycleStartNode.getBBox();
      
      const addNode = graph.addNode({
        ...graphNodeLibrary.addStart,
        x: cycleStartBBox.x + 64,
        y: cycleStartBBox.y,
        data: {
          type: 'add-node',
          label: t('workflow.addNode'),
          icon: '+',
          parentId: node.id,
          cycle: data.id,
        },
      });
      
      node.addChild(addNode);
      
      // 连接cycle-start和add-node
      const sourcePorts = cycleStartNode.getPorts();
      const targetPorts = addNode.getPorts();
      const sourcePort = sourcePorts.find((port: any) => port.group === 'right')?.id || 'right';
      const targetPort = targetPorts.find((port: any) => port.group === 'left')?.id || 'left';
      
      graph.addEdge({
        source: { cell: cycleStartNode.id, port: sourcePort },
        target: { cell: addNode.id, port: targetPort },
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
        zIndex: 10
      });
    }
  }

  const initNodes = () => {
    // 检查是否存在cycle为当前节点ID的子节点，若存在则不调用initNodes，避免重复创建
    const existingCycleNodes = graph.getNodes().filter((n: any) => 
      n.getData()?.cycle === data.id
    );
    if (existingCycleNodes.length > 0) return;
    // 添加默认子节点
    const parentBBox = node.getBBox();
    const centerX = parentBBox.x + 24; // 默认节点宽度的一半
    const centerY = parentBBox.y + 50; // 默认节点高度的一半

    const cycleStartNodeId = `cycle_start_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
    const cycleStartNode = graph.addNode({
      ...graphNodeLibrary.cycleStart,
      x: centerX,
      y: centerY,
      id: cycleStartNodeId,
      data: {
        id: cycleStartNodeId,
        type: 'cycle-start',
        parentId: node.id,
        isDefault: true, // 标记为默认节点，不可删除
        cycle: data.id,
      },
    });
    const addNode = graph.addNode({
      ...graphNodeLibrary.addStart,
      x: centerX + 64,
      y: centerY,
      data: {
        type: 'add-node',
        label: t('workflow.addNode'),
        icon: '+',
        parentId: node.id,
        cycle: data.id,
      },
    });
    node.addChild(cycleStartNode)
    node.addChild(addNode)
    const sourcePorts = cycleStartNode.getPorts()
    const targetPorts = addNode.getPorts()
    let sourcePort = sourcePorts.find((port: any) => port.group === 'right')?.id || 'right';

    const edgeConfig = {
      source: {
        cell: cycleStartNode.id,
        port: sourcePort
      },
      target: {
        cell: addNode.id,
        port: targetPorts.find((port: any) => port.group === 'left')?.id || 'left'
      },
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
      zIndex: 10
    }

    graph.addEdge(edgeConfig)
  }

  return (
    <div className={clsx('rb:cursor-pointer rb:group rb:relative rb:h-full rb:w-full rb:p-2.5 rb:border rb:rounded-xl rb:bg-white rb:hover:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.12)]', {
      'rb:border-[#155EEF]': data.isSelected,
      'rb:border-[#DFE4ED]': !data.isSelected
    })}>
      <div className="rb:flex rb:items-center rb:justify-between">
        <div className="rb:flex rb:items-center rb:gap-2 rb:flex-1">
          <img src={data.icon} className="rb:w-5 rb:h-5" />
          <div className="rb:wrap-break-word rb:line-clamp-1">{data.name ?? t(`workflow.${data.type}`)}</div>
        </div>
        
        <div 
          className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]" 
          onClick={(e) => {
            e.stopPropagation()
            node.remove()
          }}
        ></div>
      </div>
      <div className="rb:mt-3 rb:min-h-[calc(100%-36px)] rb:w-full rb:bg-[radial-gradient(circle,#e5e7eb_1px,transparent_1px)] rb:bg-size-[12px_12px]"></div>
    </div>
  );
};

export default LoopNode;
