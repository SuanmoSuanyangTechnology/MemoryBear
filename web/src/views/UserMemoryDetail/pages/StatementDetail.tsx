/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-19 16:54:52 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 11:35:37
 */
import { forwardRef, useImperativeHandle, useRef } from 'react'
import { Row, Col } from 'antd';
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
    <Row gutter={[12, 12]} className="rb:h-full!">
      <Col span={12} className="rb:h-full!">
        <Row gutter={[12, 12]}>
          <Col span={24}>
            <WordCloud />
          </Col>
          <Col span={12}>
            <EmotionTags />
          </Col>
          <Col span={12}>
            <Health />
          </Col>
        </Row>
      </Col>
      <Col span={12} className="rb:h-full!">
        <Suggestions ref={suggestionsRef} refresh={refresh} />
      </Col>
    </Row>
  )
})

export default StatementDetail