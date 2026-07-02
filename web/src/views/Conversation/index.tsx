/*
 * @Author: ZhaoYing
 * @Date: 2026-02-03 16:58:03
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-30 11:44:24
 */
/**
 * Conversation Page
 * 公共会话页面：支持会话历史、流式响应、记忆/联网搜索、人工干预等。
 * 状态与逻辑收敛在 useConversation，布局拆分为桌面端 / 移动端两套。
 */
import { type FC } from 'react'
import { Spin } from 'antd'

import { useConversation } from './hooks/useConversation'
import DesktopLayout from './components/DesktopLayout'
import MobileLayout from './components/MobileLayout'
import ShareModal from './components/ShareModal'
import ReportModal from '@/components/Chat/ReportModal'

const Conversation: FC = () => {
  const ctx = useConversation()

  if (ctx.configLoading) {
    return <Spin spinning={ctx.configLoading} fullscreen />
  }

  return (
    <>
      {ctx.isIframe || ctx.isSmallScreen
        ? <MobileLayout ctx={ctx} />
        : <DesktopLayout ctx={ctx} />
      }

      <ShareModal
        ref={ctx.shareModalRef}
        conversationId={ctx.conversation_id as string}
        streamLoading={ctx.streamLoadingRef.current}
        shareToken={ctx.shareToken as string}
      />
      <ReportModal
        ref={ctx.reportModalRef}
        shareToken={ctx.shareToken as string}
      />
    </>
  )
}

export default Conversation
