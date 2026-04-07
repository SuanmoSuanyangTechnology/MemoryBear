/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-24 17:57:08 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-07 14:05:50
 */
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
import { App, Button, Collapse, Flex } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined, RightOutlined, ArrowLeftOutlined } from '@ant-design/icons'
import copy from 'copy-to-clipboard'

import styles from './chat.module.css'
import type { ChatItem } from '@/components/Chat/types'
import Markdown from '@/components/Markdown'
import CodeBlock from '@/components/Markdown/CodeBlock'
import RbAlert from '@/components/RbAlert'

/**
 * Runtime component props
 * @param item - Chat item containing workflow execution data
 * @param index - Index of the chat item in the list
 */
const Runtime: FC<{ item: ChatItem; index: number;}> = ({
  item,
  index
}) => {
  const { t } = useTranslation()
  const { message } = App.useApp()
  // Stores the currently selected detail view (for nested loop/iteration exploration)
  const [detail, setDetail] = useState<any>(null)
  // Tracks whether the current detail view is for a loop (true) or iteration (false)
  const [loop, setLoop] = useState<boolean | null>(null)
  const [expanded, setExpanded] = useState(false)

  /**
   * Handles navigation into nested loop/iteration details
   * @param vo - The node object containing subContent to display
   * @param isLoop - Whether this is a loop node (true) or iteration node (false)
   */
  const handleViewDetail = (vo: any, isLoop: boolean) => {
    setDetail(vo)
    setLoop(isLoop)
  }

  /**
   * Returns CSS class for status-based text color
   * @param status - Node execution status: 'completed', 'failed', or other
   * @returns Tailwind CSS class for appropriate color
   */
  const getStatus = (status?: string) => {
    return status === 'completed' ? 'rb:text-[#369F21]!' : status === 'failed' ? 'rb:text-[#FF5D34]!' : 'rb:text-[#5B6167]!'
  }

  /**
   * Renders child nodes grouped by cycle index (for loop/iteration nodes)
   * Groups nodes by their cycle_idx and displays them in separate collapsible sections
   * @param list - Array of child node execution data
   */
  const renderDetailChild = (list: any) => {
    // Group nodes by cycle_idx to organize loop/iteration cycles
    const groupedByCycle = list.reduce((acc: any, item: any) => {
      const idx = item.cycle_idx ?? 0
      if (!acc[idx]) acc[idx] = []
      acc[idx].push(item)
      return acc
    }, {})


    return (
      <Flex gap={8} vertical>
        {Object.entries(groupedByCycle).map(([cycleIdx, items]: [string, any]) => {
          return (
            <Collapse
              key={cycleIdx}
              items={[{
                key: cycleIdx,
                label: <div className="rb:flex rb:items-center rb:gap-1">
                  <span>{t(`workflow.runtime.${loop ? 'loop' : 'iteration'}`)} {Number(cycleIdx) + 1}</span>
                </div>,
                className: styles.collapseItem,
                children: renderChild(items)
              }]}
            />
          )
        })}
      </Flex>
    )
  }

  /**
   * Renders detailed view of child nodes with their execution information
   * Displays node status, input/output data, errors, and nested cycles
   * @param list - Array of node execution data or error message string
   */
  const renderChild = (list: any) => {
    if (Array.isArray(list)) {
      return <Flex gap={8} vertical>
        {list?.map(vo => {
          const isLoop = vo.node_type === 'loop';
          // Render cycle variables for loop nodes without node_name
          if (typeof vo.cycle_idx === 'number' && isLoop && !vo.node_name) {
            return <div className="rb:bg-[#F0F3F8] rb:rounded-md">
              <div className="rb:py-2 rb:px-3 rb:flex rb:justify-between rb:items-center rb:text-[12px]">
                {t(`workflow.config.loop.cycle_vars`)}
                <Button
                  className="rb:py-0! rb:px-1! rb:text-[12px]!"
                  size="small"
                  onClick={() => handleCopy(typeof vo.content === 'object' && vo.content?.input ? JSON.stringify(vo.content.input, null, 2) : '{}')}
                >{t('common.copy')}</Button>
              </div>
              <div className="rb:max-h-40 rb:overflow-auto">
                <CodeBlock
                  size="small"
                  value={typeof vo.content === 'object' && vo.content?.input ? JSON.stringify(vo.content.input, null, 2) : '{}'}
                  needCopy={false}
                  showLineNumbers={true}
                />
              </div>
            </div>
          }
          // Skip rendering if no node_name is present
          if (!vo.node_name) return null

          // Render collapsible node with status, timing, and execution details
          return (
            <Collapse
              key={vo.node_id}
              bordered={false}
              className="rb:bg-[#F6F6F6]"
              items={[{
                key: vo.node_id,
                label: <div className={clsx("rb:flex rb:justify-between rb:items-center")}>
                  <Flex gap={6} align="center" className="rb:flex-1!">
                    {vo.icon && <div className={`rb:size-6 rb:bg-cover ${vo.icon}`} />}
                    <div className="rb:wrap-break-word rb:line-clamp-1 rb:font-medium">{vo.node_name}</div>
                  </Flex>
                  <Flex align="center" gap={8} className="rb:text-[12px]">
                    {typeof vo.elapsed_time == 'number' && <>{vo.elapsed_time?.toFixed(3)}ms</>}
                    {vo.status === 'completed'
                      ? <CheckCircleFilled className={`rb:mr-1 ${getStatus(vo.status)}`} />
                      : vo.status === 'failed'
                      ? <CloseCircleFilled className={`rb:mr-1 ${getStatus(vo.status)}`} />
                      : <LoadingOutlined className={`rb:mr-1 ${getStatus(vo.status)}`} />
                    }
                  </Flex>
                </div>,
                className: styles.collapseItem,
                children: (
                  <Flex gap={8} vertical>
                    {/* Display error message for failed nodes */}

                    {item.error &&
                      <RbAlert color="orange" className="rb:pb-0!">
                        <Flex vertical className="rb:w-full!">
                          <Flex align="center" justify="space-between">
                            {t(`workflow.error`)}
                            <Button
                              className="rb:py-0! rb:px-1! rb:text-[12px]!"
                              size="small"
                              onClick={() => handleCopy(vo.content?.error || '')}
                            >{t('common.copy')}</Button>
                          </Flex>
                          <Markdown content={vo.content?.error || ''} />
                        </Flex>
                      </RbAlert>
                    }
                    {/* Display navigation to nested cycles if subContent exists */}
                    {vo.subContent?.length > 0 && (
                      <Flex justify="space-between" className="rb:bg-[#F0F3F8] rb:rounded-md rb:py-2! rb:px-3! rb:cursor-pointer" onClick={() => handleViewDetail(vo, vo.node_type === 'loop')}>
                        <span>{Math.max(...vo.subContent.map((itemVo: any) => itemVo.cycle_idx + 1))} {t(`workflow.${isLoop ? 'loopNum' : 'iterationNum'}`)}</span>
                        <RightOutlined />
                      </Flex>
                    )}
                    {/* Display input and output data as JSON code blocks */}
                    {['input', 'output'].map(key => (
                      <div key={key} className="rb:bg-[#EBEBEB] rb:rounded-lg">
                        <div className="rb:py-2 rb:px-3 rb:flex rb:justify-between rb:items-center rb:text-[12px]">
                          {isLoop ? t(`workflow.runtime.${key}_cycle_vars`) : t(`workflow.${key}`)}
                          <Button
                            className="rb:py-0! rb:px-1! rb:text-[12px]!"
                            size="small"
                            onClick={() => handleCopy(typeof vo.content === 'object' && vo.content?.[key] ? JSON.stringify(vo.content[key], null, 2) : '{}')}
                          >{t('common.copy')}</Button>
                        </div>
                        <div className="rb:max-h-40 rb:overflow-auto">
                          <CodeBlock
                            size="small"
                            value={typeof vo.content === 'object' && vo.content?.[key] ? JSON.stringify(vo.content[key], null, 2) : '{}'}
                            needCopy={false}
                            showLineNumbers={true}
                            background="#EBEBEB"
                          />
                        </div>
                      </div>
                    ))}
                  </Flex>
                )
              }]}
            />
          )
        })}
      </Flex>
    }
    return <div className={clsx("rb:bg-[#FBFDFF] rb:rounded-md rb:py-2 rb:px-3 ", getStatus('failed'))}>
      <Markdown content={list || ''} />
    </div>
  }

    /** Copy value to clipboard and show success message */
    const handleCopy = (value: string) => {
      copy(value)
      message.success(t('common.copySuccess'))
    }

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
            ? <CheckCircleFilled className={`rb:mr-1 ${getStatus(item.status)}`} />
            : item.status === 'failed'
            ? <CloseCircleFilled className={`rb:mr-1 ${getStatus(item.status)}`} />
            : <LoadingOutlined className={`rb:mr-1 ${getStatus(item.status)}`} />
          }
          {t('application.workflow')}
        </span>
        <Flex
          align="center"
          justify="center"
          className={clsx("rb:size-6.5 rb:cursor-pointer rb-border rb:rounded-lg", {
            'rb:hover:bg-[#F6F6F6]!': expanded
          })}
          onClick={() => { setExpanded(v => !v); setDetail(null) }}
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
        detail
          ? (
            <div className="rb:bg-[#FBFDFF] rb:rounded-md rb:mb-4">
              <Button type="link" icon={<ArrowLeftOutlined />} onClick={() => setDetail(null)} className="rb:px-0! rb:text-[12px]!">
                {t('common.return')}
              </Button>
              {renderDetailChild(detail.subContent)}
            </div>
          )
          : <div className="rb:mb-4">
            {item.error &&
              <RbAlert color="orange" className="rb:pb-0! rb:mb-2!"><Markdown content={item.error} /></RbAlert>
            }
            {renderChild(item.subContent)}
          </div>
      )}
    </div>
  )
}
export default Runtime