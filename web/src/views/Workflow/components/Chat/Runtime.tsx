/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-24 17:57:08 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-12 13:39:24
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
import { Space, Button, Collapse, Flex } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined, RightOutlined, ArrowLeftOutlined } from '@ant-design/icons'

import styles from './chat.module.css'
import type { ChatItem } from '@/components/Chat/types'
import Markdown from '@/components/Markdown'
import CodeBlock from '@/components/Markdown/CodeBlock'

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
  // Stores the currently selected detail view (for nested loop/iteration exploration)
  const [detail, setDetail] = useState<any>(null)
  // Tracks whether the current detail view is for a loop (true) or iteration (false)
  const [loop, setLoop] = useState<boolean | null>(null)

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
    return status === 'completed' ? 'rb:text-[#369F21]' : status === 'failed' ? 'rb:text-[#FF5D34]' : 'rb:text-[#5B6167]'
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
      <Space size={8} direction="vertical" className="rb:w-full!">
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
      </Space>
    )
  }

  /**
   * Renders detailed view of child nodes with their execution information
   * Displays node status, input/output data, errors, and nested cycles
   * @param list - Array of node execution data or error message string
   */
  const renderChild = (list: any) => {
    if (Array.isArray(list)) {
      return <Space size={8} direction="vertical" className="rb:w-full!">
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
              items={[{
                key: vo.node_id,
                label: <div className={clsx("rb:flex rb:justify-between rb:items-center", getStatus(vo.status))}>
                  <div className="rb:flex rb:items-center rb:gap-1 rb:flex-1">
                    {vo.icon && <img src={vo.icon} className="rb:size-4" />}
                    <div className="rb:wrap-break-word rb:line-clamp-1">{vo.node_name}</div>
                  </div>
                  <span>
                    {typeof vo.elapsed_time == 'number' && <>{vo.elapsed_time?.toFixed(3)}ms</>}
                    {vo.status === 'completed' ? <CheckCircleFilled className="rb:ml-1" /> : vo.status === 'failed' ? <CloseCircleFilled className="rb:ml-1" /> : <LoadingOutlined className="rb:ml-1" />}
                  </span>
                </div>,
                className: styles.collapseItem,
                children: (
                  <Space size={8} direction="vertical" className="rb:w-full!">
                    {/* Display error message for failed nodes */}
                    {vo.status === 'failed' &&
                      <div className={clsx("rb:bg-[#F0F3F8] rb:rounded-md", getStatus(vo.status))}>
                        <div className="rb:py-2 rb:px-3 rb:flex rb:justify-between rb:items-center rb:text-[12px]">
                          {t(`workflow.error`)}
                          <Button
                            className="rb:py-0! rb:px-1! rb:text-[12px]!"
                            size="small"
                          >{t('common.copy')}</Button>
                        </div>
                        <div className="rb:pb-2 rb:px-3 rb:max-h-40 rb:overflow-auto">
                          <Markdown content={vo.content?.error || ''} />
                        </div>
                      </div>
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
                      <div key={key} className="rb:bg-[#F0F3F8] rb:rounded-md">
                        <div className="rb:py-2 rb:px-3 rb:flex rb:justify-between rb:items-center rb:text-[12px]">
                          {isLoop ? t(`workflow.runtime.${key}_cycle_vars`) : t(`workflow.${key}`)}
                          <Button
                            className="rb:py-0! rb:px-1! rb:text-[12px]!"
                            size="small"
                          >{t('common.copy')}</Button>
                        </div>
                        <div className="rb:max-h-40 rb:overflow-auto">
                          <CodeBlock
                            size="small"
                            value={typeof vo.content === 'object' && vo.content?.[key] ? JSON.stringify(vo.content[key], null, 2) : '{}'}
                            needCopy={false}
                            showLineNumbers={true}
                          />
                        </div>
                      </div>
                    ))}
                  </Space>
                )
              }]}
            />
          )
        })}
      </Space>
    }
    return <div className={clsx("rb:bg-[#FBFDFF] rb:rounded-md rb:py-2 rb:px-3 ", getStatus('failed'))}>
      <Markdown content={list || ''} />
    </div>
  }

  return (
    <div key={index} className="rb:min-w-100 rb:max-w-full rb:mb-2">
      <Collapse
        className={styles[item.status || 'default']}
        items={[{
          key: 0,
          label: <div className={getStatus(item.status)}>
            {item.status === 'completed' ? <CheckCircleFilled className="rb:mr-1" /> : item.status === 'failed' ? <CloseCircleFilled className="rb:mr-1" /> : <LoadingOutlined className="rb:mr-1" />}
            {t('application.workflow')}
          </div>,
          className: styles.collapseItem,
          children: (
            detail
              ? (
                <div className="rb:bg-[#FBFDFF] rb:rounded-md">
                  <Button type="link" icon={<ArrowLeftOutlined />} onClick={() => setDetail(null)} className="rb:px-0! rb:text-[12px]!">
                    {t('common.return')}
                  </Button>
                  {renderDetailChild(detail.subContent)}
                </div>
              )
              : <>
                {item.error &&
                 <div className={clsx("rb:bg-[#FBFDFF] rb:rounded-md rb:py-2 rb:px-3 rb:mb-2 rb:-mt-4", getStatus('failed'))}>
                  <Markdown content={item.error} />
                  </div>
                }
                {renderChild(item.subContent)}
              </>
          )
        }]}
      />
    </div>
  )
}
export default Runtime