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
import { Row, Col, Form, Flex, Tooltip, App } from 'antd';
import copy from 'copy-to-clipboard'

import type { Data } from './types'
import { userMemoryListUrl } from '@/api/memory';
import { useUser } from '@/store/user'
import RbCard from '@/components/RbCard/Card'
import SearchInput from '@/components/SearchInput';
import RbStatistic from '@/components/RbStatistic';
import MoreDropdown from '@/components/MoreDropdown'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import DeleteConfirmModal, { type DeleteConfirmModalRef } from './components/DeleteConfirmModal';

export default function UserMemory() {
  const { t } = useTranslation();
  const navigate = useNavigate()
  const { storageType } = useUser()
  const { message } = App.useApp()

  const [form] = Form.useForm()
  const keyword = Form.useWatch(['keyword'], form)

  const scrollListRef = useRef<PageScrollListRef>(null)
  const deleteConfirmModalRef = useRef<DeleteConfirmModalRef>(null)

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

  /** Copy value to clipboard and show success message */
  const handleCopy = (e: React.MouseEvent, value: string) => {
    e.preventDefault();
    e.stopPropagation();
    copy(value)
    message.success(t('common.copySuccess'))
  }

  /** Open delete confirmation modal */
  const handleDelete = (item: Data) => {
    deleteConfirmModalRef.current?.handleOpen(item);
  }

  // 获取用户显示名称
  const getUserName = (item: Data) => {
    return item?.end_user?.other_name && item?.end_user?.other_name !== '' 
      ? item?.end_user?.other_name 
      : item?.end_user?.id || ''
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
          const name = getUserName(item)
          return (
            <RbCard
              key={item.end_user?.id}
              title={() => <Flex gap={4}>
                <div className="rb:size-6 rb:text-center rb:font-semibold rb:leading-6 rb:rounded-md rb:text-white rb:bg-[#155EEF] rb:shrink-0">{name[0]}</div>

                <Tooltip title={name || '-'}><div className={`rb:flex-1 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap`}>{name || '-'}</div></Tooltip>
              </Flex>}
              extra={<MoreDropdown
                items={[
                  {
                    key: 'delete',
                    danger: true,
                    icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red_big.svg')]" />,
                    label: t('common.delete'),
                    onClick: (info) => {
                      info.domEvent?.stopPropagation();
                      handleDelete(item);
                    },
                  },
                ]}
              />}
              headerType="border"
              headerClassName="rb:h-[48px]! rb:mx-4!"
              bodyClassName="rb:py-3! rb:px-4!"
              className="rb:cursor-pointer"
              onClick={() => handleViewDetail(end_user?.id)}
            >
              <Flex align="center" gap={8} className="rb:mb-3! rb:w-full rb:cursor-pointer" onClick={(e) => handleCopy(e, end_user?.id || '')}>
                <div className="rb:text-[#5B6167]">ID:</div>
                <Flex align="center" gap={4}>
                  {end_user?.id || '-'}
                  <span className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/copy_dark.svg')]"></span>
                </Flex>
              </Flex>
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

      {/* 删除确认弹窗 */}
      <DeleteConfirmModal
        ref={deleteConfirmModalRef}
        refreshTable={() => scrollListRef.current?.refresh()}
      />
    </div>
  );
}