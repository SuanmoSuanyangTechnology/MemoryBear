import { type FC, type ReactNode } from 'react'
import { isPrivateAvailable } from '@/utils/private'

interface PrivateWrapProps {
  /**
   * Content to render when the private package is available.
   * When the content is a private component, use the function form `() => <PrivateComp />`:
   * this defers JSX creation until the private package is confirmed available, avoiding the
   * React "type is invalid ... got: null" warning when the component is null because the package
   * is missing (JSX has its type validated synchronously at creation time).
   */
  children: ReactNode | (() => ReactNode)
  /** Fallback content when the private package is unavailable; renders nothing by default */
  fallback?: ReactNode
}

// Generic wrapper for private components: renders children only when the real private package is available, otherwise renders fallback
const PrivateWrap: FC<PrivateWrapProps> = ({ children, fallback = null }) => {
  if (!isPrivateAvailable) return <>{fallback}</>
  return <>{typeof children === 'function' ? children() : children}</>
}

export default PrivateWrap
