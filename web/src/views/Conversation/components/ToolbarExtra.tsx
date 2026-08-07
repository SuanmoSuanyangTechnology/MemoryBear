/**
 * ToolbarExtra
 * 会话输入框工具栏的扩展按钮组：深度思考、联网搜索、记忆召回展示、记忆开关。
 * 移动端作为 leftExtra、桌面端作为 rightExtra 复用。
 */
import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Flex, Tooltip } from 'antd'
import clsx from 'clsx'

import type { ConversationCtx } from '../hooks/useConversation'

interface ToolbarExtraProps {
  ctx: ConversationCtx
}

const ToolbarExtra: FC<ToolbarExtraProps> = ({ ctx }) => {
  const { t } = useTranslation()
  const {
    features, isDeepThinking, isHasMemory, thinking, webSearch, memory, showMemoryRecall, config,
    handleChangeDeepThinking, setWebSearch, handleChangeMemory, setShowMemoryRecall,
  } = ctx

  if (!(features?.web_search?.enabled || isHasMemory || isDeepThinking)) return null

  return (
    <Flex align="center" justify="end" gap={8}>
      {isDeepThinking &&
        <Tooltip title={t('memoryConversation.deepThinking')}>
          <Flex justify="center" align="center"
            className={clsx("rb:size-7 rb:cursor-pointer rb:border rb:hover:bg-[#F6F6F6] rb:rounded-full rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]", {
              'rb:bg-[rgba(21,94,239,0.06)] rb:border-[rgba(21,94,239,0.25)]': thinking,
              'rb:border-[#EBEBEB]': !thinking,
            })}
            onClick={handleChangeDeepThinking}
          >
            <div className={clsx("rb:size-4 rb:bg-cover", {
              "rb:bg-[url('@/assets/images/conversation/deepThinking.svg')]": !thinking,
              "rb:bg-[url('@/assets/images/conversation/deepThinkingChecked.svg')]": thinking
            })} />
          </Flex>
        </Tooltip>
      }
      {features?.web_search?.enabled &&
        <Tooltip title={t('memoryConversation.web_search')}>
          <Flex justify="center" align="center"
            className={clsx("rb:size-7 rb:border rb:cursor-pointer rb:hover:bg-[#F6F6F6] rb:rounded-full rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]", {
              'rb:bg-[rgba(21,94,239,0.06)] rb:border-[rgba(21,94,239,0.25)]': webSearch,
              'rb:border-[#EBEBEB]': !webSearch,
            })}
            onClick={() => setWebSearch(prev => !prev)}
          >
            <div className={clsx("rb:size-4 rb:bg-cover", {
              "rb:bg-[url('@/assets/images/conversation/online.svg')]": !webSearch,
              "rb:bg-[url('@/assets/images/conversation/onlineChecked.svg')]": webSearch
            })} />
          </Flex>
        </Tooltip>
      }
      {isHasMemory &&
        <>
          <Tooltip title={t('memoryConversation.memory')}>
            <Flex justify="center" align="center"
              className={clsx("rb:size-7 rb:border rb:hover:bg-[#F6F6F6] rb:rounded-full rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]", {
                'rb:bg-[rgba(21,94,239,0.06)] rb:border-[rgba(21,94,239,0.25)]': memory,
                'rb:border-[#EBEBEB]': !memory,
                'rb:cursor-pointer': config.app_type !== 'workflow',
                'rb:cursor-not-allowed rb:opacity-65': config.app_type === 'workflow',
              })}
              onClick={handleChangeMemory}
            >
              <div className={clsx("rb:size-4 rb:bg-cover", {
                "rb:bg-[url('@/assets/images/conversation/memoryFunction.svg')]": !memory,
                "rb:bg-[url('@/assets/images/conversation/memoryFunctionChecked.svg')]": memory
              })} />
            </Flex>
          </Tooltip>
          {config.app_type === 'agent' &&
            <Tooltip title={t('memoryConversation.memoryRecallToggle')}>
              <Flex justify="center" align="center"
                className={clsx("rb:size-7 rb:border rb:hover:bg-[#F6F6F6] rb:rounded-full rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]", {
                  'rb:bg-[rgba(21,94,239,0.06)] rb:border-[rgba(21,94,239,0.25)]': showMemoryRecall,
                  'rb:border-[#EBEBEB]': !showMemoryRecall,
                  'rb:cursor-pointer': memory,
                  'rb:cursor-not-allowed rb:opacity-45': !memory,
                })}
                onClick={() => {
                  if (memory) setShowMemoryRecall((value: boolean) => !value)
                }}
              >
                <div className={clsx("rb:size-4 rb:bg-cover", {
                  "rb:bg-[url('@/assets/images/conversation/memoryRecall.svg')]": !showMemoryRecall,
                  "rb:bg-[url('@/assets/images/conversation/memoryRecallChecked.svg')]": showMemoryRecall,
                })} />
              </Flex>
            </Tooltip>
          }
        </>
      }
    </Flex>
  )
}

export default ToolbarExtra
