/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:02:47 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-02 15:47:24
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
import PageLoading from './PageLoading'

interface BodyWrapperProps {
  /** Content to render when not loading or empty */
  children: ReactNode
  /** Whether to show loading state */
  loading?: boolean
  /** Whether the content is empty */
  empty: boolean
}
const BodyWrapper: FC<BodyWrapperProps> = ({ children, loading = false, empty }) => {
  // Show loading spinner while data is being fetched
  if (loading) {
    return <PageLoading />
  }
  // Show empty state when no data is available
  if (!loading && empty) {
    return <PageEmpty />
  }
  // Render actual content when data is loaded and available
  return children
}
export default BodyWrapper
