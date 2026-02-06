/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:29:45 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:29:45 
 */
import { type FC, useState, useEffect } from 'react';
import { Row, Col, Flex, DatePicker } from 'antd';
import type { Dayjs } from 'dayjs'
import dayjs from 'dayjs';

const { RangePicker } = DatePicker;

import type { Application } from '@/views/ApplicationManagement/types'
import { getAppStatistics } from '@/api/application';
import LineCard from './components/LineCard'
import type { StatisticsData, StatisticsItem } from './types'

/**
 * Mapping of daily statistics keys to total statistics keys
 */
const TotalObj: Record<string, keyof StatisticsData> = {
  daily_conversations: 'total_conversations',
  daily_new_users: 'total_new_users',
  daily_api_calls: 'total_api_calls',
  daily_tokens: 'total_tokens',
}

/**
 * Statistics page component
 * Displays application usage statistics with charts and date range filtering
 * @param application - Application data
 */
const Statistics: FC<{ application: Application | null }> = ({ application }) => {
  const [data, setData] = useState<StatisticsData>({
    daily_conversations: [],
    total_conversations: 0,
    daily_new_users: [],
    total_new_users: 0,
    daily_api_calls: [],
    total_api_calls: 0,
    daily_tokens: [],
    total_tokens: 0
  })
  const [query, setQuery] = useState({
    start_date: dayjs().subtract(6, 'd'),
    end_date: dayjs().subtract(0, 'd'),
  })

  useEffect(() => {
    getData()
  }, [application, query])
  /**
   * Fetch statistics data
   */
  const getData = () => {
    if (!application?.id) {
      return
    }
    const params = {
      start_date: query.start_date.startOf('d').valueOf(),
      end_date: query.end_date.endOf('d').valueOf(),
    }

    getAppStatistics(application.id, params)
      .then(res => {
        setData(res as StatisticsData)
      })
  }
  /**
   * Handle date range change
   * @param date - Selected date range
   */
  const handleChange = (date: [Dayjs | null, Dayjs | null] | null) => {
    if (!date || !date[0] || !date[1]) return
    setQuery({
      start_date: date[0],
      end_date: date[1],
    })
  }
  return (
    <div className="rb:w-250 rb:mt-5 rb:pb-5 rb:mx-auto">
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Flex justify="end">
            <RangePicker defaultValue={[query.start_date, query.end_date]} onChange={handleChange} />
          </Flex>
        </Col>
        {Object.entries(data).map(([key, value]) => {
          if (key.includes('total')) {
            return null
          }
          const totalKey = TotalObj[key];
          return (
            <Col span={12} key={key}>
              <LineCard
                type={key}
                total={totalKey ? (data[totalKey] as number) : 0}
                chartData={value as StatisticsItem[]}
              />
            </Col>
          )
        })}
      </Row>
    </div>
  );
}
export default Statistics;