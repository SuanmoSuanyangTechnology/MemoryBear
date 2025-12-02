import { type FC, type ReactNode } from 'react'
import RbCard from '@/components/RbCard/Card'

interface CardProps {
  title?: string | ReactNode;
  children: ReactNode;
  extra?: ReactNode;
}

const Card: FC<CardProps> = ({
  title,
  children,
  extra,
}) => {
  return (
    <RbCard
      title={title}
      extra={extra}
      headerType="borderL"
      headerClassName="rb:before:bg-[#155EEF]! rb:before:h-[19px]"
    >
      {children}
    </RbCard>
  )
}

export default Card