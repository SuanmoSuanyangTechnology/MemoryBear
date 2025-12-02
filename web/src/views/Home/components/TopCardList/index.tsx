import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import totalMemoryCapacity from '@/assets/images/home/totalMemoryCapacity.svg';
import userMemory from '@/assets/images/home/userMemory.svg';
import knowledgeBaseCount from '@/assets/images/home/knowledgeBaseCount.svg';
import apiCallCount from '@/assets/images/home/apiCallCount.svg';
import styles from './index.module.css'
import clsx from 'clsx';
import type { DashboardData } from '../../index'

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
const TopCardList: FC<{data?: DashboardData}> = ({ data }) => {
  const { t } = useTranslation()
  return (
    <div className="rb:grid rb:grid-cols-4 rb:gap-[16px]">
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
            {data?.[item.key] || item.value || 0}
            <div className={styles.contentRight}>
              {item.trendValue && <div className={clsx(styles.trend, styles[item.trend])}>{item.trendValue}</div>}
              {item.trendDesc && <div>{t(`dashboard.${item.trendDesc}`)}</div>}
            </div>
          </div>
          </div>
        )
      })}
    </div>
  )
}

export default TopCardList