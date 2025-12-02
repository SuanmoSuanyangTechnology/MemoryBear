import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import copy from 'copy-to-clipboard'
import { Button, App } from 'antd'


type ICopyBtnProps = {
  value: string;
  className?: string;
  style?: React.CSSProperties;
}

const CopyBtn: FC<ICopyBtnProps> = ({
  value,
  className,
  style,
}) => {
  const { t } = useTranslation()
  const { message } = App.useApp()

  const handleCopy = () => {
    copy(value)
    message.success(t('common.copySuccess'))
  }

  return (
    <Button onClick={handleCopy} className={className} style={style}>{t('common.copy')}</Button>
  )
}

export default CopyBtn
