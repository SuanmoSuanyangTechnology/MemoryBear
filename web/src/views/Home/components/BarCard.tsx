import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Progress, Skeleton, Flex } from 'antd'

import Card from './Card'
import Empty from '@/components/Empty'
import type { WorkspaceStatistics } from '../index'

interface BarCardProps {
  statistics: WorkspaceStatistics['statistics'];
  loading?: boolean;
  className?: string;
}

const BarCard: FC<BarCardProps> = ({ statistics, loading = false, className }) => {
  const { t } = useTranslation()
  const topStatistics = [...statistics]
    .sort((current, next) => next.count - current.count)
    .slice(0, 5)

  return (
    <Card
      title={t('dashboard.memoryScaleDistribution')}
      headerOperate={
        <span className="rb:text-gray-600 rb:text-[12px]">
          {t('dashboard.byMemoryType')}
        </span>
      }
      className={`rb:pb-6 rb:min-w-0 rb:max-w-full ${className ?? ''}`}
      bodyClassName="rb:min-w-0 rb:max-w-full rb:h-[calc(100%-58px)]! rb:overflow-hidden! rb:p-4! rb:pt-0!"
    >
      <Flex vertical gap={12} className="rb:h-full! rb:overflow-hidden!">
        <div className="rb:font-semibold rb:text-[16px] rb:leading-5.5 rb:shrink-0">
          {t('dashboard.topFiveMemoryTypes')}
        </div>
        {loading ? (
          <Skeleton active title={false} paragraph={{ rows: 5 }} className="rb:mt-4" />
        ) : topStatistics.length === 0 ? (
          <Empty size={88} className="rb:h-55" />
        ) : (
          <div className="rb:flex-1 rb:justify-between rb:grid rb:w-full rb:items-center rb:gap-x-3 rb:gap-y-4.5 rb:grid-cols-[max-content_minmax(0,1fr)_max-content]">
            {topStatistics.map(item => {
              return (
                <div key={item.type} className="rb:contents">
                  <span
                    title={t(`userMemory.${item.type}`)}
                    className="rb:truncate rb:text-[12px] rb:text-gray-600"
                  >
                    {t(`userMemory.${item.type}`)}
                  </span>
                  <Progress
                    percent={item.percentage}
                    showInfo={false}
                    strokeColor="#155EEF"
                    trailColor="#EEF2F8"
                    strokeWidth={8}
                    className="rb:w-full! rb:min-w-0 rb:mb-0!"
                  />
                  <span className="rb:text-right rb:text-[14px] rb:font-semibold">
                    {item.count.toLocaleString()}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </Flex>
    </Card>
  )
}

export default BarCard
