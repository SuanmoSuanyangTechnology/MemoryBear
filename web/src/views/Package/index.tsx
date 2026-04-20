/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-14 11:34:42 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-16 17:23:49
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

import { useRef, useMemo, useState, useEffect, type FC, type ComponentType, type SVGProps } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Tooltip, Divider, Button, type SegmentedProps } from 'antd';
import clsx from 'clsx';
import Icon from '@ant-design/icons'

import type { Package } from './types'
import { getPackageList } from '@/api/package';
import PageTabs from '@/components/PageTabs'
import { billingUnits } from './constant'
import RbCard from '@/components/RbCard/Card'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import { useI18n } from '@/store/locale'

import SpaceSvg from '@/assets/images/package/space.svg?react'
import SkillSvg from '@/assets/images/package/skill.svg?react'
import AppSvg from '@/assets/images/package/app.svg?react'
import KnowledgeSvg from '@/assets/images/package/knowledge.svg?react'
import MemoryConfigSvg from '@/assets/images/package/memory_config.svg?react'
import EndUserSvg from '@/assets/images/package/end_user.svg?react'
import OntologySvg from '@/assets/images/package/ontology.svg?react'
import ModelSvg from '@/assets/images/package/model.svg?react'
import TechnicalSupportSvg from '@/assets/images/package/technical_support.svg?react'
import ApiOpsSvg from '@/assets/images/package/api_ops.svg?react'
import arrowSvg from '@/assets/images/package/arrow.svg?react'
import slaSvg from '@/assets/images/package/sla.svg?react';

const iconMap: Record<string, ComponentType<SVGProps<SVGSVGElement>>> = {
  space: SpaceSvg,
  skill: SkillSvg,
  app: AppSvg,
  knowledge: KnowledgeSvg,
  memory_config: MemoryConfigSvg,
  end_user: EndUserSvg,
  ontology: OntologySvg,
  model: ModelSvg,
  technical_support: TechnicalSupportSvg,
  api_ops: ApiOpsSvg,
  sla: slaSvg,
}
const btnClassNames = {
  permanent_free: 'rb:h-10! rb:rounded-[8px]!',
  default: 'rb:h-10! rb:rounded-[8px]! rb:bg-[#212332]! rb:text-white! rb:border-0! rb:hover:border-0! rb:hover:opacity-[0.8]',
}

export const UnitWrapper = ({ titleKey, value, icon, unit, theme_color = '#171719' }: { titleKey: string; value: number | string; icon: string; unit?: string; theme_color?: string; }) => {
  const { t } = useTranslation();

  const renderFeatureIcon = (iconKey: string, color: string) => {
    const SvgComponent = iconMap[iconKey]
    if (!SvgComponent) return null
    return <Icon component={SvgComponent} style={{ color, fontSize: 16 }} />
  }
  return (
    <Flex key={titleKey} align="start" gap={16}>
      <Flex
        align="center"
        justify="center"
        className="rb:mt-1! rb:shrink-0 rb:rounded-lg rb:size-7"
        style={{ backgroundColor: `${theme_color}14` }}
      >{renderFeatureIcon(icon, theme_color)}</Flex>
      <div className="rb:text-[13px] rb:leading-4.5">
        <div className="rb:text-[#5F6266]">{t(`package.${titleKey}`)}</div>
        <div>{value} {unit ? t(`package.${unit}`) : ''}</div>
      </div>
    </Flex>
  )
}

