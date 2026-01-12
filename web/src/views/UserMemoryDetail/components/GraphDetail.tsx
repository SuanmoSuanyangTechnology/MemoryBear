import { type FC, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Tabs } from 'antd'

import { getRelationshipEvolution, getTimelineMemories } from '@/api/memory'
import type { Node, GraphDetailRef } from '../types'
import RbDrawer from '@/components/RbDrawer'
import RbCard from '@/components/RbCard/Card'
import EmotionLine from './EmotionLine'

export interface Emotion {
  emotion_intensity: number;
  emotion_type: string;
  created_at: string | number;
}
export interface Interaction {
  name: string;
  importance_score: number;
  interaction_count: number;
}

const GraphDetail = forwardRef<GraphDetailRef>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [open, setOpen] = useState(false);
  const [vo, setVo] = useState<Node | null>(null)
  const [emotionData, setEmotionData] = useState<Emotion[]>([
    {
      "emotion_intensity": 0.1,
      "emotion_type": "neutral",
      "created_at": "2026-01-07 19:14:34"
    },
    {
      "emotion_intensity": 0.2,
      "emotion_type": "neutral",
      "created_at": "2026-02-08 19:14:34"
    },
    {
      "emotion_intensity": 0.1,
      "emotion_type": "neutral",
      "created_at": "2026-03-09 19:14:34"
    },
    {
      "emotion_intensity": 0.1,
      "emotion_type": "neutral",
      "created_at": "2026-04-10 19:14:34"
    },
    {
      "emotion_intensity": 0.1,
      "emotion_type": "sadness",
      "created_at": "2026-01-07 19:14:34"
    },
    {
      "emotion_intensity": 0.2,
      "emotion_type": "sadness",
      "created_at": "2026-02-08 19:14:34"
    },
    {
      "emotion_intensity": 0.1,
      "emotion_type": "sadness",
      "created_at": "2026-03-09 19:14:34"
    },
    {
      "emotion_intensity": 0.1,
      "emotion_type": "sadness",
      "created_at": "2026-04-10 19:14:34"
    },
  ])
  const [interactionData, setInteractionData] = useState<Interaction[]>([
    {
      "name": "小蓝",
      "importance_score": 0.5,
      "interaction_count": 1
    }
  ])
  const [timelineMemories, setTimelineMemories] = useState({
    "code": 0,
    "msg": "共同记忆时间线",
    "data": {
      "success": true,
      "data": {
        "MemorySummary": [
          "小蓝今天原计划与小明野餐、与小绿看电影，但最终选择与姐姐小红一起看戏。",
          "用户小明喜欢喝咖啡，每天都要喝拿铁。"
        ],
        "Statement": [
          "小蓝对是否去野餐或看电影感到犹豫。",
          "小蓝和她姐姐小红出去看戏。",
          "小明喜欢喝咖啡。",
          "小明每天都要喝拿铁。",
          "小明今天约小蓝出去野餐。"
        ],
        "ExtractedEntity": [
          "小明",
          "咖啡",
          "拿铁",
          "小蓝",
          "野餐"
        ],
        "timelines_memory": [
          "小蓝今天原计划与小明野餐、与小绿看电影，但最终选择与姐姐小红一起看戏。",
          "用户小明喜欢喝咖啡，每天都要喝拿铁。",
          "小蓝对是否去野餐或看电影感到犹豫。",
          "小蓝和她姐姐小红出去看戏。",
          "小明喜欢喝咖啡。",
          "小明每天都要喝拿铁。",
          "小明今天约小蓝出去野餐。",
          "小明",
          "咖啡",
          "拿铁",
          "小蓝",
          "野餐"
        ]
      }
    },
    "error": "",
    "time": 1767852781464
  })

  const handleCancel = () => {
    setVo(null)
    setOpen(false)
  }
  const handleOpen = (vo: Node) => {
    setOpen(true)
    setVo(vo)
    getTimelineMemoriesData(vo)
  }
  const getRelationshipEvolutionData = (vo: Node) => {
    if (!id || !vo.label) return

    getRelationshipEvolution({ id: id as string, label: vo.label })
      .then(res => {
        const { emotion, interaction } = res as { emotion: { data: Emotion[]}; interaction: {data: Interaction[]} } || {}
        setEmotionData(emotion?.data)
        setInteractionData(interaction?.data)
      })
  }
  const getTimelineMemoriesData = (vo: Node) => {
    if (!id || !vo.label) return

    getTimelineMemories({ id: id as string, label: vo.label })
      .then(res => {

      })
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbDrawer
      title={vo?.name}
      open={open}
      onClose={handleCancel}
      width={1000}
    >
      <div className="rb:text-[16px] rb:font-medium rb:leading-5.5 rb:mb-3">{t('useMemory.relationshipEvolution')}</div>
      <RbCard>
        <Row gutter={16}>
          <Col span={12}>
            <EmotionLine chartData={emotionData} />
          </Col>
          <Col span={12}>
            <div>{t('userMemory.interaction')}</div>
          </Col>
        </Row>
      </RbCard>
    </RbDrawer>
  )
})
export default GraphDetail