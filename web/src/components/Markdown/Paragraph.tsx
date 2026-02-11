/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:16:06 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:16:06 
 */
/**
 * Paragraph Component
 * 
 * A simple paragraph component for rendering markdown paragraphs.
 * 
 * @component
 */

import { memo } from 'react'
import type { FC, ReactNode } from 'react'

/** Props interface for Paragraph component */
interface ParagraphProps {
  node: {
    children: ReactNode;
  };
  children: string[]
}

/** Paragraph component for rendering markdown paragraphs */
const Paragraph: FC<ParagraphProps> = (props) => {
  const { children } = props

  return <p>{children}</p>
}
export default memo(Paragraph)
