import { type FC, useEffect, useState } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { Skeleton } from 'antd';
import chunkCountIcon from '@/assets/images/home/chunk_count.svg';
import statementsCountIcon from '@/assets/images/home/statements_count.svg';
import tripletCountIcon from '@/assets/images/home/triplet_count.svg';
import temporalCountIcon from '@/assets/images/home/temporal_count.svg';
import activityEmpty from '@/assets/images/home/ActivityEmpty.svg'
import Empty from '@/components/Empty';
import Card from './Card';
import { getRecentActivityStats } from '@/api/memory';

interface Data {
  latest_relative: string;
  stats: RecentActivities;
}
interface RecentActivities {
  "chunk_count": number; // 数据分块
  "statements_count": number; // 语句提取
  "triplet_entities_count": number; // 实体关系萃取-实体节点
  "triplet_relations_count": number; // 实体关系萃取 - 关系连接
  "temporal_count": number; // 时间萃取
}

const activityList = [
  { key: 'chunk_count', icon: chunkCountIcon },
  { key: 'statements_count', icon: statementsCountIcon },
  { key: 'triplet_count', icon: tripletCountIcon },
  { key: 'temporal_count', icon: temporalCountIcon },
]

const RecentActivity:FC = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<Data | null>(null);
  const [recentActivities, setRecentActivities] = useState<RecentActivities | null>(null);

  useEffect(() => {
    getRecentActivityList()
  }, [])

  // 最近活动统计
  const getRecentActivityList = () => {
    setLoading(true)
    getRecentActivityStats().then(res => {
      const response = res as Data || {}
      setData(response)
      setRecentActivities(response.stats as RecentActivities || {})
    })
    .finally(() => setLoading(false))
  }

  return (
    <Card
      title={t('dashboard.recentMemoryActivities')}
    >
      {loading
        ? <Skeleton />
        : !recentActivities || Object.keys(recentActivities).length === 0
        ? <Empty url={activityEmpty} subTitle={t('dashboard.activityEmpty')} size={120} className="rb:mt-[45px] rb:mb-[81px]" />
        : activityList.map((item, index) => (
          <div key={item.key} className={clsx("rb:flex rb:justify-between rb:items-center rb:not-italic", {
            'rb:mt-[24px]': index !== 0
          })}>
            <div className="rb:flex rb:items-center rb:text-[#060419] rb:text-[16px] rb:font-medium">
              <img className="rb:w-[40px] rb:h-[40px] rb:mr-[16px]" src={item.icon} />
              <div>
                {t(`dashboard.${item.key}`)}
                <div className="rb:text-[#5B6167] rb:text-[14px] rb:font-normal">
                  {item.key === 'triplet_count' 
                    ? t(`dashboard.${item.key}_desc`, { entities_count: recentActivities.triplet_entities_count, relations_count: recentActivities.triplet_relations_count })
                    : t(`dashboard.${item.key}_desc`, { count: recentActivities[item.key as keyof RecentActivities] })
                  }
                </div>
              </div>
            </div>
            <div className="rb:text-[#5F6266] rb:text-right rb:whitespace-nowrap">{data?.latest_relative || ''}</div>
          </div>
        ))
      }
    </Card>
  )
}
export default RecentActivity