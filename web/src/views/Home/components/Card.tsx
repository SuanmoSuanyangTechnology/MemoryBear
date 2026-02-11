/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:27:28 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:27:28 
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
}

const Card: FC<CardProps> = ({ children, title, headerOperate, className }) => {
  return (
    <RbCard 
      headerType="borderless"
      title={title}
      extra={headerOperate}
      className={`rb:h-full! ${className}`}
    >
      {children}
    </RbCard>
  )
}
export default Card;