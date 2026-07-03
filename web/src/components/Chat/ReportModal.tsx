/**
 * ReportModal Component
 *
 * A shared modal dialog for reporting chat messages. It works for both the
 * public share view (submitting through `reportMessage` with a `shareToken`)
 * and the app-scoped debug panels (submitting through `draftRunReportMessage`
 * with an `appId`). Exactly one of `appId` / `shareToken` is provided.
 *
 * @component
 */
import { forwardRef, useImperativeHandle, useState } from 'react'
import { Form, Input, App } from 'antd'
import { useTranslation } from 'react-i18next'

import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect'
import type { ChatItem } from '@/components/Chat/types'
import type { ReportModalRef } from '@/views/Conversation/types'
import { reportTypesUrl, reportMessage, draftRunReportMessage } from '@/api/application'

/** Props for ReportModal — provide either an app id or a share token. */
type ReportModalProps =
  | { appId: string; shareToken?: never }
  | { appId?: never; shareToken: string }

/** Report modal component for reporting inappropriate messages */
const ReportModal = forwardRef<ReportModalRef, ReportModalProps>((props, ref) => {
  const { appId, shareToken } = props
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

  /** Handle form submission, routing to the app / share endpoint. */
  const onSubmit = () => {
    form.validateFields()
      .then((values) => {
        if (!currentItem?.id) return
        const payload = {
          ...values,
          selected_text: currentItem.content || '',
        }
        const request = appId
          ? draftRunReportMessage(appId, currentItem.id as string, payload)
          : shareToken
            ? reportMessage(shareToken, currentItem.id as string, payload)
            : undefined
        if (!request) return

        setLoading(true)
        request
          .then(() => {
            message.success(t('memoryConversation.reportSuccess'))
            handleClose()
          })
          .finally(() => {
            setLoading(false)
          })
      })
  }

  /** Expose handleOpen method to parent component via ref */
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
