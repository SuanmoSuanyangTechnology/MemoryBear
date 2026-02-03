/**
 * Card Component
 * Styled wrapper for conversation and analysis panels
 * Provides consistent layout and styling
 */

import { Card } from 'antd'
import { type FC, type ReactNode } from 'react'

/**
 * Component props
 */
interface RbCardProps {
  children: ReactNode;
  title: string;
  bodyClassName?: string;
  style?: React.CSSProperties;
}
const RbCard: FC<RbCardProps> = ({ children, title, bodyClassName, style, ...props }) => {
  return (
    <Card 
      title={title}
      classNames={{
        header: "rb:min-h-[40px]! rb:p-[0_16px]! rb:rounded-[12px_12px_0_0]! rb:text-[14px]! rb:leading-[20px]! rb:font-medium! rb:border-b-[#DFE4ED]",
        body: `rb:h-[calc(100%-40px)] rb:p-[16px]! ${bodyClassName || ''}`,
      }}
      style={{
        borderRadius: '12px',
        borderColor: '#DFE4ED',
        background: '#FBFDFF',
        height: 'calc(100vh - 152px)',
        ...style
      }}
      {...props}
    >
      {children}
    </Card>
  )
}
export default RbCard