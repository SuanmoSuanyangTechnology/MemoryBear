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

import { memo, useEffect, useState, type FC } from 'react'

/** Props interface for VideoBlock component */
interface VideoBlockProps {
  node: {
    children: { properties: { src: string } }[]
  }
}

interface VideoSourceProps {
  src: string
}

const VideoSource: FC<VideoSourceProps> = ({ src }) => {
  const [hasError, setHasError] = useState(false)
  const isRelativePath = !/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(src) && !src.startsWith('/')

  useEffect(() => {
    setHasError(false)
  }, [src])

  if (isRelativePath || hasError) {
    return <span className="rb:break-all">{src}</span>
  }

  return <video src={src} controls onError={() => setHasError(true)} />
}

/** Video block component that renders video elements from markdown nodes */
const VideoBlock: FC<VideoBlockProps> = (props) => {
  const { children } = props.node;
  /** Extract video source URLs from node children and filter out empty values */
  const srcs = children.map(item => item.properties?.src).filter(item => item)

  return (
    <>
      {srcs.map(src => <VideoSource key={src} src={src} />)}
    </>
    
  )
}
export default memo(VideoBlock)
