/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:19:59 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:19:59 
 */
/**
 * RbAlert Component
 * 
 * A custom alert component with predefined color themes and optional icon support.
 * Provides consistent styling for informational messages across the application.
 * 
 * @component
 */

import { type FC, type ReactNode } from 'react'

/** Props interface for RbAlert component */
interface RbAlertProps {
  /** Color theme for the alert */
  color?: 'blue' | 'green' | 'orange' | 'purple',
  /** Alert content */
  children: ReactNode | string;
  /** Optional icon to display before content */
  icon?: ReactNode;
  /** Additional CSS classes */
  className?: string;
}

/** Color theme mappings with text, background, and border colors */
const colors = {
  blue: 'rb:text-[rgba(21,94,239,1)] rb:bg-[rgba(21,94,239,0.08)] rb:border-[rgba(21,94,239,0.30)]',
  green: 'rb:text-[rgba(54,159,33,1)] rb:bg-[rgba(54,159,33,0.08)] rb:border-[rgba(54,159,33,0.30)]',
  orange: 'rb:text-[rgba(255,93,52,1)] rb:bg-[rgba(255,138,76,0.06)] rb:border-[rgba(255,138,76,0.30)]',
  purple: 'rb:text-[rgba(156,111,255,1)] rb:bg-[rgba(156,111,255,0.08)] rb:border-[rgba(156,111,255,0.30)]',
}

/** Custom alert component with color themes and optional icon */
const RbAlert: FC<RbAlertProps> = ({ color = 'blue', icon, className, children }) => {
  return (
    <div className={`${colors[color]} ${className} rb:p-[6px_9px] rb:flex rb:items-center rb:text-[12px] rb:font-regular rb:leading-4 rb:border rb:rounded-md`}>
      {icon && <span className="rb:text-[16px] rb:mr-2.25">{icon}</span>}
      {children}
    </div>
  )
}
export default RbAlert
