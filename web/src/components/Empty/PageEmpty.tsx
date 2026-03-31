/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:04:18 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-09 13:50:04
 */
/**
 * PageEmpty Component
 * 
 * A full-page empty state component that displays when no content is available.
 * Uses the Empty component with a page-specific empty icon and messages.
 * 
 * @component
 */

import { type FC } from 'react';
import { useTranslation } from 'react-i18next'

import pageEmptyIcon from '@/assets/images/empty/pageEmpty.png'
import Empty from './index'

/**
 * @param size - Icon size in pixels - single number or [width, height] array (default: [240, 210])
 */
const PageEmpty: FC<{ size?: number | number[]; className?: string; }> = ({ size = [240, 210], className = '' }) => {
  const { t } = useTranslation()
  return (
    <Empty
      url={pageEmptyIcon}
      title={t('empty.pageEmpty')}
      subTitle={t('empty.pageEmptyDesc')}
      size={size}
      className={`rb:h-full ${className}`}
    />
  )
}
export default PageEmpty;