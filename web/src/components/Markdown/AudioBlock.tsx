import { memo } from 'react'

import type { FC } from 'react'

interface AudioBlockProps {
  node: {
    children: { properties: { src: string } }[]
  }
}
const AudioBlock: FC<AudioBlockProps> = (props) => {
  // console.log('AudioBlock', props)
  const { children } = props.node;
  const srcs = children.map(item => item.properties?.src).filter(item => item)

  return (
    <>
      {srcs.map(src => <audio key={src} src={src} controls />)}
    </>
    
  )
}
export default memo(AudioBlock)
