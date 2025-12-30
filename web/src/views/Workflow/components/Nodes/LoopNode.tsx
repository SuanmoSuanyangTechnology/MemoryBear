import { useEffect } from 'react';
import { useTranslation } from 'react-i18next'
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
  const data = node.getData() || {};
  const { t } = useTranslation()

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
      <div className={clsx('rb:cursor-pointer rb:group rb:relative rb:h-16 rb:w-60 rb:p-2.5 rb:border rb:rounded-xl rb:bg-white rb:hover:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.12)]', {
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
        <div className="rb:mt-6 rb:min-h-37.5 rb:w-full rb:bg-[radial-gradient(circle,#e5e7eb_1px,transparent_1px)] rb:bg-size-[12px_12px]"></div>
      </div>
    );
};

export default LoopNode;
