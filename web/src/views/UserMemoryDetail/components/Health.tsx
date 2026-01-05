import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Progress } from 'antd'

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import { getEmotionHealth } from '@/api/memory'
interface Health {
  health_score: number;
  level: string;
  dimensions: {
    positivity_rate: {
      score: number;
      positive_count: number;
      negative_count: number;
      neutral_count: number;
    };
    stability: {
      score: number;
      std_deviation: number;
    };
    resilience: {
      score: number;
      recovery_rate: number;
    };
  };
  emotion_distribution: {
    joy: number;
    sadness: number;
    anger: number;
    fear: number;
    surprise: number;
    neutral: number;
  };
  time_range: string;
}
const Health: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    getWordCloudData()
  }, [id])

  const getWordCloudData = () => {
    if (!id) {
      return
    }
    getEmotionHealth(id)
      .then((res) => {
        setHealth(res as Health)
      })
  }

  return (
    <RbCard
      title={t('statementDetail.health')}
      headerType="borderless"
      headerClassName="rb:text-[18px]! rb:leading-[24px]"
    >
      {health?.health_score && health?.health_score > 0
        ? <>
          <div className="rb:flex rb:justify-center rb:items-center">
            <Progress
              size={250}
              type="circle"
              strokeColor={{
                '0%': '#108ee9',
                '100%': '#87d068',
              }}
              percent={health.health_score}
              format={(percent) => `${percent}(${health.level})`}
            />
          </div>

          {health.dimensions && <>
            <div className="rb:flex rb:items-center rb:justify-between rb:mt-6">
              <div className="rb:w-40 rb:mr-3">{t('statementDetail.positivity_rate')}</div> 
              <Progress className="rb:w-[calc(100%-180px)]" percent={health.dimensions.positivity_rate.score} />
            </div>
            <div className="rb:flex rb:items-center rb:gap-3 rb:mt-3">
              <div className="rb:w-40 rb:mr-3">{t('statementDetail.stability')}</div>
              <Progress className="rb:w-[calc(100%-180px)]" percent={health.dimensions.stability.score} />
            </div>
            <div className="rb:flex rb:items-center rb:gap-3 rb:mt-3">
              <div className="rb:w-40 rb:mr-3">{t('statementDetail.resilience')}</div> 
              <Progress className="rb:w-[calc(100%-180px)]" percent={health.dimensions.resilience.score} />
            </div>
          </>}
        </>
        : <Empty size={88} className="rb:h-full" />
      }
    </RbCard>
  )
}

export default Health