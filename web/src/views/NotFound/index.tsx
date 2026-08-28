/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:35:01 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-14 14:07:33
 */
/**
 * Not Found Page (404)
 * Displays when requested route or resource does not exist
 */

import { useTranslation } from 'react-i18next';

import notFoundImg from '@/assets/images/empty/404.png';
import Empty from '@/components/Empty';

const NotFound = () => {
  const { t } = useTranslation();

  return (
    <Empty
      url={notFoundImg}
      size={[328, 146]}
      title={t('empty.notFound')}
      subTitle={t('empty.notFoundDesc')}
      className="rb:h-full!"
    />
  )
}
export default NotFound;