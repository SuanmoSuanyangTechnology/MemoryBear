import React from 'react';
import clsx from 'clsx';
import { Button } from 'antd'
import type { ReactShapeConfig } from '@antv/x6-react-shape';

const ConditionNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node?.getData() || {};

  const addPort = (e: React.MouseEvent) => {
    if (!node || !node.addPort) return;
    e.stopPropagation();
    
    const currentPorts = node.getPorts();
    const totalPorts = currentPorts.length;
    
    // 如果没有端口，添加第一个端口和ELSE端口
    if (totalPorts === 0) {
      // 添加第一个ELIF端口
      node.addPort({
        id: 'elif_1',
        group: 'right',
        attrs: {
          text: {
            text: 'ELIF 1',
          },
        },
      });
      // 添加ELSE端口
      node.addPort({
        id: 'else',
        group: 'right',
        attrs: {
          text: {
            text: 'ELSE',
          },
        },
      });
      return;
    }
    
    // 如果只有一个端口，确保它是ELSE，然后在之前添加ELIF
    if (totalPorts === 1) {
      const existingPort = currentPorts[0];
      
      // 如果现有端口不是ELSE，先移除它
      if (node.removePort && existingPort.id !== 'else') {
        node.removePort(existingPort.id as string);
        
        // 添加ELIF端口
        node.addPort({
          id: 'elif_1',
          group: 'right',
          attrs: {
            text: {
              text: 'ELIF 1',
            },
          },
        });
      }
      
      // 添加或确保存在ELSE端口
      if (existingPort.id !== 'else') {
        node.addPort({
          id: 'else',
          group: 'right',
          attrs: {
            text: {
              text: 'ELSE',
            },
          },
        });
      }
      return;
    }
    
    // 获取最后一个端口，确保它是ELSE
    let lastPort = currentPorts[totalPorts - 1];
    
    // 如果最后一个端口不是ELSE，先移除它
    if (node.removePort && lastPort.id !== 'else') {
      node.removePort(lastPort.id as string);
      
      // 添加ELSE端口作为最后一个
      node.addPort({
        id: 'else',
        group: 'right',
        attrs: {
          text: {
            text: 'ELSE',
          },
        },
      });
      
      // 更新currentPorts和totalPorts
      const updatedPorts = node.getPorts();
      const updatedTotal = updatedPorts.length;
      lastPort = updatedPorts[updatedTotal - 1];
    }
    
    // 计算新的ELIF端口数量（最后一个是ELSE，不算在内）
    const elifCount = totalPorts - 1;
    const newElifCount = elifCount + 1;
    
    // 如果有removePort方法，先移除最后一个端口(ELSE)，添加新的ELIF端口，再添加回ELSE端口
    if (node.removePort) {
      // 移除最后一个端口(ELSE)
      node.removePort(lastPort.id as string);
      
      // 添加新的ELIF端口在倒数第二个位置
      node.addPort({
        id: `elif_${newElifCount}`,
        group: 'right',
        attrs: {
          text: {
            text: `ELIF ${newElifCount}`,
          },
        },
      });
      
      // 添加回ELSE端口
      node.addPort({
        id: 'else',
        group: 'right',
        attrs: {
          text: {
            text: 'ELSE',
          },
        },
      });
    }
  };

  // const removeElif = (e: React.MouseEvent) => {
  //   e.stopPropagation();
  // };

  return (
    <div className={clsx(`rb:border rb:rounded-[12px] rb:relative rb:min-w-[200px] rb:min-h-[120px] rb:p-2`, {
      'rb:border-orange-500 rb:border-[3px] rb:bg-orange-50 rb:text-gray-700': data.isSelected,
      'rb:border-[#d1d5db] rb:bg-[#FFFFFF] rb:text-[#374151]': !data.isSelected
    })}>

      <Button onClick={addPort}>+ 添加 ELIF</Button>
      {/* 标题区域 */}
      <div className="rb:absolute rb:-top-3 rb:left-2 rb:bg-blue-500 rb:rounded-2xl rb:px-3 rb:py-1 rb:flex rb:items-center rb:gap-1.5 rb:text-white rb:text-xs rb:font-bold rb:z-10">
        <div className="rb:w-4 rb:h-4 rb:bg-white rb:rounded rb:flex rb:items-center rb:justify-center rb:text-blue-500 rb:text-[10px]">
          🔀
        </div>
        条件分支
      </div>
    </div>
  );
};

export default ConditionNode;