/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:16:10 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-25 14:02:17
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
const RbButton: FC<ButtonProps> = (props) => {
  const { children } = props;

  return (
    <Button>
      {children}
    </Button>
  )
}
export default memo(RbButton)
