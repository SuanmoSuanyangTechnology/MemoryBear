/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:14 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-23 17:46:25
 */
import { type FC, useEffect, useMemo, useState } from 'react'
import { Flex, Input } from 'antd'
import clsx from 'clsx'

import type { ChatInputProps } from './types'
import FileList from './FileList'

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
  const [inputValue, setInputValue] = useState('')
  const [isFocus, setIsFocus] = useState(false)

  // Clear input when external message is cleared
  useEffect(() => {
    if (!message) setInputValue('')
  }, [message])

  // Clear input when loading
  useEffect(() => {
    if (loading) setInputValue('')
  }, [loading])

  const handleDelete = (file: any) => {
    fileChange?.(fileList?.filter(item => {
      return item.thumbUrl && file.thumbUrl ? item.thumbUrl !== file.thumbUrl
        : item.url && file.url ? item.url !== file.url
          : item.uid !== file.uid
    }) || [])
  }

  const previewFileList = useMemo(() => {
    return fileList?.map(file => ({
      ...file,
      url: file.thumbUrl || file.url || (file.originFileObj ? URL.createObjectURL(file.originFileObj) : undefined)
    })) || []
  }, [fileList])


  const handleSend = () => {
    if (loading || !inputValue || inputValue.trim() === '') return
    onSend(inputValue)
  }

  const canSend = !loading && inputValue.trim() !== ''

  return (
    <div className={`rb:absolute rb:bottom-3 rb:left-0 rb:right-0 rb:w-full ${className}`}>
      <Flex gap={0} vertical justify="space-between" className={clsx("rb-border rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)] rb:rounded-3xl rb:min-h-30", {
        ' rb:border-[#171719]!': isFocus
      })}>
        <div className="rb:overflow-x-auto rb:max-w-full">
          <FileList fileList={previewFileList} onDelete={handleDelete} />
        </div>
        {/* Message input area */}
        <Input.TextArea
          value={inputValue}
          className="rb:m-[10px_12px_10px_12px]! rb:p-0! rb:w-[calc(100%-24px)]! rb:flex-[1_1_auto] rb:h-15! rb:resize-none! rb:rounded-none!"
          variant="borderless"
          onChange={(e) => {
            setInputValue(e.target.value)
            onChange?.(e.target.value)
          }}
          onKeyDown={(e) => {
            // Enter to send, Shift+Enter for new line
            if (e.key === 'Enter' && !e.shiftKey && (e.target as HTMLTextAreaElement).value?.trim() !== '' && !loading) {
              e.preventDefault();
              handleSend();
            }
          }}
          onFocus={() => setIsFocus(true)}
          onBlur={() => setIsFocus(false)}
        />

        {/* Bottom action area */}
        <Flex align="center" justify="space-between" gap={8} className="rb:mx-2.5! rb:mb-2.5!">
          <div className="rb:flex-1">{children}</div>
          <Flex align="center" justify="center"
            className={clsx('rb:size-7 rb:rounded-full rb:shadow-[0px 2px 12px 0px rgba(23,23,25,0.1)]', {
              'rb:cursor-not-allowed rb:bg-[#F6F6F6]': !canSend,
              'rb:cursor-pointer rb:bg-[#171719]': canSend
            })}
            onClick={handleSend}
          >
            <div className={clsx("rb:size-4 rb:bg-cover", {
              "rb:bg-[url('@/assets/images/conversation/loading.svg')]": loading,
              "rb:bg-[url('@/assets/images/conversation/sendDisabled.svg')]": !loading && !canSend,
              "rb:bg-[url('@/assets/images/conversation/send.svg')]": canSend
            })}></div>
          </Flex>
        </Flex>
      </Flex>
    </div>
  )
}

export default ChatInput
