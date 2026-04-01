import { useEffect, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Space, Button, Row, Col, Flex } from 'antd';

import TopCardList from './components/TopCardList';
import GuideCard from './components/GuideCard';
import VersionCard from './components/VersionCard';
import QuickActions from './components/QuickActions';
import Table, { type TableRef } from '@/components/Table'
import type { ColumnsType } from 'antd/es/table';
import { formatDateTime } from '@/utils/format';
import { 
  getDashboardData,
  getDashboardStatistics,
  type DataResponse } from '@/api/common';
import { switchWorkspace } from '@/api/workspaces'
const Index = () => {
  const { t } = useTranslation();
  const navigate = useNavigate()
  const [dashboardData, setDashboardData] = useState<DataResponse>();
  const tableRef = useRef<TableRef>(null);
  const tableApi = getDashboardData;
  const getDashboardCount = async () => {
      try{
        const res = await getDashboardStatistics();
        setDashboardData(res);
      }catch(e) {
        console.log(e)
      }
  }
  const handleJump = (id: string) => {
    switchWorkspace(id)
      .then(() => {
        localStorage.removeItem('user')
        navigate('/')
      })
  }
  const columns: ColumnsType = [
    {
      title: t('space.spaceName'),
      dataIndex: 'name',
      key: 'name',
      className: 'rb:text-[#212332]'
    },
    {
      title: t('space.spaceIcon'),
      dataIndex: 'icon',
      key: 'icon',
      render:(value: string, record: any) => {
        return value ? (
          <img src={value} alt="icon" className='rb:size-6' />
        ) : (
          <div className='rb:size-6 rb:bg-[#155EEF] rb:text-white rb:rounded rb:flex rb:items-center rb:justify-center rb:text-xs rb:font-medium'>
            {record.name?.charAt(0)?.toUpperCase() || '?'}
          </div>
        )
      }
    },
    {
      title: t('index.appCount'),
      dataIndex: 'app_count',
      key: 'app_count',
    },
    {
      title: t('index.userCount'),
      dataIndex: 'user_count',
      key: 'user_count',
    },
    {
      title: t('apiKey.createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      render:(value:string) => {
        return(
          <span>{formatDateTime(Number(value) ,'YYYY-MM-DD HH:mm:ss')}</span>
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
          <Button type="link" onClick={() => handleJump(record.id)}>{t('space.enterSpace')}</Button>
        </Space>
      ),
    },
  ]
  
  useEffect(() => {
    tableRef.current?.loadData();
  }, [tableApi]);
  useEffect(() => {
    getDashboardCount();
  }, [])


  return (
    <Row gutter={12}>
      <Col flex="1">
        <Flex vertical>
          <div className='rb:w-full rb:h-26 rb:p-4 rb:bg-cover rb:bg-[url("@/assets/images/index/index_bg@2x.png")] rb:rounded-xl rb:overflow-hidden'>
            <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-white rb:text-[18px] rb:leading-7">
              {t('index.spaceTitle')}
            </div>
            <div className='rb:mt-2 rb:text-[12px] rb:leading-4.5 rb:text-white rb:max-w-139.75'>
              {t('index.spaceSubTitle')}
            </div>
          </div>
          {/* 统计卡片 */}
          <TopCardList data={dashboardData} />
          <div className="rb:rounded-xl rb:bg-white rb:pt-3 rb:px-3 rb:overflow-y-hidden rb:my-3 rb:flex-1">
            <Table
              ref={tableRef}
              apiUrl={tableApi}
              columns={columns}
              rowKey="id"
              bordered={false}
              scrollY="100%"
            />
          </div>
        </Flex>
      </Col>
      <Col flex="328px">
        {/* 引导 */}
        <GuideCard />
        <VersionCard />
        <QuickActions onNavigate={navigate} />
      </Col>
    </Row>
  );
}

export default Index