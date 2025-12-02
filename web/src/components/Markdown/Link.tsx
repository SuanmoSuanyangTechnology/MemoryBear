import { memo } from 'react'

import type { FC, ReactNode } from 'react'

interface LinkProps {
  href: string;
  children: ReactNode;
}
const Link: FC<LinkProps> = (props) => {
  // console.log('Link', props)
  const { children, href } = props;
  return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
}
export default memo(Link)
