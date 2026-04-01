/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-24 15:41:20 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 16:20:32
 */
import { type FC, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import { Flex, Button } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { getAppLogsUrl } from '@/api/application';
import Table from '@/components/Table'
import { formatDateTime } from '@/utils/format';
import type { LogItem, LogDetailModalRef } from './types'
import LogDetailModal from './components/LogDetailModal'

const Statistics: FC = () => {
  const { t } = useTranslation();
  const { id } = useParams();
  const logDetailRef = useRef<LogDetailModalRef>(null);

  const handleViewDetail = (item: LogItem) => {
    logDetailRef.current?.handleOpen(item);
  }

  /** Table column configuration */
  const columns: ColumnsType<LogItem> = [
    {
      title: t('application.logTitle'),
      dataIndex: 'title',
      key: 'title',
      className: 'rb:text-[#212332]'
    },
    {
      title: t('application.created_at'),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (createdAt: string) => formatDateTime(createdAt, 'YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: t('common.updated_at'),
      dataIndex: 'updated_at',
      key: 'updated_at',
      render: (updatedAt: string) => updatedAt ? formatDateTime(updatedAt, 'YYYY-MM-DD HH:mm:ss') : '-',
    },
    {
      title: t('common.operation'),
      key: 'action',
      render: (_, record) => (
        <Flex wrap>
          <Button
            type="link"
            onClick={() => handleViewDetail(record as LogItem)}
          >
            {t('common.view')}
          </Button>
        </Flex>
      ),
    },
  ];
  return (
    <div className="rb:bg-white rb:rounded-lg rb:pt-3 rb:px-3">
      <Table<LogItem>
        apiUrl={getAppLogsUrl(id || '')}
        apiParams={{
          is_draft: false,
        }}
        columns={columns}
        rowKey="id"
        isScroll={true}
        scrollY="calc(100vh - 214px)"
      />
      <LogDetailModal ref={logDetailRef} />
    </div>
  );
}
export default Statistics;