/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:23:01 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-11-18 11:27:47
 */
/**
 * RbModal Component
 * 
 * A customized modal component that extends Ant Design's Modal with:
 * - Default width and styling
 * - Internationalized cancel button text
 * - Scrollable content area with max height
 * - Prevents closing on mask click
 * - Auto-destroys on hidden
 * 
 * @component
 */

import { type FC } from 'react'
import { Modal, type ModalProps } from 'antd'
import { useTranslation } from 'react-i18next'

import './index.css'

interface RbModalProps extends ModalProps {
  needWrapper?: boolean;
}

/** Custom modal component wrapper with default configurations */
const RbModal: FC<RbModalProps> = ({
  onOk,
  onCancel,
  children,
  className,
  needWrapper = true,
  ...props
}) => {
  const { t } = useTranslation()
  return (
    <Modal
      onCancel={onCancel}
      width={480}
      cancelText={t('common.cancel')}
      onOk={onOk}
      destroyOnHidden={true}
      className={`rb-modal ${className || ''}`}
      maskClosable={false}
      {...props}
    >
      {/* Scrollable content container */}
      {needWrapper
        ? (
          <div className='rb:max-h-[calc(100vh-202px)] rb:overflow-y-auto rb:overflow-x-hidden'>
            {children}
          </div>
        )
        : children
      }
    </Modal>
  )
}

export default RbModal