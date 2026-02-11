/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:23:01 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:23:01 
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

/** Custom modal component wrapper with default configurations */
const RbModal: FC<ModalProps> = ({
  onOk,
  onCancel,
  children,
  className,
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
      <div className='rb:max-h-137.5 rb:overflow-y-auto rb:overflow-x-hidden'>
        {children}
      </div>
    </Modal>
  )
}

export default RbModal