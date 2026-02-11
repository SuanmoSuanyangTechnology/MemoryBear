/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:30:51 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:30:51 
 */
/**
 * Card Component
 * Collapsible card wrapper for configuration sections
 */

import { type FC, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx';

import RbCard from '@/components/RbCard/Card'
import down from '@/assets/images/userMemory/down.svg'

/**
 * Component props
 */
interface CardProps {
  type?: string;
  title: string | ReactNode;
  subTitle?: string | ReactNode;
  children: ReactNode;
  expanded?: boolean;
  handleExpand?: (type: string) => void;
  className?: string;
  headerClassName?: string;
  bodyClassName?: string;
}

const Card: FC<CardProps> = ({
  type,
  title,
  subTitle,
  children,
  expanded,
  handleExpand,
  className,
  headerClassName,
  bodyClassName,
}) => {
  const { t } = useTranslation()
  return (
    <RbCard
      title={title}
      subTitle={subTitle}
      headerType="borderless"
      extra={type && handleExpand && (
        <div 
          className="rb:flex rb:items-center rb:text-[14px] rb:text-[#5B6167] rb:cursor-pointer rb:font-regular rb:leading-5" 
          onClick={() => handleExpand(type)}
        >
          {expanded ? t('common.foldUp') : t('common.expanded')}
          <img src={down} className={clsx("rb:w-4 rb:h-4 rb:ml-1", {
            'rb:rotate-180': !expanded,
          })} />
        </div>
      )}
      className={className}
      headerClassName={headerClassName}
      bodyClassName={bodyClassName}
    >
      {(expanded || !(type && handleExpand)) && children}
    </RbCard>
  )
}

export default Card