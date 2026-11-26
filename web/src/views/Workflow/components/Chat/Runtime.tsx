/*
 * Runtime Component
 * 
 * This component displays the execution runtime details of workflow nodes in a chat interface.
 * It provides a hierarchical view of workflow execution with support for:
 * - Node execution status (completed, failed, running)
 * - Nested loop and iteration cycles
 * - Input/output data visualization
 * - Error messages for failed nodes
 * - Elapsed time tracking
 */
import { type FC, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { Flex } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined, PauseCircleFilled } from '@ant-design/icons'

import type { ChatItem } from '@/components/Chat/types'
import Markdown from '@/components/Markdown'
import RbAlert from '@/components/RbAlert'
import RuntimeNodeList, { getRuntimeNodeStatus } from './RuntimeNodeList'

/**
 * Runtime component props
 * @param item - Chat item containing workflow execution data
 * @param index - Index of the chat item in the list
 */
const Runtime: FC<{ source: string; item: ChatItem; index: number;}> = ({
  source,
  item,
  index
}) => {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  return (
    <div
      key={index}
      className={clsx("rb:mb-4 rb-border rb:rounded-xl rb:px-4 rb:pt-3 rb:bg-white rb:max-w-full", {
        'rb:hover:bg-[#F6F6F6] rb:w-64': !expanded
      })}
    >
      <Flex align="center" justify="space-between" className="rb:font-medium rb:pb-3!">
        <span className="rb:font-medium rb:leading-5">
          {item.status === 'completed'
            ? <CheckCircleFilled className={`rb:mr-1 ${getRuntimeNodeStatus(item.status)}`} />
            : item.status === 'failed'
            ? <CloseCircleFilled className={`rb:mr-1 ${getRuntimeNodeStatus(item.status)}`} />
            : item.status === 'waiting_human'
            ? <PauseCircleFilled className={`rb:mr-1 ${getRuntimeNodeStatus(item.status)}`} />
            : <LoadingOutlined className={`rb:mr-1 ${getRuntimeNodeStatus(item.status)}`} />
          }
          {t(`application.${source}`)}
        </span>
        <Flex
          align="center"
          justify="center"
          className={clsx("rb:size-6.5 rb:cursor-pointer rb-border rb:rounded-lg", {
            'rb:hover:bg-[#F6F6F6]!': expanded
          })}
          onClick={() => setExpanded(v => !v)}
        >
          <div
            className={clsx("rb:size-4 rb:bg-cover", {
              'rb:bg-[url("@/assets/images/conversation/compress.svg")]': expanded,
              'rb:bg-[url("@/assets/images/conversation/expand.svg")]': !expanded
            })}
          />
        </Flex>
      </Flex>
      {expanded && (
        <>
          {item.error && item.error !== '' &&
            <RbAlert color="orange" className="rb:pb-0! rb:mb-2!"><Markdown content={item.error} /></RbAlert>
          }
          <RuntimeNodeList source={source} list={item.subContent || []} />
        </>
      )}
    </div>
  )
}
export default Runtime