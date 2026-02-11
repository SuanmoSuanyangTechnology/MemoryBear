/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:12:43 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-03 17:26:04
 */
/**
 * Home Dashboard Page
 * Main dashboard displaying memory statistics, charts, activities, and quick operations
 */

import { useEffect, useState } from 'react';
import { Row, Col } from 'antd';

import TopCardList from './components/TopCardList'
import LineCard from './components/LineCard'
import PieCard from './components/PieCard'
import { getDashboardData, getMemoryIncrement, getKbTypes } from '@/api/memory';
import RecentActivity from './components/RecentActivity'
import TagList from './components/TagList'
import QuickOperation from './components/QuickOperation'

/**
 * Dashboard statistics data
 */
export interface DashboardData {
  totalMemoryCapacity?: number;
  application?: number;
  knowledgeBaseCount?: number;
  apiCallCount?: number;
}

const Home = () => {
  const [dashboardData, setDashboardData] = useState<DashboardData>({});
  const [loading, setLoading] = useState({
    knowledgeTypeDistribution: true,
  });
  const [knowledgeTypeDistribution, setKnowledgeTypeDistribution] = useState<Array<{ name: string; value: number }>>([]);
  const [memoryIncrement, setMemoryIncrement] = useState<Array<{ updated_at: string; total_num: number; }>>([]);
  const [limit, setLimit] = useState(7);

  /** Simulate API data fetch */
  useEffect(() => {
    getData()
    getKnowledgeTypeDistribution()
  }, []);
  /** Fetch memory total, app count, knowledge base count, API call count */
  const getData = () => {
    getDashboardData().then(res => {
      const response = res as {
        storage_type: 'rag' | 'neo4j',
        neo4j_data?:  {
          total_memory?: number;
          total_app?: number;
          total_knowledge?: number;
          total_api_call?: number;
        };
        rag_data?: {
          total_memory?: number;
          total_app?: number;
          total_knowledge?: number;
          total_api_call?: number;
        }
      }
      const { storage_type = 'neo4j' } = response || {}
      const responseData = storage_type === 'neo4j' ? response.neo4j_data : response.rag_data
      setDashboardData({
        totalMemoryCapacity: responseData?.total_memory || 0,
        application: responseData?.total_app || 0,
        knowledgeBaseCount: responseData?.total_knowledge || 0,
        apiCallCount: responseData?.total_api_call || 0
      })
    })
  }
  /** Fetch knowledge base type distribution */
  const getKnowledgeTypeDistribution = () => {
    setLoading({
      ...loading,
      knowledgeTypeDistribution: true,
    })

    getKbTypes().then(res => {
      const response = res as Record<string, number>
      const list: Array<{ name: string; value: number }> = []
      Object.entries(response).map(([type, count]) => {
        if (count > 0 && type !== 'total') {
          list.push({
            name: type,
            value: count
          })
        }
        return null
      })
      setKnowledgeTypeDistribution(list)
    })
    .finally(() => {
      setLoading({
        ...loading,
        knowledgeTypeDistribution: false,
      })
    })
  }
  /** Fetch memory growth trend data */
  const getMemoryIncrementData = () => {
    getMemoryIncrement(limit).then(res => {
      const response = res as { updated_at: string; total_num: number; }[]
      setMemoryIncrement(response || [])
    })
  }
  useEffect(() => {
    getMemoryIncrementData()
  }, [limit])

  const handleRangeChange = (value: string, type: string) => {
    switch (type) {
      case 'memoryGrowthTrend':
        setLimit(Number(value))
        break
    }
  }

  return (
    <div className="rb:pb-6">
      <TopCardList data={dashboardData} />

      <Row className="rb:mt-4" gutter={16}>
        <Col span={12}>
          <LineCard
            chartData={memoryIncrement}
            limit={limit}
            onChange={handleRangeChange}
            type="memoryGrowthTrend"
            seriesList={['total_num']}
          />
        </Col>
        <Col span={12}>
          <PieCard
            loading={loading.knowledgeTypeDistribution}
            chartData={knowledgeTypeDistribution}
          />
        </Col>
      </Row>

      <Row className="rb:mt-4" gutter={16}>
        <Col span={12}>
          <RecentActivity />
        </Col>
        <Col span={12}>
          <TagList />
        </Col>
      </Row>

      <Row className="rb:mt-4" gutter={16}>
        <Col span={24}>
          <QuickOperation />
        </Col>
      </Row>
    </div>
  );
}

export default Home