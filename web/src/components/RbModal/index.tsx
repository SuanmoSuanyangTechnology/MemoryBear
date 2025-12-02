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
      {...props}
    >
      <div className='rb:max-h-[550px] rb:overflow-y-auto rb:overflow-x-hidden'>
        {children}
      </div>
    </Modal>
  )
}

export default RbModal