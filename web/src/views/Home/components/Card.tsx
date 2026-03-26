/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:27:28 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-26 11:16:09
 */
/**
 * Card Component
 * Reusable card wrapper for dashboard sections
 */

import { type FC, type ReactNode } from 'react'

import RbCard from '@/components/RbCard/Card'

/**
 * Component props
 */
interface CardProps {
  children: ReactNode;
  title: string;
  headerOperate?: ReactNode;
  className?: string;
  bodyClassName?: string;
}

const Card: FC<CardProps> = ({ children, title, headerOperate, className, bodyClassName }) => {
  return (
    <RbCard 
      headerType="borderless"
      title={title}
      extra={headerOperate}
      variant="borderless"
      className={`rb:h-full! rb:bg-[#FFFFFF]! ${className}`}
      bodyClassName={bodyClassName}
      headerClassName="rb:min-h-[58px]!"
    >
      {children}
    </RbCard>
  )
}
export default Card;