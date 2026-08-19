/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:09 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-14 14:29:46
 */
import { type FC } from 'react'
import { Flex } from 'antd'

import ChatInput from './ChatInput'
import type { ChatProps } from './types'
import ChatContent from './ChatContent'

/**
 * Chat Component - Main component consisting of content area and input box
 * Provides complete chat interface functionality, including message display and input interaction
 */
const Chat: FC<ChatProps> = ({
  data,
  onChange,
  onSend,
  loading,
  message,
  contentClassName = 'rb:mx-[16px] rb:pt-3! rb:flex-1! rb:min-h-0!',
  children,
  fileList,
  fileChange,
  className,
  conversationId,
  readOnly,
  ...restProps
}) => {
  return (
    <Flex vertical className={`rb:h-full! rb:relative rb:pt-2 rb:overflow-hidden! ${className}`}>
      {/* Chat content display area */}
      <ChatContent
        key={conversationId ?? 'new'}
        classNames={contentClassName}
        data={data}
        onSend={onSend}
        {...restProps}
      />

      {/* Chat input area */}
      {!readOnly &&
        <Flex className="rb:relative rb:mx-4! rb:mt-3! rb:mb-0!">
          <ChatInput
            fileList={fileList}
            message={message}
            onChange={onChange}
            onSend={onSend}
            loading={loading}
            fileChange={fileChange}
            className="rb:relative! rb:mt-4!"
          >
            {children}
          </ChatInput>
        </Flex>
      }
    </Flex>
  )
}
export default Chat
