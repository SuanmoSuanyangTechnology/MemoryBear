import { useEffect } from 'react';
import { useTranslation } from 'react-i18next'
import clsx from 'clsx';
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { Flex } from 'antd';

import { graphNodeLibrary, edgeAttrs } from '../../constant';
import NodeTools from './NodeTools'

const LoopNode: ReactShapeConfig['component'] = ({ node, graph }) => {
  const data = node.getData() || {};
  const { t } = useTranslation()

  useEffect(() => {
    // 使用setTimeout确保在所有节点都添加完成后再创建连线
    const timer = setTimeout(() => {
      initNodes()
      checkAndAddAddNode()
    }, 50)
    
    return () => clearTimeout(timer)
  }, [graph])

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
        x: cycleStartBBox.x + 84,
        y: cycleStartBBox.y + 4,
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

      // 然后创建连线
      graph.addEdge({
        source: { cell: cycleStartNode.id, port: sourcePort },
        target: { cell: addNode.id, port: targetPort },
        ...edgeAttrs,
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
    const centerX = parentBBox.x + 24;
    const centerY = parentBBox.y + 70;

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
      x: centerX + 84,
      y: centerY + 4,
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
      ...edgeAttrs
    }
    graph.addEdge(edgeConfig)
  }

  return (
    <div className={clsx('rb:cursor-pointer rb:group rb:relative rb:h-full rb:w-full rb:p-3 rb:border rb:rounded-2xl rb:bg-[#FCFCFD] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)]', {
      'rb:border-[#171719]': data.isSelected,
      'rb:border-[#DFE4ED]': !data.isSelected
    })}>
      <NodeTools node={node} />
      <Flex align="center" gap={8} className="rb:flex-1">
        <img src={data.icon} className="rb:size-6" />
        <div className="rb:wrap-break-word rb:line-clamp-1">{data.name ?? t(`workflow.${data.type}`)}</div>
      </Flex>
      <div className="rb:mt-3 rb:min-h-[calc(100%-36px)] rb:w-full rb:bg-[radial-gradient(circle,#939AB1_1px,#F0F3F8_1px)] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:rounded-[10px] rb:bg-size-[12px_12px]"></div>
    </div>
  );
};

export default LoopNode;
