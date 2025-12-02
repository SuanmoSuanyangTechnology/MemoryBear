import { useTranslation } from 'react-i18next'
import LoadingIcon from '@/assets/images/loading.svg'
import Empty from './index'
const Loading = ({ size = 200 }: { size?: number }) => {
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