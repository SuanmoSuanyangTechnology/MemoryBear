/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-07 14:16:33
 * @LastEditors: yujiangping
 * @LastEditTime: 2025-11-27 20:02:46
 */
import { type FC, useState, useEffect } from 'react'
import { Button, Drawer, Space } from 'antd';
import type { DrawerProps } from 'antd';
import { CloseOutlined  } from '@ant-design/icons';

const RbDrawer: FC<DrawerProps> =({
    children,
    size = 'large',
    open: externalOpen,
    onClose,
    ...props
}) => {
  // 内部状态管理，组件内部完全控制 open 状态
  const [internalOpen, setInternalOpen] = useState(false);
  
  // 当外部 open 变化时，同步到内部状态
  useEffect(() => {
    if (externalOpen !== undefined) {
      setInternalOpen(externalOpen);
    }
  }, [externalOpen]);
  
  // 确保当外部 open 为 true 时，内部状态也同步为 true（处理重复打开的情况）
  useEffect(() => {
    if (externalOpen === true && !internalOpen) {
      setInternalOpen(true);
    }
  }, [externalOpen, internalOpen]);

  const handleClose = (e: React.MouseEvent | React.KeyboardEvent) => {
    // 更新内部状态，关闭抽屉
    setInternalOpen(false);
    // 如果外部传入了 onClose，调用它通知外部
    onClose?.(e);
  }

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
          <Button type='text' icon={<CloseOutlined />} onClick={handleButtonClose}/>
        </Space>
      }
      {...props}
    >
      <div className='rb:flex rb:flex-col rb:h-full'>
        {children}
      </div>
    </Drawer>
  )
}

export default RbDrawer;