import { type FC } from 'react'
import { Flex } from 'antd'
import clsx from 'clsx';

import Plugin from '@/assets/images/menuNew/plugin.svg'
import Skill from '@/assets/images/menuNew/skill.svg'

const SkillIcon: FC<{ className?: string; iconClassName?: string; plugin?: boolean; }> = ({
  className,
  iconClassName,
  plugin,
}) => {
  return (
    <Flex align="center" justify="center" className={clsx("rb:bg-gray-50 rb:size-6 rb:rounded-lg rb:shrink-0 rb:overflow-hidden!", plugin ? 'rb:bg-[rgba(156,111,255,0.06)]!' : "rb:bg-[rgba(21,94,239,0.06)]!", className)}>
      <img src={plugin ? Plugin : Skill} alt="" className={clsx("rb:size-5", iconClassName)} />
    </Flex>
  )
}
export default SkillIcon
