import { useEffect, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom'
import { Row, Col, List, Skeleton } from 'antd';
import Empty from '@/components/Empty'

import type { Data } from './types'
import { getUserMemoryList } from '@/api/memory';
import { useUser } from '@/store/user'
import RbCard from '@/components/RbCard/Card'
import SearchInput from '@/components/SearchInput';

export default function UserMemory() {
  const { t } = useTranslation();
  const navigate = useNavigate()
  const { storageType } = useUser()
  const [loading, setLoading] = useState<boolean>(false);
  const [data, setData] = useState<Data[]>([]);
  const [search, setSearch] = useState<string | undefined>(undefined);

  // 获取数据
  useEffect(() => {
    getData()
  }, []);

  const getData = () => {
    setLoading(true)
    getUserMemoryList().then((res) => {
      setData(res as Data[] || [])
    })
    .finally(() => {
      setLoading(false)
    })
  }
  const handleViewDetail = (id: string | number) => {
    switch (storageType) {
      case 'neo4j':
        navigate(`/user-memory/neo4j/${id}`)
        break;
      default:
        navigate(`/user-memory/${id}`)
    }
  }
  const handleViewMemoryConfig = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    navigate(`/memory`)
  }

  const filterData = useMemo(() => {
    if (search && search.trim() !== '') {
      return data.filter((item) => {
        const { end_user } = item as Data;
        const name = end_user?.other_name && end_user?.other_name !== '' ? end_user?.other_name : end_user?.id
        return name?.includes(search)
      })
    }

    return data
  }, [search, data])

  return (
    <div>
      <Row gutter={16} className="rb:mb-4">
        <Col span={8}>
          <SearchInput
            placeholder={t('userMemory.searchPlaceholder')}
            onSearch={(value) => setSearch(value)}
            style={{ width: '100%' }}
          />
        </Col>
      </Row>
      {loading ?
        <Skeleton active />
        : filterData.length > 0 ? (
          <List
            grid={{ gutter: 16, column: 3 }}
            dataSource={filterData}
            renderItem={(item, index) => {
              const { end_user, memory_num, memory_config } = item as Data;
              const name = end_user?.other_name && end_user?.other_name !== '' ? end_user?.other_name : end_user?.id
              return (
                <List.Item key={index}>
                  <RbCard
                    avatar={<div className="rb:w-12 rb:h-12 rb:text-center rb:font-semibold rb:text-[28px] rb:leading-12 rb:rounded-lg rb:text-[#FBFDFF] rb:bg-[#155EEF] rb:mr-2">{name[0]}</div>}
                    title={name || '-'}
                    extra={<div
                      className="rb:w-7 rb:h-7 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/userMemory/goto.svg')]"
                    ></div>}
                    className="rb:cursor-pointer"
                    onClick={() => handleViewDetail(end_user.id)}
                  >
                    <div className="rb:flex rb:justify-between rb:items-center">
                      <div>{t('userMemory.capacity')}</div>
                      <div>{memory_num?.total || 0} {t('userMemory.memoryNum')}</div>
                    </div>
                    <div className="rb:flex rb:justify-between rb:items-center rb:mt-2.5">
                      <div>{t('userMemory.type')}</div>
                      <div>{t(`userMemory.${item.type || 'person'}`)}</div>
                    </div>

                    <div className="rb:relative rb:z-2 rb:mt-3 rb:bg-[#F6F8FC] rb:rounded-lg rb:border rb:border-[#DFE4ED] rb:py-2 rb:px-3" onClick={handleViewMemoryConfig}>
                      <div className="rb:text-[#5B6167] rb:leading-5 rb:flex rb:justify-between rb:items-center">
                        {t('userMemory.memory_config_name')}
                        <div
                          className="rb:w-7 rb:h-7 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/userMemory/arrow_right.svg')]"
                        ></div>
                      </div>
                      <div className="rb:font-medium rb:leading-5 rb:mt-1">{memory_config?.memory_config_name || '-'}</div>
                    </div>
                  </RbCard>
                </List.Item>
              )
            }}
          />
        ) : <Empty />
      }
    </div>
  );
}