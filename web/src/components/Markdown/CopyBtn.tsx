/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:15:21 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:15:21 
 */
/**
 * CopyBtn Component
 * 
 * A button component that copies text to clipboard and displays a success message.
 * Uses the copy-to-clipboard library for cross-browser compatibility.
 * 
 * @component
 */

import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import copy from 'copy-to-clipboard'
import { Button, App } from 'antd'

/** Props interface for CopyBtn component */
type ICopyBtnProps = {
  value: string;
  className?: string;
  style?: React.CSSProperties;
}

/** Copy button component that copies text to clipboard and shows success message */
const CopyBtn: FC<ICopyBtnProps> = ({
  value,
  className,
  style,
}) => {
  const { t } = useTranslation()
  const { message } = App.useApp()

  /** Copy value to clipboard and show success message */
  const handleCopy = () => {
    copy(value)
    message.success(t('common.copySuccess'))
  }

  return (
    <Button onClick={handleCopy} className={className} style={style}>{t('common.copy')}</Button>
  )
}

export default CopyBtn
