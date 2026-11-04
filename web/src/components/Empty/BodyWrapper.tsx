/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:02:47 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-11-04 12:00:19
 */
/**
 * BodyWrapper Component
 * 
 * A wrapper component that conditionally renders loading, empty, or content states.
 * Simplifies state management for data-driven components.
 * 
 * @component
 */

import type { FC, ReactNode } from 'react'

import PageEmpty from './PageEmpty'
import { Skeleton } from 'antd'

interface BodyWrapperProps {
  /** Content to render when not loading or empty */
  children: ReactNode
  /** Whether to show loading state */
  loading?: boolean
  /** Whether the content is empty */
  empty: boolean;
  className?: string;
}
const BodyWrapper: FC<BodyWrapperProps> = ({ children, loading = false, empty, className = 'rb:max-h-[calc(100%-48px)]!' }) => {
  // Show skeleton while data is being fetched
  if (loading) {
    return <Skeleton active className={className} />
  }
  // Show empty state when no data is available
  if (!loading && empty) {
    return <PageEmpty className={className} />
  }
  // Render actual content when data is loaded and available
  return children
}
export default BodyWrapper
