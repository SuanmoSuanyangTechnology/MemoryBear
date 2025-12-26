import React from 'react';
import { Button } from 'antd';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import personal from '@/assets/images/order/personal.png'
import team from '@/assets/images/order/team.png'
import biz from '@/assets/images/order/biz.png'
import commerce from '@/assets/images/order/commerce.png'
import checkIcon from '@/assets/images/login/checkBg.png'
import alertIcon from '@/assets/images/order/alert.svg';
import { useUser } from '@/store/user'

interface PriceItem {
  type: string;
  label: string;
  typeDesc: string;
  priceDescObj: {
    solution: string;
    targetAudience: string;
  };
  priceObj: {
    type: string;
    price?: number;
    time: string;
  };
  btnType: string;
  memoryCapacity: string;
  intelligentSearchFrequency: string;
  mostPopular?: boolean;
  flexibleDeployment?: boolean;
  reliableGuarantee?: boolean;
}
const btnClassNames = {
  personal: 'rb:h-10! rb:rounded-[8px]!',
  team: 'rb:h-10! rb:rounded-[8px]! rb:bg-[#FF5D34]! rb:text-white! rb:border-0! rb:hover:border-0! rb:hover:opacity-[0.8]',
  biz: 'rb:h-10! rb:rounded-[8px]!',
  commerce: 'rb:h-10! rb:rounded-[8px]! rb:bg-[#212332]! rb:text-white! rb:border-0! rb:hover:border-0! rb:hover:opacity-[0.8]',
}

export const PRICE_LIST: PriceItem[] = [
  {
    type: 'personal',
    label: 'pricing.personal.label',
    typeDesc: 'pricing.personal.typeDesc',
    priceDescObj: {
      solution: 'pricing.personal.solution',
      targetAudience: 'pricing.personal.targetAudience',
    },
    priceObj: {
      type: 'default',
      price: 0,
      time: 'pricing.personal.priceDesc',
    },
    btnType: 'started', // started / choosePlan / contact
    memoryCapacity: '2000',
    intelligentSearchFrequency: '100',
  },
  {
    type: 'team',
    label: 'pricing.team.label',
    typeDesc: 'pricing.team.typeDesc',
    priceDescObj: {
      solution: 'pricing.team.solution',
      targetAudience: 'pricing.team.targetAudience',
    },
    priceObj: {
      type: 'default',
      price: 19,
      time: 'pricing.team.priceDesc'
    },
    btnType: 'choosePlan', // started / choosePlan / contact
    memoryCapacity: '20,000',
    intelligentSearchFrequency: '10,000',
  },
  {
    type: 'biz',
    label: 'pricing.biz.label',
    typeDesc: 'pricing.biz.typeDesc',
    priceDescObj: {
      solution: 'pricing.biz.solution',
      targetAudience: 'pricing.biz.targetAudience',
    },
    mostPopular: true,
    priceObj: {
      type: 'default', // default / biz
      price: 299,
      time: 'pricing.biz.priceDesc'
    },
    btnType: 'choosePlan', // started / choosePlan / contact
    memoryCapacity: '100,000',
    intelligentSearchFrequency: '50,000',
  },
  {
    type: 'commerce',
    label: 'pricing.commerce.label',
    typeDesc: 'pricing.commerce.typeDesc',
    priceDescObj: {
      solution: 'pricing.commerce.solution',
      targetAudience: 'pricing.commerce.targetAudience',
    },
    priceObj: {
      type: 'commerce', // default / commerce
      time: 'pricing.commerce.priceDesc'
    },
    btnType: 'contact', // started / choosePlan / contact

    memoryCapacity: '20,000',
    intelligentSearchFrequency: '10,000',
    flexibleDeployment: true,
    reliableGuarantee: true
  },
]

