import { memo } from 'react'

import type { FC } from 'react'

interface VideoBlockProps {
  node: {
    children: { properties: { src: string } }[]
  }
}
const VideoBlock: FC<VideoBlockProps> = (props) => {
  // console.log('VideoBlock', props)
  const { children } = props.node;
  const srcs = children.map(item => item.properties?.src).filter(item => item)

  return (
    <>
      {srcs.map(src => <video key={src} src={src} controls />)}
    </>
    
  )
}
export default memo(VideoBlock)
