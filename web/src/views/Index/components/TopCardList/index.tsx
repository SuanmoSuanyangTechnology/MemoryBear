import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Flex } from 'antd';
import clsx from 'clsx';

import  { type DataResponse } from '@/api/common'
import Tag from '@/components/Tag'

const list = [
  {
    key: 'models',
    icon: 'rb:bg-[url("@/assets/images/index/models.svg")]',
    background: 'linear-gradient( 136deg, rgba(21,94,239,0.06) 0%, rgba(251,253,255,0) 100%)'
  },
  {
    key: 'spaces',
    icon: 'rb:bg-[url("@/assets/images/index/spaces.svg")]',
    background: 'linear-gradient( 134deg, rgba(54,159,33,0.06) 0%, rgba(251,253,255,0) 100%)',
  },
  {
    key: 'users',
    icon: 'rb:bg-[url("@/assets/images/index/users.svg")]',
    background: 'linear-gradient( 136deg, rgba(77,168,255,0.06) 0%, rgba(251,253,255,0) 100%)',
  },
  {
    key: 'running_apps',
    icon: 'rb:bg-[url("@/assets/images/index/apps.svg")]',
    background: 'linear-gradient( 136deg, rgba(156,111,255,0.06) 0%, rgba(251,253,255,0) 100%)',
  },
]
const TopCardList: FC<{data?: DataResponse}> = ({ data }) => {
  const { t } = useTranslation()
  return (
    <div className="rb:grid rb:grid-cols-4 rb:gap-3">
      {list.map((item) => {
        return (
          <div 
            key={item.key}
            className="rb:bg-white rb:rounded-xl rb:p-4"
          >
            <Flex justify="space-between" align="center" gap={24}>
              <div className="rb:text-[12pxx] rb:font-medium rb:flex-1">{t(`dashboard.${'total_' + item.key}`)}</div>
              <div className={`rb:size-8 rb:bg-cover ${item.icon}`}></div>
            </Flex>

            <div className="rb:mt-5 rb:font-[MiSans-Bold] rb:font-bold rb:text-[24px] rb:leading-8">
              {item.key === 'spaces' && String(data?.active_workspaces || 0)}
              {item.key === 'running_apps' &&  String(data?.[`${item.key}` as keyof DataResponse] || 0)}
              {item.key !== 'spaces' && item.key !== 'running_apps' && String(data?.[`total_${item.key}` as keyof DataResponse] || 0)}
            </div>
            <Flex vertical align="start" className='rb:mt-2!'>
              {item.key === 'models'
                ? (
                  <div className='rb:text-xs rb:leading-4 rb:text-[#5F6266] rb:w-32.25'>
                    {t(`dashboard.${'desc_' + item.key}`, { account: data?.total_llm, nums: data?.total_embedding })}
                  </div>
                )
                : (<>                  
                  <Flex align="center" gap={4} className='rb:text-xs rb:leading-4'> 
                    {item.key === 'spaces' && (<>
                      <div className={clsx("rb:size-3 rb:bg-cover rb:mr-0.5", {
                        "rb:bg-[url('@/assets/images/index/arrow_up_d.svg')]": Number(data?.new_workspaces_this_week || 0) >= 0,
                        "rb:bg-[url('@/assets/images/index/arrow_down_d.svg')]": Number(data?.new_workspaces_this_week || 0) < 0,
                      })}></div>
                      <span className={clsx('rb:font-medium', {
                        "rb:text-[#369F21]": Number(data?.new_workspaces_this_week || 0) >= 0,
                        "rb:text-[#FF5D34]": Number(data?.new_workspaces_this_week || 0) < 0,
                      })}>{Number(data?.new_workspaces_this_week || 0) >= 0 ? '+' : '-'}{Math.abs(Number(data?.new_workspaces_this_week || 0))}</span>
                    </>)}
                    {item.key === 'users' && (<>
                      <div className={clsx("rb:size-3 rb:bg-cover rb:mr-0.5", {
                        "rb:bg-[url('@/assets/images/index/arrow_up_d.svg')]": Number(data?.new_users_this_week || 0) >= 0,
                        "rb:bg-[url('@/assets/images/index/arrow_down_d.svg')]": Number(data?.new_users_this_week || 0) < 0,
                      })}></div>
                      <span className={clsx('rb:font-medium', {
                        "rb:text-[#369F21]": Number(data?.new_users_this_week || 0) >= 0,
                        "rb:text-[#FF5D34]": Number(data?.new_users_this_week || 0) < 0,
                      })}>{Number(data?.new_users_this_week || 0) >= 0 ? '+' : '-'}{Math.abs(Number(data?.new_users_this_week || 0))}</span>
                    </>)}
                    {item.key === 'running_apps' && (<>
                      <div className={clsx("rb:size-3 rb:bg-cover rb:mr-0.5", {
                        "rb:bg-[url('@/assets/images/index/arrow_up_d.svg')]": Number(data?.new_apps_this_week || 0) >= 0,
                        "rb:bg-[url('@/assets/images/index/arrow_down_d.svg')]": Number(data?.new_apps_this_week || 0) < 0,
                      })}></div>
                      <span className={clsx('rb:font-medium', {
                        "rb:text-[#369F21]": Number(data?.new_apps_this_week || 0) >= 0,
                        "rb:text-[#FF5D34]": Number(data?.new_apps_this_week || 0) < 0,
                      })}>{Number(data?.new_apps_this_week || 0) >= 0 ? '+' : '-'}{Math.abs(Number(data?.new_apps_this_week || 0))}</span>
                    </>)}
                  </Flex>
                  <div className='rb:text-[12px] rb:leading-4 rb:text-[#5F6266]'>
                    {t(`dashboard.${'desc_' + item.key}`)}
                  </div>
                </>)
              }
            </Flex>
            
            {item.key === 'models' && (<Tag color={Number(data?.model_week_growth_rate || 0) >= 0 ? "success" : "warning"} className="rb:mt-2">
              <Flex align="center">
                <div className={clsx("rb:size-3.5 rb:bg-cover rb:mr-0.5", {
                  "rb:bg-[url('@/assets/images/index/arrow_up.svg')]": Number(data?.model_week_growth_rate || 0) >= 0,
                  "rb:bg-[url('@/assets/images/index/arrow_down.svg')]": Number(data?.model_week_growth_rate || 0) < 0,
                })}></div>
                <span>{Math.abs(Number(data?.model_week_growth_rate || 0))}% {t('dashboard.thisWeek')}</span>
              </Flex>
            </Tag>)}
            {item.key === 'spaces' && (<Tag color={Number(data?.workspace_week_growth_rate || 0) >= 0 ? "success" : "warning"} className="rb:mt-2">
              <Flex align="center">
                <div className={clsx("rb:size-3.5 rb:bg-cover rb:mr-0.5", {
                  "rb:bg-[url('@/assets/images/index/arrow_up.svg')]": Number(data?.workspace_week_growth_rate || 0) >= 0,
                  "rb:bg-[url('@/assets/images/index/arrow_down.svg')]": Number(data?.workspace_week_growth_rate || 0) < 0,
                })}></div>
                <span>{Math.abs(Number(data?.workspace_week_growth_rate || 0))}% {t('dashboard.thisWeek')}</span>
              </Flex>
            </Tag>)}
            {item.key === 'users' && (<Tag color={Number(data?.user_week_growth_rate || 0) >= 0 ? "success" : "warning"} className="rb:mt-2">
              <Flex align="center">
                <div className={clsx("rb:size-3.5 rb:bg-cover rb:mr-0.5", {
                  "rb:bg-[url('@/assets/images/index/arrow_up.svg')]": Number(data?.user_week_growth_rate || 0) >= 0,
                  "rb:bg-[url('@/assets/images/index/arrow_down.svg')]": Number(data?.user_week_growth_rate || 0) < 0,
                })}></div>
                <span>{Math.abs(Number(data?.user_week_growth_rate || 0))}% {t('dashboard.thisWeek')}</span>
              </Flex>
            </Tag>)}
            {item.key === 'running_apps' && (<Tag color={Number(data?.app_week_growth_rate || 0) >= 0 ? "success" : "warning"} className="rb:mt-2">
              <Flex align="center">
                <div className={clsx("rb:size-3.5 rb:bg-cover rb:mr-0.5", {
                  "rb:bg-[url('@/assets/images/index/arrow_up.svg')]": Number(data?.app_week_growth_rate || 0) >= 0,
                  "rb:bg-[url('@/assets/images/index/arrow_down.svg')]": Number(data?.app_week_growth_rate || 0) < 0,
                })}></div>
                <span>{Math.abs(Number(data?.app_week_growth_rate || 0))}% {t('dashboard.thisWeek')}</span>
              </Flex>
            </Tag>)}
          </div>
        )
      })}
    </div>
  )
}

export default TopCardList