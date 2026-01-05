import { useEffect, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Row, Col, Space, Button } from 'antd';
import TopCardList from './components/TopCardList';
import GuideCard from './components/GuideCard';
import VersionCard from './components/VersionCard';
import QuickActions from './components/QuickActions';
import bgImg from '@/assets/images/index/index_bg@2x.png'
import type { DashboardData } from './types';
import Table, { type TableRef } from '@/components/Table'
import type { ColumnsType } from 'antd/es/table';
import { formatDateTime } from '@/utils/format';
const Index = () => {
  const { t } = useTranslation();
  const [dashboardData, setDashboardData] = useState<DashboardData>({
    total_models: 24,
    total_spaces: 156,
    total_users: 1248,
    total_apps_runs: '12.8k',
  });
  const tableRef = useRef<TableRef>(null);
  const tableApi = '/workspaces';
  const [loading, setLoading] = useState({
    knowledgeTypeDistribution: true,
  });
  const [knowledgeTypeDistribution, setKnowledgeTypeDistribution] = useState<Array<{ name: string; value: number }>>([]);
  const [memoryIncrement, setMemoryIncrement] = useState<Array<{ updated_at: string; total_num: number; }>>([]);
  const [limit, setLimit] = useState(7);
  const columns: ColumnsType = [
    {
      title: t('space.spaceName'),
      dataIndex: 'name',
      key: 'name',
    },
    // {
    //   title: t('space.associated') + ' ' + t('memorySummary.user'),
    //   dataIndex: 'name',
    //   key: 'name',
    // },
    {
      title: t('space.spaceIcon'),
      dataIndex: 'icon',
      key: 'icon',
      render:(value:string) => {
        return(
          <img src={value} alt="icon" className='rb:w-[24px] rb:h-[24px]' />
        )
      }
    },
    {
      title: t('apiKey.createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      render:(value:string) => {
        return(
          <span>{formatDateTime(Number(value) * 1000 ,'YYYY-MM-DD HH:mm:ss')}</span>
        )
      }
    },
    {
      title: t('common.operation'),
      key: 'action',
      fixed: 'right',
      width: 100,
      render: (_, record) => (
        <Space size="middle">
          <Button color="primary" variant="text">{t('space.enterSpace')}</Button>
        </Space>
      ),
    },
  ]
  // 模拟API获取数据
  useEffect(() => {
    tableRef.current?.loadData();
  }, [tableApi]);



  return (
    <div className="rb:pb-[24px]">
      <Row className="rb:mt-[16px]" gutter={16}>
        <Col span={19}>
          <div className='rb:flex-col rb:w-full rb:h-[120px] rb:mb-4 rb:p-6 rb:leading-[30px]' style={{backgroundImage: `url(${bgImg})`, backgroundSize: '100% 100%'}}>
              <div className='rb:flex rb:text-[22px] rb:text-[#0041C3] rb:font-semibold'>
                { t('index.spaceTitle' )}
              </div>
              <div className='rb:flex rb:mt-2 rb:text-xs rb:leading-[18px] rb:text-[#5F6266] rb:max-w-[560px]'>
                { t('index.spaceSubTitle' )}
              </div>
          </div>
          {/* 统计卡片 */}
          <TopCardList data={dashboardData} />
          <div className="rb:rounded rb:max-h-[calc(100%-100px)] rb:overflow-y-auto rb:mt-4">
            <Table
              ref={tableRef}
              apiUrl={tableApi}
              columns={columns}
              rowKey="id"
              
            />
        </div>
        </Col>
        <Col span={5}>
            {/* 引导 */}
            <GuideCard />
            <div className='rb:w-full rb:mt-4 '>
                <VersionCard />
            </div>
            {/* 快捷操作 */}
            <div className='rb:w-full rb:mt-4'>
                <QuickActions />
            </div>
        </Col>
      </Row>

     
    </div>
  );
}

export default Index