/*
 * Debug chat logic (streaming AI reply: start / message / end)
 */
import { useState, useRef, type MutableRefObject } from 'react'
import type { AnyObject } from 'antd/es/_util/type'
import { useTranslation } from 'react-i18next'

import type { ChatItem } from '@/components/Chat/types'
import { type ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import { memoryExtractionChat } from '@/api/memory'
import { type SSEMessage } from '@/utils/stream'

/**
 * Debug chat hook
 * @param id config id
 * @param abortRef abort reference shared with the extraction stream (used to cancel ongoing chat streaming)
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

  /** Send a debug chat message: invoke memoryExtractionChat and update chatList via the returned message events */
  const handleChatSend = (msg?: string) => {
    if (!id || chatLoading) return
    const content = (msg || '').trim()
    if (!content) return
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))

    const userMessage: ChatItem = {
      role: 'user',
      content,
      created_at: Date.now(),
      meta_data: { files },
    }
    const assistantPlaceholder: ChatItem = { role: 'assistant', content: '', created_at: Date.now() + 1 }
    setChatList(prev => [...prev, userMessage, assistantPlaceholder])
    toolbarRef.current?.setFiles([])
    setFileList([])
    setMsg('')
    setChatLoading(true)
    streamLoadingRef.current = true

    abortRef.current?.()
    abortRef.current = null

    const handleStreamMessage = (list: SSEMessage[]) => {
      list.forEach((item: AnyObject) => {
        const { content, error } = (item.data || {}) as AnyObject
        switch (item.event) {
          case 'message':
            updateAssistantContent(typeof content === 'string' ? content : '')
            break
          case 'error':
            updateAssistantContent(t('memoryExtractionEngine.debugReply'))
            break;
          case 'end':
            if (error) {
              setChatList(prev => {
                const next = [...prev]
                const lastIdx = next.length - 1
                const lastMsg = next[lastIdx]
                if (lastMsg?.role !== 'assistant') return prev
                const metaData = { ...(lastMsg.meta_data || {}) } as AnyObject
                next[lastIdx] = {
                  ...lastMsg,
                  ...(error ? { error } : {}),
                  meta_data: metaData as ChatItem['meta_data'],
                }
                return next
              })
            }
            streamLoadingRef.current = false
            setChatLoading(false)
            break
        }
      })
    }

    memoryExtractionChat({
      config_id: id,
      files: files?.map(file => {
        if (file.transfer_method === 'remote_url') {
          return file
        }
        return {
          type: file.type,
          transfer_method: 'local_file',
          upload_file_id: file.response?.data?.file_id,
        }
      }) || undefined,
      history: chatList.map(item => ({
        role: item.role,
        content: item.content,
        files: item.meta_data?.files,
      })),
      message: content,
    }, handleStreamMessage, (abort) => { abortRef.current = abort })
      .catch(() => {
        updateAssistantContent(t('memoryExtractionEngine.debugReply'))
      })
      .finally(() => {
        streamLoadingRef.current = false
        setChatLoading(false)
      })
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
