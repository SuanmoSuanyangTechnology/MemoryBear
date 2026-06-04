/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:09 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-04 11:36:37
 */
import { type FC } from 'react'
import ChatInput from './ChatInput'
import type { ChatProps } from './types'
import ChatContent from './ChatContent'

/**
 * Chat Component - Main component consisting of content area and input box
 * Provides complete chat interface functionality, including message display and input interaction
 */
const Chat: FC<ChatProps> = ({
  empty,
  data,
  onChange,
  onSend,
  contentClassName = '',
  loading,
  children,
  fileList,
  fileChange,
  className,
  conversationId,
  ...restProps
}) => {
  return (
    <div className={`rb:h-full rb:relative rb:pt-2 ${className}`}>
      {/* Chat content display area */}
      <ChatContent
        key={conversationId ?? 'new'}
        classNames={contentClassName}
        data={data}
        onSend={onSend}
        {...restProps}
      />

      {/* Chat input area */}
      <ChatInput
        fileList={fileList}
        onChange={onChange}
        onSend={onSend}
        loading={loading}
        fileChange={fileChange}
      >
        {children}
      </ChatInput>
    </div>
  )
}
export default Chat
