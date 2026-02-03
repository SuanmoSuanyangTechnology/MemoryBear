/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:29:57 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:29:57 
 */
/**
 * Tag Component
 * 
 * A custom tag component with predefined color themes.
 * Supports different status colors: processing, error, success, warning, and default.
 * 
 * @component
 */

import { type FC, type ReactNode } from 'react'

/** Props interface for Tag component */
export interface TagProps {
  /** Color theme for the tag */
  color?: 'processing' | 'error' | 'success' | 'warning' | 'default',
  /** Tag content */
  children: ReactNode;
  /** Additional CSS classes */
  className?: string;
}

/** Color theme mappings with text, border, and background colors */
const colors = {
  processing: 'rb:text-[#155EEF] rb:border-[rgba(21,94,239,0.25)] rb:bg-[rgba(21,94,239,0.06)]',
  error: 'rb:text-[#FF5D34] rb:border-[rgba(255,138,76,0.20)] rb:bg-[rgba(255,138,76,0.08)]',
  success: 'rb:text-[#369F21] rb:border-[rgba(54,159,33,0.25)] rb:bg-[rgba(54,159,33,0.06)]',
  warning: 'rb:text-[#FF5D34] rb:border-[rgba(255,93,52,0.30)] rb:bg-[rgba(255,93,52,0.08)]',
  default: 'rb:text-[#5B6167] rb:border-[rgba(91,97,103,0.30)] rb:bg-[rgba(91,97,103,0.08)]',
}

/** Custom tag component with color themes */
const Tag: FC<TagProps> = ({ color = 'processing', children, className }) => {
  return (
    <span className={`rb:inline-block rb:px-1 rb:py-0.5 rb:rounded-sm rb:text-[12px] rb:font-regular! rb:leading-4 rb:border ${colors[color]} ${className || ''}`}>
      {children}
    </span>
  )
}
export default Tag
