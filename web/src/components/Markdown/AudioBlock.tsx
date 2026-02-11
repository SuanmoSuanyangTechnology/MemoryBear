/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:14:59 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:14:59 
 */
/**
 * AudioBlock Component
 * 
 * Renders audio elements from markdown nodes.
 * Extracts audio source URLs and creates HTML audio players with controls.
 * 
 * @component
 */

import { memo, type FC } from 'react'

/** Props interface for AudioBlock component */
interface AudioBlockProps {
  node: {
    children: { properties: { src: string } }[]
  }
}

/** Audio block component that renders audio elements from markdown nodes */
const AudioBlock: FC<AudioBlockProps> = (props) => {
  const { children } = props.node;
  /** Extract audio source URLs from node children and filter out empty values */
  const srcs = children.map(item => item.properties?.src).filter(item => item)

  return (
    <>
      {srcs.map(src => <audio key={src} src={src} controls />)}
    </>
    
  )
}
export default memo(AudioBlock)
