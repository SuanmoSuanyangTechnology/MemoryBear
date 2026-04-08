/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:53:44 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-31 12:15:59
 */
/**
 * User Memory Page
 * Displays list of end users with their memory statistics and configuration
 */

import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom'
import { Row, Col, Form, Flex, Tooltip } from 'antd';

import type { Data } from './types'
import { userMemoryListUrl } from '@/api/memory';
import { useUser } from '@/store/user'
import RbCard from '@/components/RbCard/Card'
import SearchInput from '@/components/SearchInput';
import RbStatistic from '@/components/RbStatistic';
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'

export default function UserMemory() {
  const { t } = useTranslation();
  const navigate = useNavigate()
  const { storageType } = useUser()

  const [form] = Form.useForm()
  const keyword = Form.useWatch(['keyword'], form)

  const scrollListRef = useRef<PageScrollListRef>(null)

  /** Navigate to user memory detail */
  const handleViewDetail = (id: string | number) => {
    switch (storageType) {
      case 'neo4j':
        navigate(`/user-memory/neo4j/${id}`)
        break;
      default:
        navigate(`/user-memory/${id}`)
    }
  }
  /** Navigate to memory configuration */
  const handleViewMemoryConfig = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    navigate(`/memory`)
  }

  return (
    <div>
      <Form form={form}>
        <Row gutter={16} className="rb:mb-4">
          <Col span={8}>
            <Form.Item name="keyword" noStyle>
              <SearchInput
                placeholder={t('userMemory.searchPlaceholder')}
                className="rb:w-full!"
              />
            </Form.Item>
          </Col>
        </Row>
      </Form>

      
      <PageScrollList<Data, { keyword: string; }>
        ref={scrollListRef}
        url={userMemoryListUrl}
        query={{ keyword }}
        column={3}
        renderItem={(item) => {
          const { end_user, memory_num, memory_config } = item as Data;
          const name = end_user?.other_name && end_user?.other_name !== '' ? end_user?.other_name : end_user?.id
          return (
            <RbCard
              key={item.end_user.id}
              title={<Flex gap={4}>
                <div className="rb:size-6 rb:text-center rb:font-semibold rb:leading-6 rb:rounded-md rb:text-white rb:bg-[#155EEF]">{name[0]}</div>

                <Tooltip title={name || '-'}><div className={`rb:flex-1 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap`}>{name || '-'}</div></Tooltip>
              </Flex>}
              headerType="border"
              headerClassName="rb:h-[48px]! rb:mx-4!"
              bodyClassName="rb:py-3! rb:px-4!"
              className="rb:cursor-pointer"
              onClick={() => handleViewDetail(end_user.id)}
            >
              <Row>
                <Col span={12}>
                  <RbStatistic title={t('userMemory.capacity')} value={memory_num?.total || 0} suffix={t('userMemory.memoryNum')} />
                </Col>
                <Col span={12}>
                  <RbStatistic title={t('userMemory.type')} value={t(`userMemory.${item.type || 'person'}`)} />
                </Col>
              </Row>

              <div className="rb:relative rb:z-2 rb:mt-3 rb:bg-[#F6F6F6] rb:rounded-lg rb:py-2 rb:px-3 rb:leading-5" onClick={handleViewMemoryConfig}>
                <Flex align="center" justify="space-between" className="rb:text-[#5B6167]">
                  {t('userMemory.memory_config_name')}
                  <div
                    className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/userMemory/arrow_right_dark.svg')]"
                  ></div>
                </Flex>
                <div className="rb:font-medium rb:text-[#212332] rb:mt-1">{memory_config?.memory_config_name || '-'}</div>
              </div>
            </RbCard>
          )
        }}
      />
    </div>
  );
}