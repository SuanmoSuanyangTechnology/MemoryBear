/*
 * @Description: 通用工具栏图标按钮
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-06-10 16:27:41
 * @LastEditors: Please set LastEditors
 * @LastEditTime: 2026-06-12 16:54:06
 */
import type { FC, ReactNode } from 'react';
import { Tooltip, Flex, Badge } from 'antd';
import clsx from 'clsx';

interface IconButtonProps {
  title: ReactNode;
  icon: string;
  onClick: () => void;
  className?: string;
  badge?: number;
}

const IconButton: FC<IconButtonProps> = ({ title, icon, onClick, className, badge }) => {
  return (
    <Tooltip title={title}>
      <Flex
        align="center"
        justify="center"
        className={clsx("rb:relative rb:size-7.5 rb:cursor-pointer rb:rounded-lg rb:hover:bg-[#F6F6F6] rb:overflow-visible", className)}
        onClick={onClick}
      >
        {badge !== undefined && badge !== null ? (
          <Badge
            count={badge}
            size="small"
            overflowCount={99}
          >
            <div className={`rb:size-4 rb:bg-cover ${icon}`} />
          </Badge>
        ) : (
          <div className={`rb:size-4 rb:bg-cover ${icon}`} />
        )}
      </Flex>
    </Tooltip>
  );
};

export default IconButton;
