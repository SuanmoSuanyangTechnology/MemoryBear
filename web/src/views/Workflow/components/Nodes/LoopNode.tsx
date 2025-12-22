import { useEffect } from 'react';
import clsx from 'clsx';
import { Dropdown } from 'antd';
import { SmallDashOutlined } from '@ant-design/icons';
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { graphNodeLibrary } from '../../constant';

interface NodeData {
  isSelected?: boolean;
  type?: string;
  label?: string;
  icon?: string;
  parentId?: string;
  isGroup?: boolean;
}

const LoopNode: ReactShapeConfig['component'] = ({ node, graph }) => {
  const data = node.getData() as NodeData;

  useEffect(() => {
    initNodes()
  }, [])

  const initNodes = () => {
    // 添加默认子节点
    const parentBBox = node.getBBox();
    const centerX = parentBBox.x + 24; // 默认节点宽度的一半
    const centerY = parentBBox.y + 50; // 默认节点高度的一半
    
    const childNode1 = graph.addNode({
      ...graphNodeLibrary.groupStart,
      x: centerX,
      y: centerY,
      data: {
        type: 'default',
        label: '开始',
        // icon: '📌',
        parentId: node.id,
        isDefault: true // 标记为默认节点，不可删除
      },
    });
    const childNode2 = graph.addNode({
      ...graphNodeLibrary.addStart,
      x: centerX + 150,
      y: centerY,
      data: {
        type: 'default',
        label: '添加节点',
        icon: '+',
        parentId: node.id,
      },
    });
    node.addChild(childNode1)
    node.addChild(childNode2)
  }
  
  return (
    <div className={clsx('rb:group rb:border-2 rb:border-dashed rb:rounded-[12px] rb:relative rb:min-w-[300px] rb:min-h-[200px] rb:p-4', {
      'rb:border-orange-500 rb:border-[3px] rb:bg-white rb:text-gray-700': data?.isSelected,
      'rb:border-[#d1d5db] rb:bg-white rb:text-[#374151]': !data?.isSelected
    })}>
      {/* 标题区域 */}
      <div className="rb:absolute rb:-top-3 rb:left-4 rb:bg-[#10b981] rb:rounded-[20px] rb:p-[8px_16px] rb:flex rb:items-center rb:gap-2 rb:text-white rb:text-[14px] rb:font-bold rb:z-10">
        <div className="rb:w-5 rb:h-5 rb:bg-[#FFFFFF] rb:rounded-sm rb:flex rb:items-center rb:justify-center rb:text-[12px] rb:text-[#10b981]">
          ♻️
        </div>
        循环
      </div>
      <Dropdown
        menu={{items: [
          {
            key: '1',
            label: '删除',
          },
          {
            key: '2',
            label: '复制',
          },
          {
            key: '3',
            label: '删除',
          }
        ]}}
      >
        <SmallDashOutlined 
          className={clsx("rb:cursor-pointer rb:right-1 rb:top-1 rb:invisible rb:absolute rb:group-hover:visible", {
            'rb:visible': data.isSelected
          })}
        />
      </Dropdown>
      
      {/* 画布内容区域 */}
      <div className="rb:mt-6 rb:min-h-[150px] rb:w-full rb:bg-[radial-gradient(circle,#e5e7eb_1px,transparent_1px)] rb:bg-size-[12px_12px]"></div>
    </div>
  );
};

export default LoopNode;