const PricingView: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useUser();

  const handleChoosePlan = (type: string) => {
    switch(type) {
      case 'team':
      case 'biz':
        navigate(`/order-pay?type=${type}`);
        break
      case 'personal':
        navigate(user.current_workspace_id ? '/' : '/space');
        break
      case 'commerce':
        break
    }
  };
  const goToHistory = () => {
    navigate('/orders');
  }

  const getCardIcon = (type: string) => {
    const iconMap: Record<string, string> = {
      personal: personal,
      team: team, 
      biz: biz,
      commerce: commerce
    };
    return iconMap[type] || commerce;
  };

  return (
    <div className="rb:h-[calc(100vh-88px)] rb:overflow-y-auto rb:w-full rb:p-3">
      {/* <div className="rb:p-[20px_24px] rb:flex rb:items-center rb:justify-end rb:bg-[url(@/assets/images/order/bg.png)] rb:h-25 rb:rounded-xl rb:mb-6 rb:bg-cover rb:bg-no-repeat rb:bg-center rb:mb-[20px rb:w-full">
        <div className="rb:flex rb:items-center">
          <img src={getCardIcon('personal')} className="rb:size-15 rb:mr-3.5 rb:shrink-0" />
          <div className="rb:text-white rb:text-[24px] rb:font-semibold rb:leading-8">
            {t(`pricing.${'personal'}.type`)}
            <div className="rb:mt-1 rb:leading-5 rb:text-[14px] rb:font-regular">
              {t('pricing.currentAccountType')} | {t(`pricing.validUntil`)} <span className="rb:font-medium">December 31, 2024</span>
            </div>
          </div>
        </div>
        <Button className="rb:group rb:text-[#212332] rb:font-medium!" onClick={goToHistory}>
          <div
            className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/order/order.svg')] rb:group-hover:bg-[url('@/assets/images/order/order_hover.svg')]"
          ></div>
          {t('pricing.orderHistory')}
        </Button>
      </div> */}
      <div className="rb:flex rb:items-center rb:justify-end  rb:rounded-xl rb:mb-6 rb:w-full">
        <Button className="rb:group rb:text-[#212332] rb:font-medium!" onClick={goToHistory}>
          <div
            className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/order/order.svg')] rb:group-hover:bg-[url('@/assets/images/order/order_hover.svg')]"
          ></div>
          {t('pricing.orderHistory')}
        </Button>
      </div>
      
      <div className="rb:grid rb:grid-cols-4 rb:gap-6">
        {PRICE_LIST.map((item) => (
          <div
            key={item.type}
            className={`rb:relative rb:bg-[#FBFDFF] rb:rounded-xl rb:border rb:border-[#DFE4ED] rb:px-5 rb:py-6 rb:shadow-sm rb:transition-all rb:duration-200 hover:rb:shadow-lg ${
              item.mostPopular ? 'rb:-top-3' : ''
            }`}
          >
            {item.mostPopular && (
              <div className="rb:absolute rb:right-0 rb:top-0 rb:bg-[#FF5D34] rb:rounded-[0px_12px_0px_12px] rb:text-[12px] rb:text-white rb:font-regular rb:leading-4 rb:p-[4px_8px]">
                {t('pricing.mostPopular')}
              </div>
            )}

            {/* Icon */}
            <img src={getCardIcon(item.type)} className="rb:size-15 rb:mb-6" />

            {/* Title */}
            <h3 className="rb:text-[28px] rb:font-extrabold rb:mb-2">
              {t(`pricing.${item.type}.type`)}
            </h3>

            {/* Description */}
            <p className="rb:text-[#5B6167] rb:mb-8">
              {t(item.typeDesc)}
            </p>

            {/* Price */}
            <div className="rb:mb-5">
              {typeof item.priceObj.price !== 'undefined' ? (
                <div className="rb:flex rb:items-baseline rb:h-16">
                  <span className="rb:text-[16px] rb:text-[#5B6167] rb:font-regular rb:mr-1 rb:mb-1">$</span>
                  <span className="rb:text-[40px] rb:font-extrabold">
                    {item.priceObj.price.toLocaleString()}
                  </span>
                  <span className="rb:text-[16px] rb:text-[#5B6167] rb:ml-1 rb:mb-1">
                    {t(item.priceObj.time)}
                  </span>
                </div>
              ) : (
                  <div className="rb:text-[28px] rb:h-16 rb:pb-1 rb:font-extrabold rb:flex rb:items-end">
                    {t(item.priceObj.time)}
                </div>
              )}
            </div>

            {/* CTA Button */}
            <Button
              type={item.type === 'biz' ? 'primary' : 'default'}
              block
              className={btnClassNames[item.type as keyof typeof btnClassNames]}
              onClick={() => handleChoosePlan(item.type)}
            >
              {item.btnType === 'started' ? t('pricing.startedBtn') : item.btnType === 'choosePlan' ? t('pricing.choosePlanBtn') : t('pricing.contactBtn')}
            </Button>
            {Object.keys(item.priceDescObj).map(key => (
              <div key={key} className="rb:mt-4 rb:border-t rb:border-[#DFE4ED]">
                <div className="rb:font-[Gilroy] rb:font-extrabold rb:text-[12px] rb:h-auto rb:leading-4 rb:mt-4">{t(`pricing.${key}`)}</div>
                <div className="rb:font-[PingFangSC] rb:font-normal rb:text-[12px] rb:text-[#5B6167] rb:leading-4 rb:mt-1.5">{t(item.priceDescObj[key as keyof typeof item.priceDescObj])}</div>
              </div>
            ))}

            {/* Features */}
            <div className="rb:space-y-3 rb:border-t rb:border-t-[#DFE4ED] rb:mt-6 rb:pt-6">
              <div className="rb:flex rb:mb-2">
                <img src={checkIcon} className="rb:w-4 rb:h-4 rb:mr-1 rb:mt-0.5" />
                <div className="rb:font-regular rb:text-[12px] rb:text-[#5B6167] rb:leading-5">
                  {t('pricing.memoryCapacity')} { item.memoryCapacity } {t('pricing.entries')}
                </div>
              </div>
              <div className="rb:flex rb:mb-2">
                <img src={checkIcon} className="rb:w-4 rb:h-4 rb:mr-1 rb:mt-0.5" />
                <div className="rb:font-regular rb:text-[12px] rb:text-[#5B6167] rb:leading-5">
                  {t('pricing.intelligentSearchFrequency')}<span>{ item.intelligentSearchFrequency } {t('pricing.timesMonth')}</span>
                </div>
              </div>
              {['supportServices', 'flexibleDeployment', 'reliableGuarantee'].map(type => {
                if ((item as any)[type] || type === 'supportServices') {
                  return (
                    <div key={type} className="rb:flex rb:mb-2">
                      <img src={checkIcon} className="rb:w-4 rb:h-4 rb:mr-1 rb:mt-0.5" />
                      <div className="rb:font-regular rb:text-[12px] rb:text-[#5B6167] rb:leading-5">
                        {t(`pricing.${type}`)}{t(`pricing.${item.type}.${type}`)}
                      </div>
                    </div>
                  )
                }
                return null
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Warning Notice */}
      <div className="rb:mt-6 rb:bg-[rgba(255,93,52,0.06)] rb:border rb:border-[rgba(255,93,52,0.25)] rb:rounded-lg rb:p-4">
        <div className="rb:flex rb:items-start rb:gap-2">
          <img src={alertIcon} className="rb:w-5 rb:h-5 rb:shrink-0" />
          <div>
            <h4 className="rb:text-sm rb:font-medium rb:text-[#FF5D34] rb:mb-1">
              {t('pricing.alertTitle')}
            </h4>
            <p className="rb:mt-2 rb:font-regular rb:text-[12px] rb:leading-4.25 rb:text-[#5B6167]">
              {t('pricing.alertContent')}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PricingView;