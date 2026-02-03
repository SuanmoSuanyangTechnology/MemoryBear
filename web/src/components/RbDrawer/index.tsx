/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-07 14:16:33
 * @LastEditors: ZhaoYing
 * @LastEditTime: 2026-02-02 15:23:01 
 */
/**
 * RbDrawer Component
 * 
 * A customized drawer component that extends Ant Design's Drawer with:
 * - Internal state management for open/close
 * - Custom close button in header
 * - Full-height flex layout for content
 * - Automatic state synchronization with external control
 * 
 * @component
 */

import { type FC, useState, useEffect } from 'react'
import { Button, Drawer, Space } from 'antd';
import type { DrawerProps } from 'antd';
import { CloseOutlined  } from '@ant-design/icons';

/** Custom drawer component with internal state management and custom close button */
const RbDrawer: FC<DrawerProps> =({
    children,
    size = 'large',
    open: externalOpen,
    onClose,
    ...props
}) => {
  /** Internal state management - component fully controls open state internally */
  const [internalOpen, setInternalOpen] = useState(false);
  
  /** Sync internal state when external open prop changes */
  useEffect(() => {
    if (externalOpen !== undefined) {
      setInternalOpen(externalOpen);
    }
  }, [externalOpen]);
  
  /** Ensure internal state syncs to true when external open is true (handles repeated opening) */
  useEffect(() => {
    if (externalOpen === true && !internalOpen) {
      setInternalOpen(true);
    }
  }, [externalOpen, internalOpen]);

  /** Handle drawer close - updates internal state and notifies parent */
  const handleClose = (e: React.MouseEvent | React.KeyboardEvent) => {
    /** Update internal state to close drawer */
    setInternalOpen(false);
    /** If external onClose is provided, call it to notify parent */
    onClose?.(e);
  }

  /** Handle close button click */
  const handleButtonClose = (e: React.MouseEvent) => {
    handleClose(e);
  }

  return (
    <Drawer
      placement="right"
      closeIcon={null}
      size={size}
      width={800}
      onClose={handleClose}
      open={internalOpen}
      extra={
        <Space>
          {/* Custom close button in header */}
          <Button type='text' icon={<CloseOutlined />} onClick={handleButtonClose}/>
        </Space>
      }
      {...props}
    >
      {/* Full-height flex container for content */}
      <div className='rb:flex rb:flex-col rb:h-full'>
        {children}
      </div>
    </Drawer>
  )
}

export default RbDrawer;