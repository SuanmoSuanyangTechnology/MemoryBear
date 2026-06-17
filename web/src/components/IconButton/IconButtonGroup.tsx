/*
 * @Description: Group of icon buttons displayed in a single bordered container
 * @Version: 0.0.1
 * @Author: ZhaoYing
 * @Date: 2026-06-11 17:10:00
 * @LastEditors: Please set LastEditors
 * @LastEditTime: 2026-06-12 16:25:23
 */
import type { FC, ReactNode } from 'react';
import { Flex } from 'antd';
import clsx from 'clsx';

import IconButton from './index';

export interface IconButtonItem {
  title: ReactNode;
  icon: string;
  onClick: () => void;
  className?: string;
  badge?: number;
}

interface IconButtonGroupProps {
  /** Buttons rendered in order from left to right. */
  items: IconButtonItem[];
  /** Extra classes appended to the default container styles. */
  className?: string;
  iconClassName?: string;
}

const IconButtonGroup: FC<IconButtonGroupProps> = ({ items, className, iconClassName }) => {
  return (
    <Flex
      gap={2}
      align="center"
      className={clsx('rb-border rb:rounded-lg rb:h-8', className)}
    >
      {items.map((item, index) => (
        <IconButton
          key={index}
          title={item.title}
          icon={item.icon}
          onClick={item.onClick}
          className={item.className || iconClassName}
          badge={item.badge}
        />
      ))}
    </Flex>
  );
};

export default IconButtonGroup;
