/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:03:52 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-02 15:48:41
 */
/**
 * Loading Component
 * 
 * A specialized empty state component that displays a loading animation.
 * Uses the Empty component with a loading icon and localized loading messages.
 * 
 * @component
 */

import { type FC } from 'react';
import { useTranslation } from 'react-i18next'

import LoadingIcon from '@/assets/images/loading.svg'
import Empty from './index'

/**
 * @param size - Icon size in pixels (default: 200)
 */
const Loading: FC<{ size?: number }> = ({ size = 200 }) => {
  const { t } = useTranslation()
  return (
    <Empty
      url={LoadingIcon}
      title={t('empty.loadingEmpty')}
      subTitle={t('empty.loadingEmptyDesc')}
      size={size}
    />
  )
}
export default Loading;