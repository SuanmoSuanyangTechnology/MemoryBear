import { memo } from 'react'

import type { FC, ReactNode } from 'react'

interface ParagraphProps {
  node: {
    children: ReactNode;
  };
  children: string[]
}
const Paragraph: FC<ParagraphProps> = (props) => {
  // console.log('Paragraph', props)
  const { children } = props

  return <p>{children}</p>
}
export default memo(Paragraph)
