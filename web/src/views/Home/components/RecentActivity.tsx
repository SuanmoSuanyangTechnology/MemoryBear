/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:15:33 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:15:33 
 */
/**
 * Recent Activity Component
 * Displays recent memory processing activities with statistics
 * Shows chunk count, statements, entity relations, and temporal data
 */

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

/**
 * API response data structure
 */
interface Data {
  latest_relative: string;
  stats: RecentActivities;
}
/**
 * Recent activity statistics
 */
interface RecentActivities {
  /** Data chunk count */
  "chunk_count": number;
  /** Statement extraction count */
  "statements_count": number;
  /** Entity node count */
  "triplet_entities_count": number;
  /** Relation connection count */
  "triplet_relations_count": number;
  /** Temporal extraction count */
  "temporal_count": number;
}

/** Activity list configuration */
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

  /** Fetch recent activity statistics */
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
        ? <Empty url={activityEmpty} subTitle={t('dashboard.activityEmpty')} size={120} className="rb:mt-11.25 rb:mb-20.25" />
        : activityList.map((item, index) => (
          <div key={item.key} className={clsx("rb:flex rb:justify-between rb:items-center rb:not-italic", {
            'rb:mt-6': index !== 0
          })}>
            <div className="rb:flex rb:items-center rb:text-[#060419] rb:text-[16px] rb:font-medium">
              <img className="rb:w-10 rb:h-10 rb:mr-4" src={item.icon} />
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