/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-14 12:28:23 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-16 17:34:02
 */

import { useState, forwardRef, useImperativeHandle } from 'react';
import { Flex, Tooltip, Divider } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import RbModal from '@/components/RbModal';
import type { Subscription } from './index'
import { billingUnits } from '@/views/Package/constant'
import { useI18n } from '@/store/locale'
import { UnitWrapper } from '@/views/Package'

export interface SubscriptionDetailModalRef {
  handleOpen: (subscription: Subscription | null) => void;
}

const SubscriptionDetailModal = forwardRef<SubscriptionDetailModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const { language } = useI18n()
  const [detail, setDetail] = useState<Subscription | null>(null);

  const handleOpen = (subscription: Subscription | null) => {
    setOpen(true)
    setDetail(subscription);
  };

  const handleCancel = () => {
    setOpen(false);
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  const getKeyWithLanguage = (key: string) => {
    return (language === 'en' ? `${key}_en` : key) as keyof Subscription['package_plan']
  }

  return (
    <RbModal
      title={[t('package.packageDetail'), detail?.package_plan?.[getKeyWithLanguage('name')]].filter(item => item).join(' - ')}
      open={open}
      onCancel={handleCancel}
      footer={null}
    >
      {/* Header */}
      <h3 className="rb:text-[18px] rb:font-bold rb:text-[MiSans-Bold]" style={{ color: detail?.package_plan?.theme_color }}>
        {String(detail?.package_plan?.[getKeyWithLanguage('name')] ?? '')}
      </h3>

      {/* Subtitle */}
      <p className="rb:text-[#5B6167] rb:mb-3">
        {String(detail?.package_plan?.[getKeyWithLanguage('core_value')] ?? '')}
      </p>

      {/* Price */}
      <div className="rb:h-10">
        {detail?.package_plan?.billing_cycle !== 'permanent_free' && <>
          <span className="rb:text-[#5B6167] rb:inline-block rb:leading-5 rb:pt-3.25 rb:pb-1.75 rb:mr-1">¥</span>
          <span className="rb:text-[28px] rb:text-[MiSans-Bold] rb:font-bold rb:leading-10">{detail?.package_plan?.price}</span>
        </>}
        {detail?.package_plan?.billing_cycle && (
          <span className={clsx({
            'rb:text-[28px] rb:text-[MiSans-Bold] rb:font-bold rb:leading-10': detail?.package_plan?.billing_cycle === 'permanent_free',
            'rb:text-[#5B6167] rb:inline-block rb:leading-5 rb:pt-3.25 rb:pb-1.75 rb:ml-1': detail?.package_plan?.billing_cycle !== 'permanent_free'
          })}>
            {detail?.package_plan?.billing_cycle !== 'permanent_free' && ' /'}
            {t(`package.${detail?.package_plan?.billing_cycle}`)}
          </span>
        )}
      </div>

      <Divider className="rb:my-4" />

      {/* Features */}
      <Flex gap={12} vertical className="rb:space-y-3 rb:mb-4 rb:h-[calc(100vh-341px)]! rb:overflow-y-auto">
        {billingUnits.map(({ key, unit, icon }) => {
          const value = detail?.quota[key as keyof Subscription['quota']];
          if (value === undefined || value === null) return null;
          return (
            <UnitWrapper
              key={key}
              titleKey={key}
              value={value}
              unit={unit}
              icon={icon}
              theme_color={detail?.package_plan?.theme_color}
            />
          )
        })}
        {detail?.package_plan?.tech_support && (
          <UnitWrapper
            titleKey="tech_support"
            value={String(detail?.package_plan?.[getKeyWithLanguage('tech_support')] ?? '')}
            icon="technical_support"
            theme_color={detail?.package_plan?.theme_color}
          />
        )}
        {detail?.package_plan?.sla_compliance && (
          <UnitWrapper
            titleKey="sla"
            value={String(detail?.package_plan?.[getKeyWithLanguage('sla_compliance')] ?? '')}
            icon="sla"
            theme_color={detail?.package_plan?.theme_color}
          />
        )}
      </Flex>
    </RbModal>
  );
});

export default SubscriptionDetailModal;
