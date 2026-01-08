import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Row, Col } from 'antd'

import PerceptualLastInfo from '../components/PerceptualLastInfo'
import Timeline from '../components/Timeline'

const PerceptualDetail: FC = () => {
  const { t } = useTranslation()

  return (
    <div className="rb:h-full rb:max-w-266 rb:mx-auto">
      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-3 rb:py-2.5 rb:font-medium rb:leading-5 rb:mt-6 rb:rounded-md rb:mb-4">{t('perceptualDetail.lastInfo')}</div>

      <Row gutter={[16, 16]}>
        <Col span={8}>
          <PerceptualLastInfo type="last_visual" />
        </Col>
        <Col span={8}>
          <PerceptualLastInfo type="last_listen" />
        </Col>
        <Col span={8}>
          <PerceptualLastInfo type="last_text" />
        </Col>
      </Row>

      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-3 rb:py-2.5 rb:font-medium rb:leading-5 rb:mt-6 rb:rounded-md rb:mb-4">{t('perceptualDetail.timeLine')}</div>
      <Timeline />
    </div>
  )
}
export default PerceptualDetail