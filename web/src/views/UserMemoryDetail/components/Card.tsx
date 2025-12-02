import { type FC, type ReactNode } from 'react'
import clsx from 'clsx'

interface CardProps {
  title?: string;
  children: ReactNode;
  theme?: 'default' | 'custom';
  className?: string;
}

const Card: FC<CardProps> = ({ title, children, theme = 'default', className }) => {
  return (
    <div className={clsx('rb:h-full rb:border rb:rounded-[12px] rb:p-[16px] rb:border-[#DFE4ED]', {
      'rb:bg-[#FBFDFF]': theme === 'default',
      'rb:bg-[linear-gradient(180deg,_#F1F9FE_0%,_#FBFCFF_100%)]': theme === 'custom',
    }, className)}>
      {title && <div className="rb:text-[18px] rb:font-semibold rb:leading-[25px] rb:pb-[16px]">{title}</div>}
      {children}
    </div>
  )
}

export default Card