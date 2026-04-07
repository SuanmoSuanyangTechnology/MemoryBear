/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:28:07 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-07 23:23:04
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

import type { DashboardData } from '../index'

/** Card configuration with styling */
const list = [
  {
    key: 'total_memory',
    background: 'rb:bg-[url("@/assets/images/home/totalMemoryCapacity.png")] rb:bg-cover rb:bg-no-repeat',
  },
  {
    key: 'total_app',
  },
  {
    key: 'total_knowledge',
  },
  {
    key: 'total_api_call',
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
              'rb:text-[#FFFFFF]': item.key === 'total_memory',
              'rb:text-[#5B6167]': item.key !== 'total_memory',
            })}>{t(`dashboard.${item.key}`)}</div>

            <div className={clsx("rb:text-[20px] rb:font-bold rb:leading-7 rb:mt-1 rb:font-[MiSans-Bold]", {
              'rb:text-[#FFFFFF]': item.key === 'total_memory',
            })}>
              {data?.[item.key as keyof DashboardData] || 0}
            </div>

            <Flex align="center" className={clsx('rb:font-medium rb:mt-7.5!', {
              'rb:text-[#FF5D34]': data?.[`${item.key}_change` as keyof DashboardData] && data?.[`${item.key}_change` as keyof DashboardData] < 0,
              'rb:text-[#369F21]': !data?.[`${item.key}_change` as keyof DashboardData] || data?.[`${item.key}_change` as keyof DashboardData] >= 0,
            })}>
              {data?.[`${item.key}_change` as keyof DashboardData] && typeof data?.[item.key as keyof DashboardData] === 'number'
                ? (100 * data?.[`${item.key}_change` as keyof DashboardData]).toFixed(2)
                : 0
              }%
              <div className={clsx("rb:size-3.5 rb:cursor-pointer rb:bg-cover", {
                "rb:bg-[url('@/assets/images/home/arrow_down.png')]": data?.[`${item.key}_change` as keyof DashboardData] && data?.[`${item.key}_change` as keyof DashboardData] < 0,
                "rb:bg-[url('@/assets/images/home/arrow_up_success.svg')]": !data?.[`${item.key}_change` as keyof DashboardData] || data?.[`${item.key}_change` as keyof DashboardData] >= 0,
              })}></div>
            </Flex>
            <div className={clsx("rb:text-[12px] rb:leading-4 rb:mt-0.5", {
              'rb:text-[#FFFFFF]': item.key === 'total_memory',
              'rb:text-[#5B6167]': item.key !== 'total_memory',
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