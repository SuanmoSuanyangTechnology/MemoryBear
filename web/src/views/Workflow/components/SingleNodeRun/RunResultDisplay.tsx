import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Flex, Skeleton } from 'antd'
import copy from 'copy-to-clipboard'
import { App } from 'antd'

import CodeBlock from '@/components/Markdown/CodeBlock'
import Markdown from '@/components/Markdown'
import RbAlert from '@/components/RbAlert'
import { hasProcessNodes } from '../../constant'

export interface RunResult {
  status: 'completed' | 'failed' | 'running';
  node_id?: string;
  node_type?: string;
  inputs?: Record<string, any>;
  outputs?: any;
  token_usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  process?: any;
  elapsed_time?: number;
  error?: string | null;
}

interface RunResultDisplayProps {
  result: RunResult | null
  loading: boolean
  nodeData: any
}

const RunResultDisplay: FC<RunResultDisplayProps> = ({ result, loading, nodeData }) => {
  const { t } = useTranslation()
  const { message } = App.useApp()

  const handleCopy = (val: string) => {
    copy(val)
    message.success(t('common.copySuccess'))
  }

  const statusColor = result?.status === 'completed' ? '#369F21' : result?.status === 'failed' ? '#FF5D34' : '#5B6167'

  if (!result) return null

  return (
    <>
      <div className="rb:rounded-lg rb:border rb:border-[#E8E8E8] rb:p-3 rb:bg-[#F6FFF4]">
        <Flex justify="space-between" align="start">
          <Flex vertical align="start" gap={2}>
            <span className="rb:text-[11px] rb:text-[#5B6167]">{t('workflow.status')}</span>
            <span className="rb:font-medium rb:text-[13px]" style={{ color: statusColor }}>
              {loading ? <Skeleton active paragraph={false} className="rb:w-20!" /> : result.status?.toUpperCase()}
            </span>
          </Flex>
          <Flex vertical align="start" gap={2}>
            <span className="rb:text-[11px] rb:text-[#5B6167]">{t('workflow.elapsedTime')}</span>
            {loading ? <Skeleton active paragraph={false} className="rb:w-20!" /> : result.elapsed_time != null && <span className="rb:font-medium rb:text-[13px]">{result.elapsed_time?.toFixed(3)}ms</span>}
          </Flex>
          <Flex vertical gap={2} align="start">
            <span className="rb:text-[11px] rb:text-[#5B6167]">{t('workflow.totalTokens')}</span>
            {loading ? <Skeleton active paragraph={false} className="rb:w-20!" /> : <span className="rb:font-medium rb:text-[13px]">{ result?.token_usage?.total_tokens || 0} Tokens</span>}
          </Flex>
        </Flex>
      </div>

      {(['inputs', 'process', 'outputs'] as const).map(key => {
        if (!hasProcessNodes.includes(nodeData.type) && key === 'process') return null
        const content = typeof result[key as keyof RunResult] === 'object' && result[key as keyof RunResult] ? JSON.stringify(result[key as keyof RunResult], null, 2) : result[key as keyof RunResult] ? result[key as keyof RunResult] : '{}'
        return (
          <div key={key} className="rb:bg-[#EBEBEB] rb:rounded-lg">
            <div className="rb:py-2 rb:px-3 rb:flex rb:justify-between rb:items-center rb:text-[12px]">
              {t(`workflow.${key}_result`)}
              {!loading &&
                <Button
                  className="rb:py-0! rb:px-1! rb:text-[12px]!"
                  size="small"
                  onClick={() => handleCopy(content)}
                >{t('common.copy')}</Button>
              }
            </div>
            <div className="rb:max-h-40 rb:overflow-auto">
              {loading
                ? <Skeleton active title={false} className="rb:m-3! rb:w-[calc(100%-24px)]!" />
                : <CodeBlock
                    size="small"
                    value={content}
                    needCopy={false}
                    showLineNumbers={true}
                    background="#EBEBEB"
                  />
              }
            </div>
          </div>
        )
      })}

      {result?.error && (
        <RbAlert color="orange" className="rb:pb-0!">
          <Flex vertical className="rb:w-full!">
            <Flex align="center" justify="space-between">
              {t(`workflow.error`)}
              <Button
                className="rb:py-0! rb:px-1! rb:text-[12px]!"
                size="small"
                onClick={() => handleCopy(result?.error || '')}
              >{t('common.copy')}</Button>
            </Flex>
            <Markdown className="rb:wrap-break-word!" content={result?.error || ''} />
          </Flex>
        </RbAlert>
      )}
    </>
  )
}

export default RunResultDisplay