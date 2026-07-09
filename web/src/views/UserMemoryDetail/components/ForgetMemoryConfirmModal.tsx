/**
 * ForgetMemoryConfirmModal
 * Confirmation dialog for manually forgetting a single memory node from the graph.
 * The confirm action stays disabled until the user acknowledges the irreversible move.
 */
import { forwardRef, useImperativeHandle, useState, useEffect, useRef } from 'react'
import { App, Checkbox } from 'antd'
import { useTranslation } from 'react-i18next'

import RbModal from '@/components/RbModal'
import { deleteMemoryNode } from '@/api/memory'

/** Data required to describe the node being forgotten. */
export interface ForgetMemoryPayload {
  endUserId: string
  nodeId: string
  nodeLabel?: string
  name: string
  /** Number of graph relationships that will be disconnected. */
  relations: number
  /** Number of associated memories affected. */
  associated: number
}

export interface ForgetMemoryConfirmModalRef {
  handleOpen: (payload: ForgetMemoryPayload) => void
  handleClose: () => void
}

interface ForgetMemoryConfirmModalProps {
  /** Called after the node has been forgotten successfully. */
  onSuccess: () => void
}

const ForgetMemoryConfirmModal = forwardRef<ForgetMemoryConfirmModalRef, ForgetMemoryConfirmModalProps>(({ onSuccess }, ref) => {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [visible, setVisible] = useState(false)
  const [checked, setChecked] = useState(false)
  const [loading, setLoading] = useState(false)
  const [payload, setPayload] = useState<ForgetMemoryPayload | null>(null)
  // Seconds left before the confirm button becomes clickable after acknowledging.
  const [countdown, setCountdown] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const clearTimer = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }

  // Start a 5s countdown once acknowledged; reset when unchecked or closed.
  useEffect(() => {
    clearTimer()
    if (checked && visible) {
      setCountdown(5)
      timerRef.current = setInterval(() => {
        setCountdown(prev => {
          if (prev <= 1) {
            clearTimer()
            return 0
          }
          return prev - 1
        })
      }, 1000)
    } else {
      setCountdown(0)
    }
    return clearTimer
  }, [checked, visible])

  const handleClose = () => {
    setVisible(false)
    setChecked(false)
    setLoading(false)
    setPayload(null)
  }

  const handleOpen = (data: ForgetMemoryPayload) => {
    setChecked(false)
    setPayload(data)
    setVisible(true)
  }

  const handleConfirm = () => {
    if (!payload || !checked || countdown > 0) return
    setLoading(true)
    deleteMemoryNode({
      end_user_id: payload.endUserId,
      element_id: payload.nodeId,
    })
      .then(() => {
        message.success(t('userMemory.forgetMemorySuccess', { name: payload?.name || '' }))
        onSuccess()
        handleClose()
      })
      .finally(() => {
        setLoading(false)
      })
  }

  useImperativeHandle(ref, () => ({ handleOpen, handleClose }))

  return (
    <RbModal
      title={t('userMemory.forgetMemoryConfirmTitle')}
      open={visible}
      onCancel={handleClose}
      okText={countdown > 0 ? `${t('userMemory.confirmForget')} (${countdown}s)` : t('userMemory.confirmForget')}
      onOk={handleConfirm}
      confirmLoading={loading}
      okButtonProps={{ disabled: !checked || countdown > 0, danger: true }}
    >
      <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5.5">
        {t('userMemory.forgetMemoryConfirmDesc', {
          name: payload?.name || '',
          relations: payload?.relations ?? 0,
          associated: payload?.associated ?? 0,
        })}
      </div>
      <Checkbox
        className="rb:mt-6!"
        checked={checked}
        onChange={(e) => setChecked(e.target.checked)}
      >
        {t('userMemory.forgetMemoryConfirmCheckbox')}
      </Checkbox>
    </RbModal>
  )
})

export default ForgetMemoryConfirmModal
