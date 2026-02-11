/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:33:01 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:33:01 
 */
import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Progress } from 'antd'
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
      headerClassName="rb:leading-[24px] rb:bg-[#F6F8FC]! rb:min-h-[46px]! rb:border-b! rb:border-b-[#DFE4ED]!"
      bodyClassName="rb:px-[28px]! rb:py-[16px]!"
    >
      {health?.health_score && health?.health_score > 0
        ? <>
          <Row gutter={59}>
            <Col span={12}>
              <div className="rb:flex rb:justify-center rb:items-center">
                <ReactEcharts
                  option={{
                    series: [{
                      type: 'pie',
                      radius: ['65%', '80%'],
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
                  style={{ height: '200px', width: '200px' }}
                />
              </div>
            </Col>
            <Col span={12}>
              {health.dimensions && <div className="rb:space-y-7">
                <div>
                  <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167]">
                    {t('statementDetail.positivity_rate')}
                    <div className="rb:text-[12px] rb:text-[#155EEF] rb:font-medium">{health.dimensions.positivity_rate.score}%</div>
                  </div>
                  <Progress strokeColor="#155EEF" percent={health.dimensions.positivity_rate.score} showInfo={false} />
                </div>
                <div>
                  <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167]">
                    {t('statementDetail.stability')}
                    <div className="rb:text-[12px] rb:text-[#155EEF] rb:font-medium">{health.dimensions.stability.score}%</div>
                  </div>
                  <Progress strokeColor="#155EEF" percent={health.dimensions.stability.score} showInfo={false} />
                </div>
                <div>
                  <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167]">
                    {t('statementDetail.resilience')}
                    <div className="rb:text-[12px] rb:text-[#155EEF] rb:font-medium">{health.dimensions.resilience.score}%</div>
                  </div>
                  <Progress strokeColor="#155EEF" percent={health.dimensions.resilience.score} showInfo={false} />
                </div>
              </div>}
            </Col>
          </Row>

        </>
        : <Empty size={88} className="rb:h-full" />
      }
    </RbCard>
  )
}

export default Health