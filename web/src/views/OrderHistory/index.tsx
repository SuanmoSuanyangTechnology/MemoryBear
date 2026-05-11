/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:35:41 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-08 17:36:40
 */
/**
 * Order History Page
 * Displays order list with filtering by status, product type, and time range
 * Supports order detail viewing
 */

import React, { useRef, useState, useCallback } from 'react';
import { Button, Space, Select, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ColumnsType } from 'antd/es/table';

import Table, { type TableRef } from '@/components/Table'
import StatusTag from '@/components/StatusTag'
import { formatDateTime } from '@/utils/format';
import type { Order, OrderDetailRef, Query } from './types'
import OrderDetail from './components/OrderDetail'
import { orderListUrl } from '@/api/package'
import { useI18n } from '@/store/locale'
import type { Package } from '@/views/Package/types'
import { STATUS, typeMap } from './constant'

const OrderHistory: React.FC = () => {
  const { t } = useTranslation();
  const { language } = useI18n()
  const orderDetailRef = useRef<OrderDetailRef>(null)
  const tableRef = useRef<TableRef>(null);
  const [query, setQuery] = useState<Query>({
    status: null,
    product_type: null,
    business_type: null,
  } as Query)

  const productTypeOptions = [
    { label: t('pricing.allType'), value: null },
    { label: t('package.saas_personal'), value: 'saas_personal' },
    { label: t('package.commercial_deployment'), value: 'commercial_deployment' },
    ...Object.keys(typeMap).map(type => ({
      label: t(`pricing.${typeMap[type] || 'ENTERPRISE'}.type`),
      value: type
    }))
  ]

  const businessTypeOptions = [
    { label: t('pricing.allBusinessType'), value: null },
    { label: t('pricing.purchase'), value: 'purchase' },
    { label: t('pricing.renewal'), value: 'renewal' },
    { label: t('pricing.upgrade'), value: 'upgrade' },
    { label: t('pricing.recharge'), value: 'recharge' },
    { label: t('pricing.free'), value: 'free' }
  ]

  const handleView = (order: Order) => {
    orderDetailRef.current?.handleOpen(order)
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
  const handleChangeBusinessType = (value: string) => {
    if (value !== query.business_type) {
      setQuery(prev => ({
        ...prev,
        business_type: value
      }))
    }
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
  
  const getKeyWithLanguage = useCallback((key: string) => {
    return (language === 'en' ? `${key}_en` : key) as keyof Package
  }, [language])
  /** Table column configuration */
  const columns: ColumnsType<Order> = [
    {
      title: t('pricing.order_no'),
      dataIndex: 'order_no',
      key: 'order_no',
      fixed: 'left',
    },
    {
      title: t('pricing.package_snapshot'),
      dataIndex: 'package_snapshot',
      key: 'package_snapshot',
      render: (package_snapshot, record) => {
        return record.from_view === 'platform' ? t(`pricing.${getProductType(record.product_type)}.type`) : package_snapshot[getKeyWithLanguage('name')] || '-'
      }
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
      render: (status: Order['status']) => <StatusTag status={STATUS[status].status} text={t(`pricing.${STATUS[status].key}`)} />
    },
    {
      title: t('pricing.business_type'),
      dataIndex: 'business_type',
      key: 'business_type',
      render: (business_type: Order['business_type']) => t(`pricing.${business_type}`)
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
    <div className="rb:h-full rb:overflow-hidden rb:bg-white rb:rounded-lg rb:pt-3 rb:px-3">
      <Flex className="rb:mb-3!" gap={10}>
        {/* 订单状态 pending/approved/rejected */}
        <Select
          defaultValue={query.status}
          placeholder={t('common.select')}
          options={[
            { label: t('pricing.allStatus'), value: null },
            ...(Object.keys(STATUS) as Array<keyof typeof STATUS>).map(status => ({
              value: status,
              label: t(`pricing.${STATUS[status].key}`)
            }))
          ]}
          className="rb:w-40"
          onChange={handleChangeStatus}
        />
        {/* 业务类型 purchase/renewal/recharge/free */}
        <Select
          defaultValue={query.business_type}
          placeholder={t('common.select')}
          options={businessTypeOptions}
          className="rb:w-40"
          onChange={handleChangeBusinessType}
        />
        {/* 产品类型 saas_personal/commercial_deployment */}
        <Select
          defaultValue={query.product_type}
          placeholder={t('common.select')}
          options={productTypeOptions}
          className="rb:w-40"
          onChange={handleChangeType}
        />
      </Flex>
      <Table<Order, Query>
        ref={tableRef}
        apiUrl={orderListUrl}
        apiParams={query}
        columns={columns}
        rowKey="id"
        isScroll={true}
      />

      <OrderDetail ref={orderDetailRef} getProductType={getProductType} />
    </div>
  );
};

export default OrderHistory;