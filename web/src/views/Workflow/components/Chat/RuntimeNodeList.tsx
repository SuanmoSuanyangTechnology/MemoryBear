/*
 * Runtime Node List
 *
 * Shared rendering of runtime step cards (status, duration, input/output, errors,
 * nested loops / agent iterations). Used by Runtime and by log detail timeline.
 */
import { type FC, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { App, Button, Collapse, Flex } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined, RightOutlined, ArrowLeftOutlined, PauseCircleFilled } from '@ant-design/icons'
import copy from 'copy-to-clipboard'

import styles from './chat.module.css'
import Markdown from '@/components/Markdown'
import CodeBlock from '@/components/Markdown/CodeBlock'
import RbAlert from '@/components/RbAlert'
import { hasProcessNodes } from '../../constant'

export const getRuntimeNodeStatus = (status?: string) => {
  return status === 'completed'
    ? 'rb:text-[#369F21]!'
    : status === 'failed'
    ? 'rb:text-[#FF5D34]!'
    : status === 'waiting_human'
    ? 'rb:text-[#FF5D34]!'
    : 'rb:text-[#5B6167]!'
}

type Props = {
  source: string
  list: any[]
  /** Override icon visibility; Runtime hides icons for agent source by default. */
  showIcon?: boolean
  /** Use a white card with border instead of the default gray card. */
  outlined?: boolean
  /** Hide the raw `llm` block (which contains `llm.input`) in agent iteration details.
   *  Share pages must set this to avoid exposing system prompts / full inputs. */
  showLlmDetail?: boolean
}

