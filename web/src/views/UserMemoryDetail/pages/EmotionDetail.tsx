import { type FC } from 'react'
import { Row, Col } from 'antd';

import WordCloud from '../components/WordCloud'
import EmotionTags from '../components/EmotionTags'
import Health from '../components/Health'
import Suggestions from '../components/Suggestions'


const EmotionDetail: FC = () => {
  return (
    <Row gutter={[16, 16]}>
      <Col span={12}>
        <WordCloud />
      </Col>
      <Col span={12}>
        <EmotionTags />
      </Col>
      <Col span={12}>
        <Health />
      </Col>
      <Col span={12}>
        <Suggestions />
      </Col>
    </Row>
  )
}

export default EmotionDetail