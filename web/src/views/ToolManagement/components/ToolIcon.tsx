import { type FC } from 'react'
import { Flex } from 'antd'
import clsx from 'clsx';

import Tool from '@/assets/images/tool/tool.svg'

const ToolIcon: FC<{ className?: string; iconClassName?: string; iconUrl?: string | null; }> = ({
  className,
  iconClassName,
  iconUrl,
}) => {
  if (iconUrl) {
    return <img src={iconUrl} alt="" className={clsx("rb:size-6 rb:shrink-0 rb:rounded-lg", className)} />
  }
  return (
    <Flex align="center" justify="center" className={clsx("rb:bg-[#4da8ff] rb:size-6 rb:rounded-lg rb:shrink-0 rb:overflow-hidden!", className)}>
      <img src={Tool} alt="" className={clsx("rb:size-5", iconClassName)} style={{ filter: 'brightness(0) invert(1)' }} />
    </Flex>
  )
}
export default ToolIcon