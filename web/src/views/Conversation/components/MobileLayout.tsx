/**
 * MobileLayout
 * 移动端 / iframe / 浮窗（floatBtn）/ 小屏会话布局。
 */
import { type FC } from 'react'
import InfiniteScroll from 'react-infinite-scroll-component'
import { Flex, Skeleton, Tooltip, Space } from 'antd'
import clsx from 'clsx'

import Empty from '@/components/Empty'
import Chat from '@/components/Chat'
import ChatToolbar from '@/components/Chat/ChatToolbar'
import ChatEmpty from '@/assets/images/empty/chatEmpty.png'
import { formatDateTime } from '@/utils/format'

import type { ConversationCtx } from '../hooks/useConversation'
import { buildSharedChatProps, buildSharedToolbarProps } from '../utils/chatConfig'
import ToolbarExtra from './ToolbarExtra'

interface MobileLayoutProps {
  ctx: ConversationCtx
}

const MobileLayout: FC<MobileLayoutProps> = ({ ctx }) => {
  const {
    t, conversation_id, historyList, hasMore, scrollRef, toolbarCallbackRef, config,
    isShare, isIframe, isFloatBtn, isSmallScreen, chatTitle, showHistory, setShowHistory,
    getHistory, handleChangeHistory, handleShare,
  } = ctx

  return (
    <Flex className={clsx("rb:bg-[#FFFFFF]", {
      'rb:rounded-tl-2xl! rb:h-full! rb:w-full!': isFloatBtn,
      'rb:w-full rb:h-full': !isFloatBtn,
    })}>
      <div className="rb:flex-1">
        {!isShare &&
          <Flex
            justify={isFloatBtn || isSmallScreen ? "space-between" : 'end'}
            className={clsx("rb:p-3! rb:h-11.25! rb:w-full! rb-border-b", {
              'rb:bg-[#171719] rb:text-[#FFFFFF]': isFloatBtn,
              'rb:text-[#171719]': !isFloatBtn,
              'rb:rounded-tl-2xl': showHistory && isFloatBtn,
              'rb:rounded-tl-2xl rb:rounded-tr-2xl': !showHistory && isFloatBtn
            })}
          >
            {isFloatBtn && config.app_name && <span className="rb:font-medium">{config.app_name}</span>}
            {isSmallScreen && <span className="rb:font-medium">{chatTitle || t('memoryConversation.newConversation')}</span>}
            <Space size={12}>
              {!isIframe && isSmallScreen && historyList.length > 0 &&
                <Tooltip title={t('memoryConversation.history')}>
                  <div
                    className={clsx("rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/history_dark.svg')]", {
                      "rb:bg-[url('@/assets/images/conversation/history_white.svg')]": isFloatBtn,
                    })}
                    onClick={() => setShowHistory(true)}
                  ></div>
                </Tooltip>
              }
              {chatTitle &&
                <Tooltip title={t('memoryConversation.shareConversation')}>
                  <div
                    className="rb:cursor-pointer rb:size-4.5 rb:bg-cover rb:bg-[url('@/assets/images/conversation/share.svg')]"
                    onClick={handleShare}
                  ></div>
                </Tooltip>
              }
              <Tooltip placement="left" title={t('memoryConversation.startANewConversation')}>
                <div
                  className={clsx("rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/refresh_dark.svg')]", {
                    "rb:bg-[url('@/assets/images/refresh_white.svg')]": isFloatBtn,
                  })}
                  onClick={() => handleChangeHistory(null)}
                ></div>
              </Tooltip>

              {isFloatBtn &&
                <div
                  className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/close.svg')]"
                  onClick={() => {
                    window.parent?.postMessage({ type: 'CLOSE_CHAT' }, '*')
                  }}
                ></div>
              }
            </Space>
          </Flex>
        }

        <div className={clsx("rb:px-4", {
          "rb:h-[calc(100%-45px)]": !isShare,
          'rb:h-full': isShare,
        })}>
          <Chat
            {...buildSharedChatProps(ctx)}
            empty={isShare ? null : <Empty url={ChatEmpty} className="rb:h-full" size={[320, 180]} title={t('memoryConversation.chatEmpty')} subTitle={t('memoryConversation.emptyDesc')} />}
            labelFormat={(item) => isFloatBtn ? formatDateTime(item.created_at, 'HH:mm') : formatDateTime(item.created_at, 'MMMM D, YYYY [at] h:mm A', 'en')}
          >
            <ChatToolbar
              ref={toolbarCallbackRef}
              {...buildSharedToolbarProps(ctx)}
              leftExtra={<ToolbarExtra ctx={ctx} />}
            />
          </Chat>
        </div>
      </div>
      {showHistory &&
        <div className="rb:w-55 rb-border-l">
          <Flex justify="space-between" className="rb:p-3! rb:h-11.25! rb:w-full! rb-border-b rb:text-[#171719] rb:font-medium">
            <Space>
              <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/clock.svg')]"></div>
              {t('memoryConversation.history')}
            </Space>

            <div
              className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/close.svg')]"
              onClick={() => setShowHistory(false)}
            ></div>
          </Flex>

          <div
            ref={scrollRef}
            id="scrollableDiv"
            className="rb:overflow-y-auto rb:h-[calc(100vh-144px)]"
          >
            <InfiniteScroll
              dataLength={historyList.length}
              next={getHistory}
              hasMore={hasMore}
              loader={<Skeleton active />}
              scrollableTarget="scrollableDiv"
            >
              {historyList.map(item => (
                <div
                  className={clsx("rb:cursor-pointer rb:px-4 rb:py-2 rb:hover:bg-[#F6F6F6]", {
                    "rb:bg-[rgba(21,94,239,0.08)]! rb:relative rb:border-l-[3px] rb:border-[#155EEF]": item.id === conversation_id,
                  })}
                  onClick={() => handleChangeHistory(item.id)}
                >
                  <div className="rb:text-[12px] rb:font-medium rb:mb-1">{item.title}</div>
                  <Flex justify="space-between" className="rb:text-[12px] rb:text-[#5B6167]">
                    <span>{formatDateTime(item.updated_at, 'MM-DD')}</span>
                    <span>{formatDateTime(item.updated_at, 'HH:mm')}</span>
                  </Flex>
                </div>
              ))}
            </InfiniteScroll>
          </div>
        </div>
      }
    </Flex>
  )
}

export default MobileLayout
