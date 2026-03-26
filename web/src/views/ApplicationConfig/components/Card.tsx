/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:31 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-04 10:25:35
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
  variant?: 'borderL';
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
  variant
}) => {
  return (
    <RbCard
      title={title}
      subTitle={subTitle}
      extra={extra}
      variant={variant}
      headerType="borderless"
      headerClassName="rb:h-11.5! rb:py-3! rb:leading-5.5!"
      titleClassName="rb:font-[MiSans-Bold] rb:font-bold"
    >
      {children}
    </RbCard>
  )
}

export default Card