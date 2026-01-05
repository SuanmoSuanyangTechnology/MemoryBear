import { type FC, type ReactNode } from 'react'

interface RbAlertProps {
  color?: 'blue' | 'green' | 'orange' | 'purple',
  children: ReactNode | string;
  icon?: ReactNode;
  className?: string;
}

const colors = {
  blue: 'rb:text-[rgba(21,94,239,1)] rb:bg-[rgba(21,94,239,0.08)] rb:border-[rgba(21,94,239,0.30)]',
  green: 'rb:text-[rgba(54,159,33,1)] rb:bg-[rgba(54,159,33,0.08)] rb:border-[rgba(54,159,33,0.30)]',
  orange: 'rb:text-[rgba(255,93,52,1)] rb:bg-[rgba(255,138,76,0.06)] rb:border-[rgba(255,138,76,0.30)]',
  purple: 'rb:text-[rgba(156,111,255,1)] rb:bg-[rgba(156,111,255,0.08)] rb:border-[rgba(156,111,255,0.30)]',
}

const RbAlert: FC<RbAlertProps> = ({ color = 'blue', icon, className, children }) => {
  return (
    <div className={`${colors[color]} ${className} rb:p-[6px_9px] rb:flex rb:items-center rb:text-[12px] rb:font-regular rb:leading-4 rb:border rb:rounded-md`}>
      {icon && <span className="rb:text-[16px] rb:mr-2.25">{icon}</span>}
      {children}
    </div>
  )
}
export default RbAlert
