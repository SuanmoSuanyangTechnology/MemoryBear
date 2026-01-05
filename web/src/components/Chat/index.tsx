/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:09 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2025-12-11 13:43:51
 */
import { type FC } from 'react'
import ChatInput from './ChatInput'
import type { ChatProps } from './types'
import ChatContent from './ChatContent'

/**
 * 聊天组件 - 主要组件，由内容区域和输入框组成
 * 提供完整的聊天界面功能，包括消息显示和输入交互
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
  errorDesc
}) => {
  return (
    <div className="rb:h-full rb:relative rb:pt-2">
      {/* 聊天内容显示区域 */}
      <ChatContent
        classNames={contentClassName} 
        data={data} 
        streamLoading={streamLoading}
        empty={empty}
        labelFormat={labelFormat}
        errorDesc={errorDesc}
      />

      {/* 聊天输入框区域 */}
      <ChatInput onChange={onChange} onSend={onSend} loading={loading}>
        {children}
      </ChatInput>
    </div>
  )
}
export default Chat
