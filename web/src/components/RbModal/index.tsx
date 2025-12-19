import { type FC } from 'react'
import { Modal, type ModalProps } from 'antd'
import { useTranslation } from 'react-i18next'

const RbModal: FC<ModalProps> = ({
  onOk,
  onCancel,
  children,
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