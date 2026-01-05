/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-12-16 10:19:18
 * @LastEditors: yujiangping
 * @LastEditTime: 2025-12-22 12:31:31
 */
import { type FC } from 'react'
import { Modal, type ModalProps } from 'antd'
import { useTranslation } from 'react-i18next'
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
      <div className='rb:max-h-137.5 rb:overflow-y-auto rb:overflow-x-hidden'>
        {children}
      </div>
    </Modal>
  )
}

export default RbModal