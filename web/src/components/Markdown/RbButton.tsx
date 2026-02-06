/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:16:10 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:16:10 
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
import type { FC, ReactNode } from 'react'
import { Button } from 'antd'

/** Props interface for RbButton component */
interface RbButtonProps {
  node: {
    children: ReactNode;
  };
  children: string[]
}

/** Button component for rendering buttons in markdown */
const RbButton: FC<RbButtonProps> = (props) => {
  const { children } = props;

  return (
    <Button>
      {children}
    </Button>
  )
}
export default memo(RbButton)
