import { type FC, type ReactNode } from 'react'

export interface TagProps {
  color?: 'processing' | 'error' | 'success' | 'warning' | 'default',
  children: ReactNode;
  className?: string;
}

const colors = {
  processing: 'rb:text-[#155EEF] rb:border-[rgba(21,94,239,0.25)] rb:bg-[rgba(21,94,239,0.06)]',
  error: 'rb:text-[#FF5D34] rb:border-[rgba(255,138,76,0.20)] rb:bg-[rgba(255,138,76,0.08)]',
  success: 'rb:text-[#369F21] rb:border-[rgba(54,159,33,0.25)] rb:bg-[rgba(54,159,33,0.06)]',
  warning: 'rb:text-[#FF5D34] rb:border-[rgba(255,93,52,0.30)] rb:bg-[rgba(255,93,52,0.08)]',
  default: 'rb:text-[#5B6167] rb:border-[rgba(91,97,103,0.30)] rb:bg-[rgba(91,97,103,0.08)]',
}

const Tag: FC<TagProps> = ({ color = 'processing', children, className }) => {
  return (
    <span className={`rb:inline-block rb:px-1 rb:py-0.5 rb:rounded-sm rb:text-[12px] rb:font-regular! rb:leading-4 rb:border ${colors[color]} ${className || ''}`}>
      {children}
    </span>
  )
}
export default Tag
