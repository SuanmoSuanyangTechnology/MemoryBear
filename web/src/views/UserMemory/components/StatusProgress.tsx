import type { FC } from 'react'
import { Progress } from 'antd'

export const formatQuotaStatus = (activeCount: number, memoryLimit: number) => {
  const percent = activeCount / memoryLimit
  return percent > 1
    ? 'overLimit'
    : percent >= 0.9
    ? 'nearLimit'
    : percent >= 0.7
    ? 'warning'
    : 'normal'
}
export const quotaColorClass = (status: string) => ({
  'rb:text-[#FF5D34]': ['overLimit', 'nearLimit'].includes(status),
  'rb:text-[#FF8A4C]': status === 'warning',
  'rb:text-[#212332]': status === 'normal',
})
export const StatusProgress: FC<{status: string, percent: number}> = ({
  status, percent
}) => (
  <Progress
    percent={percent}
    showInfo={false}
    strokeColor={['overLimit', 'nearLimit'].includes(status)
      ? '#FF5D34'
      : status === 'warning'
      ? '#FF8A4C'
      : '#155EEF'
    }
  />
)