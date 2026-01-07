import { type FC } from 'react'
import { Row, Col, Space } from 'antd';

import WordCloud from '../components/WordCloud'
import EmotionTags from '../components/EmotionTags'
import Health from '../components/Health'
import Suggestions from '../components/Suggestions'


const StatementDetail: FC = () => {
  return (
    <Row gutter={[16, 16]}>
      <Col span={12}>
        <Space size={16} direction="vertical" className="rb:w-full">
          <WordCloud />
          <EmotionTags />
          <Health />
        </Space>
      </Col>
      <Col span={12}>
        <Suggestions />
      </Col>
    </Row>
  )
}

export default StatementDetail