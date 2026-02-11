/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:35:41 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:35:41 
 */
/**
 * Order History Page
 * Displays order list with filtering by status, product type, and time range
 * Supports order detail viewing
 */

import React, { useRef, useState, useEffect } from 'react';
import { Button, Space, Select, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ColumnsType } from 'antd/es/table';
import type { SelectProps } from 'antd/es/select'
import dayjs from 'dayjs';

import Table, { type TableRef } from '@/components/Table'
import StatusTag from '@/components/StatusTag'
import { getOrderListUrl, getOrderStatus } from '@/api/order'
import { formatDateTime } from '@/utils/format';
import type { Order, OrderDetailRef, Query } from './types'
import OrderDetail from './components/OrderDetail'
import SearchInput from '@/components/SearchInput'


/** Order status mapping */
export const STATUS = {
  100: {
    status: 'warning',
    key: 'PENDING'
  },
  150: {
    key: 'APPROVED',
    status: 'success'
  },
  500: {
    key: 'REJECTED',
    status: 'error'
  }
}
const OrderHistory: React.FC = () => {
  const { t } = useTranslation();
  const orderDetailRef = useRef<OrderDetailRef>(null)
  const tableRef = useRef<TableRef>(null);
  const [query, setQuery] = useState<Query>({
    status: null,
    product_type: null,
    start_time: null,
    end_time: null
  } as Query)
  const [statusOptions, setStatusOptions] = useState<SelectProps['options']>([])
  const [timeType, setTimeType] = useState<string>('all')
  const timeOptions = [
    { label: t('pricing.allTime'), value: 'all' },
    { label: t('pricing.today'), value: 'today' },
    { label: t('pricing.week'), value: '7d' },
    { label: t('pricing.month'), value: '1month' },
    { label: t('pricing.threeMonth'), value: '3month' },
    { label: t('pricing.year'), value: '1year' },
  ]
  const productTypeOptions = [
    { label: t('pricing.allType'), value: null },
    { label: t('pricing.personal.type'), value: 'FREE' },
    { label: t('pricing.team.type'), value: 'TEAM' },
    { label: t('pricing.biz.type'), value: 'ENTERPRISE' },
    { label: t('pricing.commerce.type'), value: 'OEM' },
  ]

  const handleView = (order: Order) => {
    orderDetailRef.current?.handleOpen(order)
  }

  useEffect(() => {
    getStatus()
  }, [])
  /** Fetch order status options */
  const getStatus = () => {
    getOrderStatus()
      .then(res => {
        const response = res as Record<string, { value: number }>
        setStatusOptions([
          {
            label: t(`pricing.allStatus`),
            value: null
          },
          ...Object.keys(response).map(key => ({
            label: t(`pricing.${key}`),
            value: response[key].value
          }))
        ])
      })
  }
  /** Handle status filter change */
  const handleChangeStatus = (value: string) => {
    if (value !== query.status) {
      setQuery(prev => ({
        ...prev,
        status: value
      }))
    }
  }
  /** Handle product type filter change */
  const handleChangeType = (value: string) => {
    if (value !== query.product_type) {
      setQuery(prev => ({
        ...prev,
        product_type: value
      }))
    }
  }
  /** Handle time range filter change */
  const handleChangeTime = (value: string) => {
    setTimeType(value)
    let start_time = null;
    let end_time: number | null = dayjs().endOf('day').valueOf()

    switch(value) {
      case 'all':
        start_time = null;
        end_time = null
        break
      case 'today':
        start_time = dayjs().startOf('day').valueOf()
        break
      case '7d':
        start_time = dayjs().subtract(7, 'day').startOf('day').valueOf()
        break
      case '1month':
        start_time = dayjs().subtract(1, 'month').startOf('day').valueOf()
        break
      case '3month':
        start_time = dayjs().subtract(3, 'month').startOf('day').valueOf()
        break
      case '1year':
        start_time = dayjs().subtract(1, 'year').startOf('day').valueOf()
        break
    }
    setQuery(prev => ({
      ...prev,
      start_time,
      end_time
    }))
  }

  /** Map product type to translation key */
  const getProductType = (type: string) => {
    const typeMap: Record<string, string> = {
      'FREE': 'personal',
      'TEAM': 'team',
      'ENTERPRISE': 'biz',
      'OEM': 'commerce'
    };
    return typeMap[type] || 'ENTERPRISE';
  };
  /** Table column configuration */
  const columns: ColumnsType = [
    {
      title: t('pricing.order_no'),
      dataIndex: 'order_no',
      key: 'order_no',
      fixed: 'left',
    },
    {
      title: t('pricing.product_type'),
      dataIndex: 'product_type',
      key: 'product_type',
      render: (type) => t(`pricing.${getProductType(type)}.type`)
    },
    {
      title: t('pricing.payable_amount'),
      dataIndex: 'payable_amount',
      key: 'payable_amount',
      render: (amount: number) => `￥${amount}`,
    },
    {
      title: t('pricing.status'),
      dataIndex: 'status',
      key: 'status',
      render: (status: number) => <StatusTag status={STATUS[status as keyof typeof STATUS].status as 'warning' | 'success' | 'error'} text={t(`pricing.${STATUS[status as keyof typeof STATUS].key}`)} />
    },
    {
      title: t('pricing.pay_time'),
      dataIndex: 'pay_time',
      key: 'pay_time',
      render: (pay_time: unknown) => formatDateTime(pay_time as string, 'YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: t('common.operation'),
      key: 'action',
      fixed: 'right',
      render: (_, record) => (
        <Space size="large">
          <Button
            type="link"
            onClick={() => handleView(record as Order)}
          >
            {t(`common.viewDetail`)}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="rb:h-[calc(100vh-80px)] rb:overflow-hidden">
      <Flex justify="space-between" className="rb:mb-4!">
        <Space size={10}>
          <Select
            defaultValue={query.status}
            placeholder={t('common.select')}
            options={statusOptions}
            className="rb:w-30"
            onChange={handleChangeStatus}
          />
          <Select
            defaultValue={query.product_type}
            placeholder={t('common.select')}
            options={productTypeOptions}
            className="rb:w-30"
            onChange={handleChangeType}
          />
          <Select
            defaultValue={timeType}
            placeholder={t('common.select')}
            options={timeOptions}
            className="rb:w-30"
            onChange={handleChangeTime}
          />
        </Space>
        <SearchInput
          placeholder={t('pricing.searchPlaceholder')}
          onSearch={(value) => setQuery(prev => ({ ...prev, search: value }))}
          className="rb:w-70"
        />
      </Flex>
      <Table
        ref={tableRef}
        apiUrl={getOrderListUrl}
        apiParams={query}
        columns={columns}
        rowKey="id"
        currentPageKey="page_index"
        isScroll={true}
      />

      <OrderDetail ref={orderDetailRef} getProductType={getProductType} />
    </div>
  );
};

export default OrderHistory;