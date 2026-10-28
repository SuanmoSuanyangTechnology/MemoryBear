import { useRef, useState, type Key } from 'react';
import { App, Button, Flex, Form, Select, Space, Switch } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ColumnsType } from 'antd/es/table';
import type { TableProps } from 'antd';

import {
  getFaqListUrl,
  enabledFaq,
  deleteFaq,
  batchEnabledFaq,
  batchApproveFaq,
} from '@/api/kbWiki';
import SearchInput from '@/components/SearchInput';
import Table, { type TableRef } from '@/components/Table';
import StatusTag from '@/components/StatusTag';
import FaqEditModal, { type FaqEditModalRef } from './FaqEditModal';
import FaqReviewModal, { type FaqReviewModalRef } from './FaqReviewModal';
import FaqDetailDrawer, { type FaqDetailDrawerRef } from './FaqDetailDrawer';
import { formatDateTime } from '@/utils/format'
import type { FaqItem, FaqStatus, AllFaqStatus, FaqListResponse } from './types';
import { statusColors } from './constants';
import Tag from '@/components/Tag';

const statuses: AllFaqStatus[] = ['all', 'pending', 'approved', 'change_pending'];

export default function FaqWorkspace({ knowledgeBaseId, onOpenWikiPage }: {
  knowledgeBaseId: string;
  onOpenWikiPage: (pageId: string) => void;
}) {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const [form] = Form.useForm<{ review_status: FaqItem['review_status']; keywords?: string; is_enabled?: boolean; }>();
  const values = Form.useWatch([], form) ?? {};
  
  const [totalAll, setTotalAll] = useState<number>(0);
  const [reviewCounts, setReviewCounts] = useState<FaqListResponse['review_counts']>({} as FaqListResponse['review_counts'])
  const [selected, setSelected] = useState<Key[]>([]);
  const [selectedList, setSelectedList] = useState<FaqItem[]>([]);

  const editModalRef = useRef<FaqEditModalRef>(null);
  const faqReviewModalRef = useRef<FaqReviewModalRef>(null)
  const faqDetailDrawerRef = useRef<FaqDetailDrawerRef>(null)
  const tableRef = useRef<TableRef>(null);

  const [busy, setBusy] = useState(false);

  const rowSelection: TableProps<FaqItem>['rowSelection'] = {
    selectedRowKeys: selected,
    onChange: (selectedRowKeys: React.Key[], selectedRows: FaqItem[]) => {
      setSelected(selectedRowKeys)
      setSelectedList(selectedRows)
      console.log(`selectedRowKeys: ${selectedRowKeys}`, 'selectedRows: ', selectedRows);
    },
  };

  const refresh = () => {
    tableRef.current?.loadData()
    setSelected([])
    setSelectedList([])
  }

  const handleSwitch = (vo: FaqItem) => {
    setBusy(true)
    enabledFaq(knowledgeBaseId, vo.id, {
      expected_row_version: vo.row_version,
      is_enabled: !vo.is_enabled
    })
      .then(() => {
        message.success(t('common.operateSuccess'))
        refresh()
      })
      .finally(() => {
        setBusy(false)
      })
  }
  const handleBatchSwitch = (is_enabled: boolean) => {
    setBusy(true)
    batchEnabledFaq(knowledgeBaseId, {
      items: selectedList.map(vo => ({
        faq_id: vo.id,
        expected_row_version: vo.row_version
      })),
      is_enabled
    })
      .then(() => {
        message.success(t('common.operateSuccess'))
        refresh()
      })
      .finally(() => {
        setBusy(false)
      })
  }
  const handleBatchApprove = () => {
    setBusy(true)
    batchApproveFaq(knowledgeBaseId, {
      items: selectedList.map(vo => ({
        faq_id: vo.id
      }))
    })
      .then(() => {
        message.success(t('common.operateSuccess'))
        refresh()
      })
      .finally(() => {
        setBusy(false)
      })
  }
  const handleDelete = (vo: FaqItem) => {
      modal.confirm({
        title: t('common.confirmDeleteDesc', { name: vo.standard_question }),
        okText: t('common.delete'),
        cancelText: t('common.cancel'),
        okType: 'danger',
        onOk: () => {
          setBusy(true)
          deleteFaq(knowledgeBaseId, vo.id, vo.row_version)
            .then(() => {
              refresh()
              message.success(t('common.deleteSuccess'))
            })
            .finally(() => {
              setBusy(false)
            })
        }
      })
  }

  const onLoad = (res: FaqListResponse) => {
    setReviewCounts(res.review_counts)
    setTotalAll(res.total_all)
  }

  const column: ColumnsType<FaqItem> = [
    {
      title: t('knowledgeBase.faq.question&answer'),
      dataIndex: 'standard_question',
      render: (value, row) => (
        <Flex vertical gap={4} className="rb:max-w-120! rb:overflow-hidden! rb:cursor-pointer">
          <Flex align="center" gap={4} wrap className="rb:min-w-0">
            <span className="rb:min-w-0 rb:font-medium rb:text-gray-900 rb:hover:text-blue-500"
              onClick={() => faqDetailDrawerRef.current?.handleOpen(row)}
            >
              {value}
            </span>
            {row.source?.title && row.source.wiki_page_id && row.source.source_status === 'available' && (
              <Tag color="default" className="rb:hover:text-blue-500 rb:hover:border-[rgba(21,94,239,0.25)] rb:hover:bg-[rgba(21,94,239,0.06)]"
                onClick={(event) => {
                  event?.stopPropagation();
                  if (row.source.wiki_page_id) onOpenWikiPage(row.source.wiki_page_id);
                }}
              >
                {t('knowledgeBase.faq.sourceLabel', { name: row.source.title })}
              </Tag>
            )}
          </Flex>
          <div className="rb:text-gray-600 rb:truncate">{row.standard_answer}</div>
        </Flex>
      ),
    },
    {
      title: t('knowledgeBase.faq.reviewStatus'),
      dataIndex: 'review_status',
      width: '100px',
      render: (value: FaqStatus) => (
        value
          ? <StatusTag
            status={statusColors[value]}
            text={t(`knowledgeBase.faq.${value}`)}
          />
          : null
      ),
    },
    {
      title: t('knowledgeBase.faq.enable'),
      dataIndex: 'is_enabled',
      width: '80px',
      render: (value, row) => (
        <Switch checked={value} disabled={['pending', 'change_pending'].includes(row.review_status)} onChange={() => handleSwitch(row)} />
      ),
    },
    {
      title: t('knowledgeBase.faq.updatedAt'),
      dataIndex: 'updated_at_ms',
      width: '180px',
      render: (value) => formatDateTime(value),
    },
    {
      title: t('knowledgeBase.faq.actions'),
      width: '180px',
      fixed: 'right',
      render: (_, row) => (
        <Space size={8}>
          {['pending', 'change_pending'].includes(row.review_status) &&
            <Button
              type="link"
              disabled={busy}
              onClick={() => faqReviewModalRef.current?.handleOpen(row)}
            >
              {t('knowledgeBase.faq.approve')}
            </Button>
          }
          <Button type="link" onClick={() => faqDetailDrawerRef.current?.handleOpen(row, true)}>
            {t('knowledgeBase.faq.edit')}
          </Button>
          <Button type="link" danger disabled={busy}
            onClick={() => handleDelete(row)}
          >
            {t('knowledgeBase.faq.delete')}
          </Button>
        </Space>
      ),
    },
  ]

  console.log('selectedList', selectedList, busy)

  return (
    <Flex vertical gap={12} className="rb:pt-3! rb:pr-3! rb:h-full rb:overflow-auto">
      <Flex justify="space-between" gap={16} wrap>
        <Form
          form={form}
          initialValues={{ review_status: null, is_enabled: null }}
          layout="inline"
        >
          <Form.Item name="keyword">
            <SearchInput
              placeholder={t('knowledgeBase.faq.search')}
              variant="outlined"
            />
          </Form.Item>
          <Form.Item name="review_status">
            <Select
              options={statuses.map((key) => {
                const isAll = key === 'all';
                const labelKey = isAll ? 'allReviewStatus' : key;
                const count = (isAll ? totalAll : reviewCounts[key]) ?? 0
                return {
                  value: isAll ? null : key,
                  label: `${t(`knowledgeBase.faq.${labelKey}`)} (${count.toLocaleString()})`,
                }
              })}
              className="rb:w-40!"
            />
          </Form.Item>
          <Form.Item name="is_enabled">
            <Select
              options={['all', 'enable', 'disable'].map(value => ({
                value: value == 'all' ? null : value === 'enable',
                label: t(`knowledgeBase.faq.${value === 'all' ? 'allEnabledStatus' : value}`),
              }))}
              className="rb:w-36!"
            />
          </Form.Item>
        </Form>
        <Space size={12} wrap>
          <Button
            disabled={selectedList.filter(vo => ['pending', 'change_pending'].includes(vo.review_status)).length < 1 || busy}
            onClick={handleBatchApprove}
          >
            {t('knowledgeBase.faq.reviewApprove')}
          </Button>
          <Button
            disabled={selectedList.filter(vo => !vo.is_enabled).length < 1 || busy}
            onClick={() => handleBatchSwitch(true)}
          >
            {t('knowledgeBase.faq.enable')}
          </Button>
          <Button
            disabled={selectedList.filter(vo => vo.is_enabled).length < 1 || busy}
            onClick={() => handleBatchSwitch(false)}
          >
            {t('knowledgeBase.faq.disable')}
          </Button>
          <Button type="primary" onClick={() => editModalRef.current?.handleOpen()}>
            {t('knowledgeBase.faq.add')}
          </Button>
        </Space>
      </Flex>
      <Table<FaqItem>
        ref={tableRef}
        apiUrl={getFaqListUrl(knowledgeBaseId)}
        apiParams={values}
        rowKey="id"
        fillHeight={true}
        rowSelection={rowSelection}
        columns={column}
        onLoad={onLoad}
      />
      <FaqEditModal
        ref={editModalRef}
        knowledgeBaseId={knowledgeBaseId}
        refresh={refresh}
      />
      <FaqReviewModal
        ref={faqReviewModalRef}
        onOpenWikiPage={onOpenWikiPage}
        knowledgeBaseId={knowledgeBaseId}
        refresh={refresh}
        onEdit={(item: FaqItem) => {
          faqDetailDrawerRef.current?.handleOpen(item, true, () => {
            faqReviewModalRef.current?.handleOpen(item);
          });
        }}
      />
      <FaqDetailDrawer
        ref={faqDetailDrawerRef}
        onOpenWikiPage={onOpenWikiPage}
        knowledgeBaseId={knowledgeBaseId}
        refresh={refresh}
      />
    </Flex>
  );
}
