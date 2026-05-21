/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-20 15:59:07 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-20 15:59:07 
 */
import { useState, useImperativeHandle, forwardRef, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import type { ColumnsType } from 'antd/es/table';

import type { HitHistoryDetailRef } from '../types';
import RbDrawer from '@/components/RbDrawer';
import { getAnnotationHitHistoryUrl } from '@/api/application';
import Table, { type TableRef } from '@/components/Table'
import { formatDateTime } from '@/utils/format';

interface HitHistoryItem {
  query: string;
  matched_question: string;
  answer: string;
  source: string;
  similarity: number;
  hit_at: number;
}

/**
 * Model list detail drawer component
 */
const HitHistoryDetail = forwardRef<HitHistoryDetailRef>((_props, ref) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const tableRef = useRef<TableRef>(null);
  const [id, setId] = useState<string>('');
  const [annotation_id, setAnnotationId] = useState<string>('');

  /** Open drawer with provider model data */
  const handleOpen = (id: string, annotation_id: string) => {
    setOpen(true)
    setId(id)
    setAnnotationId(annotation_id)
  }

  /** Close drawer */
  const handleClose = () => {
    setOpen(false)
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  const columns: ColumnsType<HitHistoryItem> = [
    {
      title: t('application.question'),
      dataIndex: 'query',
      key: 'query',
    },
    {
      title: t('application.matched_question'),
      dataIndex: 'matched_question',
      key: 'matched_question',
    },
    {
      title: t('application.reply'),
      dataIndex: 'answer',
      key: 'answer',
    },
    {
      title: t('application.source'),
      dataIndex: 'source',
      key: 'source',
    },
    {
      title: t('application.similarity'),
      dataIndex: 'similarity',
      key: 'similarity',
    },
    {
      title: t('application.hit_at'),
      dataIndex: 'hit_at',
      key: 'hit_at',
      render: (hitAtTime: number) => hitAtTime ? formatDateTime(hitAtTime, 'YYYY-MM-DD HH:mm:ss') : '-',
    },
  ];

  return (
    <RbDrawer
      title={t('application.hitHistory')}
      open={open}
      onClose={handleClose}
    >
      <Table<HitHistoryItem>
        ref={tableRef}
        apiUrl={getAnnotationHitHistoryUrl(id || '', annotation_id || '')}
        columns={columns}
        rowKey="id"
        isScroll={true}
        scrollY="calc(100vh - 242px)"
      />
    </RbDrawer>
  );
});

export default HitHistoryDetail;