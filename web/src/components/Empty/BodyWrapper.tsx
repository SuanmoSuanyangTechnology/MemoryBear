import type { FC, ReactNode } from 'react'
import PageEmpty from './PageEmpty'
import PageLoading from './PageLoading'

interface BodyWrapperProps {
  children: ReactNode
  loading?: boolean
  empty: boolean
}
const BodyWrapper: FC<BodyWrapperProps> = ({ children, loading = false, empty }) => {
  if (loading) {
    return <PageLoading />
  }
  if (!loading && empty) {
    return <PageEmpty />
  }
  return children
}
export default BodyWrapper
