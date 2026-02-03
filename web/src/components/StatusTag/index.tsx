/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:29:42 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:29:42 
 */
/**
 * StatusTag Component
 * 
 * A tag component that displays status with a colored indicator dot.
 * Supports multiple status types with predefined color schemes.
 * 
 * @component
 */

import { type FC } from 'react'
import { Tag } from 'antd';
import clsx from 'clsx';

/** Props interface for StatusTag component */
interface StatusTagProps {
  /** Status type determining the indicator color */
  status: 'success' | 'error' | 'warning' | 'default' | 'lightBlue' | 'purple',
  /** Text to display in the tag */
  text: string;
}

/** Color mappings for different status types */
const Colors = {
  success: 'rb:bg-[#369F21]',
  error: 'rb:bg-[#FF5D34]',
  warning: 'rb:bg-[#FF8A4C]',
  default: 'rb:bg-[#155EEF]',
  lightBlue: 'rb:bg-[#4DA8FF]',
  purple: 'rb:bg-[#9C6FFF]'
}

/** Status tag component with colored indicator dot */
const StatusTag: FC<StatusTagProps> = ({
  status,
  text
}) => {
  console.log('status', status)
  return (
    <Tag style={{ backgroundColor: '#fff', borderColor: '#DFE4ED' }}>
      <span className='rb:flex rb:items-center rb:text-[#5B6167] rb:pl-px rb:pr-px'>
        <span className={clsx(' rb:w-1.25 rb:h-1.25 rb:rounded-full rb:mr-1', Colors[status])}></span>
        { text }
      </span>
    </Tag>
  )
}

export default StatusTag