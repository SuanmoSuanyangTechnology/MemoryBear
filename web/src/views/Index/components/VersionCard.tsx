import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from 'antd';
import arrowRight from '@/assets/images/index/arrow_right.svg'
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
        <div className='rb:flex rb:items-center rb:justify-start rb:text-[#5B6167] rb:text-base rb:font-semibold'>
            { t('index.latestUpdate')}
            {versionInfo && (
              <span className='rb:ml-2 rb:text-sm rb:text-[#1890FF]'>
                {versionInfo.version}
              </span>
            )}
        </div>
        <div className='rb:flex rb:text-xs rb:text-[#5B6167] rb:leading-[18px] rb:mt-3 rb:pl-2'>
            {loading ? (
              t('index.loading')
            ) : (
              versionInfo?.introduction || t('index.latestUpdateDesc')
            )}
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