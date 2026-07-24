/*
 * Debug chat logic (streaming AI reply: start / message / end)
 */
import { useState, useRef, type MutableRefObject } from 'react'
import { useTranslation } from 'react-i18next'
import type { ChatItem } from '@/components/Chat/types'
import { type ChatToolbarRef } from '@/components/Chat/ChatToolbar'

/**
 * Debug chat hook
 * @param id config id
 * @param abortRef abort reference shared with the extraction stream (kept as a placeholder, not used by the current send logic)
 */
export const useDebugChat = (
  id: string | undefined,
  abortRef: MutableRefObject<(() => void) | null>,
) => {
  const { t } = useTranslation()
  const [msg, setMsg] = useState('')
  const [chatList, setChatList] = useState<ChatItem[]>([])
  const [chatLoading, setChatLoading] = useState(false)
  const [fileList, setFileList] = useState<any[]>([])
  const [conversationId] = useState<string | null>(null)
  const toolbarRef = useRef<ChatToolbarRef>(null)
  const streamLoadingRef = useRef(false)

  /** Append a user message */
  const appendUserMessage = (content: string, files: any[]) => {
    const userMessage: ChatItem = {
      role: 'user',
      content,
      created_at: Date.now(),
      meta_data: { files },
    }
    setChatList(prev => [...prev, userMessage])
  }

  /** Append an empty assistant message placeholder */
  const appendAssistantPlaceholder = () => {
    setChatList(prev => [...prev, { role: 'assistant', content: '', created_at: Date.now() }])
  }

  /** Append streaming content to the last assistant message */
  const updateAssistantContent = (content?: string, patch?: Partial<ChatItem>) => {
    if (!content && !patch) return
    setChatList(prev => {
      const next = [...prev]
      const lastIndex = next.length - 1
      const lastMsg = next[lastIndex] as ChatItem
      if (lastMsg?.role !== 'assistant') return prev
      next[lastIndex] = {
        ...lastMsg,
        content: (lastMsg.content || '') + (content || ''),
        ...patch,
      }
      return next
    })
  }

  /** Send a debug chat message: no API call, just append a fixed reply */
  const handleChatSend = (msg?: string) => {
    if (!id || chatLoading) return
    const content = (msg || '').trim()
    if (!content) return
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))

    appendUserMessage(content, files)
    appendAssistantPlaceholder()
    toolbarRef.current?.setFiles([])
    setFileList([])
    setMsg('')

    streamLoadingRef.current = false
    updateAssistantContent(t('memoryExtractionEngine.debugReply'))
  }

  /** Clear the debug chat content */
  const clearChat = () => {
    setChatList([])
    setMsg('')
    setFileList([])
    toolbarRef.current?.setFiles([])
  }

  return {
    msg,
    setMsg,
    chatList,
    chatLoading,
    fileList,
    setFileList,
    conversationId,
    toolbarRef,
    streamLoadingRef,
    handleChatSend,
    clearChat,
  }
}
