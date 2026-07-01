/**
 * DesktopLayout
 * 桌面端（非 iframe / 大屏）会话布局：左侧历史列表 + 右侧会话区。
 */
import { type FC } from 'react'
import InfiniteScroll from 'react-infinite-scroll-component'
import { Flex, Skeleton, Tooltip } from 'antd'
import clsx from 'clsx'

import Empty from '@/components/Empty'
import Chat from '@/components/Chat'
import ChatToolbar from '@/components/Chat/ChatToolbar'
import ChatEmpty from '@/assets/images/empty/chatEmpty.png'

import type { ConversationCtx } from '../hooks/useConversation'
import { buildSharedChatProps, buildSharedToolbarProps } from '../utils/chatConfig'
import ToolbarExtra from './ToolbarExtra'
import { formatDateTime } from '@/utils/format'

interface DesktopLayoutProps {
  ctx: ConversationCtx
}

const DesktopLayout: FC<DesktopLayoutProps> = ({ ctx }) => {
  const {
    t, conversation_id, historyList, groupHistoryList, hasMore, scrollRef, toolbarRef, config,
    isShare, chatTitle,
    getHistory, handleChangeHistory, handleShare,
  } = ctx

  return (
    <Flex className="rb:w-full rb:p-[-16px]!">
      {!isShare &&
        <div className="rb:w-80 rb:h-screen rb:bg-[#F6F6F6] rb:overflow-hidden">
          <Flex align="center" gap={8} className="rb:p-5!">
            <div
              className="rb:size-6 rb:bg-cover rb:rounded-md rb:bg-[url('@/assets/images/conversation/redbear.png')]"
              style={config.app_icon ? {
                backgroundImage: `url(${config.app_icon})`,
              } : undefined}
            ></div>
            <div className="rb:flex-1 rb:text-[16px] rb:leading-5 rb:font-[Gilroy-Extrabold] rb:font-extrabold rb:truncate">
              {config.app_name || t('memoryConversation.chatTitle')}
            </div>
          </Flex>

          <Flex align="center" gap={12}
            className="rb:cursor-pointer rb:border rb:border-[#155EEF] rb:rounded-xl rb:p-3! rb:mx-4! rb:text-[16px] rb:font-medium rb:text-[#155EEF] rb:h-12! rb:mb-5!"
            onClick={() => handleChangeHistory(null)}
          >
            <div
              className="rb:w-5 rb:h-5 rb:cursor-pointer rb:mr-2 rb:bg-cover rb:bg-[url('@/assets/images/conversation/conversation.svg')] rb:group-hover:bg-[url('@/assets/images/conversation/conversation_hover.svg')]"
            ></div>
            {t('memoryConversation.startANewConversation')}
          </Flex>
          {historyList.length > 0 &&
            <div
              ref={scrollRef}
              id="scrollableDiv"
              className="rb:overflow-y-auto rb:h-[calc(100vh-144px)] rb:px-3!"
            >
              <InfiniteScroll
                dataLength={historyList.length}
                next={getHistory}
                hasMore={hasMore}
                loader={<Skeleton active />}
                scrollableTarget="scrollableDiv"
              >
                {Object.entries(groupHistoryList).map(([date, items]) => (
                  <div key={date} className="rb:mt-6 rb:first:mt-0">
                    <div className="rb:leading-5 rb:text-[#5B6167] rb:mb-2 rb:pl-1 rb:font-regular">{date.replace(/\u200e|\u200f/g, '')}</div>

                    <Flex vertical gap={4}>
                      {items.map(item => (
                        <div key={item.updated_at} className="rb:mb-3">
                          <div className={clsx("rb:p-[8px_13px] rb:rounded-lg rb:leading-5 rb:cursor-pointer rb:hover:bg-[#F0F3F8]", {
                            'rb:bg-[#FFFFFF] rb:shadow-[0px_2px_4px_0px_rgba(0,0,0,0.15)] rb:font-medium rb:hover:bg-[#FFFFFF]!': item.id === conversation_id,
                          })}
                            onClick={() => handleChangeHistory(item.id)}
                          >
                            {item.title}
                          </div>
                        </div>
                      ))}
                    </Flex>
                  </div>
                ))}
              </InfiniteScroll>
            </div>
          }
        </div>
      }

      <div className="rb:relative rb:h-screen rb:px-4 rb:flex-[1_1_auto]">
        {!isShare &&
          <div className="rb:text-[#212332] rb:text-[16px] rb:leading-6 rb:font-medium rb:text-center rb:h-16 rb:py-5 rb:relative">
            <div className="rb:w-190 rb:mx-auto">{chatTitle || t('memoryConversation.newConversation')}</div>

            {chatTitle &&
              <Tooltip title={t('memoryConversation.shareConversation')}>
                <div
                  className="rb:absolute rb:right-6 rb:top-5 rb:cursor-pointer rb:size-6 rb:bg-cover rb:bg-[url('@/assets/images/conversation/share.svg')]"
                  onClick={handleShare}
                ></div>
              </Tooltip>
            }
          </div>
        }
        <div className={clsx("rb:w-190 rb:mx-auto rb:pb-3", {
          'rb:h-[calc(100vh-64px)]': !isShare,
          'rb:h-full': isShare,
        })}>
          <Chat
            {...buildSharedChatProps(ctx)}
            empty={<Empty url={ChatEmpty} className="rb:h-full" size={[320, 180]} title={t('memoryConversation.chatEmpty')} subTitle={t('memoryConversation.emptyDesc')} />}
            labelFormat={(item) => formatDateTime(item.created_at, 'MMMM D, YYYY [at] h:mm A', 'en')}
          >
            <ChatToolbar
              ref={toolbarRef}
              {...buildSharedToolbarProps(ctx)}
              rightExtra={<ToolbarExtra ctx={ctx} />}
            />
          </Chat>
        </div>
      </div>
    </Flex>
  )
}

export default DesktopLayout
