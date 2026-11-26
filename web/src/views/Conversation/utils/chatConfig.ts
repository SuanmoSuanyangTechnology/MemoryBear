/**
 * Shared Chat / ChatToolbar props configuration.
 * Chat and ChatToolbar props shared by desktop and mobile layouts are
 * centralized here to avoid duplicated maintenance.
 * Divergent items (empty, labelFormat, user/assistant avatars, left/rightExtra,
 * etc.) are still passed by each layout.
 */
import clsx from 'clsx'

import type { ChatProps } from '@/components/Chat/types'
import { shareFileUploadUrlWithoutApiPrefix } from '@/api/fileStorage'

import type { ConversationCtx } from '../hooks/useConversation'

/** Chat props shared by desktop and mobile layouts */
export const buildSharedChatProps = (ctx: ConversationCtx) => {
  const {
    conversation_id, chatList, fileList, setFileList, toolbarRef, config, isShare,
    streamLoadingRef, chatIsEnded, loading, showMemoryRecall, setMessage, handleSend, regenerateMessages,
    deleteMessage, reportMsg, handleVersionChange, handleFeedback, handleFavorite,
    handleInterventionActionClick, loadEarlierMessages, chatHasNext, loadingEarlier,
  } = ctx

  const isSupportTools = !!config.app_type
  return {
    contentClassName: clsx({
      'rb:h-full rb:w-full': isShare,
      'rb:h-[calc(100%-144px)] rb:w-full': !fileList.length && !isShare,
      'rb:h-[calc(100%-208px)] rb:w-full': fileList.length && !isShare,
    }),
    data: chatList,
    streamLoading: streamLoadingRef.current,
    showMemoryRecall,
    memoryRecallStreaming: loading,
    loading,
    onChange: setMessage,
    onSend: handleSend,
    conversationId: conversation_id,
    fileList,
    fileChange: (list: any[]) => {
      setFileList(list || [])
      toolbarRef.current?.setFiles(list || [])
    },
    isSupportTools: isSupportTools && !isShare,
    handleFeedback: isSupportTools ? handleFeedback : undefined,
    handleFavorite,
    isEnded: chatIsEnded.current,
    readOnly: isShare,
    deleteMsg: isSupportTools ? deleteMessage : undefined,
    reportMsg: isSupportTools ? reportMsg : undefined,
    regenerateMessages: isSupportTools ? regenerateMessages : undefined,
    regenerateMaxCount: 5,
    handleVersionChange: isSupportTools ? handleVersionChange : undefined,
    handleInterventionActionClick,
    loadEarlierMessages: chatHasNext ? loadEarlierMessages : undefined,
    loadingEarlier,
    source: config?.app_type
  } satisfies Partial<ChatProps>
}

/** ChatToolbar props shared by desktop and mobile layouts (ref, leftExtra/rightExtra passed by each layout) */
export const buildSharedToolbarProps = (ctx: ConversationCtx) => {
  const { features, shareToken, setFileList, handleChangeVariables } = ctx

  return {
    features,
    onFilesChange: setFileList,
    uploadAction: shareFileUploadUrlWithoutApiPrefix,
    uploadRequestConfig: {
      headers: {
        'Content-Type': 'multipart/form-data',
        Authorization: `Bearer ${shareToken || ''}`,
      },
    },
    onVariablesChange: handleChangeVariables,
  }
}
