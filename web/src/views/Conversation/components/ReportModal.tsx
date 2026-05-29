/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-21 14:00:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-26 10:18:42
 */
/**
 * ReportModal Component
 * 
 * A modal dialog for reporting chat messages.
 * Supports selecting report type and providing additional description.
 * 
 * @component
 */

import { forwardRef, useImperativeHandle, useState } from 'react'
import { Form, Input, App } from 'antd'
import { useTranslation } from 'react-i18next'

import RbModal from '@/components/RbModal'
import type { ChatItem } from '@/components/Chat/types'
import { reportTypesUrl, reportMessage } from '@/api/application'
import type { ReportModalRef } from '../types'
import CustomSelect from '@/components/CustomSelect'

/** Props interface for ReportModal component */
interface ReportModalProps {
  /** Share token for API authentication */
  shareToken: string
}

/** Report modal component for reporting inappropriate messages */
const ReportModal = forwardRef<ReportModalRef, ReportModalProps>(({
  shareToken,
}, ref) => {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [visible, setVisible] = useState(false)
  const [loading, setLoading] = useState(false)
  const [currentItem, setCurrentItem] = useState<ChatItem | null>(null)
  const [form] = Form.useForm()

  /** Close the modal */
  const handleClose = () => {
    setVisible(false)
    setCurrentItem(null)
    form.resetFields()
  }

  /** Open the modal with the specified message item */
  const handleOpen = (item: ChatItem) => {
    setCurrentItem(item)
    setVisible(true)
  }

  /** Handle form submission */
  const onSubmit = () => {
    form.validateFields()
      .then((values) => {
        if (!currentItem?.id || !shareToken || shareToken === '') return
        
        setLoading(true)
        reportMessage(shareToken, currentItem.id as string, {
          ...values,
          selected_text: currentItem.content || '',
        })
          .then(() => {
            message.success(t('memoryConversation.reportSuccess'))
            handleClose()
          })
          .finally(() => {
            setLoading(false)
          })
      })
  }

  /** Expose handleOpen and handleClose methods to parent component via ref */
  useImperativeHandle(ref, () => ({
    handleOpen,
  }))

  return (
    <RbModal
      title={t('memoryConversation.reportContent')}
      open={visible}
      onCancel={handleClose}
      okText={t('memoryConversation.submitReport')}
      onOk={onSubmit}
      confirmLoading={loading}
      width={480}
    >
      <Form form={form} layout="vertical">
        {/* Report Type Section */}
        <Form.Item
          name="report_type"
          label={t('memoryConversation.reportType')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <CustomSelect
            url={reportTypesUrl}
            hasAll={false}
          />
        </Form.Item>

        {/* Description Section */}
        <Form.Item
          name="report_reason"
          label={<>
            {t('memoryConversation.additionalDescription')}
            <span className="rb:text-[#9CA3AF]">{t('memoryConversation.optional')}</span>
          </>}
        >
          <Input.TextArea
            className="rb:w-full"
            placeholder={t('common.pleaseEnter')}
            rows={6}
          />
        </Form.Item>
      </Form>
    </RbModal>
  )
})

export default ReportModal
