import { type FC, type ReactNode, useState } from 'react'
import { DownOutlined, RightOutlined } from '@ant-design/icons'
import clsx from 'clsx';

import type { ToolItem, ToolMethod } from './types'
import ToolIcon from '@/views/ToolManagement/components/ToolIcon'

interface ToolRowProps {
  size?: 'small' | 'default';
  tool: ToolItem;
  methods?: ToolMethod[];
  leading?: ReactNode;
  trailing?: ReactNode;
  disabled?: boolean;
  onClick?: () => void;
  renderMethod?: (method: ToolMethod, index: number) => ReactNode;
}

const expandableTypes: ToolItem['tool_type'][] = ['mcp', 'custom']

const ToolRow: FC<ToolRowProps> = ({ size = 'default', tool, methods, leading, trailing, disabled, onClick, renderMethod }) => {
  const [expanded, setExpanded] = useState(false)
  const hasMultipleMethods = methods && methods.length >= 1 && expandableTypes.includes(tool.tool_type)

  return (
    <div className={size === 'small' ? 'rb:text-[12px]!' : ''}>
      <div
        className={clsx(`rb:flex rb:items-center rb:gap-2`, disabled ? 'rb:opacity-45 rb:cursor-not-allowed' : onClick ? 'rb:cursor-pointer' : '', {
          'rb:min-h-11': size === 'default',
          'rb:min-h-10 ': size === 'small',
        })}
        onClick={disabled ? undefined : onClick}
      >
        {leading}
        <ToolIcon iconUrl={tool.icon} className={size === 'small' ? 'rb:size-5! rb:rounded-md!' : undefined} iconClassName={size === 'small' ? 'rb:size-3.5!' : undefined} />
        <span className="rb:flex-1 rb:min-w-0 rb:truncate" title={tool.name}>{tool.name}</span>
        {hasMultipleMethods && (
          <button
            type="button"
            aria-label={`${expanded ? '收起' : '展开'}${tool.name}的方法`}
            aria-expanded={expanded}
            onClick={event => { event.stopPropagation(); setExpanded(value => !value) }}
            className="rb:cursor-pointer rb:text-gray-500 rb:p-2"
          >
            {expanded ? <DownOutlined /> : <RightOutlined />}
          </button>
        )}
        {trailing}
      </div>
      {hasMultipleMethods && expanded && (
        <div className={"rb:ml-3 rb-border-l rb:pl-2 rb:mb-2"}>
          {methods.map((method, index) => renderMethod ? renderMethod(method, index) : (
            <div key={method.method_id || index} className="rb:flex rb:items-center rb:gap-2 rb:min-h-9 rb:pl-2">
              <span className="rb:min-w-0 rb:flex-1 rb:truncate" title={method.name}>
                {method.name}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default ToolRow
