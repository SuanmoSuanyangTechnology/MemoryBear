/**
 * ToolCard - 通用工具卡片组件
 * 
 * 统一内置工具、MCP、自定义工具、工作流的卡片展示样式，
 * 支持自定义底部操作区域。
 */
import { type ReactNode } from 'react';
import { Tooltip } from 'antd';
import RbCard from '@/components/RbCard';
import type { ToolItem, WorkflowToolItem } from '../types';
import ToolIcon from './ToolIcon'

interface ToolCardProps {
  item: ToolItem | WorkflowToolItem;
  statusTag: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  onClick?: () => void;
}

export default function ToolCard({
  item,
  statusTag,
  description,
  children,
  onClick,
}: ToolCardProps) {
  return (
    <RbCard
      isNeedTooltip={false}
      avatarText={<ToolIcon className="rb:size-11" iconClassName="rb:size-7" iconUrl={item.icon} />}
      avatarClassName="rb:bg-transparent!"
      title={
        <div className="rb:flex-1!">
          <div className="rb:leading-5.5 rb:wrap-break-word rb:line-clamp-1">
            {item.name}
          </div>
          {description && (
            <Tooltip title={description}>
              <div className="rb:text-gray-600 rb:text-[12px] rb:font-normal! rb:leading-5 rb:wrap-break-word rb:line-clamp-1">
                {description}
              </div>
            </Tooltip>
          )}
        </div>
      }
      extra={<div className="rb:ml-2">{statusTag}</div>}
      className={onClick ? 'rb:group/card rb:cursor-pointer' : undefined}
      onClick={onClick}
    >
      {children}
    </RbCard>
  );
}
