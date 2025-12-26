import { useEffect, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom'
import { Row, Col, Radio, Button, List, Skeleton, Space } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { RadioChangeEvent } from 'antd';
import { AppstoreOutlined, MenuOutlined } from '@ant-design/icons';
import Empty from '@/components/Empty'

import type { Data, ConfigModalRef } from './types'
import totalNum from '@/assets/images/memory/totalNum.svg'
import onlineNum from '@/assets/images/memory/onlineNum.svg'
import Table from '@/components/Table'
import { getTotalEndUsers, userMemoryListUrl, getUserMemoryList } from '@/api/memory';
import ConfigModal from './components/ConfigModal';
import { useUser } from '@/store/user'

const bgList = [
  'linear-gradient( 180deg, #F1F6FE 0%, #FBFDFF 100%)',
  'linear-gradient( 180deg, #F1F9FE 0%, #FBFDFF 100%)',
  'linear-gradient( 180deg, #FEFBF7 0%, #FBFDFF 100%)',
  'linear-gradient( 180deg, #F1F9FE 0%, #FBFDFF 100%)',
]

const countList = [
  'total_num', 'online_num',
]
const IconList: Record<string, string> = {
  total_num: totalNum,
  online_num: onlineNum,
}
export default function UserMemory() {
  const { t } = useTranslation();
  const navigate = useNavigate()
  const { storageType } = useUser()
  const configModalRef = useRef<ConfigModalRef>(null)
  const [loading, setLoading] = useState<boolean>(false);
  const [data, setData] = useState<Data[]>([]);
  const [countData, setCountData] = useState<Record<string, number>>({});
  const [layout, setLayout] = useState<'card' | 'list'>('card');

  // 获取数据
  useEffect(() => {
    getCountData()
    getData()
  }, []);

  // 用户记忆统计
  const getCountData = () => {
    getTotalEndUsers().then((res) => {
      setCountData(res as Record<string, number> || {})
    })
  }
  const getData = () => {
    setLoading(true)
    getUserMemoryList().then((res) => {
      setData(res as Data[] || [])
    })
    .finally(() => {
      setLoading(false)
    })
  }
  console.log('storageType', storageType)
  const handleViewDetail = (id: string | number) => {
    switch (storageType) {
      case 'neo4j':
        navigate(`/user-memory/neo4j/${id}`)
        break;
      default:
        navigate(`/user-memory/${id}`)
    }
  }
  const handleChangeLayout = (e: RadioChangeEvent) => {
    const type = e.target.value
    setLayout(type)
  }
  // 表格列配置
  const columns: ColumnsType = [
    {
      title: t('userMemory.user'),
      dataIndex: 'end_user',
      key: 'end_user',
      render: (value) => value?.other_name && value?.other_name !== '' ? value?.other_name : value?.id || '-'
    },
    {
      title: t('userMemory.knowledgeEntryCount'),
      dataIndex: 'memory_num',
      key: 'memory_num',
      render: (value) => value?.total || 0
    },
    {
      title: t('common.operation'),
      key: 'action',
      render: (_, record) => (
          <Button
            type="link"
            onClick={() => handleViewDetail(record.end_user?.id)}
          >
            {t('common.viewDetail')}
          </Button>
      ),
    },
  ];

  return (
    <div>
      <Row gutter={16} className="rb:mb-[16px]">
        {countList.map(key => (
          <Col key={key} span={6}>
            <div className="rb:bg-[#FBFDFF] rb:border-[1px] rb:border-[#DFE4ED] rb:rounded-[12px] rb:p-[18px_20px_20px_20px]">
              <div className="rb:text-[28px] rb:font-extrabold rb:leading-[35px] rb:flex rb:items-center rb:justify-between rb:mb-[12px]">
                {countData[key] || 0}{key === 'avgInteractionTime' ? 's' : ''}
                <img className="rb:w-[24px] rb:h-[24px]" src={IconList[key]} />
              </div>
              <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-[16px]">{t(`userMemory.${key}`)}</div>
            </div>
          </Col>
        ))}
        <Col span={12} className="rb:text-right">
          <Space>
            <Button type="primary" onClick={() => configModalRef?.current?.handleOpen()}>{t('userMemory.chooseModel')}</Button>
            <Radio.Group value={layout} onChange={handleChangeLayout}>
              <Radio.Button value="card" disabled={layout === 'card'}><AppstoreOutlined /></Radio.Button>
              <Radio.Button value="list" disabled={layout === 'list'}><MenuOutlined /></Radio.Button>
            </Radio.Group>
          </Space>
        </Col>
      </Row>
      {layout === 'card' &&
        <>
          {loading ? 
            <Skeleton active />
          : data.length > 0 ? (
            <List
              grid={{ gutter: 16, column: 4 }}
              dataSource={data}
              renderItem={(item, index) => {
                const { end_user, memory_num } = item as Data;
                const name = end_user?.other_name && end_user?.other_name !== '' ? end_user?.other_name : end_user?.id
                return (
                  <List.Item key={index}>
                    <div
                      className="rb:p-[20px] rb:rounded-[12px] rb:border-[1px] rb:border-[#DFE4ED] rb:cursor-pointer"
                      style={{
                        background: bgList[index % bgList.length],
                      }}
                      onClick={() => handleViewDetail(end_user.id)}
                    >
                      <div className="rb:flex rb:items-center">
                        <div className="rb:w-[48px] rb:h-[48px] rb:text-center rb:font-semibold rb:text-[28px] rb:leading-[48px] rb:rounded-[8px] rb:text-[#FBFDFF] rb:bg-[#155EEF]">{name[0]}</div>
                        <div className="rb:max-w-[calc(100%-60px)] rb:text-base rb:font-medium rb:leading-[24px] rb:ml-[12px] rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">
                          {name || '-'}<br/>
                        </div>
                      </div>
                      <div className="rb:grid rb:grid-cols-1 rb:gap-[12px] rb:mt-[28px] rb:mb-[28px]">
                        <div className="rb:text-center">
                          <div className="rb:text-[24px] rb:leading-[30px] rb:font-extrabold">{memory_num.total || 0}</div>
                          <div className="rb:break-words">{t(`userMemory.knowledgeEntryCount`)}</div>
                        </div>
                      </div>
                    </div>
                  </List.Item>
                )
              }}
            />
          ) : <Empty />}
        </>
      }

      {layout === 'list' &&
        <Table
          apiUrl={userMemoryListUrl}
          columns={columns}
          rowKey="end_user.id"
          pagination={false}
        />
      }
      <ConfigModal ref={configModalRef} />
    </div>
  );
}