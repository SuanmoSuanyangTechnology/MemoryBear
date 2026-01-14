/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-01-12 16:34:59
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-01-13 19:14:30
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Divider } from 'antd';
// import arrowRight from '@/assets/images/index/arrow_right.svg'
import { getVersion, type versionResponse } from '@/api/common'

const GuideCard: React.FC = () => {
  const { t } = useTranslation();
  const [versionInfo, setVersionInfo] = useState<versionResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const fetchVersion = async () => {
      try {
        setLoading(true);
        const response = await getVersion();
        setVersionInfo(response);
      } catch (error) {
        console.error('Failed to fetch version:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchVersion();
  }, []);
    
  return (
    <div className='rb:w-full rb:p-4 rb:border-1 rb:border-[#DFE4ED] rb:bg-[#FBFDFF] rb:rounded-xl'>
        <div className='rb:flex rb:items-center rb:justify-start rb:text-[#5B6167] rb:text-base rb:font-semibold rb:gap-2'>
            { t('index.latestUpdate')} 
            <span className='rb:text-xs rb:text-[#1890FF]'>
              {versionInfo?.version}
            </span>
        </div>
        <div className='rb:flex rb:flex-col rb:text-[#5B6167]'>
            {versionInfo && (<>  
              <div className='rb:flex rb:items-center rb:gap-2 rb:text-sm rb:text-[#5B6167] rb:leading-5 '>
                 
                  <span className='rb:text-xs rb:text-[#5B6167]'>
                    {t('version.releaseDate')}: {versionInfo.introduction?.releaseDate}
                  </span>
                  <Divider type='vertical' />
                  <span className='rb:text-xs rb:text-[#5B6167]'>
                    {t('version.name')}: {versionInfo.introduction?.codeName}
                  </span>
              </div>
              <p className='rb:text-sm rb:text-[#5B6167] rb:leading-5 rb:mt-2 '>  
                  {versionInfo.introduction?.upgradePosition}
              </p>
              {versionInfo.introduction?.coreUpgrades?.map((item,index) => (
                  <p className='rb:text-sm rb:text-[#5B6167] rb:leading-5'>
                    {index + 1}. {item}
                  </p>
              ))}
            </>)}
            {/* {loading ? (
              t('index.loading')
            ) : (
              versionInfo?.introduction || t('index.latestUpdateDesc')
            )} */}
        </div>
        {/* <div className='rb:flex rb:w-full rb:items-center rb:justify-between rb:gap-3 rb:mt-4'>
            <Button className='rb:gap-2 rb:flex rb:items-center rb:text-[#212332] '>
                <span className='rb:text-xs'>{ t('index.viewDetails')}</span>
                <img src={arrowRight} className='rb:size-4' />
            </Button>
            <Button className='rb:gap-2 rb:flex rb:items-center rb:text-[#212332]'>
                <span className='rb:text-xs'>{ t('index.changeLog')}</span>
                <img src={arrowRight} className='rb:size-4' />
            </Button>
        </div> */}
    </div>
  );
};

export default GuideCard;