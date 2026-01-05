import React from 'react';
import { useTranslation } from 'react-i18next';
import guideBgImg from '@/assets/images/index/guide_bg@2x.png'
import { Button } from 'antd';
import { ArrowRightOutlined } from '@ant-design/icons'
import arrowRight from '@/assets/images/index/arrow_right_blue.svg'
const GuideCard: React.FC = () => {
  const { t } = useTranslation();

  return (
    <div className='rb:w-full rb:h-[204px] rb:p-4' style={{ backgroundImage: `url(${guideBgImg})`, backgroundSize: '100% 100%' }}>
        <div className='rb:flex rb:justify-start rb:text-white rb:text-base rb:font-semibold'>
            { t('index.getStarted')}
        </div>
        <div className='rb:flex rb:text-xs rb:text-white rb:leading-[18px] rb:mt-3'>
            { t('index.startedDesc')}
        </div>
        <div className='rb:flex rb:w-full rb:items-center rb:justify-between rb:gap-3 rb:mt-4'>
            <Button className='rb:gap-2 rb:flex rb:items-center rb:text-[#155EEF] '>
                <span className='rb:text-xs'>{ t('index.viewGuide')}</span>
                <img src={arrowRight} className='rb:size-4' />
            </Button>
            <Button className='rb:gap-2 rb:flex rb:items-center rb:text-[#155EEF]'>
                <span className='rb:text-xs'>{ t('index.watchVideo')}</span>
                <img src={arrowRight} className='rb:size-4' />
            </Button>
        </div>
    </div>
  );
};

export default GuideCard;