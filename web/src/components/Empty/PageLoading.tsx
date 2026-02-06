/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:04:43 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-02 15:49:49
 */
/**
 * PageLoading Component
 * 
 * A full-page loading state component that displays while content is being fetched.
 * Uses the Empty component with a loading icon and localized loading messages.
 * 
 * @component
 */

import { type FC } from 'react';
import { useTranslation } from 'react-i18next'

import LoadingIcon from '@/assets/images/empty/pageLoading.png'
import Empty from './index'

/**
 * @param size - Icon size in pixels - single number or [width, height] array (default: [240, 210])
 */
const PageLoading: FC<{ size?: number | number[] }> = ({ size = [240, 210] }) => {
  const { t } = useTranslation()
  return (
    <Empty
      url={LoadingIcon}
      title={t('empty.loadingEmpty')}
      subTitle={t('empty.loadingEmptyDesc')}
      size={size}
      className="rb:h-full"
    />
  )
}
export default PageLoading;