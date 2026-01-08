import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Row, Col } from 'antd'

import Preferences from '../components/Preferences'
import Portrait from '../components/Portrait'
import InterestAreas from '../components/InterestAreas'
import Habits from '../components/Habits'

const ImplicitDetail: FC = () => {
  const { t } = useTranslation()

  return (
    <div className="rb:h-full rb:max-w-266 rb:mx-auto">
      <div className="rb:text-[#5B6167] rb:leading-5 rb:mt-3">{t('implicitDetail.title')}</div>
      
      <Preferences />

      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-3 rb:py-2.5 rb:font-medium rb:leading-5 rb:mb-4 rb:mt-6 rb:rounded-md">{t('implicitDetail.portraitTitle')}</div>
      <div className="rb:my-3 rb:text-[#5B6167] rb:leading-5">{t('implicitDetail.portraitSubTitle')}</div>
      <Row gutter={[16, 16]} className="rb:mt-4">
        <Col span={12}>
          <Portrait />
        </Col>
        <Col span={12}>
          <InterestAreas />
        </Col>
      </Row>

      <Habits />
    </div>
  )
}
export default ImplicitDetail