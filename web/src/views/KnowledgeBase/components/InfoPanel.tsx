/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-18 16:27:41
 * @LastEditors: ZhaoYing
 * @LastEditTime: 2026-06-05 13:39:40 
 */
import type { ReactElement } from 'react';
import { Flex } from 'antd';

export interface InfoItem {
  key: string;
  label: string;
  value: string | number | undefined | ReactElement;
  icon?: string;
}

interface InfoPanelProps {
  title: string;
  items: InfoItem[];
  className?: string;
}

const InfoPanel = ({ title, items, className = '' }: InfoPanelProps) => {
  return (
    <div className={`rb:w-full ${className}`}>
      <h2 className="rb:text-lg rb:font-medium rb:mb-3">{title}</h2>
      <Flex vertical align="start" gap={24}>
        {items.map((item) => (
          <Flex key={item.key} align="start" justify="start" gap={8} className='rb:w-full'>
            {item.icon && <img src={item.icon} className='rb:size-4 rb:mt-0.5' alt="" />}
            <Flex vertical gap={8} className='rb:text-left'>
              <span className='rb:text-gray-500 rb:text-sm'>{item.label}</span>
              <span className='rb:text-gray-800'>{item.value ?? '-'}</span>
            </Flex>
          </Flex>
        ))}
      </Flex>
    </div>
  );
};

export default InfoPanel;
