/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-25 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-14 14:59:11
 */
/**
 * Package Component
 * 
 * Package management page with:
 * - Tabs for SaaS Personal and Commercial Deployment
 * - Package cards showing features and pricing
 * - Edit and delete actions
 * 
 * @component
 */

import { useMemo, useState, useEffect, type FC } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Flex, Row, Col, type SegmentedProps } from 'antd';
import clsx from 'clsx';

import type { Package } from './types'
import { getPackageList } from '@/api/package';
import PageTabs from '@/components/PageTabs'
import { billingUnits } from './constant'
import RbCard from '@/components/RbCard/Card'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import { useI18n } from '@/store/locale'
import RbButton from '@/components/RbButton'

const Package: FC = () => {
  const { t } = useTranslation();
  const { language } = useI18n()
    const navigate = useNavigate();
  const [data, setData] = useState<Package[]>([])

  const [activeTab, setActiveTab] = useState('saas_personal');
  const formatTabItems = useMemo(() => {
    return ['saas_personal', 'commercial_deployment'].map(value => ({
      value,
      label: t(`package.${value}`),
    }))
  }, [t])
  /** Handle tab change */
  const handleChangeTab = (value: SegmentedProps['value']) => {
    setActiveTab(value as string);
  }
  const getList = () => {
    getPackageList({ category: activeTab as Package['category'], status: true })
      .then(res => {
        setData(res as Package[] || [])
      })
  }

  useEffect(() => {
    getList()
  }, [activeTab])

  const getKeyWithLanguage = (key: string) => {
    return (language === 'en' ? `${key}_en` : key) as keyof Package
  }
  /** Navigate to order history */
  const goToHistory = () => {
    navigate('/orders');
  }
  return (
    <>
      <Flex justify="space-between" className="rb:mb-4!">
        <PageTabs
          value={activeTab}
          options={formatTabItems}
          onChange={handleChangeTab}
        />
        <RbButton className="rb:text-[#212332] rb:font-medium!" onClick={goToHistory}>
          <div
            className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/order/order.svg')]"
          ></div>
          {t('pricing.orderHistory')}
        </RbButton>
      </Flex>
      <BodyWrapper empty={data.length < 1}>
        <Row gutter={[12, 12]} className="rb:max-h-[calc(100%-48px)]! rb:overflow-y-auto">
          {data.map((pkg) => (
            <Col key={pkg.id} span={8}>
              <RbCard
                className="rb:h-full! rb:shadow-md hover:rb:shadow-lg rb:transition-shadow"
                bodyClassName="rb:p-6! rb:h-full!"
                headerClassName="rb:min-h-0!"
              >
                <Flex vertical justify="space-between" className="rb:h-full!">
                  <div>
                    {/* Header */}
                    <div className="rb:text-center rb:mb-6">
                      <h3 className="rb:text-xl rb:font-bold rb:mb-2 rb:min-h-7" style={{ color: pkg.theme_color }}>
                        {String(pkg[getKeyWithLanguage('name')] ?? '')}
                      </h3>
                      <p className="rb:text-sm rb:text-gray-500 rb:mb-4 rb:min-h-5">{String(pkg[getKeyWithLanguage('core_value')] ?? '')}</p>
                      <div className="rb:text-4xl rb:font-bold rb:mb-2">
                        {pkg.billing_cycle !== 'permanent_free' && <>¥{pkg.price}</>}
                        {pkg.billing_cycle && <span className={clsx("", {
                          'rb:text-base rb:font-normal rb:text-gray-500': pkg.billing_cycle !== 'permanent_free'
                        })}>{pkg.billing_cycle !== 'permanent_free' && '/'}{t(`package.${pkg.billing_cycle}`)}</span>}
                      </div>
                    </div>

                    {/* Features */}
                    <div className="rb:space-y-3">
                      {billingUnits.map(({ key, unit }) => {
                        if (typeof pkg.quotas[key as keyof Package['quotas']] === 'number') {
                          return (
                            <div key={key} className="rb:flex rb:items-center rb:justify-between rb:text-sm">
                              <span className="rb:text-gray-500">{t(`package.${key}`)}</span>
                              <span>{pkg.quotas[key as keyof Package['quotas']]}{t(`package.${unit}`)}</span>
                            </div>
                          )
                        }
                      })}
                      {pkg.api_ops_rate_limit &&
                        <div className="rb:flex rb:items-center rb:justify-between rb:text-sm">
                          <span className="rb:text-gray-500">{t(`package.api_ops_rate_limit`)}</span>
                          <span>{pkg.api_ops_rate_limit}{t('package.ops')}</span>
                        </div>
                      }
                      {pkg.tech_support &&
                        <div className="rb:flex rb:items-center rb:justify-between rb:text-sm">
                          <span className="rb:text-gray-500">{t(`package.tech_support`)}</span>
                          <span>{String(pkg[getKeyWithLanguage('tech_support')] ?? '')}</span>
                        </div>
                      }
                    </div>
                  </div>
                </Flex>

              </RbCard>
            </Col>
          ))}
        </Row>
      </BodyWrapper>
    </>
  );
};

export default Package;
