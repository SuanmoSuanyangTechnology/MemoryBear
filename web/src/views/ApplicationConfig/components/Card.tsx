/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:31 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:27:31 
 */
import { type FC, type ReactNode } from 'react'

import RbCard from '@/components/RbCard/Card'

/**
 * Props for Card component
 */
interface CardProps {
  /** Card title */
  title?: string | ReactNode;
  /** Card subtitle */
  subTitle?: string | ReactNode;
  /** Card content */
  children: ReactNode;
  /** Extra content in header */
  extra?: ReactNode;
}

/**
 * Card component wrapper
 * Styled card with left border accent for application configuration sections
 */
const Card: FC<CardProps> = ({
  title,
  subTitle,
  children,
  extra,
}) => {
  return (
    <RbCard
      title={title}
      subTitle={subTitle}
      extra={extra}
      headerType="borderL"
      headerClassName="rb:before:bg-[#155EEF]! rb:before:h-[19px]"
    >
      {children}
    </RbCard>
  )
}

export default Card