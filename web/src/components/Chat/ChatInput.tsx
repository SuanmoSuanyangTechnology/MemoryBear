/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:14 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-23 17:46:25
 */
import { type FC, useEffect, useMemo, useState } from 'react'
import { Flex, Input, Form, Spin } from 'antd'
import clsx from 'clsx'

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
  const [isFocus, setIsFocus] = useState(false)
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

  const handleFocus = () => {
    setIsFocus(true)
  }
  const handleBlur = () => {
    setIsFocus(false)
  }

  return (
    <div className={`rb:absolute rb:bottom-3 rb:left-0 rb:right-0 rb:w-full ${className}`}>
      <Flex gap={0} vertical justify="space-between" className={clsx("rb-border rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)] rb:rounded-3xl rb:min-h-30", {
        ' rb:border-[#171719]!': isFocus
      })}>
        {previewFileList.length > 0 && <div className="rb:overflow-x-auto rb:max-w-full">
          <Flex gap={14} className="rb:mx-3! rb:mt-3! rb:w-max!">
          {previewFileList.map((file) => {
            if (file.type?.includes('image')) {
              return (
                <Spin key={`${file.url || file.uid}_${file.status}`} spinning={file.status === 'uploading'}>
                  <div className={clsx("rb:inline-block rb:group rb:relative rb:rounded-lg rb:bg-[#F6F6F6] rb:border rb:border-[#F6F6F6]", {
                    'rb:border-[#FF5D34]': file.status === 'error'
                  })}>
                    <img src={file.url} alt={file.name} className="rb:size-12! rb:rounded-lg rb:object-cover" />
                    <div
                      className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')] rb:hover:bg-[url('@/assets/images/conversation/delete_hover.svg')]"
                      onClick={() => handleDelete(file)}
                    ></div>
                  </div>
                </Spin>
              )
            }
            if (file.type?.includes('video')) {
              return (
                <Spin key={`${file.url || file.uid}_${file.status}`} spinning={file.status === 'uploading'}>
                  <div className={clsx("rb:w-45 rb:h-12 rb:inline-block rb:group rb:relative rb:rounded-lg rb:border rb:border-[#F6F6F6]", {
                    'rb:border-[#FF5D34]': file.status === 'error'
                  })}>
                    <video src={file.url} controls className="rb:w-45 rb:h-12 rb:rounded-lg rb:object-cover" />
                    <div
                      className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')]"
                      onClick={() => handleDelete(file)}
                    ></div>
                  </div>
                </Spin>
              )
            }
            if (file.type?.includes('audio')) {
              return (
                <Spin key={`${file.url || file.uid}_${file.status}`} spinning={file.status === 'uploading'}>
                  <div className={clsx("rb:w-45 rb:h-12rb:inline-flex rb:items-center rb:group rb:relative rb:rounded-lg rb:bg-[#F6F6F6] rb:py-2 rb:px-2.5 rb:gap-2 rb:border rb:border-[#F6F6F6]", {
                    'rb:border-[#FF5D34]': file.status === 'error'
                  })}>
                    <audio src={file.url} controls className="rb:w-45 rb:h-12" />
                    <div
                      className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')]"
                      onClick={() => handleDelete(file)}
                    ></div>
                  </div>
                </Spin>
              )
            }
            return (
              <Spin key={`${file.url || file.uid}_${file.status}`} spinning={file.status === 'uploading'}>
                <Flex
                  align="center"
                  gap={10}
                  className={clsx("rb:w-45 rb:text-[12px] rb:group rb:relative rb:rounded-lg rb:bg-[#F6F6F6] rb:py-2! rb:px-2.5! rb:border rb:border-[#F6F6F6]", {
                  'rb:border-[#FF5D34]': file.status === 'error'
                })}>
                  <div
                    className={clsx(
                      "rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/pdf_disabled.svg')]",
                      file.type?.includes('pdf')
                        ? "rb:bg-[url('@/assets/images/file/pdf.svg')]"
                        : (file.type?.includes('excel') || file.type?.includes('spreadsheetml.sheet'))
                        ? "rb:bg-[url('@/assets/images/file/excel.svg')]"
                        : file.type?.includes('csv')
                        ? "rb:bg-[url('@/assets/images/file/csv.svg')]"
                        : file.type?.includes('html')
                        ? "rb:bg-[url('@/assets/images/file/html.svg')]"
                        : file.type?.includes('json')
                        ? "rb:bg-[url('@/assets/images/file/json.svg')]"
                        : file.type?.includes('ppt')
                        ? "rb:bg-[url('@/assets/images/file/ppt.svg')]"
                        : file.type?.includes('text')
                        ? "rb:bg-[url('@/assets/images/file/txt.svg')]"
                        : file.type?.includes('markdown')
                        ? "rb:bg-[url('@/assets/images/file/md.svg')]"
                        : (file.type?.includes('doc') || file.type?.includes('docx') || file.type?.includes('word') || file.type?.includes('wordprocessingml.document'))
                        ? "rb:bg-[url('@/assets/images/file/word.svg')]"
                        : null
                    )}
                  ></div>
                  <div className="rb:flex-1 rb:w-32.5">
                    <div className="rb:leading-4 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{file.name}</div>
                    <div className="rb:leading-3.5 rb:mt-0.5 rb:text-[#5B6167] rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{file.type?.split('/')[file.type?.split('/').length - 1]} · {file.size}</div>
                  </div>
                  <div
                    className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')]"
                    onClick={() => handleDelete(file)}
                  ></div>
                </Flex>
              </Spin>
            )
          })}
        </Flex>
        </div>}
        {/* Message input form */}
        <Form form={form} layout="vertical">
          <Form.Item name="message" noStyle>
            <Input.TextArea
              className="rb:m-[10px_12px_10px_12px]! rb:p-0! rb:w-[calc(100%-24px)]! rb:flex-[1_1_auto] rb:h-15! rb:resize-none! rb:rounded-none!"
              variant="borderless"
              onChange={(e) => onChange?.(e.target.value)}
              onKeyDown={(e) => {
                // Enter to send, Shift+Enter for new line
                if (e.key === 'Enter' && !e.shiftKey && (e.target as HTMLTextAreaElement).value?.trim() !== '' && !loading) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              onFocus={handleFocus}
              onBlur={handleBlur}
            />
          </Form.Item>
        </Form>

        {/* Bottom action area */}
        <Flex align="center" justify="space-between" gap={8} className="rb:mx-2.5! rb:mb-2.5!">
          {/* Child component content (such as buttons) */}
          <div className="rb:flex-1">{children}</div>
          <Flex align="center" justify="center"
            className={clsx('rb:size-7 rb:rounded-full rb:shadow-[0px 2px 12px 0px rgba(23,23,25,0.1)]', {
              'rb:cursor-not-allowed rb:bg-[#F6F6F6]': loading || !values || !values?.message || values?.message?.trim() === '',
              'rb:cursor-pointer rb:bg-[#171719]': !loading && !(!values || !values?.message || values?.message?.trim() === '')
            })}
            onClick={handleSend}
          >
            <div className={clsx("rb:size-4 rb:bg-cover", {
              "rb:bg-[url('@/assets/images/conversation/loading.svg')]": loading,
              "rb:bg-[url('@/assets/images/conversation/sendDisabled.svg')]": !loading && (!values || !values?.message || values?.message?.trim() === ''),
              "rb:bg-[url('@/assets/images/conversation/send.svg')]": !loading && !(!values || !values?.message || values?.message?.trim() === '')
            })}></div>
          </Flex>
        </Flex>
      </Flex>
    </div>
  )
}

export default ChatInput
