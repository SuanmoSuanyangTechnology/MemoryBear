/**
 * Chat / ChatToolbar 公共 props 配置
 * 桌面端与移动端布局共用的 Chat、ChatToolbar 属性集中在此，避免两处重复维护。
 * 差异项（empty、labelFormat、用户/助手头像、left/rightExtra 等）仍由各布局自行传入。
 */
import clsx from 'clsx'

import type { ChatProps } from '@/components/Chat/types'
import { shareFileUploadUrlWithoutApiPrefix } from '@/api/fileStorage'

import type { ConversationCtx } from '../hooks/useConversation'

/** 桌面端与移动端共用的 Chat props */
export const buildSharedChatProps = (ctx: ConversationCtx) => {
  const {
    conversation_id, chatList, fileList, setFileList, toolbarRef, config, isShare,
    streamLoadingRef, chatIsEnded, loading, showMemoryRecall, setMessage, handleSend, regenerateMessages,
    deleteMessage, reportMsg, handleVersionChange, handleFeedback, handleFavorite,
    handleInterventionActionClick,
  } = ctx

  const isSupportTools = ['agent', 'workflow'].includes(config.app_type)
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
    handleVersionChange: isSupportTools ? handleVersionChange : undefined,
    handleInterventionActionClick,
  } satisfies Partial<ChatProps>
}

/** 桌面端与移动端共用的 ChatToolbar props（ref、leftExtra/rightExtra 由各布局自行传入） */
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
