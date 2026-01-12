import React, { useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import guideBgImg from '@/assets/images/index/guide_bg@2x.png'
import { Button, Tour } from 'antd';
import type { TourProps } from 'antd';
import arrowRight from '@/assets/images/index/arrow_right_blue.svg'

const GuideCard: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open, setOpen] = useState<boolean>(false);
  const [currentStep, setCurrentStep] = useState<number>(0);
  const startButtonRef = useRef<HTMLButtonElement>(null);

  // Tour 步骤配置
  const steps: TourProps['steps'] = [
    {
      title: t('indexTour.startTitle'),
      description: t('indexTour.startDescription'),
      target: () => startButtonRef.current!,
    },
    {
      title: t('indexTour.stepOne'),
      description: t('indexTour.stepOneDescription'),
      target: () => document.querySelector('[data-menu-id="/model"]') as HTMLElement,
    },
    {
      title: t('indexTour.stepTwo'),
      description: t('indexTour.stepTwoDescription'),
      target: () => document.querySelector('[data-menu-id="/space"]') as HTMLElement,
    },
    {
      title: t('indexTour.stepThree'),
      description: t('indexTour.stepThreeDescription'),
      target: () => document.querySelector('[data-menu-id="/user-management"]') as HTMLElement,
    }
  ];

  // 开始引导
  const handleStartGuide = () => {
    setCurrentStep(0);
    setOpen(true);
  };

  // Tour 步骤变化处理
  const handleStepChange = (current: number) => {
    setCurrentStep(current);
    // 不再自动跳转页面，让用户通过点击菜单项来导航
  };

  // Tour 完成处理
  const handleTourFinish = () => {
    setOpen(false);
    setCurrentStep(0);
    // 完成后导航到模型管理页面
    navigate('/model');
  };

  return (
    <>
    <div className='rb:w-full rb:h-[204px] rb:p-4' style={{ backgroundImage: `url(${guideBgImg})`, backgroundSize: '100% 100%' }}>
        <div className='rb:flex rb:justify-start rb:text-white rb:text-base rb:font-semibold' >
            { t('index.getStarted')}
        </div>
        <div className='rb:flex rb:text-xs rb:text-white rb:leading-[18px] rb:mt-3'>
            { t('index.startedDesc')}
        </div>
        <div className='rb:flex rb:w-full rb:items-center rb:justify-between rb:gap-3 rb:mt-4'>
            <Button ref={startButtonRef} className='rb:gap-2 rb:w-full rb:flex rb:items-center rb:text-[#155EEF]'  onClick={handleStartGuide}>
                <span className='rb:text-xs'>{ t('index.viewGuide')}</span>
                <img src={arrowRight} className='rb:size-4' />
            </Button>
            {/* <Button className='rb:gap-2 rb:flex rb:items-center rb:text-[#155EEF]'>
                <span className='rb:text-xs'>{ t('index.watchVideo')}</span>
                <img src={arrowRight} className='rb:size-4' />
            </Button> */}
        </div>
    </div>
    
    <Tour
      open={open}
      onClose={() => setOpen(false)}
      steps={steps}
      current={currentStep}
      onChange={handleStepChange}
      onFinish={handleTourFinish}
    />
    </>
  );
};

export default GuideCard;