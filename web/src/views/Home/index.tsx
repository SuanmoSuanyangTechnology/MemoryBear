/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:12:43 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-10 11:57:58
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
import ApiLineCard from './components/ApiLineCard'

/**
 * Dashboard statistics data
 */
export interface DashboardData {
  total_memory: number;
  total_app: number;
  total_knowledge: number;
  total_api_call: number;
  total_memory_change: number;
  total_app_change: number;
  total_knowledge_change: number;
  total_api_call_change: number;
}

const Home = () => {
  const [dashboardData, setDashboardData] = useState<DashboardData>({} as DashboardData);
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
      setDashboardData(responseData as DashboardData)
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
    <Row gutter={[12, 12]}>
      <Col span={8}>
        <TopCardList data={dashboardData} />
      </Col>
      <Col span={8}>
        <LineCard
          chartData={memoryIncrement}
          limit={limit}
          onChange={handleRangeChange}
          type="memoryGrowthTrend"
          seriesList={['total_num']}
        />
      </Col>
      <Col span={8}>
        <ApiLineCard
        />
      </Col>
      <Col span={8}>
        <PieCard
          loading={loading.knowledgeTypeDistribution}
          chartData={knowledgeTypeDistribution}
        />
      </Col>
      <Col span={8}>
        <RecentActivity />
      </Col>
      <Col span={8}>
        <QuickOperation />
      </Col>
      <Col span={24}>
        <TagList />
      </Col>
    </Row>
  );
}

export default Home