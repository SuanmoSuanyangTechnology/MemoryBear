/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-01-13 11:44:06
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-01-15 20:59:57
 */
import React, { useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Button, Tour } from 'antd';
import type { TourProps } from 'antd';

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
      <div className='rb:w-full rb:bg-white rb:rounded-xl rb:pb-3'>
        <div className='rb:font-[MiSans-Bold] rb:font-bold rb:leading-5 rb:p-3 rb:bg-cover rb:rounded-tl-xl rb:rounded-tr-xl rb:bg-[url("@/assets/images/index/guide_bg@2x.png")]' >
          { t('index.getStarted')}
        </div>
        <div className='rb:leading-4.5 rb:text-[12px] rb:-mt-2 rb:pl-3 rb:pr-1.75'>
            { t('index.startedDesc')}
        </div>
        <div className="rb:mt-2 rb:pl-3 rb:pr-4">
          <Button ref={startButtonRef} block className='rb:gap-1 rb:flex rb:items-center' onClick={handleStartGuide}>
            <span className='rb:text-xs'>{t('index.viewGuide')}</span>
            <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_right_dark.svg')]"></div>
          </Button>
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