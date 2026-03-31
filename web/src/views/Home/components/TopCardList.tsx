/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:28:07 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-11 14:57:55
 */
/**
 * Top Card List Component
 * Displays dashboard summary cards for key metrics
 * Shows total memory capacity, applications, knowledge bases, and API calls
 */

import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx';
import { Flex } from 'antd';

import totalMemoryCapacity from '@/assets/images/home/totalMemoryCapacity.svg';
import userMemory from '@/assets/images/home/userMemory.svg';
import knowledgeBaseCount from '@/assets/images/home/knowledgeBaseCount.svg';
import apiCallCount from '@/assets/images/home/apiCallCount.svg';
import type { DashboardData } from '../index'

/** Card configuration with styling */
const list = [
  {
    key: 'totalMemoryCapacity',
    icon: totalMemoryCapacity,
    // value: '45,678',
    // trendValue: '12.5%',
    // trend: 'up',
    // trendDesc: 'comparedToYesterday',
    background: 'rb:bg-[url("@/assets/images/home/totalMemoryCapacity.png")] rb:bg-cover rb:bg-no-repeat',
  },
  {
    key: 'application',
    icon: userMemory,
    // value: '32,145',
    // trendValue: '12.5%',
    // trend: 'down',
    // trendDesc: 'comparedToYesterday',
    // background: 'linear-gradient( 180deg, #F1FBF5 0%, #F9FDFF 100%)',
  },
  {
    key: 'knowledgeBaseCount',
    icon: knowledgeBaseCount,
    // value: '13,533',
    // trendValue: '15.7%',
    // trend: 'up',
    // trendDesc: 'thisWeek',
    // background: 'linear-gradient( 180deg, #E6F5FE 0%, #FBFDFF 100%)',
  },
  {
    key: 'apiCallCount',
    icon: apiCallCount,
    // value: '856.2k',
    // trendValue: '23.1%',
    // trend: 'up',
    // trendDesc: 'comparedToYesterday',
    // background: 'linear-gradient( 180deg, #F8F6F5 0%, #FAFDFF 100%)',
  },
]
/**
 * Component props
 * @param data - Dashboard statistics data
 */
const TopCardList: FC<{data?: DashboardData}> = ({ data }) => {
  const { t } = useTranslation()
  return (
    <div className="rb:grid rb:grid-cols-2 rb:gap-3">
      {list.map((item) => {
        return (
          <div 
            key={item.key}
            className={`rb:rounded-2xl rb:bg-[#FFFFFF] rb:py-4 rb:px-3  ${item.background || ''}`}
          >
            <div className={clsx("rb:text-[12px] rb:leading-4", {
              'rb:text-[#FFFFFF]': item.key === 'totalMemoryCapacity',
              'rb:text-[#5B6167]': item.key !== 'totalMemoryCapacity',
            })}>{t(`dashboard.${item.key}`)}</div>

            <div className={clsx("rb:text-[20px] rb:font-bold rb:leading-7 rb:mt-1 rb:font-[MiSans-Bold]", {
              'rb:text-[#FFFFFF]': item.key === 'totalMemoryCapacity',
              // 'rb:text-[#171719]': item.key !== 'totalMemoryCapacity',
            })}>
              {data?.[item.key as keyof DashboardData] || 0}
            </div>

            <Flex align="center" className={clsx('rb:font-medium rb:mt-7.5!', {
              'rb:text-[#FFFFFF]': item.key === 'totalMemoryCapacity',
              'rb:text-[#369F21]': item.key !== 'totalMemoryCapacity',
            })}>
              0%
              <div className={clsx("rb:size-3.5 rb:cursor-pointer rb:bg-cover", {
                "rb:bg-[url('@/assets/images/home/arrow_up.svg')]": item.key === 'totalMemoryCapacity',
                "rb:bg-[url('@/assets/images/home/arrow_up_success.svg')]": item.key !== 'totalMemoryCapacity',
              })}></div>
            </Flex>
            <div className={clsx("rb:text-[12px] rb:leading-4 rb:mt-0.5", {
              'rb:text-[#FFFFFF]': item.key === 'totalMemoryCapacity',
              'rb:text-[#5B6167]': item.key !== 'totalMemoryCapacity',
            })}>
              {t('dashboard.comparedToYesterday')}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default TopCardList