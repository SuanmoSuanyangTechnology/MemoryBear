/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:14 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-06 13:36:20
 */
import { type FC, useEffect, useMemo } from 'react'
import { Flex, Input, Form } from 'antd'

import SendIcon from '@/assets/images/conversation/send.svg'
import SendDisabledIcon from '@/assets/images/conversation/sendDisabled.svg'
import LoadingIcon from '@/assets/images/conversation/loading.svg'
import type { ChatInputProps } from './types'

/**
 * Chat Input Component
 * Provides message input and send functionality, supports keyboard shortcuts and loading state display
 */
const ChatInput: FC<ChatInputProps> = ({
  message,
  onSend,
  loading,
  children,
  fileList,
  fileChange,
  className = '',
  onChange
}) => {
  const [form] = Form.useForm()
  const values = Form.useWatch([], form)
  // Monitor form value changes to control send button state

  // Clear form when external message is empty
  useEffect(() => {
    if (!message) {
      form.setFieldsValue({
        message: undefined,
      })
    }
  }, [form, message])
  
  // Clear input when loading
  useEffect(() => {
    if (loading) {
      form.setFieldsValue({
        message: undefined,
      })
    }
  }, [loading])


  const handleDelete = (file: any) => {
    fileChange?.(fileList?.filter(item => {
      return item.thumbUrl && file.thumbUrl ? item.thumbUrl !== file.thumbUrl
        : item.url && file.url ? item.url !== file.url
        : item.uid !== file.uid
    }) || [])
  }
  // Convert file object to preview URL
  const previewFileList = useMemo(() => {
    return fileList?.map(file => ({
      ...file,
      url: file.thumbUrl || file.url || (file.originFileObj ? URL.createObjectURL(file.originFileObj) : undefined)
    })) || []
  }, [fileList])

  const handleSend = () => {
    if (loading || !values || !values?.message || values?.message?.trim() === '') return
    onSend(values.message)
  }

  return (
    <div className={`rb:absolute rb:bottom-3 rb:left-0 rb:right-0 rb:w-full ${className}`}>
      <Flex vertical justify="space-between" className="rb:border rb:border-[#DFE4ED] rb:rounded-xl rb:min-h-30">
        {previewFileList.length > 0 && <div className="rb:overflow-x-auto rb:max-w-full"><Flex gap={14} className="rb:mx-3! rb:mt-3! rb:w-max!">
          {previewFileList.map((file) => {
            if (file.type.includes('image')) {
              return (
                <div key={file.url || file.uid} className="rb:inline-block rb:group rb:relative rb:rounded-lg">
                  <img src={file.url} alt={file.name} className="rb:size-12! rb:rounded-lg rb:object-cover rb:cursor-pointer" />
                  <div
                    className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')] rb:hover:bg-[url('@/assets/images/conversation/delete_hover.svg')]"
                    onClick={() => handleDelete(file)}
                  ></div>
                </div>
              )
            }
            if (file.type.includes('video')) {
              return (
                <div key={file.url || file.uid} className="rb:w-45 rb:h-16 rb:inline-block rb:group rb:relative rb:rounded-lg">
                  <video src={file.url} controls className="rb:w-45 rb:h-16 rb:rounded-lg rb:object-cover rb:cursor-pointer" />
                  <div
                    className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')] rb:hover:bg-[url('@/assets/images/conversation/delete_hover.svg')]"
                    onClick={() => handleDelete(file)}
                  ></div>
                </div>
              )
            }
            if (file.type.includes('audio')) {
              return (
                <div key={file.url || file.uid} className="rb:w-45 rb:h-16 rb:inline-flex rb:items-center rb:group rb:relative rb:rounded-lg rb:bg-[#F0F3F8] rb:py-2 rb:px-2.5 rb:gap-2">
                  <audio src={file.url} controls className="rb:w-45 rb:h-16" />
                  <div
                    className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')] rb:hover:bg-[url('@/assets/images/conversation/delete_hover.svg')]"
                    onClick={() => handleDelete(file)}
                  ></div>
                </div>
              )
            }
            return (
              <div key={file.url || file.uid} className="rb:w-45 rb:text-[12px] rb:gap-2.5 rb:flex rb:items-center rb:group rb:relative rb:rounded-lg rb:bg-[#F0F3F8] rb:py-2 rb:px-2.5">
                {(file.type.includes('doc') || file.type.includes('docx') || file.type.includes('word') || file.type.includes('wordprocessingml.document')) && <div
                  className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/word_disabled.svg')] rb:hover:bg-[url('@/assets/images/conversation/word.svg')]"
                ></div>}
                {(file.type.includes('pdf')) && <div
                  className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/pdf_disabled.svg')] rb:hover:bg-[url('@/assets/images/conversation/pdf.svg')]"
                ></div>}
                {(file.type.includes('excel') || file.type.includes('spreadsheetml.sheet') || file.type.includes('csv')) && <div
                  className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/excel_disabled.svg')] rb:hover:bg-[url('@/assets/images/conversation/excel.svg')]"
                ></div>}
                <div className="rb:flex-1 rb:w-32.5">
                  <div className="rb:leading-4 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{file.name}</div>
                  <div className="rb:leading-3.5 rb:mt-0.5 rb:text-[#5B6167] rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{file.type} · {file.size}</div>
                </div>
                <div
                  className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')] rb:hover:bg-[url('@/assets/images/conversation/delete_hover.svg')]"
                  onClick={() => handleDelete(file)}
                ></div>
              </div>
            )
          })}
        </Flex></div>}
        {/* Message input form */}
        <Form form={form} layout="vertical">
          <Form.Item name="message" noStyle>
            <Input.TextArea
              className="rb:m-[10px_12px_10px_12px]! rb:p-0! rb:w-[calc(100%-24px)]! rb:flex-[1_1_auto]"
              variant="borderless"
              autoSize={{ minRows: 2, maxRows: 2 }}
              onChange={(e) => onChange?.(e.target.value)}
              onKeyDown={(e) => {
                // Enter to send, Shift+Enter for new line
                if (e.key === 'Enter' && !e.shiftKey && (e.target as HTMLTextAreaElement).value?.trim() !== '' && !loading) {
                  e.preventDefault();
                  handleSend();
                }
              }}
            />
          </Form.Item>
        </Form>

        {/* Bottom action area */}
        <Flex align="center" justify="space-between" className="rb:m-[0_10px_10px_10px]!">
          {/* Child component content (such as buttons) */}
          <div className="rb:flex-1">{children}</div>
          <div className="rb:flex rb:items-center">
            {/* Send button - display different icons based on state */}
            {loading
              ? <img src={LoadingIcon} className="rb:w-5.5 rb:h-5.5 rb:cursor-pointer" />
              : !values || !values?.message || values?.message?.trim() === ''
                ? <img src={SendDisabledIcon} className="rb:w-5.5 rb:h-5.5 rb:cursor-pointer" />
                : <img src={SendIcon} className="rb:w-5.5 rb:h-5.5 rb:cursor-pointer" onClick={handleSend} />
            }
          </div>
        </Flex>
      </Flex>
    </div>
  )
}

export default ChatInput
