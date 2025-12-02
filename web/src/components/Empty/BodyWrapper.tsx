import type { FC, ReactNode } from 'react'
import { Skeleton } from 'antd'
import Empty from './index'

interface BodyWrapperProps {
  children: ReactNode
  loading?: boolean
  empty: boolean
}
const BodyWrapper: FC<BodyWrapperProps> = ({ children, loading = false, empty }) => {
  if (loading) {
    return <Skeleton active />
  }
  if (!loading && empty) {
    return <Empty />
  }
  return children
}
export default BodyWrapper
