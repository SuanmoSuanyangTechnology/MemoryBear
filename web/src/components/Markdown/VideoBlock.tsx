/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:16:18 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-02 15:54:55
 */
/**
 * VideoBlock Component
 * 
 * Renders video elements from markdown nodes.
 * Extracts video source URLs and creates HTML video players with controls.
 * 
 * @component
 */

import { memo } from 'react'
import type { FC } from 'react'

/** Props interface for VideoBlock component */
interface VideoBlockProps {
  node: {
    children: { properties: { src: string } }[]
  }
}

/** Video block component that renders video elements from markdown nodes */
const VideoBlock: FC<VideoBlockProps> = (props) => {
  const { children } = props.node;
  /** Extract video source URLs from node children and filter out empty values */
  const srcs = children.map(item => item.properties?.src).filter(item => item)

  return (
    <>
      {srcs.map(src => <video key={src} src={src} controls />)}
    </>
    
  )
}
export default memo(VideoBlock)
