import { useTranslation } from 'react-i18next'
import LoadingIcon from '@/assets/images/empty/pageLoading.png'
import Empty from './index'
const PageLoading = ({ size = [240, 210] }: { size?: number | number[] }) => {
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