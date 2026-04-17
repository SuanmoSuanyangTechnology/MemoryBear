/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:16:10 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-02 15:41:16
 */
/**
 * RbButton Component
 * 
 * A button component for rendering buttons in markdown content.
 * Wraps Ant Design Button component.
 * 
 * @component
 */

import { memo } from 'react'
import type { FC } from 'react'
import { Button, type ButtonProps } from 'antd'


/** Button component for rendering buttons in markdown */
const RbButton: FC<ButtonProps> = ({ children, onClick, ...props }) => {
  const size = (props['data-size'] || 'default') as ButtonProps['size']
  const type = (props['data-variant'] || 'default') as ButtonProps['type']
  return (
    <Button {...props} size={size} type={type} className="rb:mb-3" onClick={onClick}>
      {children}
    </Button>
  )
}
export default memo(RbButton)
