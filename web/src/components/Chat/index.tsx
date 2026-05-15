/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:09 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-15 17:20:31
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
  streamLoading = false,
  loading,
  contentClassName = '',
  children,
  labelFormat,
  errorDesc,
  fileList,
  fileChange,
  className,
  renderRuntime,
  conversationId,
  userIcon,
  assistantIcon,
  isSupportTools = false,
  handleFeedback,
}) => {
  return (
    <div className={`rb:h-full rb:relative rb:pt-2 ${className}`}>
      {/* Chat content display area */}
      <ChatContent
        userIcon={userIcon}
        assistantIcon={assistantIcon}
        key={conversationId ?? 'new'}
        classNames={contentClassName}
        data={data}
        streamLoading={streamLoading}
        empty={empty}
        labelFormat={labelFormat}
        errorDesc={errorDesc}
        renderRuntime={renderRuntime}
        onSend={onSend}
        isSupportTools={isSupportTools}
        handleFeedback={handleFeedback}
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
