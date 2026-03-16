/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:33:01 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-16 14:58:25
 */
import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Flex } from 'antd'
import ReactEcharts from 'echarts-for-react'

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import { getEmotionHealth } from '@/api/memory'
/**
 * Health data structure
 * @property {number} health_score - Overall health score
 * @property {string} level - Health level
 * @property {Object} dimensions - Health dimensions (positivity, stability, resilience)
 * @property {Object} emotion_distribution - Distribution of emotions
 * @property {string} time_range - Time range for analysis
 */
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

/**
 * Health Component
 * Displays emotional health score with radar chart and dimension breakdowns
 * Shows positivity rate, stability, and resilience metrics
 */
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
      headerClassName="rb:min-h-[46px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:px-[25px]! rb:pb-[30px]! rb:pt-0!"
    >
      {health?.health_score && health?.health_score > 0
        ? <Flex vertical align="center" justify="center">
          <ReactEcharts
            option={{
              series: [{
                type: 'pie',
                radius: ['75%', '90%'],
                center: ['50%', '50%'],
                startAngle: 90,
                data: [
                  {
                    value: health.health_score,
                    name: health.level,
                    itemStyle: {
                      color: '#155EEF',
                      borderRadius: [10, 10, 10, 10]
                    }
                  },
                  {
                    value: 100 - health.health_score,
                    name: '',
                    itemStyle: {
                      color: '#DFE4ED',
                      borderRadius: [10, 10, 10, 10]
                    }
                  }
                ],
                label: {
                  show: true,
                  position: 'center',
                  formatter: '{score|' + health.health_score + '}\n{level|' + health.level + '}',
                  rich: {
                    score: {
                      fontSize: 36,
                      fontWeight: 'bold',
                      color: '#212332',
                      lineHeight: 36
                    },
                    level: {
                      fontSize: 14,
                      color: '#5B6167',
                      lineHeight: 20
                    }
                  }
                },
                labelLine: { show: false },
                emphasis: { disabled: true },
                itemStyle: {
                  borderRadius: 10
                }
              }]
            }}
            style={{ height: '180px', width: '180px' }}
          />

          {health.dimensions && <Flex justify="space-between" className="rb:w-full rb:mt-7!">
            {['positivity_rate', 'stability', 'resilience'].map(key => (
              <div key={key} className="rb:text-[12px] rb:leading-4.5 rb:text-[#5B6167]">
                <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[#212332] rb:text-[14px] rb:leading-4.75 rb:mb-1">{health.dimensions[key as keyof typeof health.dimensions].score}%</div>
                {t(`statementDetail.${key}`)}
              </div>
            ))}
          </Flex>}

        </Flex>
        : <Empty size={88} className="rb:h-full" />
      }
    </RbCard>
  )
}

export default Health