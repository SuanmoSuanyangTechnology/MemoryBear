import { forwardRef, useImperativeHandle, useState } from 'react'
import ChatContent from './ChatContent'
import type { ChatItem } from './types'
import type { ReactNode } from 'react'

export interface PromptChatPanelRef {
  append: (item: ChatItem) => void
  clear: () => void
}

interface PromptChatPanelProps {
  classNames?: string
  contentClassNames?: string
  empty: ReactNode
  labelFormat: (item: ChatItem) => any
}

const PromptChatPanel = forwardRef<PromptChatPanelRef, PromptChatPanelProps>((props, ref) => {
  const [chatList, setChatList] = useState<ChatItem[]>([])

  useImperativeHandle(ref, () => ({
    append: (item) => setChatList(prev => [...prev, item]),
    clear: () => setChatList([]),
  }))

  return (
    <ChatContent
      classNames={props.classNames}
      contentClassNames={props.contentClassNames}
      empty={props.empty}
      data={chatList}
      streamLoading={false}
      labelPosition="top"
      labelFormat={props.labelFormat}
    />
  )
})

export default PromptChatPanel
