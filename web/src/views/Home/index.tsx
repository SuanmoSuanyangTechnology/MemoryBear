import { useEffect, useState } from 'react';
import { Row, Col, Space } from 'antd';

import TopCardList from './components/TopCardList'
import LineCard from './components/LineCard'
import PieCard from './components/PieCard'
import { getDashboardData, getMemoryIncrement, getKbTypes } from '@/api/memory';
import RecentActivity from './components/RecentActivity'
import TagList from './components/TagList'
import QuickOperation from './components/QuickOperation'

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

  // 模拟API获取数据
  useEffect(() => {
    getData()
    getKnowledgeTypeDistribution()
  }, []);
  // 记忆总量 / 应用数量 / 知识库数量 / API调用次数
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
      const responseData = response[storage_type + '_data'] || {}
      setDashboardData({
        totalMemoryCapacity: responseData.total_memory || 0,
        application: responseData.total_app || 0,
        knowledgeBaseCount: responseData.total_knowledge || 0,
        apiCallCount: responseData.total_api_call || 0
      })
    })
  }
  // 知识库类型分布 / 知识库数量
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
  // 记忆增长趋势
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
    <div className="rb:pb-[24px]">
      {/* 统计卡片 */}
      <TopCardList data={dashboardData} />

      <Row className="rb:mt-[16px]" gutter={16}>
        {/* 记忆增长趋势 */}
        <Col span={12}>
          <LineCard
            chartData={memoryIncrement}
            limit={limit}
            onChange={handleRangeChange}
            type="memoryGrowthTrend"
            seriesList={['total_num']}
          />
        </Col>
        {/* 知识库类型分布 */}
        <Col span={12}>
          <PieCard
            loading={loading.knowledgeTypeDistribution}
            chartData={knowledgeTypeDistribution}
          />
        </Col>
      </Row>

      <Row className="rb:mt-[16px]" gutter={16}>
        <Col span={12}>
          {/* 最近记忆活动 */}
          <RecentActivity />
        </Col>
        <Col span={12}>
          {/* 热门记忆标签 */}
          <TagList />
        </Col>
      </Row>

      <Row className="rb:mt-[16px]" gutter={16}>
        <Col span={24}>
          {/* 快速操作 */}
          <QuickOperation />
        </Col>
      </Row>
    </div>
  );
}

export default Home