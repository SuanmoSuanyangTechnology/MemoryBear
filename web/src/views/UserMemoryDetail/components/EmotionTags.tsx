import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import { getWordCloud } from '@/api/memory'

interface TagList {
  keywords: Array<{ keyword: string; frequency: number; emotion_type: string; avg_intensity: number; }>;
  total_keywords: number;
}
const EmotionTags: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [tagList, setTagList] = useState<TagList | null>(null)

  useEffect(() => {
    getEmotionTagData()
  }, [id])

  const getEmotionTagData = () => {
    if (!id) {
      return
    }
    getWordCloud(id)
      .then((res) => {
        setTagList(res as TagList)
      })
  }

  const [visibleCount, setVisibleCount] = useState(0)

  useEffect(() => {
    if (!tagList || tagList?.keywords.length === 0) return
    
    const timer = setInterval(() => {
      setVisibleCount(prev => {
        if (prev >= tagList?.keywords.length) {
          clearInterval(timer)
          return prev
        }
        return prev + 1
      })
    }, 200)

    return () => clearInterval(timer)
  }, [tagList?.keywords.length])

  const getEmotionColor = (emotionType: string) => {
    const colors: Record<string, string> = {
      joy: '#52c41a',
      anger: '#ff4d4f', 
      sadness: '#1890ff',
      fear: '#fa8c16',
      neutral: '#8c8c8c',
      surprise: '#722ed1'
    }
    return colors[emotionType] || '#8c8c8c'
  }

  const emotionStats = tagList?.keywords.reduce((acc, item) => {
    acc[item.emotion_type] = (acc[item.emotion_type] || 0) + item.frequency
    return acc
  }, {} as Record<string, number>) ?? {}

  return (
    <RbCard
      title={t('emotionDetail.emotionTags')}
      headerType="borderless"
      headerClassName="rb:text-[18px]! rb:leading-[24px]"
      bodyClassName='rb:p-0! rb:relative'
    >
      {tagList
        ? <>
          <div className="rb:flex rb:flex-wrap rb:items-center rb:gap-6 rb:text-sm rb:mt-3 rb:p-3 rb:bg-[#F0F3F8]">
            {Object.entries(emotionStats).map(([type, count]) => {
              console.log(type)
              return (
                <div key={type} className="rb:flex rb:items-center rb:gap-2">
                  <div className="rb:w-3 rb:h-3 rb:rounded-full" style={{ backgroundColor: getEmotionColor(type) }}></div>
                  <span className="rb:text-gray-600">{t(`emotionDetail.${type || 'neutral'}`)} ({count}个)</span>
                </div>
              )
            })}
          </div>
          <div className="rb:mt-6 rb:flex rb:items-center rb:flex-wrap rb:gap-3 rb:mb-3 rb:px-6">
            {tagList.keywords.slice(0, visibleCount).map((item, index) => (
              <div
                key={index}
                className="rb:flex rb:items-center rb:justify-center rb:animate-fadeIn rb:px-4 rb:py-2 rb:rounded-full rb:text-white rb:font-medium"
                style={{
                  backgroundColor: getEmotionColor(item.emotion_type),
                  fontSize: `${12 + item.avg_intensity * 8}px`,
                  animationDelay: `${index * 200}ms`,
                  height: `${20 + item.avg_intensity * 20}px`,
                  transition: 'all 0.3s ease-in-out'
                }}
              >
                {item.keyword}
              </div>
            ))}
          </div>
        </>
        : <Empty />
      }
    </RbCard>
  )
}

export default EmotionTags