const RuntimeNodeList: FC<Props> = ({ source, list, showIcon, outlined, showLlmDetail = true }) => {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [detail, setDetail] = useState<any>(null)
  const [loop, setLoop] = useState<boolean | null>(null)
  const canShowIcon = showIcon ?? source !== 'agent'

  const handleViewDetail = (vo: any, isLoop: boolean) => {
    setDetail(vo)
    setLoop(isLoop)
  }

  const handleCopy = (value: string) => {
    copy(value)
    message.success(t('common.copySuccess'))
  }

  const renderDetailChild = (list: any, node_type: string) => {
    const groupedByCycle = list.reduce((acc: any, item: any, index: number) => {
      const idx = item.cycle_idx ?? index ?? 0
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
              // defaultActiveKey={outlined ? [cycleIdx] : undefined}
              items={[{
                key: cycleIdx,
                label: (
                  <Flex align="center" gap={4}>
                    <span>{node_type === 'agent' ? 'ROUND' : t(`workflow.runtime.${loop ? 'loop' : 'iteration'}`)} {Number(cycleIdx) + 1}</span>
                  </Flex>
                ),
                className: styles.collapseItem,
                children: node_type !== 'agent'
                  ? renderChild(items, node_type)
                  : (
                    <Flex gap={8} vertical className="rb:pt-3!">
                      {(showLlmDetail ? ['llm', 'tool_calls'] : ['tool_calls']).map(key => {
                        const data = items[0][key]
                        return (
                          <div key={key} className="rb:bg-gray-200 rb:rounded-lg rb:overflow-hidden">
                            <Flex align="center" justify="space-between" className="rb:py-2! rb:px-3! rb:text-[12px]">
                              <span>{key === 'llm' ? data.model : 'TOOL CALLS'}</span>
                              <Button
                                className="rb:py-0! rb:px-1! rb:text-[12px]!"
                                size="small"
                                onClick={() => handleCopy(typeof data === 'object' && data ? JSON.stringify(data, null, 2) : '{}')}
                              >{t('common.copy')}</Button>
                            </Flex>
                            <div className="rb:max-h-40 rb:overflow-auto">
                              <CodeBlock
                                size="small"
                                value={typeof data === 'object' && data ? JSON.stringify(data, null, 2) : '{}'}
                                needCopy={false}
                                showLineNumbers={true}
                                background="#EBEBEB"
                              />
                            </div>
                          </div>
                        )
                      })}
                    </Flex>
                  )
              }]}
            />
          )
        })}
      </Flex>
    )
  }

  const renderChild = (list: any, node_type?: string) => {
    if (Array.isArray(list) && node_type !== 'agent') {
      return <Flex gap={8} vertical>
        {list?.map(vo => {
          const isLoop = vo.node_type === 'loop'
          if (typeof vo.cycle_idx === 'number' && isLoop && !vo.node_name) {
            return (
              <div className="rb:bg-[#F0F3F8] rb:rounded-md">
                <Flex align="center" justify="space-between" className="rb:py-2! rb:px-3! rb:text-[12px]">
                  {t(`workflow.config.loop.cycle_vars`)}
                  <Button
                    className="rb:py-0! rb:px-1! rb:text-[12px]!"
                    size="small"
                    onClick={() => handleCopy(typeof vo.content === 'object' && vo.content?.input ? JSON.stringify(vo.content.input, null, 2) : '{}')}
                  >{t('common.copy')}</Button>
                </Flex>
                <div className="rb:max-h-40 rb:overflow-auto">
                  <CodeBlock
                    size="small"
                    value={typeof vo.content === 'object' && vo.content?.input ? JSON.stringify(vo.content.input, null, 2) : '{}'}
                    needCopy={false}
                    showLineNumbers={true}
                  />
                </div>
              </div>
            )
          }

          if (!vo.node_name && node_type !== 'agent') return null
          return (
            <Collapse
              key={vo.node_id}
              bordered={false}
              className={outlined ? 'rb:bg-white rb:border! rb:border-solid! rb:border-gray-200! rb:overflow-hidden!' : 'rb:bg-gray-100'}
              items={[{
                key: vo.node_id,
                ...(outlined ? { styles: { header: { background: '#fff' } } } : {}),
                label: (
                  <Flex align="center" justify="space-between">
                    <Flex gap={6} align="center" className="rb:flex-1!">
                      {vo.icon && canShowIcon && <div className={`rb:size-6 rb:bg-cover ${vo.icon}`} />}
                      <div className="rb:wrap-break-word rb:line-clamp-1 rb:font-medium">{vo.node_name}</div>
                    </Flex>
                    <Flex align="center" gap={8} className="rb:text-[12px]">
                      {typeof vo.elapsed_time == 'number' && <>{vo.elapsed_time?.toFixed(3)}ms</>}
                      {vo.status === 'completed'
                        ? <CheckCircleFilled className={`rb:mr-1 ${getRuntimeNodeStatus(vo.status)}`} />
                        : vo.status === 'failed'
                        ? <CloseCircleFilled className={`rb:mr-1 ${getRuntimeNodeStatus(vo.status)}`} />
                        : vo.status === 'waiting_human'
                        ? <PauseCircleFilled className={`rb:mr-1 ${getRuntimeNodeStatus(vo.status)}`} />
                        : <LoadingOutlined className={`rb:mr-1 ${getRuntimeNodeStatus(vo.status)}`} />
                      }
                    </Flex>
                  </Flex>
                ),
                className: styles.collapseItem,
                children: (
                  <Flex gap={8} vertical>
                    {vo.content?.error && vo.content?.error !== '' &&
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
                    {(vo.subContent?.length > 0 || vo.agent_log?.iterations?.length > 0) && (
                      <Flex justify="space-between" className="rb:bg-[#F0F3F8] rb:rounded-md rb:py-2! rb:px-3! rb:mt-3! rb:cursor-pointer" onClick={() => handleViewDetail(vo, vo.node_type === 'loop')}>
                        <span>{vo.agent_log?.iterations?.length || Math.max(...vo.subContent.map((itemVo: any) => itemVo.cycle_idx + 1))} {t(`workflow.${isLoop ? 'loopNum' : 'iterationNum'}`)}</span>
                        <RightOutlined />
                      </Flex>
                    )}
                    {['input', 'process', 'output'].map(key => {
                      if ((source === 'agent' || source === 'multi_agent' || !hasProcessNodes.includes(vo.node_type)) && key === 'process') return null
                      return (
                        <div key={key} className="rb:bg-gray-200 rb:rounded-lg">
                          <Flex align="center" justify="space-between" className="rb:py-2! rb:px-3! rb:text-[12px]">
                            {isLoop ? t(`workflow.runtime.${key}_cycle_vars`) : t(`workflow.${key}_result`)}
                            <Button
                              className="rb:py-0! rb:px-1! rb:text-[12px]!"
                              size="small"
                              onClick={() => handleCopy(typeof vo.content === 'object' && vo.content?.[key] ? JSON.stringify(vo.content[key], null, 2) : '{}')}
                            >{t('common.copy')}</Button>
                          </Flex>
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
                      )
                    })}
                  </Flex>
                )
              }]}
            />
          )
        })}
      </Flex>
    }
    return <div className={clsx('rb:bg-[#FBFDFF] rb:rounded-md rb:py-2 rb:px-3 ', getRuntimeNodeStatus('failed'))}>
      <Markdown content={list || ''} />
    </div>
  }

  if (detail) {
    return (
      <div className={outlined
        ? 'rb:mb-4 rb:rounded-xl rb:border! rb:border-solid! rb:border-gray-200! rb:bg-white rb:px-3 rb:pb-3'
        : 'rb:bg-[#FBFDFF] rb:rounded-md rb:mb-4'}>
        <Button type="link" icon={<ArrowLeftOutlined />} onClick={() => setDetail(null)} className="rb:px-0! rb:text-[12px]!">
          {t('common.return')}
        </Button>
        {renderDetailChild(detail.subContent || detail.agent_log?.iterations || [], detail.node_type)}
      </div>
    )
  }

  return <div className="rb:mb-4">{renderChild(list)}</div>
}

export default RuntimeNodeList
