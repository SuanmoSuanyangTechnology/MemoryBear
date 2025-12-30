import { type FC, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Row, Col, Space } from 'antd';

import WordCloud from '../components/WordCloud'
import EmotionTags from '../components/EmotionTags'
import Health from '../components/Health'
import Suggestions from '../components/Suggestions'
import PageHeader from '../components/PageHeader'
import {
  getEndUserProfile,
} from '@/api/memory'


const StatementDetail: FC = () => {
  const { id } = useParams()
  const [name, setName] = useState<string>('')
  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  const getData = () => {
    if (!id) return
    getEndUserProfile(id).then((res) => {
      const response = res as { other_name: string; id: string; }
      setName(response.other_name ?? response.id) 
    })
  }
  return (
    <div className="rb:h-full rb:w-full">
      <PageHeader 
        name={name}
        source="statement"
      />
      <div className="rb:h-[calc(100vh-64px)] rb:overflow-y-auto rb:py-3 rb:px-4">
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
      </div>
    </div>
  )
}

export default StatementDetail