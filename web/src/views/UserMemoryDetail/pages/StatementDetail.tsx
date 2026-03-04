/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-19 16:54:52 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-04 16:28:00
 */
import { forwardRef, useImperativeHandle, useRef } from 'react'
import { Row, Col, Space } from 'antd';
import { useParams } from 'react-router-dom'

import WordCloud from '../components/WordCloud'
import EmotionTags from '../components/EmotionTags'
import Health from '../components/Health'
import Suggestions from '../components/Suggestions'
import { generateSuggestions } from '@/api/memory'


/**
 * StatementDetail - Displays emotional memory analysis for a user
 * Shows word cloud, emotion tags, health index, and personalized suggestions
 */
const StatementDetail = forwardRef<{ handleRefresh: () => void },{ refresh: () => void; }>(({
  refresh
}, ref) => {
  const { id } = useParams()
  const suggestionsRef = useRef<{ handleRefresh: () => void; }>(null)

  // Regenerate suggestions and refresh the Suggestions child component
  const handleRefresh = () => {
    if (!id) {
      return Promise.resolve()
    }

    return new Promise((resolve, reject) => {
      generateSuggestions(id)
        .then(() => {
          suggestionsRef.current?.handleRefresh()
          resolve(true)
        })
        .catch((error) => {
          reject(error)
        })
    })
  }
  useImperativeHandle(ref, () => ({
    handleRefresh
  }));
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
        <Suggestions ref={suggestionsRef} refresh={refresh} />
      </Col>
    </Row>
  )
})

export default StatementDetail