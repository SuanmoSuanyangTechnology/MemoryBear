/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:34:18 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-14 14:08:04
 */
/**
 * No Permission Page
 * Displays when user lacks access rights to a resource
 */

import { useTranslation } from 'react-i18next';

import noPermission from '@/assets/images/empty/noPermission.png';
import Empty from '@/components/Empty';

const NoPermission = () => {  
  const { t } = useTranslation();

  return (
    <Empty
      url={noPermission}
      size={[240, 240]}
      title={t('empty.noPermission')}
      subTitle={t('empty.noPermissionDesc')}
      className="rb:h-full!"
    />
  )
}
export default NoPermission;
