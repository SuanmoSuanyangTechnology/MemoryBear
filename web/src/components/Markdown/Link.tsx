/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:15:55 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:15:55 
 */
/**
 * Link Component
 * 
 * A secure link component that opens URLs in a new tab.
 * Includes security attributes (noopener, noreferrer) to prevent security vulnerabilities.
 * 
 * @component
 */

import { memo } from 'react'
import type { FC, ReactNode } from 'react'

/** Props interface for Link component */
interface LinkProps {
  href: string;
  children: ReactNode;
}

/** Link component that opens in a new tab with security attributes */
const Link: FC<LinkProps> = (props) => {
  const { children, href } = props;
  return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
}
export default memo(Link)
