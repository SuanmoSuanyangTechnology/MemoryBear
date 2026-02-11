/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:29:17 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:29:17 
 */
/**
 * Tag Component
 * Displays colored status tags with different color schemes
 */

import { type FC, type ReactNode } from 'react'

/**
 * Tag component props
 */
export interface TagProps {
  /** Tag color scheme */
  color?: 'processing' | 'warning' | 'default' | 'success';
  /** Tag content */
  children: ReactNode;
  /** Additional CSS classes */
  className?: string;
}

/**
 * Color scheme mapping
 */
const colors = {
  processing: 'rb:text-[#155EEF] rb:border-[rgba(21,94,239,0.25)] rb:bg-[rgba(21,94,239,0.06)]',
  warning: 'rb:text-[#FF5D34] rb:border-[rgba(255,93,52,0.08)] rb:bg-[rgba(255,93,52,0.08)]',
  default: 'rb:text-[#5B6167] rb:border-[rgba(91,97,103,0.30)] rb:bg-[rgba(91,97,103,0.08)]',
  success: 'rb:text-[#369F21] rb:border-[rgba(54,159,33,0.30)] rb:bg-[rgba(54,159,33,0.08)]',
}

/**
 * Tag component for displaying status labels
 */
const Tag: FC<TagProps> = ({ color = 'processing', children, className }) => {
  return (
    <span className={`rb:inline-block rb:px-2 rb:py-0.5 rb:rounded-[11px] rb:text-[12px] rb:font-regular rb:leading-4 rb:border ${colors[color]} ${className || ''}`}>
      {children}
    </span>
  )
}
export default Tag
