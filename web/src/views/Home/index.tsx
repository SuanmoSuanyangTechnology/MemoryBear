/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:12:43 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-03 17:15:29
 */
/**
 * Home Dashboard Page
 * Main dashboard displaying memory statistics, charts, activities, and quick operations
 */

import { useEffect, useState } from 'react';
import { Row, Col, Flex } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import TopCardList from './components/TopCardList'
import LineCard from './components/LineCard'
import PieCard from './components/PieCard'
import { getDashboardData, getMemoryIncrement, getKbTypes, getWorkspaceStatistics } from '@/api/memory';
import RecentActivity from './components/RecentActivity'
import TagList from './components/TagList'
import QuickOperation from './components/QuickOperation'
import ApiLineCard from './components/ApiLineCard'
import BarCard from './components/BarCard';
import Card from './components/Card'

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
export interface WorkspaceStatistics {
  statistics: Array<{
    type:
      | 'PERCEPTUAL_MEMORY'
      | 'WORKING_MEMORY'
      | 'SHORT_TERM_MEMORY'
      | 'EXPLICIT_MEMORY'
      | 'IMPLICIT_MEMORY'
      | 'EMOTIONAL_MEMORY'
      | 'EPISODIC_MEMORY'
      | 'FORGET_MEMORY';
    count: number;
    percentage: number;
    change: number;
  }>;
  total_count: number;
  total_count_change: number;
  total_users: number;
  update: number;
  generated_at: number;
}

const Home = () => {
  const { t } = useTranslation()
  const [dashboardData, setDashboardData] = useState<DashboardData>({} as DashboardData);
  const [loading, setLoading] = useState({
    knowledgeTypeDistribution: true,
  });
  const [workspaceStatisticsLoading, setWorkspaceStatisticsLoading] = useState(true)
  const [workspaceStatistics, setWorkspaceStatistics] = useState<WorkspaceStatistics>({} as WorkspaceStatistics)
  const [knowledgeTypeDistribution, setKnowledgeTypeDistribution] = useState<Array<{ name: string; value: number }>>([]);
  const [memoryIncrement, setMemoryIncrement] = useState<Array<{ updated_at: string; total_num: number; }>>([]);
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(6, 'day'),
    dayjs(),
  ]);

  /** Simulate API data fetch */
  useEffect(() => {
    getData()
    getKnowledgeTypeDistribution()
    getWorkspaceStatisticsData()
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
    getMemoryIncrement({
      start_time: dateRange[0].startOf('d').valueOf(),
      end_time: dateRange[1].endOf('d').valueOf(),
    }).then(res => {
      const response = res as { updated_at: string; total_num: number; }[]
      setMemoryIncrement(response || [])
    })
  }

  const getWorkspaceStatisticsData = () => {
    setWorkspaceStatisticsLoading(true)
    getWorkspaceStatistics()
      .then(res => {
        setWorkspaceStatistics(res as WorkspaceStatistics)
      })
      .finally(() => {
        setWorkspaceStatisticsLoading(false)
      })
  }
  useEffect(() => {
    getMemoryIncrementData()
  }, [dateRange])

  const handleRangeChange = (value: [string, string], type: string) => {
    switch (type) {
      case 'memoryGrowthTrend':
        setDateRange([dayjs(value[0]), dayjs(value[1])])
        break
    }
  }

  return (
    <Row gutter={[12, 12]} className="rb:h-full! rb:overflow-y-auto!">
      <Col span={8}>
        <TopCardList data={dashboardData} />
      </Col>
      <Col span={8}>
        <Flex vertical gap={12} className="rb:h-full! rb:overflow-hidden!">
          <div className="rb:h-30 rb:shrink-0">
            <Card
              title={t('dashboard.memoryScaleCoreMetrics')}
              bodyClassName="rb:h-[calc(100%-58px)]! rb:overflow-hidden! rb:p-4! rb:pt-0!"
            >
              <div className="rb:grid rb:grid-cols-2 rb:h-full">
                <Flex vertical justify="space-between" className="rb:text-gray-600 rb:text-[12px] rb:h-full!">
                  {t('dashboard.activeMemory')}

                  <Flex align="flex-end" gap={4}>
                    <span className="rb:text-[16px] rb:leading-4 rb:font-[MiSans-Bold] rb:text-gray-900">
                      {(workspaceStatistics.total_count ?? 0)?.toLocaleString()}
                    </span>

                    <Flex align="center" className={clsx('rb:font-medium rb:leading-3.5', {
                      'rb:text-red-500': workspaceStatistics.total_count_change < 0,
                      'rb:text-green-600': workspaceStatistics.total_count_change >= 0,
                    })}>
                      <div className={clsx("rb:size-3.5 rb:cursor-pointer rb:bg-cover", {
                        "rb:bg-[url('@/assets/images/home/arrow_down.png')]": workspaceStatistics.total_count_change < 0,
                        "rb:bg-[url('@/assets/images/home/arrow_up_success.svg')]": workspaceStatistics.total_count_change >= 0,
                      })}></div>
                      {(workspaceStatistics.total_count_change ?? 0) * 100}%
                    </Flex>
                  </Flex>
                </Flex>

                <Flex vertical justify="space-between" className="rb:text-gray-600 rb:text-[12px] rb:h-full! rb-border-l rb:pl-4!">
                  {t('dashboard.updatedToday')}

                  <Flex align="flex-end" gap={4}>
                    <span className="rb:text-[16px] rb:leading-4 rb:font-[MiSans-Bold] rb:text-gray-900">
                      {(workspaceStatistics.update ?? 0).toLocaleString()}
                    </span>
                  </Flex>
                </Flex>
              </div>   
            </Card>
          </div>
          <div className="rb:flex-1 rb:overflow-hidden">
            <LineCard
              chartData={memoryIncrement}
              dateRange={dateRange}
              onChange={handleRangeChange}
              type="memoryGrowthTrend"
              seriesList={['total_num']}
            />
          </div>
        </Flex>
      </Col>
      <Col span={8}>
        <ApiLineCard />
      </Col>
      <Col span={8}>
        <BarCard
          statistics={workspaceStatistics?.statistics || []}
          loading={workspaceStatisticsLoading}
        />
      </Col>
      <Col span={8}>
        <RecentActivity />
      </Col>
      <Col span={8}>
        <QuickOperation />
      </Col>
      <Col span={16}>
        <TagList />
      </Col>
      <Col span={8}>
        <PieCard
          loading={loading.knowledgeTypeDistribution}
          chartData={knowledgeTypeDistribution}
        />
      </Col>
    </Row>
  );
}

export default Home
