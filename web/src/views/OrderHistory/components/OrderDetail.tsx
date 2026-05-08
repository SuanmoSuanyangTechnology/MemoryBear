/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:35:49 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:35:49 
 */
/**
 * Order Detail Component
 * Modal displaying detailed order information including payment details
 */

import { forwardRef, useImperativeHandle, useState, useCallback, useMemo } from 'react';
import { Descriptions, type DescriptionsProps } from 'antd';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs';

import type { Order, OrderDetailRef } from '../types'
import RbModal from '@/components/RbModal'
import { STATUS } from '../constant';
import { getOrderDetail } from '@/api/package'
import { useI18n } from '@/store/locale'
import type { Package } from '@/views/Package/types'

const OrderDetail = forwardRef<OrderDetailRef, { getProductType: (type: string) => void; }>(({ getProductType }, ref) => {
  const { t } = useTranslation();
  const { language } = useI18n()
  const [visible, setVisible] = useState(false);
  const [data, setData] = useState<Order | null>(null)

  /** Close modal */
  const handleClose = () => {
    setVisible(false);
  };

  /** Open modal and fetch order details */
  const handleOpen = (order: Order) => {
    setVisible(true);
    getOrderDetail(order.id)
      .then(res => {
        setData(res as Order)
      })
  };

  const getKeyWithLanguage = useCallback((key: keyof Order['package_snapshot']) => {
    return (language === 'en' ? `${key}_en` : key) as keyof Package
  }, [language])
  /** Format order information items */
  const formatItems = useMemo(() => {
    if (!data) return []
    return ['order_no', 'package_snapshot', 'payable_amount', 'status', 'pay_time', 'created_at'].map(key => {
      const value = data[key as keyof Order]
      return {
        key,
        label: t(`pricing.${key}`),
        children: ['pay_time', 'created_at'].includes(key) && value
          ? dayjs(value as number).format('YYYY-MM-DD HH:mm:ss')
          : key === 'status' && value
            ? t(`pricing.${STATUS[value as keyof typeof STATUS].key}`)
            : key === 'package_snapshot'
              ? (data.from_view === 'platform' ? t(`pricing.${getProductType(data.product_type)}.type`) : (value as Package)[getKeyWithLanguage('name')])
              : value
      }
    })
  }, [data, t, getKeyWithLanguage, getProductType])
  /** Format payment information items */
  const formatPayItems = useMemo(() => {
    if (!data) return []
    return ['pay_txn_id', 'payer'].map(key => ({
      key,
      label: t(`pricing.${key}`),
      children: data[key as keyof Order]
    }))
  }, [data, t])

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('pricing.orderDetail')}
      open={visible}
      footer={null}
      onCancel={handleClose}
      width={1000}
    >
      <Descriptions title={t('pricing.orderInfo')} column={2} items={formatItems as DescriptionsProps['items']} classNames={{ label: 'rb:w-50' }} />
      <Descriptions title={t('pricing.orderPayInfo')} column={2} items={formatPayItems as DescriptionsProps['items']} classNames={{ label: 'rb:w-50' }} className="rb:mt-6!" />
    </RbModal>
  );
});

export default OrderDetail;