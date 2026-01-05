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
      className="rb:h-[calc(100vh-84px)]"
    />
  )
}
export default NotFound;