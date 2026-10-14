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
import { getDashboardData, getMemoryIncrement, getKbTypes } from '@/api/memory';
import RecentActivity from './components/RecentActivity'
import TagList from './components/TagList'
import QuickOperation from './components/QuickOperation'
import ApiLineCard from './components/ApiLineCard'
import BarCard from './components/BarCard';
import Card from './components/Card'
import type { DashboardDataResponse, DashboardData, WorkspaceStatistics } from './types';

const Home = () => {
  const { t } = useTranslation()
  const [dashboardDataLoading, setDashboardDataLoading] = useState(true);
  const [dashboardData, setDashboardData] = useState<DashboardData>({} as DashboardData);
  const [loading, setLoading] = useState({
    knowledgeTypeDistribution: true,
  });
  const [workspaceStatistics, setWorkspaceStatistics] = useState<WorkspaceStatistics>({} as WorkspaceStatistics)
  const [knowledgeTypeDistribution, setKnowledgeTypeDistribution] = useState<Array<{ name: string; value: number }>>([]);
  const [memoryIncrement, setMemoryIncrement] = useState<Array<{ date: string; total_num: number; }>>([]);
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(6, 'day'),
    dayjs(),
  ]);

  /** Simulate API data fetch */
  useEffect(() => {
    getData()
    getKnowledgeTypeDistribution()
  }, []);
  /** Fetch memory total, app count, knowledge base count, API call count */
  const getData = () => {
    setDashboardDataLoading(true)
    getDashboardData()
      .then(res => {
        const response = res as DashboardDataResponse
        const { storage_type = 'neo4j', memory_update = 0, memory_update_change = 0 } = response || {}
        const responseData = storage_type === 'neo4j' ? response.neo4j_data : response.rag_data
        setDashboardData(responseData as DashboardData)
        setWorkspaceStatistics({
          ...(response.workspace_statistics || {}),
          memory_update,
          memory_update_change
        })
      })
      .finally(() => {
        setDashboardDataLoading(false)
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
      const response = res as {items: { date: string; total_num: number; }[]}
      setMemoryIncrement(response.items || [])
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
                      {((workspaceStatistics.total_count_change ?? 0) * 100).toFixed(2)}%
                    </Flex>
                  </Flex>
                </Flex>

                <Flex vertical justify="space-between" className="rb:text-gray-600 rb:text-[12px] rb:h-full! rb-border-l rb:pl-4!">
                  {t('dashboard.updatedToday')}

                  <Flex align="flex-end" gap={4}>
                    <span className="rb:text-[16px] rb:leading-4 rb:font-[MiSans-Bold] rb:text-gray-900">
                      {(workspaceStatistics.memory_update ?? 0).toLocaleString()}
                    </span>

                    <Flex align="center" className={clsx('rb:font-medium rb:leading-3.5', {
                      'rb:text-red-500': workspaceStatistics.memory_update_change < 0,
                      'rb:text-green-600': workspaceStatistics.memory_update_change >= 0,
                    })}>
                      <div className={clsx("rb:size-3.5 rb:cursor-pointer rb:bg-cover", {
                        "rb:bg-[url('@/assets/images/home/arrow_down.png')]": workspaceStatistics.memory_update_change < 0,
                        "rb:bg-[url('@/assets/images/home/arrow_up_success.svg')]": workspaceStatistics.memory_update_change >= 0,
                      })}></div>
                      {workspaceStatistics.memory_update_change ?? 0}
                    </Flex>
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
          loading={dashboardDataLoading}
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
