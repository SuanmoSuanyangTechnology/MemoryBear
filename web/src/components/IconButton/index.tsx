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
  title?: ReactNode;
  icon: string;
  onClick?: () => void;
  className?: string;
  badge?: number;
  hideTooltip?: boolean;
  disabled?: boolean;
}

const IconBtn: FC<IconButtonProps> = ({ icon, onClick, className, badge, disabled = false }) => (
  <Flex
    align="center"
    justify="center"
    className={clsx("rb:relative rb:size-7.5 rb:rounded-lg rb:hover:bg-gray-100 rb:overflow-visible", className, {
      'rb:opacity-65 rb:cursor-not-allowed': disabled,
      'rb:cursor-pointer': !disabled
    })}
    onClick={disabled ? undefined : onClick}
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
)

const IconButton: FC<IconButtonProps> = ({ title, icon, onClick, className, badge, hideTooltip = false, disabled = false }) => {
  if (hideTooltip || !title) {
    return (
      <IconBtn icon={icon} onClick={onClick} className={className} badge={badge} disabled={disabled} />
    )
  }
  return (
    <Tooltip title={title} trigger="hover">
      <span>
        <IconBtn icon={icon} onClick={onClick} className={className} badge={badge} disabled={disabled} />
      </span>
    </Tooltip>
  );
};

export default IconButton;
