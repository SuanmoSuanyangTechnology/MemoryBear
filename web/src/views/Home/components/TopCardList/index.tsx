/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:28:07 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:28:07 
 */
/**
 * Top Card List Component
 * Displays dashboard summary cards for key metrics
 * Shows total memory capacity, applications, knowledge bases, and API calls
 */

import { type FC } from 'react'
import { useTranslation } from 'react-i18next'

import totalMemoryCapacity from '@/assets/images/home/totalMemoryCapacity.svg';
import userMemory from '@/assets/images/home/userMemory.svg';
import knowledgeBaseCount from '@/assets/images/home/knowledgeBaseCount.svg';
import apiCallCount from '@/assets/images/home/apiCallCount.svg';
import styles from './index.module.css'
import type { DashboardData } from '../../index'

/** Card configuration with styling */
const list = [
  {
    key: 'totalMemoryCapacity',
    icon: totalMemoryCapacity,
    // value: '45,678',
    // trendValue: '12.5%',
    // trend: 'up',
    // trendDesc: 'comparedToYesterday',
    background: 'linear-gradient(180deg, #E6EFFE 0%, #F9FDFF 100%)',
  },
  {
    key: 'application',
    icon: userMemory,
    // value: '32,145',
    // trendValue: '12.5%',
    // trend: 'down',
    // trendDesc: 'comparedToYesterday',
    background: 'linear-gradient( 180deg, #F1FBF5 0%, #F9FDFF 100%)',
  },
  {
    key: 'knowledgeBaseCount',
    icon: knowledgeBaseCount,
    // value: '13,533',
    // trendValue: '15.7%',
    // trend: 'up',
    // trendDesc: 'thisWeek',
    background: 'linear-gradient( 180deg, #E6F5FE 0%, #FBFDFF 100%)',
  },
  {
    key: 'apiCallCount',
    icon: apiCallCount,
    // value: '856.2k',
    // trendValue: '23.1%',
    // trend: 'up',
    // trendDesc: 'comparedToYesterday',
    background: 'linear-gradient( 180deg, #F8F6F5 0%, #FAFDFF 100%)',
  },
]
/**
 * Component props
 * @param data - Dashboard statistics data
 */
const TopCardList: FC<{data?: DashboardData}> = ({ data }) => {
  const { t } = useTranslation()
  return (
    <div className="rb:grid rb:grid-cols-4 rb:gap-4">
      {list.map((item) => {
        return (
          <div 
            key={item.key}
            className={styles.card}
            style={{
              background: item.background,
            }}
          >
          <div className={styles.header}>
            <div className={styles.avatar}><img src={item.icon} /></div>
            <div className={styles.headerTitle}>{t(`dashboard.${item.key}`)}</div>
          </div>

          <div className={styles.content}>
            {data?.[item.key as keyof DashboardData] || 0}
          </div>
          </div>
        )
      })}
    </div>
  )
}

export default TopCardList