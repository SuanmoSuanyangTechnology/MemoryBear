import { type FC, useEffect, useState } from 'react';
import { Flex } from 'antd';
import clsx from 'clsx';

import Plugin from '@/assets/images/menuNew/plugin.svg'
import Skill from '@/assets/images/menuNew/skill.svg'

const SkillIcon: FC<{ className?: string; iconClassName?: string; plugin?: boolean; iconUrl?: string | null; }> = ({
  className,
  iconClassName,
  plugin,
  iconUrl,
}) => {
  const [iconError, setIconError] = useState(false);

  useEffect(() => {
    setIconError(false);
  }, [iconUrl]);

  if (iconUrl && !iconError) {
    return <img src={iconUrl} alt="" className={clsx("rb:size-6 rb:shrink-0 rb:rounded-lg", className)} onError={() => setIconError(true)} />;
  }

  return (
    <Flex align="center" justify="center" className={clsx("rb:bg-gray-50 rb:size-6 rb:rounded-lg rb:shrink-0 rb:overflow-hidden!", plugin ? 'rb:bg-[rgba(156,111,255,0.06)]!' : "rb:bg-[rgba(21,94,239,0.06)]!", className)}>
      <img src={plugin ? Plugin : Skill} alt="" className={clsx("rb:size-5", iconClassName)} />
    </Flex>
  );
};
export default SkillIcon;