const Package: FC = () => {
  const { t } = useTranslation();
  const { language } = useI18n()
  const [data, setData] = useState<Package[]>([])
  const scrollRef = useRef<HTMLDivElement>(null)
  const CARD_WIDTH = 360
  const GAP = 12
  const [visibleCount, setVisibleCount] = useState(3)

  useEffect(() => {
    const calcVisible = () => {
      if (!scrollRef.current) return
      const w = scrollRef.current.offsetWidth
      setVisibleCount(Math.floor((w + GAP) / (CARD_WIDTH + GAP)))
    }
    calcVisible()
    window.addEventListener('resize', calcVisible)
    return () => window.removeEventListener('resize', calcVisible)
  }, [])

  const [activeTab, setActiveTab] = useState('saas_personal');

  const categories = useMemo(() => {
    const cats = [...new Set(data.map(p => p.category))]
    return cats
  }, [data])

  const formatTabItems = useMemo(() => {
    return (['saas_personal', 'commercial_deployment'] as const)
      .filter(v => categories.includes(v))
      .map(value => ({ value, label: t(`package.${value}`) }))
  }, [t, categories])

  const showTabs = categories.length > 1

  const handleChangeTab = (value: SegmentedProps['value']) => {
    setActiveTab(value as string);
  }

  const getList = () => {
    getPackageList({ status: true }).then(res => {
      setData(res as Package[] || [])
    })
  }

  useEffect(() => {
    getList()
  }, [])

  useEffect(() => {
    if (categories.length > 0 && !categories.includes(activeTab as Package['category'])) {
      setActiveTab(categories[0])
    }
  }, [categories])

  const getKeyWithLanguage = (key: string) => {
    return (language === 'en' ? `${key}_en` : key) as keyof Package
  }

  const filteredData = useMemo(() => data.filter(p => p.category === activeTab), [data, activeTab])

  const [currentPage, setCurrentPage] = useState(0)
  const totalPages = visibleCount > 0 ? Math.ceil(filteredData.length / visibleCount) : 1
  const showArrows = totalPages > 1
  const pageData = filteredData.slice(currentPage * visibleCount, (currentPage + 1) * visibleCount)

  useEffect(() => {
    setCurrentPage(0)
  }, [activeTab, visibleCount, filteredData])

  const handleChoosePlan = () => {
    window.open(`https://docs.redbearai.com/s/${language || 'en'}-memorybear`, '_blank')
  };

  return (
    <>
      {showTabs && (
        <Flex justify="space-between" className="rb:mb-4!">
          <PageTabs
            value={activeTab}
            options={formatTabItems}
            onChange={handleChangeTab}
          />
        </Flex>
      )}
      <BodyWrapper empty={filteredData.length < 1}>
        <div ref={scrollRef} className="rb:relative rb:mx-9">
          {showArrows && (
            <Flex
              align="center"
              justify="center"
              className={clsx("rb:absolute rb:-left-6 rb:top-1/2 rb:-translate-y-1/2 rb:-translate-x-3 rb:z-10 rb:h-25 rb:rounded-lg rb:w-6 rb:bg-[rgba(255,255,255,0.6)] rb:border rb:border-[rgba(255,255,255,0.6)]", {
                'rb:hover:border-[#171719] rb:cursor-pointer': currentPage > 0,
                'rb:cursor-not-allowed': currentPage === 0
              })}
              onClick={() => {
                if (currentPage === 0) return
                setCurrentPage(p => p - 1)
              }}
            >
              <Icon component={arrowSvg} style={{ color: currentPage === 0 ? '#E1E2E7' : '#171719', fontSize: 24 }} />
            </Flex>
          )}

          <Flex gap={GAP} justify="center">
            {pageData.map((pkg) => (
              <div key={pkg.id} style={{ width: CARD_WIDTH, flexShrink: 0 }}>
                <RbCard
                  className="rb:h-full! rb:hover:shadow-[0px_4px_10px_0px_rgba(0,0,0,0.12)]!"
                  bodyClassName="rb:p-0! rb:pb-4! rb:h-full!"
                  headerClassName="rb:min-h-0!"
                >
                  <div className="rb:px-5 rb:pt-4">
                    <div className="rb:h-25!">
                      {/* Header */}
                      <Flex justify="space-between" align="start" className="rb:mb-1!">
                        <Tooltip title={String(pkg[getKeyWithLanguage('name')] ?? '')}>
                          <h3 className="rb:text-[18px] rb:font-bold rb:text-[MiSans-Bold] rb:w-54.5 rb:line-clamp-2" style={{ color: pkg.theme_color }}>
                            {String(pkg[getKeyWithLanguage('name')] ?? '')}
                          </h3>
                        </Tooltip>
                      </Flex>

                      {/* Subtitle */}
                      <Tooltip title={String(pkg[getKeyWithLanguage('core_value')] ?? '')}>
                        <p className="rb:text-[#5B6167] rb:mb-4 rb:line-clamp-1">
                          {String(pkg[getKeyWithLanguage('core_value')] ?? '')}
                        </p>
                      </Tooltip>
                    </div>

                    {/* Price */}
                    <div className="rb:h-10 rb:mb-4">
                      {pkg.billing_cycle !== 'permanent_free' && <>
                        <span className="rb:text-[#5B6167] rb:inline-block rb:leading-5 rb:pt-3.25 rb:pb-1.75 rb:mr-1">¥</span>
                        <span className="rb:text-[28px] rb:text-[MiSans-Bold] rb:font-bold rb:leading-10">{pkg.price}</span>
                      </>}
                      {pkg.billing_cycle && (
                        <span className={clsx({
                          'rb:text-[28px] rb:text-[MiSans-Bold] rb:font-bold rb:leading-10': pkg.billing_cycle === 'permanent_free',
                          'rb:text-[#5B6167] rb:inline-block rb:leading-5 rb:pt-3.25 rb:pb-1.75 rb:ml-1': pkg.billing_cycle !== 'permanent_free'
                        })}>
                          {pkg.billing_cycle !== 'permanent_free' && ' /'}
                          {t(`package.${pkg.billing_cycle}`)}
                        </span>
                      )}
                    </div>

                    <Button
                      type={pkg.billing_cycle !== 'permanent_free' ? 'primary' : 'default'}
                      block
                      className={btnClassNames[pkg.billing_cycle === 'permanent_free' ? 'permanent_free' : 'default']}
                      onClick={handleChoosePlan}
                    >
                      {t('pricing.contactBtn')}
                    </Button>

                    <Divider className="rb:my-4" />

                    {/* Features */}
                    <Flex gap={12} vertical
                      className={clsx("rb:space-y-3 rb:mb-4 rb:overflow-y-auto", {
                        'rb:h-[calc(100vh-401px)]!': showTabs,
                        'rb:h-[calc(100vh-346px)]!': !showTabs
                      })}
                    >
                      {billingUnits.map(({ key, unit, icon }) => {
                        const value = pkg?.quotas?.[key as keyof Package['quotas']];
                        if (value === undefined || value === null) return null;
                        return (
                          <UnitWrapper
                            key={key}
                            titleKey={key}
                            value={value}
                            unit={unit}
                            icon={icon}
                            theme_color={pkg.theme_color}
                          />
                        )
                      })}
                      {pkg.tech_support && (
                        <UnitWrapper
                            titleKey="tech_support"
                            value={String(pkg[getKeyWithLanguage('tech_support')] ?? '')}
                            icon="technical_support"
                            theme_color={pkg.theme_color}
                          />
                      )}
                      {pkg.sla_compliance && (
                        <UnitWrapper
                          titleKey="sla"
                          value={String(pkg[getKeyWithLanguage('sla_compliance')] ?? '')}
                          icon="sla"
                          theme_color={pkg.theme_color}
                        />
                      )}
                    </Flex>
                  </div>
                </RbCard>
              </div>
            ))}
          </Flex>

          {showArrows && (
            <Flex
              align="center"
              justify="center"
              className={clsx("rb:absolute rb:-right-12 rb:top-1/2 rb:-translate-y-1/2 rb:-translate-x-3 rb:z-10 rb:h-25 rb:rounded-lg rb:w-6 rb:bg-[rgba(255,255,255,0.6)] rb:border rb:border-[rgba(255,255,255,0.6)]", {
                'rb:hover:border-[#171719] rb:cursor-pointer': currentPage < totalPages - 1,
                'rb:cursor-not-allowed': currentPage >= totalPages - 1
              })}
              onClick={() => {
                if (currentPage >= totalPages - 1) return
                setCurrentPage(p => p + 1)
              }}
            >
              <Icon component={arrowSvg} className="rb:rotate-180" style={{ color: currentPage >= totalPages - 1 ? '#E1E2E7' : '#171719', fontSize: 24 }} />
            </Flex>
          )}
        </div>
      </BodyWrapper>
    </>
  );
};

export default Package;
