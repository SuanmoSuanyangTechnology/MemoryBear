import { type FC, type ReactNode } from 'react'

export interface TagProps {
  color?: 'processing' | 'warning' | 'default' | 'success';
  children: ReactNode;
  className?: string;
}

const colors = {
  processing: 'rb:text-[#155EEF] rb:border-[rgba(21,94,239,0.25)] rb:bg-[rgba(21,94,239,0.06)]',
  warning: 'rb:text-[#FF5D34] rb:border-[rgba(255,93,52,0.08)] rb:bg-[rgba(255,93,52,0.08)]',
  default: 'rb:text-[#5B6167] rb:border-[rgba(91,97,103,0.30)] rb:bg-[rgba(91,97,103,0.08)]',
  success: 'rb:text-[#369F21] rb:border-[rgba(54,159,33,0.30)] rb:bg-[rgba(54,159,33,0.08)]',
}

const Tag: FC<TagProps> = ({ color = 'processing', children, className }) => {
  return (
    <span className={`rb:inline-block rb:px-[8px] rb:py-[2px] rb:rounded-[11px] rb:text-[12px] rb:font-regular rb:leading-[16px] rb:border-[1px] ${colors[color]} ${className || ''}`}>
      {children}
    </span>
  )
}
export default Tag
