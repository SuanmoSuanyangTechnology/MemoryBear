import { type FC, type ReactNode } from 'react'
import RbCard from '@/components/RbCard/Card'

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