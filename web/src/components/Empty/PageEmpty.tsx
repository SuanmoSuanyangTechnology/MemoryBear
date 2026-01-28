import { useTranslation } from 'react-i18next'
import pageEmptyIcon from '@/assets/images/empty/pageEmpty.png'
import Empty from './index'
const PageEmpty = ({ size = [240, 210] }: { size?: number | number[] }) => {
  const { t } = useTranslation()
  return (
    <Empty
      url={pageEmptyIcon}
      title={t('empty.pageEmpty')}
      subTitle={t('empty.pageEmptyDesc')}
      size={size}
      className="rb:h-full"
    />
  )
}
export default PageEmpty;