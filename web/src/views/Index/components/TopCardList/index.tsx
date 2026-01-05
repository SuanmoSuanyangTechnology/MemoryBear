import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import totalModels from '@/assets/images/index/models.svg';
import totalSpaces from '@/assets/images/index/spaces.svg';
import totalUsers from '@/assets/images/index/users.svg';
import totalApps from '@/assets/images/index/apps.svg';
import arrowUpDb from '@/assets/images/index/arrow_up_d.svg'
import arrowDownDb from '@/assets/images/index/arrow_down_d.svg'
import arrowUp from '@/assets/images/index/arrow_up.svg'
import arrowDown from '@/assets/images/index/arrow_down.svg'
import styles from './index.module.css'
import type { DashboardData } from '../../types'

const list = [
  {
    key: 'models',
    icon: totalModels,
    value: '24',
    // trendValue: '12.5%',
    trend: 'up',
    // trendDesc: 'comparedToYesterday',
    rate:"up",
    rateValue: '12%',
    background: 'linear-gradient( 136deg, rgba(21,94,239,0.06) 0%, rgba(251,253,255,0) 100%)'
  },
  {
    key: 'spaces',
    icon: totalSpaces,
    value: '156',
    trendValue: '+8',
    trend: 'down',
    rate:"up",
    rateValue: '5.4%',
    // trendDesc: 'comparedToYesterday',
    background: 'linear-gradient( 134deg, rgba(54,159,33,0.06) 0%, rgba(251,253,255,0) 100%)',
  },
  {
    key: 'users',
    icon: totalUsers,
    value: '1,248',
    trendValue: '+42',
    trend: 'up',
    rate:"up",
    rateValue: '12%',
    // trendDesc: 'thisWeek',
    background: 'linear-gradient( 136deg, rgba(77,168,255,0.06) 0%, rgba(251,253,255,0) 100%)',
  },
  {
    key: 'apps_runs',
    icon: totalApps,
    value: '12.8k',
    trendValue: '98.7%',
    trend: 'up',
    rate:"down",
    rateValue: '2.1%',
    // trendDesc: 'comparedToYesterday',
    background: 'linear-gradient( 136deg, rgba(156,111,255,0.06) 0%, rgba(251,253,255,0) 100%)',
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
              <div className="rb:text-xs rb:font-medium rb:text-[#212332] rb:w-[96px]">{t(`dashboard.${'total_' + item.key}`)}</div>
              <div className={styles.avatar}><img src={item.icon} /></div>
            </div>

            <div className={styles.content}>
              {data?.[`total_${item.key}` as keyof DashboardData] || item.value || 0}
            </div>
            <div className='rb:flex rb:flex-col rb:items-start'>
                {item.key === 'models' ? (
                  <div className='rb:text-xs rb:leading-4 rb:text-[#5F6266] rb:w-[130px]'>
                   {t(`dashboard.${'desc_' + item.key}`, { account: 18, nums: 6 })}
                  </div>
                ) : (<>                  
                  <div className='rb:flex rb:items-center rb:text-xs rb:leading-4 rb:gap-1'> 
                    <img src={item.trend === 'up' ? arrowUpDb : arrowDownDb} className='rb:size-3'/>
                    <span className={item.trend === 'up' ? 'rb:text-[#369F21]' : 'rb:text-[#FF5D34]'}>{item.trendValue}</span>
                  </div>
                  <div className='rb:text-xs rb:leading-4 rb:text-[#5F6266]'>
                    {t(`dashboard.${'desc_' + item.key}`)}
                  </div>
                </>)}
            </div>
            <div className={`rb:flex rb:max-w-40 rb:text-xs rb:mt-2 rb:items-center rb:gap-1 rb:border-1 rb:rounded rb:px-2 rb:py-0.5 ${item.rate === 'up' ? 'rb:text-[#369F21] rb:border-[#369F21] rb:bg-[rgba(54, 159, 33, 0.25)]' : 'rb:text-[#FF5D34] rb:border-[#FF5D34] rb:bg-[rgba(255, 93, 52, 0.25)]'}`}>
               <img src={item.rate === 'up' ? arrowUp : arrowDown} className='rb:size-3'/>  
               <span> {item.rateValue} </span>
               {(item.key === 'models' || item.key === 'users') && (<span>{t('dashboard.thisWeek')}</span>)}
               {item.key === 'apps_runs' && (<span>{t('dashboard.failureRate')}</span>)}
               {item.key === 'spaces' && (<span>{t('dashboard.thisDay')}</span>)}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default TopCardList