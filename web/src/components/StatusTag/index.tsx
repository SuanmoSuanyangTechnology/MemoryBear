import { type FC } from 'react'
import { Tag } from 'antd';
import clsx from 'clsx';

interface StatusTagProps {
  status: 'success' | 'error' | 'warning' | 'default' | 'lightBlue' | 'purple',
  text: string;
}
const Colors = {
  success: 'rb:bg-[#369F21]',
  error: 'rb:bg-[#FF5D34]',
  warning: 'rb:bg-[#FF8A4C]',
  default: 'rb:bg-[#155EEF]',
  lightBlue: 'rb:bg-[#4DA8FF]',
  purple: 'rb:bg-[#9C6FFF]'
}

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