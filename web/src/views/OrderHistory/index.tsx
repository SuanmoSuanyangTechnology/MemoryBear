/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:35:41 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-14 17:17:46
 */
/**
 * Order History Page
 * Displays order list with filtering by status, product type, and time range
 * Supports order detail viewing
 */

import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Button, Space, Select, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ColumnsType } from 'antd/es/table';
import { useLocation } from 'react-router-dom';

import Table, { type TableRef } from '@/components/Table'
import StatusTag from '@/components/StatusTag'
import { formatDateTime } from '@/utils/format';
import type { Order, GroupOrder, OrderDetailRef, Query, OrderItem } from './types'
import OrderDetail from './components/OrderDetail'
import { orderListUrl } from '@/api/package'
import { useI18n } from '@/store/locale'
import type { Package, ResourcePack, ResourcePackTier } from '@/views/Package/types'
import { STATUS, typeMap } from './constant'

const OrderHistory: React.FC = () => {
  const { t } = useTranslation();
  const { language } = useI18n()
  const location = useLocation();
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
    { label: t('package.resource_pack'), value: 'resource_pack' },
  ]

  const businessTypeOptions = [
    { label: t('pricing.allBusinessType'), value: null },
    { label: t('pricing.purchase'), value: 'purchase' },
    { label: t('pricing.renewal'), value: 'renewal' },
    { label: t('pricing.upgrade'), value: 'upgrade' },
    // { label: t('pricing.recharge'), value: 'recharge' },
    { label: t('pricing.free'), value: 'free' }
  ]

  useEffect(() => {
    if (location.state) {
      setQuery(location.state || {})
    }
  }, [location.state])

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
    // Check if type is a valid key in typeMap
    if (type in typeMap) {
      return typeMap[type as keyof typeof typeMap];
    }
    return 'ENTERPRISE';
  };
  
  const getKeyWithLanguage = useCallback((key: string) => {
    return (language === 'en' ? `${key}_en` : key) as keyof Package
  }, [language])
  const getGroupKeyWithLanguage = useCallback((key: string) => {
    return (language === 'en' ? `${key}_en` : `${key}_zh`) as keyof Package
  }, [language])
  const getProductName = (data: Order | GroupOrder) => {
    if (!data) return '-'
    if ((data as GroupOrder).orders?.length) {
      return (data as GroupOrder).orders.map((vo) => {
        const billing_units = [(vo.package_snapshot as ResourcePack).tier_snapshot].filter(Boolean)?.map((tier: ResourcePackTier) => {
          return Object.keys(tier.quota_grants).map(vo => `+${t(`package.${vo}`)}: ${tier.quota_grants[vo]}`)
        }).join(', ') || '-'
        return `${vo.package_snapshot[getGroupKeyWithLanguage('name') as keyof Order['package_snapshot']]} (${billing_units})×${vo.multiplier ?? 1}`
      }).join(', ')
    }
    if (((data as Order).package_snapshot as ResourcePack)?.billing_units?.length > 0) {
        const billing_units = [((data as Order).package_snapshot as ResourcePack).tier_snapshot].filter(Boolean)?.map((tier: ResourcePackTier) => {
          return Object.keys(tier.quota_grants).map(key => `+${t(`package.${key}`)}: ${tier.quota_grants[key]}`)
        }).join(', ') || '-'
        return `${((data as Order).package_snapshot as ResourcePack)[getGroupKeyWithLanguage('name') as keyof OrderItem['package_snapshot']]} (${billing_units})×${(data as Order).multiplier ?? 1}`
    }
    if ((data as Order).legacy_product_type) {
      return `${t(`pricing.${getProductType((data as Order).legacy_product_type as string)}.type`)}×${(data as Order).multiplier ?? 1}`
    }
    return `${((data as Order).package_snapshot as Package)?.[getKeyWithLanguage('name')] || '-'}×${(data as Order).multiplier ?? 1}`
  }
  /** Table column configuration */
  const columns: ColumnsType<Order | GroupOrder> = [
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
      render: (_, record) => getProductName(record)
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
      render: (business_type, record) => {
        return (record as GroupOrder).orders?.length
          ? t(`pricing.${(record as GroupOrder).orders[0].business_type}`)
          : t(`pricing.${business_type}`)
      }
    },
    {
      title: t('pricing.pay_time'),
      dataIndex: 'pay_time',
      key: 'pay_time',
      render: (pay_time, record) => {
        return (record as GroupOrder).orders?.length
          ? formatDateTime((record as GroupOrder).orders[0].pay_time)
          : formatDateTime(pay_time as string, 'YYYY-MM-DD HH:mm:ss')
      }
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
    <Flex vertical gap={12} className="rb:h-full! rb:overflow-hidden! rb:bg-white rb:rounded-lg rb:pt-3! rb:px-3!">
      <Flex gap={10}>
        {/* 订单状态 pending/approved/rejected */}
        <Select
          value={query.status}
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
          value={query.business_type}
          placeholder={t('common.select')}
          options={businessTypeOptions}
          className="rb:w-40"
          onChange={handleChangeBusinessType}
        />
        {/* 产品类型 saas_personal/commercial_deployment */}
        <Select
          value={query.product_type}
          placeholder={t('common.select')}
          options={productTypeOptions}
          className="rb:w-40"
          onChange={handleChangeType}
        />
      </Flex>
      <Table<Order | GroupOrder, Query>
        ref={tableRef}
        apiUrl={orderListUrl}
        apiParams={query}
        columns={columns}
        rowKey="order_no"
        fillHeight={true}
      />

      <OrderDetail ref={orderDetailRef}
        getProductName={getProductName}
      />
    </Flex>
  );
};

export default OrderHistory;