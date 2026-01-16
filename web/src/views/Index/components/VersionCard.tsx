/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-01-12 16:34:59
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-01-16 13:00:22
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Divider } from 'antd';
// import arrowRight from '@/assets/images/index/arrow_right.svg'
import { getVersion, type versionResponse } from '@/api/common'

const GuideCard: React.FC = () => {
  const { t, i18n } = useTranslation();
  const [versionInfo, setVersionInfo] = useState<versionResponse | null>(null);

  // 获取当前语言对应的介绍信息
  const getIntroduction = () => {
    if (!versionInfo) return null;
    const currentLang = i18n.language;
    return currentLang === 'zh' ? versionInfo.introduction : (versionInfo.introduction_en || versionInfo.introduction);
  };

  useEffect(() => {
    const fetchVersion = async () => {
      try {
        const response = await getVersion();
        setVersionInfo(response);
      } catch (error) {
        console.error('Failed to fetch version:', error);
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
        <div className='rb:flex rb:flex-col rb:max-h-[420px] rb:overflow-y-auto rb:text-[#5B6167]'>
            {versionInfo && (() => {
              const introduction = getIntroduction();
              return introduction ? (<>  
                <div className='rb:flex  rb:items-center rb:gap-2 rb:text-sm rb:text-[#5B6167] rb:leading-5 '>
                   
                    <span className='rb:text-xs rb:text-[#5B6167]'>
                      {t('version.releaseDate')}: {introduction.releaseDate}
                    </span>
                    <Divider type='vertical' />
                    <span className='rb:text-xs rb:text-[#5B6167]'>
                      {t('version.name')}: {introduction.codeName}
                    </span>
                </div>
                <p className='rb:text-sm rb:text-[#5B6167] rb:leading-5 rb:mt-2 '>  
                    {introduction.upgradePosition}
                </p>
                {introduction.coreUpgrades?.map((item: string, index: number) => (
                    <p key={index} className='rb:text-sm rb:text-[#5B6167] rb:leading-5'>
                      {index + 1}. {item}
                    </p>
                ))}
              </>) : null;
            })()}
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