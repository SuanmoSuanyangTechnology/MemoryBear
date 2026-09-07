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

import { memo, useEffect, useState, type FC } from 'react'

/** Props interface for AudioBlock component */
interface AudioBlockProps {
  node: {
    children: { properties: { src: string } }[]
  }
}

interface AudioSourceProps {
  src: string
}

const AudioSource: FC<AudioSourceProps> = ({ src }) => {
  const [hasError, setHasError] = useState(false)
  const isRelativePath = !/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(src) && !src.startsWith('/')

  useEffect(() => {
    setHasError(false)
  }, [src])

  if (isRelativePath || hasError) {
    return <span className="rb:break-all">{src}</span>
  }

  return <audio src={src} controls onError={() => setHasError(true)} />
}

/** Audio block component that renders audio elements from markdown nodes */
const AudioBlock: FC<AudioBlockProps> = (props) => {
  const { children } = props.node;
  /** Extract audio source URLs from node children and filter out empty values */
  const srcs = children.map(item => item.properties?.src).filter(item => item)

  return (
    <>
      {srcs.map(src => <AudioSource key={src} src={src} />)}
    </>
    
  )
}
export default memo(AudioBlock)
