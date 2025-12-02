import { type FC } from 'react'
import { Tag } from 'antd';
import clsx from 'clsx';

interface StatusTagProps {
  status: 'success' | 'error' | 'warning',
  text: string;
}
const Colors = {
  success: 'rb:bg-[#369F21]',
  error: 'rb:bg-[#FF5D34]',
  warning: 'rb:bg-[#FF8A4C]',
}

const StatusTag: FC<StatusTagProps> = ({
  status,
  text
}) => {
  return (
    <Tag style={{ backgroundColor: '#fff', borderColor: '#DFE4ED' }}>
      <span className='rb:flex rb:items-center rb:text-[#5B6167] rb:pl-[1px] rb:pr-[1px]'>
        <span className={clsx(' rb:w-[5px] rb:h-[5px] rb:rounded-full rb:mr-[4px]', Colors[status])}></span>
        { text }
      </span>
    </Tag>
  )
}

export default StatusTag